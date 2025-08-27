import os
from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Body, Path
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import io
import zipfile
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from typing import List, Optional, Dict
from collections import defaultdict
from dotenv import load_dotenv
from mangum import Mangum
from pydantic import BaseModel
from datetime import datetime, timezone
from fastapi import Request

# Load environment variables from .env if present
load_dotenv()

# For AWS Lambda, credentials and region are automatically provided by the environment.
# Only S3_BUCKET should be loaded from environment variables.
S3_BUCKET = os.getenv("S3_BUCKET")

# Use default boto3 session (credentials and region are handled by Lambda)
s3_client = boto3.client("s3")

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

class FileItem(BaseModel):
    name: str
    device: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM:SS
    part: Optional[str] = None
    ext: str
    patient: Optional[str] = None

class DayFiles(BaseModel):
    date: str
    files: List[str]
# ...existing code...

# New endpoint: group files by day
@app.get("/files/by-day/", response_model=List[DayFiles])
def list_files_by_day():
    """
    Returns files grouped by date, each with a list of filenames for that day.
    """
    try:
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET)
        contents = response.get("Contents", [])
        files_by_day = defaultdict(list)
        for obj in contents:
            key = obj["Key"]
            fi = parse_file_name(key)
            if fi.date:
                files_by_day[fi.date].append(fi.name)
        result = [DayFiles(date=day, files=sorted(files)) for day, files in sorted(files_by_day.items())]
        return result
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

# New endpoint: download ZIP of all files for a given day
@app.post("/download-zip-by-day/")
def download_zip_by_day(date: str = Body(..., embed=True)):
    """
    Create a ZIP of all S3 files for a given date and return a presigned download URL.
    Body: { "date": "YYYY-MM-DD" }
    """
    try:
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET)
        contents = response.get("Contents", [])
        selected_keys = []
        for obj in contents:
            key = obj["Key"]
            fi = parse_file_name(key)
            if fi.date == date:
                selected_keys.append(key)
        if not selected_keys:
            raise HTTPException(status_code=404, detail="No files found for this date.")
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zipf:
            for key in selected_keys:
                s3_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                file_bytes = s3_obj["Body"].read()
                zipf.writestr(key, file_bytes)
        zip_buffer.seek(0)
        zip_key = f"{date}_files.zip"
        s3_client.upload_fileobj(zip_buffer, S3_BUCKET, zip_key)
        url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": S3_BUCKET, "Key": zip_key},
            ExpiresIn=3600
        )
        return {"download_url": url}
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

class DevicePatientRecord(BaseModel):
    device: str
    patient: Optional[str] = None
    updatedAt: Optional[str] = None

def parse_file_name(key: str) -> FileItem:
    name = os.path.basename(key)

    # extension from last dot
    last_dot = name.rfind(".")
    ext = name[last_dot + 1:] if last_dot > -1 else ""

    # split into at most 4 segments: device, yyyymmdd, hhmmss, remainder (part+ext)
    parts = name.split("_", 3)
    device = parts[0] if len(parts) > 0 else ""
    ymd = parts[1] if len(parts) > 1 else ""
    hms = parts[2] if len(parts) > 2 else ""
    remainder = parts[3] if len(parts) > 3 else ""

    # date
    yyyy = ymd[0:4] if len(ymd) >= 4 else ""
    mm = ymd[4:6] if len(ymd) >= 6 else ""
    dd = ymd[6:8] if len(ymd) >= 8 else ""
    date = f"{yyyy}-{mm}-{dd}" if (yyyy and mm and dd) else ""

    # time
    hh = hms[0:2] if len(hms) >= 2 else ""
    mi = hms[2:4] if len(hms) >= 4 else ""
    ss = hms[4:6] if len(hms) >= 6 else ""
    time = f"{hh}:{mi}:{ss}" if (hh and mi and ss) else ""

    # part = text before first dot in the remainder (if any)
    part = None
    if remainder:
        dot_idx = remainder.find(".")
        part = remainder[:dot_idx] if dot_idx > -1 else remainder or None

    return FileItem(name=name, device=device, date=date, time=time, part=part, ext=ext)

@app.post("/upload/")
async def upload_file(file: UploadFile = File(...)):
    try:
        if not file.filename.endswith('.txt'):
            raise HTTPException(status_code=400, detail="Only .txt files are allowed.")
        s3_client.upload_fileobj(file.file, S3_BUCKET, file.filename)
        return {"filename": file.filename, "message": "Upload successful"}
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/files/", response_model=List[str])
def list_files():
    try:
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET)
        contents = response.get("Contents", [])
        return [obj["Key"] for obj in contents]
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

from typing import Any
@app.get("/files/metadata/")
def get_files_metadata() -> Dict[str, Any]:
    try:
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET)
        contents = response.get("Contents", [])
        keys = [obj["Key"] for obj in contents]

        # Load device→patient mapping from DynamoDB
        mapping: Dict[str, Optional[str]] = {}
        table = _get_ddb_table()
        scan_kwargs: Dict = {"ProjectionExpression": "device, patient"}
        while True:
            dresp = table.scan(**scan_kwargs)
            for it in dresp.get("Items", []):
                dev = it.get("device")
                pat = it.get("patient")
                if dev:
                    mapping[dev] = pat if (pat is not None and pat != "") else None
            if "LastEvaluatedKey" in dresp:
                scan_kwargs["ExclusiveStartKey"] = dresp["LastEvaluatedKey"]
            else:
                break

        from collections import defaultdict
        # Group by (date, device)
        grouped = defaultdict(lambda: {"files": [], "patient": None})
        for k in keys:
            fi = parse_file_name(k)
            pat = mapping.get(fi.device)
            if fi.date and fi.device:
                key = (fi.date, fi.device)
                file_record = {
                    "time": fi.time,
                    "ext": fi.ext,
                    "part": fi.part,
                    "fullname": k
                }
                grouped[key]["files"].append(file_record)
                grouped[key]["patient"] = pat if (pat is not None and pat != "") else "none"
        # Convert to desired output format
        result = []
        for (date, device), value in grouped.items():
            result.append({
                "device": device,
                "date": date,
                "files": value["files"],
                "patient": value["patient"]
            })
        return {"data": result, "error": None}
    except (BotoCoreError, ClientError, Exception) as e:
        return {"data": [], "error": str(e)}

@app.get("/download/{filename}")
def download_file(filename: str):
    try:
        fileobj = s3_client.get_object(Bucket=S3_BUCKET, Key=filename)["Body"]
        return StreamingResponse(
            fileobj,
            media_type="text/plain",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/plain"
            }
        )
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/generate-upload-url/")
async def generate_upload_url(filename: str = Query(...), request: Request = None):
    """
    Optionally accepts 'tags' as a query parameter (tags as key1=value1&key2=value2).
    """
    try:
        tags = request.query_params.get("tags") if request else None
        params = {"Bucket": S3_BUCKET, "Key": filename}
        if tags:
            params["Tagging"] = tags
        url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params=params,
            ExpiresIn=3600
        )
        return {"upload_url": url}
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/generate-download-url/")
def generate_download_url(filename: str = Query(...)):
    try:
        url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": S3_BUCKET, "Key": filename},
            ExpiresIn=3600  # URL valid for 1 hour
        )
        return {"download_url": url}
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/missing-files/")
def missing_files(filenames: List[str] = Body(...)):
    """
    Given a list of filenames, return the ones not present in S3.
    """
    try:
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET)
        s3_files = set(obj["Key"] for obj in response.get("Contents", []))
        missing = [f for f in filenames if f not in s3_files]
        return {"missing_files": missing}
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download-all-url/")
def download_all_url():
    """
    Create a ZIP of all S3 files, upload to S3, and return a presigned download URL.
    """
    try:
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET)
        contents = response.get("Contents", [])
        if not contents:
            raise HTTPException(status_code=404, detail="No files found in S3 bucket.")
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zipf:
            for obj in contents:
                key = obj["Key"]
                s3_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                file_bytes = s3_obj["Body"].read()
                zipf.writestr(key, file_bytes)
        zip_buffer.seek(0)
        zip_key = "all_files.zip"
        # Upload ZIP to S3
        s3_client.upload_fileobj(zip_buffer, S3_BUCKET, zip_key)
        # Generate presigned URL
        url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": S3_BUCKET, "Key": zip_key},
            ExpiresIn=3600  # 1 hour
        )
        return {"download_url": url}
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------- DynamoDB helpers ----------------------

def _get_ddb_table():
    table_name = os.getenv("DDB_TABLE")
    if not table_name:
        raise HTTPException(status_code=500, detail="DDB_TABLE env not set")
    ddb = boto3.resource("dynamodb")
    return ddb.Table(table_name)

# ---------------------- DynamoDB mapping endpoints ----------------------
@app.get("/ddb/device-patient-map", response_model=List[DevicePatientRecord])
def ddb_get_device_patient_map():
    """Return full list of records with device, patient, updatedAt from DynamoDB."""
    try:
        table = _get_ddb_table()
        records: List[DevicePatientRecord] = []
        scan_kwargs: Dict = {"ProjectionExpression": "device, patient, updatedAt"}
        while True:
            resp = table.scan(**scan_kwargs)
            for it in resp.get("Items", []):
                records.append(DevicePatientRecord(
                    device=it.get("device", ""),
                    patient=it.get("patient"),
                    updatedAt=it.get("updatedAt")
                ))
            if "LastEvaluatedKey" in resp:
                scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            else:
                break
        return records
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ddb/device-patient-map/details", response_model=List[DevicePatientRecord])
def ddb_get_device_patient_map_details():
    """Return full records with device, patient, updatedAt from DynamoDB."""
    try:
        table = _get_ddb_table()
        records: List[DevicePatientRecord] = []
        scan_kwargs: Dict = {"ProjectionExpression": "device, patient, updatedAt"}
        while True:
            resp = table.scan(**scan_kwargs)
            for it in resp.get("Items", []):
                records.append(DevicePatientRecord(
                    device=it.get("device", ""),
                    patient=it.get("patient"),
                    updatedAt=it.get("updatedAt")
                ))
            if "LastEvaluatedKey" in resp:
                scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            else:
                break
        return records
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ddb/device-patient-map/{device}")
def ddb_get_device_mapping(device: str):
    try:
        table = _get_ddb_table()
        resp = table.get_item(Key={"device": device})
        item = resp.get("Item")
        if not item:
            raise HTTPException(status_code=404, detail="Device not found")
        return {"device": device, "patient": item.get("patient"), "updatedAt": item.get("updatedAt")}
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/ddb/device-patient-map", response_model=List[DevicePatientRecord])
def ddb_put_device_patient_map(mapping: Dict[str, str] = Body(...)):
    """Replace the map by writing items and return full records (device, patient, updatedAt)."""
    try:
        table = _get_ddb_table()
        written: List[DevicePatientRecord] = []
        devices = list(mapping.keys())
        for i in range(0, len(devices), 25):
            chunk = devices[i:i+25]
            with table.batch_writer() as batch:
                for d in chunk:
                    ts = datetime.now(timezone.utc).isoformat()
                    batch.put_item(Item={
                        "device": d,
                        "patient": mapping[d],
                        "updatedAt": ts,
                    })
                    written.append(DevicePatientRecord(device=d, patient=mapping[d], updatedAt=ts))
        return written
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/ddb/device-patient-map/{device}")
def ddb_put_device_mapping(device: str, payload: Dict[str, str] = Body(...)):
    patient = payload.get("patient")
    if not patient:
        raise HTTPException(status_code=400, detail="'patient' is required")
    try:
        table = _get_ddb_table()
        ts = datetime.now(timezone.utc).isoformat()
        table.put_item(Item={
            "device": device,
            "patient": patient,
            "updatedAt": ts,
        })
        return {"device": device, "patient": patient, "updatedAt": ts}
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/ddb/device-patient-map/{device}")
def ddb_delete_device_mapping(device: str):
    try:
        table = _get_ddb_table()
        resp = table.delete_item(
            Key={"device": device},
            ConditionExpression="attribute_exists(device)",
            ReturnValues="ALL_OLD",
        )
        attrs = resp.get("Attributes", {}) or {}
        return {
            "device": attrs.get("device", device),
            "patient": attrs.get("patient"),
            "updatedAt": attrs.get("updatedAt"),
            "deleted": True,
        }
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise HTTPException(status_code=404, detail="Device not found")
        raise HTTPException(status_code=500, detail=str(e))
    except BotoCoreError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/devices/unregistered", response_model=List[str])
def get_unregistered_devices():
    """Return devices present in S3 filenames but missing in DynamoDB mapping."""
    try:
        # Collect unique devices from S3 object keys
        devices_in_s3 = set()
        resp = s3_client.list_objects_v2(Bucket=S3_BUCKET)
        contents = resp.get("Contents", [])
        for obj in contents:
            key = obj.get("Key")
            if not key:
                continue
            dev = parse_file_name(key).device
            if dev:
                devices_in_s3.add(dev)
        while resp.get("IsTruncated"):
            resp = s3_client.list_objects_v2(
                Bucket=S3_BUCKET,
                ContinuationToken=resp.get("NextContinuationToken")
            )
            for obj in resp.get("Contents", []):
                key = obj.get("Key")
                if not key:
                    continue
                dev = parse_file_name(key).device
                if dev:
                    devices_in_s3.add(dev)

        # Collect registered devices from DynamoDB
        table = _get_ddb_table()
        registered = set()
        scan_kwargs: Dict = {"ProjectionExpression": "device"}
        while True:
            dresp = table.scan(**scan_kwargs)
            for it in dresp.get("Items", []):
                dev = it.get("device")
                if dev:
                    registered.add(dev)
            if "LastEvaluatedKey" in dresp:
                scan_kwargs["ExclusiveStartKey"] = dresp["LastEvaluatedKey"]
            else:
                break

        missing = sorted(list(devices_in_s3 - registered))
        return missing
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/patients", response_model=List[str])
def list_unique_patients():
    """Return a sorted unique list of patient names from DynamoDB (exclude empty/null)."""
    try:
        table = _get_ddb_table()
        patients = set()
        scan_kwargs: Dict = {"ProjectionExpression": "patient"}
        while True:
            resp = table.scan(**scan_kwargs)
            for it in resp.get("Items", []):
                p = it.get("patient")
                if p is not None and str(p).strip() != "":
                    patients.add(str(p))
            if "LastEvaluatedKey" in resp:
                scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            else:
                break
        return sorted(patients)
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

handler = Mangum(app)

# Endpoint: download ZIP of files for a user and date (accepts metadata file list)
@app.post("/download-zip-by-user-date/")
def download_zip_by_user_date(files: List[Dict] = Body(...)):
    """
    Accepts the 'files' array from metadata (list of dicts), extracts 'fullname' from each, zips those files, uploads the ZIP to S3, and returns a presigned download URL.
    Body: [ {"fullname": "file1.txt", ...}, ... ]
    """
    try:
        if not files:
            raise HTTPException(status_code=400, detail="No files provided.")
        filenames = [f.get("fullname") for f in files if f.get("fullname")]
        if not filenames:
            raise HTTPException(status_code=400, detail="No valid 'fullname' fields found.")
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zipf:
            for key in filenames:
                try:
                    s3_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                    file_bytes = s3_obj["Body"].read()
                    zipf.writestr(key, file_bytes)
                except (BotoCoreError, ClientError) as e:
                    raise HTTPException(status_code=404, detail=f"File not found: {key}")
        zip_buffer.seek(0)
        # Use first file's device and date for ZIP name if available
        zip_key = "user_date_files.zip"
        if files and files[0].get("fullname"):
            first = files[0]["fullname"]
            parts = first.split("_")
            if len(parts) >= 3:
                device = parts[0]
                ymd = parts[1]
                zip_key = f"{device}_{ymd}_files.zip"
        s3_client.upload_fileobj(zip_buffer, S3_BUCKET, zip_key)
        url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": S3_BUCKET, "Key": zip_key},
            ExpiresIn=3600
        )
        return {"download_url": url}
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))
