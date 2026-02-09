from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os

app = FastAPI(title="Digitize documents API")

class OutputFormat(str):
    TEXT = "text"
    MD = "md"
    JSON = "json"

class DigitizeRequest(BaseModel):
    ingest: bool
    output_format: OutputFormat = OutputFormat.JSON

@app.post("/v1/digitize")
def digitize_document(request: DigitizeRequest):
    try:
        if request.ingest:
            # Handle the ingestion logic here, e.g., call the ingest function from cli.py
            return {"job_id": "UUID of the digitization job"}
        else:
            # Handle the logic to return the digitized document in the requested format
            return {"document": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/digitize/{job_id}")
def get_digitization_status(job_id: str):
    try:
        # Placeholder for actual status retrieval logic
        return {"job_id": job_id, "status": "In Progress"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=os.getenv("PORT", 4000))