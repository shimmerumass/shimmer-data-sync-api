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

# Tunable grouping window for /files/combined-meta/ (seconds)
GROUP_WINDOW_SECONDS = 15
from dotenv import load_dotenv
from mangum import Mangum
from pydantic import BaseModel
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import Request
import struct
from shimmerCaliberate import read_shimmer_dat

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
    shimmer1: Optional[str] = None
    shimmer2: Optional[str] = None
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
        # Read file bytes for decoding
        file_bytes = await file.read()
        # Upload to S3
        file.file.seek(0)
        s3_client.upload_fileobj(io.BytesIO(file_bytes), S3_BUCKET, file.filename)

        # Decode header (reuse decode_shimmer_header from combined-meta)
        def decode_shimmer_header(file_bytes):
            HEADER_LENGTH = 256
            if len(file_bytes) < HEADER_LENGTH:
                return {}
            header = file_bytes[:HEADER_LENGTH]
            def get_byte(offset):
                return header[offset]
            def get_bytes(offset, length):
                return header[offset:offset+length]
            SDH_MAC_ADDR_C_OFFSET = 24
            MAC_ADDRESS_LENGTH = 6
            mac_bytes = get_bytes(SDH_MAC_ADDR_C_OFFSET, MAC_ADDRESS_LENGTH)
            mac_address = ':'.join(f'{b:02X}' for b in mac_bytes)
            SDH_SAMPLE_RATE_0 = 0
            sample_rate_ticks = struct.unpack('<H', get_bytes(SDH_SAMPLE_RATE_0, 2))[0]
            sample_rate = 32768 / sample_rate_ticks if sample_rate_ticks else None
            SDH_SENSORS0 = 3
            SDH_SENSORS1 = 4
            SDH_SENSORS2 = 5
            sensors0 = get_byte(SDH_SENSORS0)
            sensors1 = get_byte(SDH_SENSORS1)
            sensors2 = get_byte(SDH_SENSORS2)
            SDH_CONFIG_SETUP_BYTE3 = 11
            configByte3 = get_byte(SDH_CONFIG_SETUP_BYTE3)
            SDH_TRIAL_CONFIG0 = 16
            SDH_TRIAL_CONFIG1 = 17
            trialConfig0 = get_byte(SDH_TRIAL_CONFIG0)
            trialConfig1 = get_byte(SDH_TRIAL_CONFIG1)
            SDH_SHIMMERVERSION_BYTE_0 = 30
            shimmer_version = struct.unpack('>H', get_bytes(SDH_SHIMMERVERSION_BYTE_0, 2))[0]
            SDH_MYTRIAL_ID = 32
            experiment_id = get_byte(SDH_MYTRIAL_ID)
            SDH_NSHIMMER = 33
            n_shimmer = get_byte(SDH_NSHIMMER)
            SDH_FW_VERSION_TYPE_0 = 34
            SDH_FW_VERSION_MAJOR_0 = 36
            SDH_FW_VERSION_MINOR = 38
            SDH_FW_VERSION_INTERNAL = 39
            fw_type = struct.unpack('>H', get_bytes(SDH_FW_VERSION_TYPE_0, 2))[0]
            fw_major = struct.unpack('>H', get_bytes(SDH_FW_VERSION_MAJOR_0, 2))[0]
            fw_minor = get_byte(SDH_FW_VERSION_MINOR)
            fw_internal = get_byte(SDH_FW_VERSION_INTERNAL)
            return {
                "mac_address": mac_address,
                "sample_rate": sample_rate,
                "sensors0": sensors0,
                "sensors1": sensors1,
                "sensors2": sensors2,
                "configByte3": configByte3,
                "trialConfig0": trialConfig0,
                "trialConfig1": trialConfig1,
                "shimmer_version": shimmer_version,
                "experiment_id": experiment_id,
                "n_shimmer": n_shimmer,
                "fw_type": fw_type,
                "fw_major": fw_major,
                "fw_minor": fw_minor,
                "fw_internal": fw_internal
            }

        # Parse filename for metadata
        def parse_custom_filename(fname):
            parts = fname.split("__")
            device = parts[0] if len(parts) > 0 else "none"
            timestamp = parts[1] if len(parts) > 1 else "none"
            experiment_name = parts[2] if len(parts) > 2 else "none"
            shimmer_field = parts[3] if len(parts) > 3 else "none"
            filename = parts[5] if len(parts) > 5 else "none"
            shimmer_device = shimmer_field
            shimmer_day = "none"
            if shimmer_field != "none" and "-" in shimmer_field:
                shimmer_device, shimmer_day = shimmer_field.rsplit("-", 1)
            ext = ""
            part = None
            if filename and "." in filename:
                ext = filename.split(".")[-1]
                part = filename.split(".")[0]
            elif filename:
                part = filename
            date = "none"
            time = "none"
            if timestamp and "_" in timestamp:
                ymd, hms = timestamp.split("_", 1)
                if len(ymd) == 8 and len(hms) == 6:
                    date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
                    time = f"{hms[:2]}:{hms[2:4]}:{hms[4:6]}"
            return {
                "full_file_name": fname,
                "device": device,
                "timestamp": timestamp,
                "date": date,
                "time": time,
                "experiment_name": experiment_name,
                "shimmer_device": shimmer_device,
                "shimmer_day": shimmer_day,
                "filename": filename,
                "ext": ext,
                "part": part
            }

        meta = parse_custom_filename(file.filename)
        decoded = decode_shimmer_header(file_bytes)

        # Combine metadata and decoded info
        item = {**meta, **decoded, "updatedAt": datetime.now(timezone.utc).isoformat()}

        # Store in DynamoDB (use separate table for file metadata)
        file_table_name = os.getenv("DDB_FILE_TABLE")
        if not file_table_name:
            raise HTTPException(status_code=500, detail="DDB_FILE_TABLE env not set")
        ddb = boto3.resource("dynamodb")
        file_table = ddb.Table(file_table_name)
        file_table.put_item(Item=item)

        return {"filename": file.filename, "message": "Upload and decode successful", "ddb_item": item}
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
        # Group by (device, date)
        def parse_custom_filename(fname):
            parts = fname.split("__")
            device = parts[0] if len(parts) > 0 else "none"
            timestamp = parts[1] if len(parts) > 1 else "none"
            experiment_name = parts[2] if len(parts) > 2 else "none"
            shimmer_field = parts[3] if len(parts) > 3 else "none"
            filename = parts[5] if len(parts) > 5 else "none"
            # Split shimmer_field into shimmer_device and shimmer_day
            shimmer_device = shimmer_field
            shimmer_day = "none"
            if shimmer_field != "none" and "-" in shimmer_field:
                shimmer_device, shimmer_day = shimmer_field.rsplit("-", 1)
            # ext and part from filename
            ext = ""
            part = None
            if filename and "." in filename:
                ext = filename.split(".")[-1]
                part = filename.split(".")[0]
            elif filename:
                part = filename
            # Parse date and time from timestamp (format: YYYYMMDD_HHMMSS)
            date = "none"
            time = "none"
            if timestamp and "_" in timestamp:
                ymd, hms = timestamp.split("_", 1)
                if len(ymd) == 8 and len(hms) == 6:
                    date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
                    time = f"{hms[:2]}:{hms[2:4]}:{hms[4:6]}"
            return {
                "device": device,
                "timestamp": timestamp,
                "time": time,
                "experiment_name": experiment_name,
                "shimmer_device": shimmer_device,
                "shimmer_day": shimmer_day,
                "date": date,
                "filename": filename,
                "ext": ext,
                "part": part
            }

        grouped = defaultdict(lambda: {"files": [], "patient": None, "shimmer_devices": set()})
        for k in keys:
            meta = parse_custom_filename(os.path.basename(k))
            device = meta["device"]
            date = meta["date"]
            experiment_name = meta["experiment_name"]
            shimmer_device = meta["shimmer_device"]
            timestamp = meta["timestamp"]
            pat = mapping.get(device)
            file_record = {
                "fullname": k,
                "timestamp": timestamp,
                "time": meta["time"],
                "filename": meta["filename"],
                "shimmer_device": meta["shimmer_device"],
                "shimmer_day": meta["shimmer_day"],
                "ext": meta["ext"],
                "part": meta["part"],
                "experiment_name": experiment_name
            }
            grouped[(device, date, pat)]["files"].append(file_record)
            grouped[(device, date, pat)]["patient"] = pat if (pat is not None and pat != "") else "none"
            grouped[(device, date, pat)]["experiment_name"] = experiment_name
            if shimmer_device != "none":
                grouped[(device, date, pat)]["shimmer_devices"].add(shimmer_device)
        # Convert to desired output format
        result = []
        for (device, date, patient), value in grouped.items():
            shimmers = list(value["shimmer_devices"])
            shimmer1 = shimmers[0] if len(shimmers) > 0 else "none"
            shimmer2 = shimmers[1] if len(shimmers) > 1 else "none"
            result.append({
                "device": device,
                "date": date,
                "experiment_name": value["experiment_name"],
                "shimmer1": shimmer1,
                "shimmer2": shimmer2,
                "files": value["files"],
                "patient": patient
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
        scan_kwargs: Dict = {"ProjectionExpression": "device, patient, shimmer1, shimmer2, updatedAt"}
        while True:
            resp = table.scan(**scan_kwargs)
            for it in resp.get("Items", []):
                records.append(DevicePatientRecord(
                    device=it.get("device", ""),
                    patient=it.get("patient"),
                    shimmer1=it.get("shimmer1"),
                    shimmer2=it.get("shimmer2"),
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
        scan_kwargs: Dict = {"ProjectionExpression": "device, patient, shimmer1, shimmer2, updatedAt"}
        while True:
            resp = table.scan(**scan_kwargs)
            for it in resp.get("Items", []):
                records.append(DevicePatientRecord(
                    device=it.get("device", ""),
                    patient=it.get("patient"),
                    shimmer1=it.get("shimmer1"),
                    shimmer2=it.get("shimmer2"),
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
        return {
            "device": device,
            "patient": item.get("patient"),
            "shimmer1": item.get("shimmer1"),
            "shimmer2": item.get("shimmer2"),
            "updatedAt": item.get("updatedAt")
        }
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
                    patient = mapping[d].get("patient") if isinstance(mapping[d], dict) else mapping[d]
                    shimmer1 = mapping[d].get("shimmer1") if isinstance(mapping[d], dict) else None
                    shimmer2 = mapping[d].get("shimmer2") if isinstance(mapping[d], dict) else None
                    batch.put_item(Item={
                        "device": d,
                        "patient": patient,
                        "shimmer1": shimmer1,
                        "shimmer2": shimmer2,
                        "updatedAt": ts,
                    })
                    written.append(DevicePatientRecord(device=d, patient=patient, shimmer1=shimmer1, shimmer2=shimmer2, updatedAt=ts))
        return written
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/ddb/device-patient-map/{device}")
def ddb_put_device_mapping(device: str, payload: Dict[str, str] = Body(...)):
    patient = payload.get("patient")
    shimmer1 = payload.get("shimmer1")
    shimmer2 = payload.get("shimmer2")
    if not patient:
        raise HTTPException(status_code=400, detail="'patient' is required")
    try:
        table = _get_ddb_table()
        ts = datetime.now(timezone.utc).isoformat()
        table.put_item(Item={
            "device": device,
            "patient": patient,
            "shimmer1": shimmer1,
            "shimmer2": shimmer2,
            "updatedAt": ts,
        })
        return {
            "device": device,
            "patient": patient,
            "shimmer1": shimmer1,
            "shimmer2": shimmer2,
            "updatedAt": ts
        }
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



@app.get("/file/parse-name/")
def parse_filename(filename: str = Query(...)):
    """
    Parses a filename and returns its components as JSON.
    Handles the custom format: device__timestamp__experiment__shimmer_field__filename
    """
    try:
        def parse_custom_filename(fname):
            parts = fname.split("__")
            device = parts[0] if len(parts) > 0 else "none"
            timestamp = parts[1] if len(parts) > 1 else "none"
            experiment_name = parts[2] if len(parts) > 2 else "none"
            shimmer_field = parts[3] if len(parts) > 3 else "none"
            filename = parts[5] if len(parts) > 5 else "none"
            
            # Split shimmer_field into shimmer_device and shimmer_day
            shimmer_device = shimmer_field
            shimmer_day = "none"
            if shimmer_field != "none" and "-" in shimmer_field:
                shimmer_device, shimmer_day = shimmer_field.rsplit("-", 1)
            
            # ext and part from filename
            ext = ""
            part = None
            if filename and "." in filename:
                ext = filename.split(".")[-1]
                part = filename.split(".")[0]
            elif filename:
                part = filename
            
            # Parse date and time from timestamp (format: YYYYMMDD_HHMMSS)
            date = "none"
            time = "none"
            if timestamp and "_" in timestamp:
                ymd, hms = timestamp.split("_", 1)
                if len(ymd) == 8 and len(hms) == 6:
                    date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
                    time = f"{hms[:2]}:{hms[2:4]}:{hms[4:6]}"
            
            return {
                "original_filename": fname,
                "device": device,
                "timestamp": timestamp,
                "date": date,
                "time": time,
                "experiment_name": experiment_name,
                "shimmer_device": shimmer_device,
                "shimmer_day": shimmer_day,
                "filename": filename,
                "ext": ext,
                "part": part
            }
        
        parsed = parse_custom_filename(filename)
        return parsed
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/files/deconstructed/")
def get_deconstructed_files():
    """
    Returns a list of all files in S3 with their parsed components as individual JSON records.
    Each file is returned as a separate record with all its parsed fields.
    Skips .zip files.
    """
    try:
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET)
        contents = response.get("Contents", [])
        
        def parse_custom_filename(fname):
            parts = fname.split("__")
            device = parts[0] if len(parts) > 0 else "none"
            timestamp = parts[1] if len(parts) > 1 else "none"
            experiment_name = parts[2] if len(parts) > 2 else "none"
            shimmer_field = parts[3] if len(parts) > 3 else "none"
            filename = parts[5] if len(parts) > 5 else "none"
            
            # Split shimmer_field into shimmer_device and shimmer_day
            shimmer_device = shimmer_field
            shimmer_day = "none"
            if shimmer_field != "none" and "-" in shimmer_field:
                shimmer_device, shimmer_day = shimmer_field.rsplit("-", 1)
            
            # ext and part from filename
            ext = ""
            part = None
            if filename and "." in filename:
                ext = filename.split(".")[-1]
                part = filename.split(".")[0]
            elif filename:
                part = filename
            
            # Parse date and time from timestamp (format: YYYYMMDD_HHMMSS)
            date = "none"
            time = "none"
            if timestamp and "_" in timestamp:
                ymd, hms = timestamp.split("_", 1)
                if len(ymd) == 8 and len(hms) == 6:
                    date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
                    time = f"{hms[:2]}:{hms[2:4]}:{hms[4:6]}"
            
            return {
                "fullname": fname,
                "device": device,
                "timestamp": timestamp,
                "date": date,
                "time": time,
                "experiment_name": experiment_name,
                "shimmer_device": shimmer_device,
                "shimmer_day": shimmer_day,
                "filename": filename,
                "ext": ext,
                "part": part
            }
        
        result = []
        for obj in contents:
            key = obj["Key"]
            # Skip .zip files
            if key.lower().endswith('.zip'):
                continue
            parsed = parse_custom_filename(key)
            result.append(parsed)
        
        return {"data": result, "error": None}
    
    except (BotoCoreError, ClientError, Exception) as e:
        return {"data": [], "error": str(e)}


@app.get("/files/combined-meta/")
def get_combined_meta():
    """
    Combines decoded file metadata from DynamoDB with patient mapping.
    Each record includes S3 pointer ('decode_s3_key') to full decoded arrays.
    Excludes heavy fields like headerBytes and raw sensor channels.
    """
    try:
        # ----------- Load decoded metadata -----------
        file_table_name = os.getenv("DDB_FILE_TABLE")
        if not file_table_name:
            return {"data": [], "error": "DDB_FILE_TABLE env not set"}

        ddb = boto3.resource("dynamodb")
        file_table = ddb.Table(file_table_name)
        items = []
        scan_kwargs = {}
        while True:
            resp = file_table.scan(**scan_kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" in resp:
                scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            else:
                break

        # ----------- Load patient mapping -----------
        mapping_table_name = os.getenv("DDB_TABLE")
        mapping_table = ddb.Table(mapping_table_name) if mapping_table_name else None

        from collections import defaultdict
        grouped = defaultdict(lambda: {"shimmer1_decoded": [], "shimmer2_decoded": []})

        # Group by (patient, device, date, within 15 sec)
        # 1. Collect all records by (patient, device, date)
        from collections import defaultdict
        records_by_key = defaultdict(list)
        for item in items:
            device = item.get("device", "none")
            date = item.get("date", "none")
            shimmer_name = item.get("shimmer_device", "none")
            decode_s3_key = item.get("decode_s3_key", None)
            # Get patient
            patient = "none"
            if mapping_table and device != "none":
                try:
                    resp = mapping_table.get_item(Key={"device": device})
                    patient = resp.get("Item", {}).get("patient", "none")
                except Exception:
                    pass
            # Remove unneeded heavy keys (just in case)
            EXCLUDE_KEYS = {"headerBytes", "Accel_LN_X", "Accel_LN_Y", "Accel_LN_Z",
                            "Gyro_X", "Gyro_Y", "Gyro_Z", "Mag_X", "Mag_Y", "Mag_Z"}
            record = {k: v for k, v in item.items() if k not in EXCLUDE_KEYS}
            record["decode_s3_key"] = decode_s3_key
            record["shimmer_name"] = shimmer_name
            record["patient"] = patient
            # Parse timestamp as unix
            recorded_ts = item.get("recordedTimestamp")
            try:
                ts_unix = None
                if recorded_ts and isinstance(recorded_ts, str):
                    ts_unix = datetime.fromisoformat(recorded_ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts_unix = None
            record["_ts_unix"] = ts_unix
            records_by_key[(patient, device, date)].append(record)

        # 2. For each (patient, device, date), group by within GROUP_WINDOW_SECONDS
        grouped = defaultdict(lambda: {"shimmer1_decoded": [], "shimmer2_decoded": []})
        for key, recs in records_by_key.items():
            # Sort by timestamp
            recs = sorted([r for r in recs if r["_ts_unix"] is not None], key=lambda r: r["_ts_unix"])
            group_id = 0
            group_start = None
            for rec in recs:
                if group_start is None or rec["_ts_unix"] - group_start > GROUP_WINDOW_SECONDS:
                    group_id += 1
                    group_start = rec["_ts_unix"]
                group_key = (*key, f"group{group_id}")
                group = grouped[group_key]
                group["device"] = key[1]
                group["date"] = key[2]
                group["patient"] = key[0]
                group["group_id"] = f"group{group_id}"
                shimmer_name = rec["shimmer_name"]
                if not group.get("shimmer1"):
                    group["shimmer1"] = shimmer_name
                    group["shimmer1_decoded"].append(rec)
                elif shimmer_name == group.get("shimmer1"):
                    group["shimmer1_decoded"].append(rec)
                else:
                    group["shimmer2"] = shimmer_name
                    group["shimmer2_decoded"].append(rec)

        result = list(grouped.values())
        return {"data": result, "error": None}

    except Exception as e:
        return {"data": [], "error": str(e)}



# Place this endpoint after app = FastAPI() initialization

@app.post("/decode-and-store/")
def decode_and_store(full_file_name: str = Body(..., embed=True)):
    """
    Given a full S3 filename, download, decode header, and store metadata in DynamoDB file table.
    Large decoded arrays and unnecessary raw fields (like headerBytes) are stored in S3 under 'decode/'.
    DynamoDB stores only lightweight metadata + pointer to decode_s3_key.
    """
    print(f"[decode-and-store] Called with full_file_name: {full_file_name}")
    try:
        # ----------- Download file from S3 -----------
        print(f"[decode-and-store] Downloading file from S3: {full_file_name}")
        s3_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=full_file_name)
        file_bytes = s3_obj["Body"].read()
        print(f"[decode-and-store] Downloaded {len(file_bytes)} bytes from S3.")

        # ----------- Parse filename -----------
        def parse_custom_filename(fname):
            parts = fname.split("__")
            device = parts[0] if len(parts) > 0 else "none"
            timestamp = parts[1] if len(parts) > 1 else "none"
            experiment_name = parts[2] if len(parts) > 2 else "none"
            shimmer_field = parts[3] if len(parts) > 3 else "none"
            filename = parts[5] if len(parts) > 5 else "none"

            shimmer_device = shimmer_field
            shimmer_day = "none"
            if shimmer_field != "none" and "-" in shimmer_field:
                shimmer_device, shimmer_day = shimmer_field.rsplit("-", 1)

            ext, part = "", None
            if filename and "." in filename:
                ext = filename.split(".")[-1]
                part = filename.split(".")[0]
            elif filename:
                part = filename

            date, time = "none", "none"
            if timestamp and "_" in timestamp:
                ymd, hms = timestamp.split("_", 1)
                if len(ymd) == 8 and len(hms) == 6:
                    date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
                    time = f"{hms[:2]}:{hms[2:4]}:{hms[4:6]}"

            return {
                "full_file_name": fname,
                "device": device,
                "timestamp": timestamp,
                "date": date,
                "time": time,
                "experiment_name": experiment_name,
                "shimmer_device": shimmer_device,
                "shimmer_day": shimmer_day,
                "filename": filename,
                "ext": ext,
                "part": part
            }

        meta = parse_custom_filename(full_file_name)
        print(f"[decode-and-store] Parsed filename meta: {meta}")

        # ----------- Patient mapping -----------
        patient = None
        try:
            mapping_table_name = os.getenv("DDB_TABLE")
            if mapping_table_name and meta.get("device"):
                ddb = boto3.resource("dynamodb")
                mapping_table = ddb.Table(mapping_table_name)
                resp = mapping_table.get_item(Key={"device": meta["device"]})
                patient = resp.get("Item", {}).get("patient")
                print(f"[decode-and-store] Patient mapping found: {patient}")
        except Exception as e:
            print(f"[decode-and-store] Error in patient mapping: {e}")
            patient = None
        if patient:
            meta["patient"] = patient

        # ----------- Decode shimmer data -----------
        print(f"[decode-and-store] Decoding shimmer data...")
        decoded = read_shimmer_dat(file_bytes)
        print(f"[decode-and-store] Decoded keys: {list(decoded.keys())}")

        # ----------- Remove unneeded heavy keys (just in case)
        EXCLUDE_KEYS = {
            "timestamp", "headerInfo", "headerBytes", "sampleRate", "channelNames", "packetLengthBytes",
            "Accel_LN_X", "Accel_LN_Y", "Accel_LN_Z", "VSenseBatt", "INT_A13", "INT_A14",
            "Gyro_X", "Gyro_Y", "Gyro_Z",
            "Accel_WR_X", "Accel_WR_Y", "Accel_WR_Z",
            "Mag_X", "Mag_Y", "Mag_Z",
            "Accel_WR_y"  # in case of typo variant
        }

        # ----------- Separate large vs small data -----------
        large_data, small_data = {}, {}
        for k, v in decoded.items():
            if k in EXCLUDE_KEYS:
                continue
            if isinstance(v, (list, dict)) and len(str(v)) > 2000:
                large_data[k] = v
            else:
                small_data[k] = v
        print(f"[decode-and-store] Large data keys: {list(large_data.keys())}")
        print(f"[decode-and-store] Small data keys: {list(small_data.keys())}")

        # ----------- Store large data to S3 using presigned URL -----------
        import json
        decode_key = f"decode/{os.path.splitext(full_file_name)[0]}_decoded.json"
        presigned_url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params={"Bucket": S3_BUCKET, "Key": decode_key, "ContentType": "application/json"},
            ExpiresIn=600
        )
        print(f"[decode-and-store] Presigned URL for decoded upload: {presigned_url}")
        try:
            import requests
            resp = requests.put(presigned_url, data=json.dumps(large_data), headers={"Content-Type": "application/json"})
            print(f"[decode-and-store] S3 upload response status: {resp.status_code}")
            if resp.status_code not in [200, 201]:
                print(f"[decode-and-store] S3 upload error: {resp.text}")
                return {"error": f"Failed to upload decoded file to S3: {resp.text}"}
        except ImportError:
            print("[decode-and-store] requests library is not installed.")
            return {"error": "requests library is required for presigned URL upload. Please install it."}

        # ----------- Prepare DynamoDB record -----------
        def convert_floats(obj):
            from decimal import Decimal
            if isinstance(obj, float):
                return Decimal(str(obj))
            if isinstance(obj, dict):
                return {k: convert_floats(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [convert_floats(v) for v in obj]
            return obj

        # Extract recordedTimestamp from first timestampCal value and convert from Unix to ISO format (no rounding)
        recorded_timestamp = None
        if "timestampCal" in decoded and isinstance(decoded["timestampCal"], list) and len(decoded["timestampCal"]) > 0:
            try:
                unix_timestamp = float(decoded["timestampCal"][0])
                dt = datetime.fromtimestamp(unix_timestamp, tz=timezone.utc)
                recorded_timestamp = dt.isoformat()
            except (ValueError, TypeError, OSError):
                recorded_timestamp = None

        merged = {**meta, **small_data, "decode_s3_key": decode_key}
        if recorded_timestamp is not None:
            merged["recordedTimestamp"] = recorded_timestamp
        item = convert_floats(merged)
        item["updatedAt"] = datetime.now(timezone.utc).isoformat()
        print(f"[decode-and-store] DynamoDB item prepared: {item}")

        # ----------- Save to DynamoDB -----------
        file_table_name = os.getenv("DDB_FILE_TABLE")
        if not file_table_name:
            print("[decode-and-store] DDB_FILE_TABLE env not set")
            return {"error": "DDB_FILE_TABLE env not set"}
        ddb = boto3.resource("dynamodb")
        file_table = ddb.Table(file_table_name)
        file_table.put_item(Item=item)
        print(f"[decode-and-store] Item stored in DynamoDB table: {file_table_name}")

        print(f"[decode-and-store] Returning success response.")
        return {
            "filename": full_file_name,
            "message": "Decode and store successful",
            "ddb_item": item,
            "decode_s3_key": decode_key
        }

    except (BotoCoreError, ClientError, Exception) as e:
        print(f"[decode-and-store] Exception: {e}")
        return {"error": str(e)}

@app.get("/get-decoded-field-direct/")
def get_decoded_field_direct(
    full_file_name: str = Query(...),
    field_name: str = Query(...)
):
    """
    Directly fetches the field from 'decode/{filename_without_ext}_decoded.json' in S3,
    skipping DynamoDB lookup.
    """
    try:
        import json, os
        decoded_key = f"decode/{os.path.splitext(full_file_name)[0]}_decoded.json"
        s3_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=decoded_key)
        decoded_data = json.loads(s3_obj["Body"].read().decode("utf-8"))
        if field_name not in decoded_data:
            raise HTTPException(status_code=404, detail=f"Field '{field_name}' not found.")
        return {
            "decode_s3_key": decoded_key,
            "field": field_name,
            "length": len(decoded_data[field_name]),
            "values": decoded_data[field_name]
        }
    except (BotoCoreError, ClientError, Exception) as e:
        raise HTTPException(status_code=500, detail=str(e))
