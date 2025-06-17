# FastAPI S3 Sync Service

This project provides a FastAPI-based API for syncing text files to AWS S3. It allows uploading, listing, and downloading text files from a specified S3 bucket.

## Features
- Upload text files to S3
- List files in the S3 bucket
- Download files from S3

## Setup
1. Install dependencies:
   ```sh
   pip install fastapi uvicorn boto3 python-multipart
   ```
2. Set your AWS credentials as environment variables or in `~/.aws/credentials`.
3. Run the server:
   ```sh
   uvicorn main:app --reload
   ```

## Endpoints
- `POST /upload/` - Upload a text file
- `GET /files/` - List files in the bucket
- `GET /download/{filename}` - Download a file

## Configuration
- Configure your S3 bucket and AWS region in the environment or in the code.
