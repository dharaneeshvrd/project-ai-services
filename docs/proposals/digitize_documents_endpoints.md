# Digitize Documents Service 
 
## Current Design: 

User can interact with document ingestion pipeline via cli commands and can run the below command to run the ingestion(convert/process/chunk/index) after placing the documents in the expected directory of the host i.e. /var/lib/ai-services/applications/<app-name>/docs. 
```
ai-services application start <app-name> --pod=<app-name>--ingest-docs 
```
In case if user wants to clean it up, can run below command which will clean up the vdb & local cache of processed files. 
```
ai-services application start <app-name> --pod=<app-name>--clean-docs 
```
There is no option to just convert and not ingest the converted content into the vdb. 
 
## Proposal: 
 
As per the requirement from PM, need to convert cli into microservice which expose REST endpoints to do the digitize document service’s tasks, which are to
- Convert the file from source format pdf to output format and return the result
- Ingest the document after the conversion by going through following tasks 
    - process text & table 
    - chunking 
    - create embeddings & index it to vdb. 
- Also need to expose the service to the outside world for the end user consumption. Port 4000 can be considered
 
## Endpoints: 

### POST /v1/digitize - Synchronous conversion

**Content Type:** multipart/form-data 
 
**Payload:**
```
{ 
    "output_format": "json" 
}

output_format - str  - Optional flag to mention the required output format of the pdf, Options: 'md/text/json', Default: 'json'
```

**Description:**
- User can pass the pdf directly as a byte stream and API server should convert the file into output_format(default: json) and return the result. 
- User should pass only one file at a time for conversion and it should not exceed 100 pages, since it would take lot of time to convert and cannot be done on a sync network call. 
- Why 100 pages?
    - This is something we need to assess and get feedback from PM to decide the number of pages to be supported. To get started on a number I mentioned 100 since that is not too much and not too small as well. 
    - The docling serve API project https://github.com/docling-project/docling-serve currently returns the result irrespective of the number of pages for the sync conversion requests. So we can also follow similar fashion to return the results.
    - Also from our experience 100 page pdf can be converted within couple of mins. Hence the size.
- Rate limiter would be added around conversion job to limit the number of conversion happen in parallel

**Sample curl:**
```
> curl -X 'POST' \ 
  'http://localhost:4000/v1/digitize' \ 
  -F 'file=@/path/to/file.pdf' \ 
  -F 'payload={"output_format": "md"};type=application/json' 
> 
``` 
**Response:** 

200 OK 
{ 
"docling_document": "", 
    	... 
} 
 
4xx - No file attached
    - More than one file attached
    - Sanity check on file 
        - Should not be corrupt  
        - Should be a pdf 
    - Number of page exceeds the limit
    - Rate limit errors
 
5xx - Server-side errors 


### POST /v1/digitize/ingest - Asynchronous ingestion 

**Content Type:** multipart/form-data 

**Description:**
- User can send single or multiple files on the request and the ingestion will happen in a background process
- Only one ingestion will be allowed at a time
- App server should do following things on receiving the request: 
    - Validate no lock file exist already to ensure there is no ingestion in progress. 
    - Start the ingestion process in a background process  
    - Generate a UUID as job_id to uniquely identify this ingestion  
    - Create a LOCK file in /var/lib/ai-services/applications/<app-name>/cache/
    - Create /var/lib/ai-services/applications/<app-name>/cache/job_id_status.json to manage/view the status of ingestion 
    - End the request with job_id 
- Background ingestion process should write the status into job_id_status.json like following information 
    - Current stage(conversion/processing/chunking/indexing) 
    - Stats of current stage (timings, number of pages, tables …) 
    - Once done with all the stages, it should remove the lock and conclude the job. 

**Sample curl:**
```
> # Ingest the attached files 
> curl -X 'POST' \ 
  'http://localhost:4000/v1/digitize/ingest' \ 
  -F 'file=@/path/to/file1.pdf' \ 
  -F 'file=@/path/to/file2.pdf' \ 
> 
```
**Response:** 
200 OK 
>  {"job_id": "4ae018be-9674-4bf9-be8b-af311a5d4d92", "status": "accepted"} 
 
4xx - No files attached
    - Duplicate file names
    - Sanity check on file 
        - Should not be corrupt  
        - Should be a pdf
    - Conflict with running ingestion
 
5xx - Server side errors 

### GET /v1/digitize/{job_id}
- When new ingestion is submitted, a job id might have been returned. 
- User can query its status using this API. 

**Sample curl:**
```
> curl \ 
  'http://localhost:4000/v1/digitize/{job_id} 
>  
```

**Response:** 
200 OK
```
{
    "job_id": "4ae018be-9674-4bf9-be8b-af311a5d4d92",
    "status": "partial",
    "created_at": "2026-01-10T10:00:00Z",
    "total_pages": 10,
    "total_tables": 5,
    "docs": {
        "pdf1": { 
            "status": "chunking", // possible values: conversion, processing, chunking, indexing  
            "stats": { 
                "num_pages": 10,
                "num_tables": 5,
                "num_chunks": 25, 
                "timings": { 
                    "conversion": 12.5, 
                    "processing": 30.2, 
                    "chunking": 15.0, 
                    "indexing": 20.3 
                } 
            } 
        }, 
        "pdf2": { 
            "status": "error", 
            "stats": { 
                "num_pages": 0, 
                "timings": {} 
            } 
        } 
    }
} 
```
4xx - Invalid job id

5xx - Server-side errors 

### DELETE /v1/digitize/ingest/{file_name}
- Ensure there is no ingestion happening currently by checking the lock file
- Remove the vectors of a specific file in vdb and clean up the local cache generated for the specific file

**Sample curl:**
```
> curl -X DELETE \ 
  'http://localhost:4000/v1/digitize/ingest/{file_name}
>  
```
**Response:** 
200 OK

4xx - File not exist
    - Conflict with running ingestion

5xx - Server-side errors

### DELETE /v1/digitize/ingest
- Ensure there is no ingestion happening currently by checking the lock file
- Equivalent to clean-db command, will clean up the vdb and remove the local cache.

**Sample curl:**
```
> curl -X DELETE\ 
  'http://localhost:4000/v1/digitize/ingest
>  
```
**Response:** 
200 OK

4xx - Conflict with running ingestion

5xx - Server-side errors 

### Assumptions:
- Digitize documents pod/container mounted with a Read/Write persistent volume and data persists over restarts to store cached results
- In case of multiple replicas, same volume should be shared to maintain the ingestion job status
- During ingestion
    - User should pass files with unique names
    - In case user pass same file again, vdb will be upserted

### Future:

#### To support asynchronous multiple files conversion:
**POST /v1/digitize/async**
**Content Type:** multipart/form-data 
 
**Payload:**
```
{ 
    "ingest": false, 
    "output_format": "json",
    "async": true
}
```

To run the conversion job asynchronously over multiple files and the results can be collected over `GET /v1/digitize/{job_id}/result & /v1/digitize/{job_id}/result/{file_name}`
