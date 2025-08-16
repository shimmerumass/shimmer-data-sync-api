from fastapi import FastAPI, HTTPException, Query

app = FastAPI()

@app.get("/mock-generate-upload-url/")
def mock_generate_upload_url(filename: str = Query(...)):
    """
    Mock endpoint for pre-signed URL failure. Always returns a 500 error.
    """
    raise HTTPException(status_code=500, detail="Failed to generate pre-signed upload URL (mock error)")
