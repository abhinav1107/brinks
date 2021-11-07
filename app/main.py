from fastapi import FastAPI, Response, status, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import VERSION, PROJECT_NAME, DEBUG, APP_PATH
from app.core.logger import get_logger
from app.core.utils import get_jenkins_build_files
import os
import uuid
import time
import ipaddress


logger = get_logger(__name__)
brinks = FastAPI(title=PROJECT_NAME, version=VERSION, debug=DEBUG)
brinks.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

logger.info("Server started successfully...")


@brinks.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    rid = '{}-{}'.format(int(ipaddress.ip_address(request.client.host)), uuid.uuid4().hex)
    request.state.rid = rid
    logger.info("starting request {} for client {} at url {}".format(rid, request.client.host, request.url.path))
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = '{:.3f}'.format(process_time)
    response.headers["X-Request-ID"] = rid
    return response


@brinks.head("/")
@brinks.get("/")
def get_root():
    return {"message": "You've reached us. Now what?"}


@brinks.head("/ping")
@brinks.get("/ping")
def get_ping():
    return {"ping": "pong"}


@brinks.get("/status/")
def get_status(job_name: str, build_number: str, response: Response):
    job_name = job_name.strip().strip('/')
    build_number = build_number.strip().strip('/')
    job_status_file = '{}/downloads/{}/{}/.brinks.status'.format(APP_PATH, job_name, build_number)
    if not os.path.exists(job_status_file):
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        return {"message": "Thomas had never seen such bullshit build before :angry_thomas_face_emoji:"}

    with open(job_status_file) as f1:
        status_file_data = f1.read().strip()

    if status_file_data == 'complete':
        response.status_code = status.HTTP_200_OK
        message = "job available for download"
    elif status_file_data.startswith('failed'):
        response.status_code = status.HTTP_204_NO_CONTENT
        status_file_data_array = status_file_data.split(':', 1)
        message = "sync for job has failed. Error message was: {}".format(status_file_data_array[-1])
    else:
        response.status_code = status.HTTP_206_PARTIAL_CONTENT
        message = "sync for job is in progress"

    return {"message": message}


@brinks.put("/getfiles/")
def get_files(req: Request, resp: Response, btask: BackgroundTasks,
              job_name: str, build_number: str, archive_path: str = "/archive"):
    job_name = job_name.strip().strip('/')
    build_number = build_number.strip().strip('/')
    archive_path = archive_path.strip().strip('/')
    request_id = req.state.rid
    rs, rmsg = get_jenkins_build_files(bt=btask, rid=request_id, job=job_name, build=build_number, path=archive_path)
    resp.status_code = rs
    return {"message": rmsg}

