# Shimmer Data Sync API

RESTful API for managing and processing Shimmer wearable sensor data in the cloud. Handles file uploads to S3, decodes binary sensor streams with inertial calibration, stores metadata in DynamoDB, and provides endpoints for patient management and data retrieval.

## Features

### Data Management
- Upload Shimmer sensor files (.txt) to S3
- Automatic filename parsing (device, timestamp, experiment, shimmer device)
- Patient-device mapping with DynamoDB
- Batch file downloads (by day, by user/date)
- Generate presigned upload/download URLs

### Sensor Data Processing
- Binary sensor data decoding (Shimmer3 format)
- Multi-channel support: Accelerometer (Low-Noise & Wide-Range), Gyroscope, Magnetometer
- Inertial sensor calibration (offset, gain, alignment matrix)
- Time synchronization with phone RTC and rollover correction
- Computed metrics: Accel_WR_Absolute (magnitude), Accel_WR_VAR (variance)

### Smart Storage
- **Full decoded data** → S3 as JSON (handles 60k+ samples)
- **Summary metrics only** → DynamoDB (stays under 400KB limit)
- Scalable architecture for large sensor datasets

### API Endpoints
- File operations (upload, download, list, search)
- Device-patient mapping (CRUD operations)
- File metadata with grouping by device/date
- Decode and store sensor data
- Retrieve full decoded data from S3

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
