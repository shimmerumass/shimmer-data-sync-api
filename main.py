import os
from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Body
from fastapi.responses import StreamingResponse
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from typing import List
from dotenv import load_dotenv
from mangum import Mangum

# Load environment variables from .env if present
load_dotenv()

# For AWS Lambda, credentials and region are automatically provided by the environment.
# Only S3_BUCKET should be loaded from environment variables.
S3_BUCKET = os.getenv("S3_BUCKET")

# Use default boto3 session (credentials and region are handled by Lambda)
s3_client = boto3.client("s3")

app = FastAPI()

@app.post("/upload/")
async def upload_file(file: UploadFile = File(...)):
    try:
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

@app.get("/download/{filename}")
def download_file(filename: str):
    try:
        fileobj = s3_client.get_object(Bucket=S3_BUCKET, Key=filename)["Body"]
        return StreamingResponse(fileobj, media_type="text/plain", headers={"Content-Disposition": f"attachment; filename={filename}"})
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

handler = Mangum(app)
