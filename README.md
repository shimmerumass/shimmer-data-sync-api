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
- **Binary sensor data decoding** (Shimmer3 format)
  - 256-byte header containing device info, sample rate, enabled sensors, calibration parameters
  - Variable-length data packets (3-byte timestamp + sensor channels)
  
- **Multi-channel support** with raw and calibrated data:
  - **Accel_LN** (Low-Noise Accelerometer): X, Y, Z axes - high precision, lower range
  - **Accel_WR** (Wide-Range Accelerometer): X, Y, Z axes - lower precision, higher range
  - **Gyro** (Gyroscope): X, Y, Z axes - angular velocity
  - **Mag** (Magnetometer): X, Y, Z axes - magnetic field
  - Each channel provides both raw values and calibrated (_cal) values
  
- **Inertial sensor calibration**
  - Offset correction (3 values per sensor)
  - Gain scaling (3 values per sensor)
  - Alignment matrix (3x3, values scaled by 100)
  - Applied to all inertial sensors (Accel_LN, Accel_WR, Gyro, Mag)
  
- **Time synchronization** with phone RTC and rollover correction
  - Initial RTC sync from phone timestamp (Unix epoch)
  - Final output: Unix timestamps in `timestampCal` array
  - Conversion to human-readable ISO 8601 format in `timestampReadable`
  
- **Computed metrics**:
  - `Accel_WR_Absolute`: Magnitude (√(x² + y² + z²)) for each sample
  - `Accel_WR_VAR`: Range (max - min) of absolute acceleration across recording

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

## Tech Stack
- **Backend**: FastAPI with Mangum (AWS Lambda compatible)
- **Storage**: AWS S3 (raw files + decoded JSON)
- **Database**: AWS DynamoDB (metadata + summaries)
- **Deployment**: AWS Lambda with API Gateway

## Setup

### Local Development
1. Install dependencies:
   ```sh
   pip install fastapi uvicorn boto3 python-dotenv mangum pydantic
   ```

2. Configure environment variables (`.env`):
   ```env
   S3_BUCKET=your-bucket-name
   DDB_TABLE=device-patient-mapping-table
   DDB_FILE_TABLE=file-metadata-table
   AWS_REGION=us-east-1
   ```

3. Run locally:
   ```sh
   uvicorn main:app --reload
   ```

### AWS Lambda Deployment
1. Package dependencies:
   ```sh
   pip install -t lambda_package/ -r requirements.txt
   cp main.py shimmer_decode.py shimmerCalibrate.py lambda_package/
   cd lambda_package && zip -r ../lambda_package.zip .
   ```

2. Deploy to Lambda and configure API Gateway

## Key Endpoints

### File Management
- `POST /upload/` - Upload Shimmer sensor file
- `GET /files/` - List all files
- `GET /files/metadata/` - Get files grouped by device/date/patient
- `GET /files/combined-meta/` - Get combined metadata from DynamoDB
- `GET /download/{filename}` - Download file
- `POST /download-zip-by-day/` - Download all files for a date
- `POST /download-zip-by-user-date/` - Download files for user/date

### Sensor Data Processing
- `GET /file/decode/` - Decode sensor file (returns full data)
- `POST /decode-and-store/` - Decode and store (summary in DDB, full data in S3)
- `GET /file/decoded-full/` - Retrieve full decoded data from S3

### Device/Patient Mapping
- `GET /ddb/device-patient-map` - List all mappings
- `GET /ddb/device-patient-map/{device}` - Get mapping for device
- `PUT /ddb/device-patient-map/{device}` - Create/update mapping
- `DELETE /ddb/device-patient-map/{device}` - Delete mapping
- `GET /devices/unregistered` - Find devices without patient mapping

## Architecture Notes

### DynamoDB Size Limit Solution
Shimmer files can contain 60,000+ samples, making arrays too large for DynamoDB's 400KB item limit. Our solution:

1. **Full decoded data** → Stored in S3 at `decoded/{filename}.json`
2. **Summary metrics** → Stored in DynamoDB (num_samples, accel_wr_var, etc.)
3. **Reference link** → DynamoDB item includes `decoded_s3_key` for full data retrieval
4. **Recording timestamp** → DynamoDB includes `recordedTimestamp` field with human-readable ISO format timestamp (e.g., `2024-09-24T22:38:36+00:00`)
   - Shimmer files store timestamps as Unix timestamps (seconds since epoch) in the `timestampCal` array
   - The first value from `timestampCal[0]` is extracted and converted from Unix format to ISO 8601 format with UTC timezone
   - This provides quick access to recording start time without fetching the full 60k-sample timestamp array from S3

This keeps DynamoDB items small (~2-5 KB) while preserving full data access via S3 and providing quick access to key metadata like recording start time.

## Project Structure
```
.
├── main.py                    # FastAPI application & endpoints
├── shimmerCalibrate.py        # Calibrated decoder with inertial cal
├── test/                      # scripts to test the decoder code
└── README.md
```

## Contributing
This project is part of the Shimmer UMass research platform. For access or collaboration, contact the Shimmer research team.

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
