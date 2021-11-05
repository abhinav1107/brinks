from fastapi import FastAPI, Response, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from starlette.config import Config
from starlette.datastructures import CommaSeparatedStrings
from typing import List
import os
import logging
import socket
import paramiko

__package_dir = os.path.abspath(os.path.dirname(os.path.abspath(__file__)) + '/../')
VERSION = '0.1.0'
PROJECT_NAME = "Brinks App - Using FastAPI"

config = Config(".env")
APP_PATH = config("APP_PATH", default=__package_dir)
JENKINS_HOST: str = config("JENKINS_HOST")
JENKINS_USER: str = config("JENKINS_USER", default="root")
JENKINS_HOME: str = config("JENKINS_HOME", default='/var/lib/jenkins')
DEBUG: bool = config("DEBUG", cast=bool, default=False)
ALLOWED_HOST: List[str] = config("ALLOWED_HOST", cast=CommaSeparatedStrings, default="")

app_log_path = APP_PATH + '/logs'
if not os.path.isdir(app_log_path):
    os.mkdir(app_log_path)
app_download_dir = APP_PATH + '/downloads'
if not os.path.isdir(app_download_dir):
    os.mkdir(app_download_dir)

# logger setup setting
logger = logging.getLogger(__name__)
if DEBUG:
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.WARNING)
fh = logging.FileHandler(app_log_path + '/brinks.log')
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)

brinks = FastAPI(title=PROJECT_NAME, version=VERSION, debug=DEBUG)
brinks.add_middleware(CORSMiddleware, allow_origins=ALLOWED_HOST or ["*"], allow_methods=["*"], allow_headers=["*"])
logger.info("Brinks server started")


@brinks.head("/")
@brinks.get("/")
def get_root():
    return {"message": "You've reached us. Now what?"}


@brinks.head("/ping")
@brinks.get('/ping')
def get_ping():
    return {"ping": "pong"}


def compare_sftp_and_local_files(sftp_client, local_abs_path, remote_abs_path):
    try:
        remote_file_stat = sftp_client.stat(remote_abs_path)
    except IOError as e1:
        logger.debug("remote file {} not found. message {}".format(remote_abs_path, e1))
        return 1

    if os.path.exists(local_abs_path) and os.path.getsize(local_abs_path) == remote_file_stat.st_size:
        logger.debug("remote file {} exists locally at {}".format(remote_abs_path, local_abs_path))
        return 0

    return 2


def rsync_files_from_build(job_name, build_number, file_path, job_build_path, job_build_status_file):
    k = os.path.abspath(os.path.expanduser(os.path.expandvars('~/.ssh/id_rsa')))
    source_folder = '{}/jobs/{}/builds/{}/{}'.format(JENKINS_HOME, job_name, build_number, file_path)
    sftp_done = False
    loop_count = 0
    logger.debug("connecting to jenkins at {} with user {} and ssh key {}".format(JENKINS_HOME, JENKINS_USER, k))
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_key = paramiko.RSAKey.from_private_key_file(k)
    try:
        ssh.connect(hostname=JENKINS_HOST, username=JENKINS_USER, pkey=ssh_key)
        logger.debug("ssh connection successful")
    except (paramiko.BadHostKeyException, paramiko.AuthenticationException, paramiko.SSHException, socket.error) as e1:
        logger.debug("ssh connection failed with message: {}".format(e1))
        with open(job_build_status_file, 'w') as f1:
            f1.write("failed: {}".format(e1))
        return

    sftp = ssh.open_sftp()
    logger.debug("sftp connection opened")
    try:
        sftp.stat(source_folder)
        logger.debug("folder {} found on jenkins server".format(source_folder))
    except Exception as e1:
        logger.debug("folder {} not found on jenkins server. error: {}".format(source_folder, e1))
        with open(job_build_status_file, 'w') as f1:
            f1.write("failed: folder doesn't exists on jenkins server. error: {}".format(source_folder, e1))
        return

    while not sftp_done:
        err_message = None
        all_build_files = sftp.listdir(source_folder)
        logger.debug("files to get: {}".format(all_build_files))
        for each_file in all_build_files:
            each_file_status = compare_sftp_and_local_files(
                sftp_client=sftp,
                local_abs_path='{}/{}'.format(job_build_path, each_file),
                remote_abs_path='{}/{}'.format(source_folder, each_file)
            )
            if each_file_status == 0:
                continue
            elif each_file_status == 1:
                logger.debug("file {} not found on jenking server at {}".format(each_file, source_folder))
                err_message = 'file {} for job {}/{} disappeared from source during rsync. unable to continue'.format(
                    each_file, job_name, build_number
                )
                loop_count = 4
                break
            else:
                try:
                    sftp.get('{}/{}'.format(source_folder, each_file), '{}/{}'.format(job_build_path, each_file))
                    logger.debug("file {} downloaded locally".format(each_file))
                except Exception as e2:
                    err_message = "failed to get file {} from jenkins server. message: {}".format(each_file, e2)
                    logger.debug(err_message)

        if not err_message:
            logger.debug("all files copied locally for job {} and number {}".format(job_name, build_number))
            with open(job_build_status_file, 'w') as f1:
                f1.write('complete')
            sftp_done = True
        elif loop_count > 3:
            logger.debug("failed to get job files over ssh. final message: {}".format(err_message))
            with open(job_build_status_file, 'w') as f2:
                f2.write('failed: {}'.format(err_message))
            sftp_done = True
        else:
            loop_count += 1

    ssh.close()


@brinks.put("/getfiles/")
def get_files(resp: Response, bt: BackgroundTasks, job_name: str, build_number: str, archive_path: str = "/archive"):
    job_name = job_name.strip().strip('/')
    build_number = build_number.strip().strip('/')
    archive_path = archive_path.strip().strip('/')
    local_job_path = '{}/{}/{}'.format(app_download_dir, job_name, build_number)
    status_file = local_job_path + '/.brinks.status'
    if os.path.isdir(local_job_path):
        if os.path.exists(status_file):
            with open(status_file) as f1:
                status_file_content = f1.read().strip()
            if status_file_content == 'complete':
                resp.status_code = status.HTTP_208_ALREADY_REPORTED
                return {"message": 'Job already exists and status is complete'}
            elif status_file_content == 'start':
                resp.status_code = status.HTTP_202_ACCEPTED
                return {'message': 'Job already accepted and copy in progress'}

    else:
        try:
            os.makedirs(local_job_path)
            logger.debug("folder {} created locally".format(local_job_path))
        except (IOError, OSError) as e1:
            logger.debug("failed create build directory {} locally. Message: {}".format(local_job_path, e1))
            resp.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {'message': 'failed to create {}. error {}'.format(local_job_path, e1)}

    try:
        with open(status_file, 'w') as f2:
            f2.write('start')
    except (IOError, OSError) as e2:
        msg = 'failed to create status file. error {}'.format(e2)
        resp.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {'message': msg}

    bt.add_task(rsync_files_from_build,
                job_name=job_name,
                build_number=build_number,
                file_path=archive_path,
                job_build_path=local_job_path,
                job_build_status_file=status_file)

    resp.status_code = status.HTTP_201_CREATED
    return {'message': 'sync initiated'}


@brinks.get("/status/")
def get_status(job_name: str, build_number: str, response: Response):
    job_name = job_name.strip().strip('/')
    build_number = build_number.strip().strip('/')
    job_status_file = '{}/{}/{}/.brinks.status'.format(app_download_dir, job_name, build_number)
    if not os.path.exists(job_status_file):
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        return {"message": "Thomas had never seen such bullshit build before :angry_thomas_face_emoji:"}

    with open(job_status_file) as f1:
        status_file_data = f1.read().strip()

    if status_file_data == 'complete':
        response.status_code = status.HTTP_200_OK
        message = "job available for download"
    elif status_file_data.startswith('failed'):
        status_file_data_array = status_file_data.split(':', 1)
        response.status_code = status.HTTP_204_NO_CONTENT
        message = "sync for job has failed. Error message was: {}".format(status_file_data_array[-1])
    else:
        response.status_code = status.HTTP_206_PARTIAL_CONTENT
        message = "sync for job is in progress"

    return {"message": message}
