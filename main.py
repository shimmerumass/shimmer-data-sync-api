import os
from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Body
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import io
import zipfile
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from typing import List, Optional
from dotenv import load_dotenv
from mangum import Mangum
from pydantic import BaseModel

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

@app.get("/files/metadata/", response_model=List[FileItem])
def list_files_metadata():
    try:
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET)
        contents = response.get("Contents", [])
        keys = [obj["Key"] for obj in contents]
        return [parse_file_name(k) for k in keys]
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=str(e))

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
def generate_upload_url(filename: str = Query(...)):
    try:
        url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params={"Bucket": S3_BUCKET, "Key": filename},
            ExpiresIn=3600  # URL valid for 1 hour
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

handler = Mangum(app)
