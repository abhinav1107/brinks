from app.core.logger import get_logger
from app.core.config import APP_PATH, JENKINS_HOST, JENKINS_USER, JENKINS_HOME
from fastapi import status, BackgroundTasks
import os
import socket
import paramiko

# common place for all functions used across different modules
logger = get_logger(__name__)


def compare_sftp_local_files(crid1, sftp_client1, local_path, remote_path):
    try:
        remote_stat = sftp_client1.stat(remote_path)
    except (IOError, OSError) as file_err:
        logger.error('{}: remote file {} not found during file sync: {}'.format(crid1, remote_path, file_err))
        return 1

    if os.path.exists(local_path) and os.path.getsize(local_path) == remote_stat.st_size:
        logger.info("{}: remote file {} exists locally at {}".format(crid1, remote_path, local_path))
        return 0

    return 2


def get_sftp_files(crid, jenkins_path, local_path):
    logger.info("{}: starting files copy between jenkins and local servers".format(crid))
    sftp_done = False
    loop_count = 0
    status_file = '{}/.brinks.status'.format(local_path)
    ssh_key_file = os.path.abspath(os.path.expanduser(os.path.expandvars('~/.ssh/id_rsa')))
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_key = paramiko.RSAKey.from_private_key_file(ssh_key_file)
    ssh.connect(hostname=JENKINS_HOST, username=JENKINS_USER, pkey=ssh_key)
    sftp1 = ssh.open_sftp()
    while not sftp_done:
        err_message = None
        all_build_files = sftp1.listdir(jenkins_path)
        logger.info("{}: files to get from jenkins server: {}".format(crid, ','.join(all_build_files)))
        for each_file in all_build_files:
            file_status = compare_sftp_local_files(
                crid1=crid,
                sftp_client1=sftp1,
                local_path='{}/{}'.format(local_path, each_file),
                remote_path='{}/{}'.format(jenkins_path, each_file)
            )

            if file_status == 0:
                continue
            elif file_status == 1:
                err_message = 'file {} disappeared during local copy'.format(each_file)
                logger.error("{}: {}".format(crid, err_message))
                loop_count = 4
                break
            else:
                try:
                    sftp1.get('{}/{}'.format(jenkins_path, each_file), '{}/{}'.format(local_path, each_file))
                except Exception as sftp_get_err:
                    err_message = 'failed to get file from jenkins server. err: {}'.format(each_file, sftp_get_err)
                    logger.error("{}: {}".format(crid, err_message))

        if not err_message:
            logger.info("{}: all files copied locally from jenkins server".format(crid))
            with open(status_file, 'w') as f1:
                f1.write("complete")
            sftp_done = True
        elif loop_count > 3:
            logger.error("{}: failed to get job files over ssh. final message: {}".format(crid, err_message))
            with open(status_file, 'w') as f2:
                f2.write('failed: {}'.format(err_message))
            sftp_done = True
        else:
            loop_count += 1

    ssh.close()


def get_jenkins_build_files(bt, rid, job, build, path):
    jenkins_job_path = '{}/jobs/{}/builds/{}/{}'.format(JENKINS_HOME, job, build, path)
    local_job_path = '{}/downloads/{}/{}'.format(APP_PATH, job, build)
    job_status_file = '{}/.brinks.status'.format(local_job_path)

    if os.path.exists(job_status_file):
        with open(job_status_file) as f1:
            status_content = f1.read().strip()
        if status_content == 'complete':
            return status.HTTP_208_ALREADY_REPORTED, 'files already exists and are ready for download'
        elif status_content == 'start':
            return status.HTTP_206_PARTIAL_CONTENT, 'request already accepted. sync in progress. wait some more time'

    ssh_key_file = os.path.abspath(os.path.expanduser(os.path.expandvars('~/.ssh/id_rsa')))
    if not os.path.exists(ssh_key_file):
        msg = "{}: ssh private key file not found at: {}".format(rid, ssh_key_file)
        logger.error(msg)
        return status.HTTP_511_NETWORK_AUTHENTICATION_REQUIRED, msg

    logger.info("{}: connecting to {} with user {} using ssh key".format(rid, JENKINS_HOST, JENKINS_USER))
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_key = paramiko.RSAKey.from_private_key_file(ssh_key_file)
    try:
        ssh.connect(hostname=JENKINS_HOST, username=JENKINS_USER, pkey=ssh_key)
    except (
            paramiko.BadHostKeyException,
            paramiko.AuthenticationException,
            paramiko.SSHException,
            socket.error
    ) as ssh_error:
        msg = '{}: ssh connection failed with message: {}'.format(rid, ssh_error)
        logger.error(msg)
        return status.HTTP_500_INTERNAL_SERVER_ERROR, msg

    logger.info("{}: ssh connection successful".format(rid))
    sftp = ssh.open_sftp()
    logger.info("{}: sftp connection opened".format(rid))
    try:
        sftp.stat(jenkins_job_path)
    except Exception as sftp_folder_error:
        msg = '{}: folder {} not found on jenkins server. err was: {}'.format(rid, jenkins_job_path, sftp_folder_error)
        logger.error(msg)
        return status.HTTP_412_PRECONDITION_FAILED, msg

    all_path_files = []
    all_sftp_dir_list = sftp.listdir(jenkins_job_path)
    for each_item in all_sftp_dir_list:
        each_full_path = '{}/{}'.format(jenkins_job_path, each_item)
        try:
            sftp.listdir(each_full_path)
        except IOError:
            all_path_files.append(each_item)
    if len(all_path_files) == 0:
        msg = 'no files found for download under {}/{}/{}'.format(job, build, path)
        logger.info("{}: {}".format(rid, msg))
        return status.HTTP_404_NOT_FOUND, msg

    logger.info("{}: folder {} found on jenkins server. getting build files".format(rid, jenkins_job_path))
    try:
        os.makedirs(local_job_path, exist_ok=True)
    except (IOError, OSError) as folder_create_err:
        msg = "failed to create local path {}. err: {}".format(local_job_path, folder_create_err)
        logger.error("{}: {}".format(rid, msg))
        return status.HTTP_500_INTERNAL_SERVER_ERROR, msg

    ssh.close()

    with open(job_status_file, 'w') as f1:
        f1.write('start')

    bt.add_task(get_sftp_files, crid=rid, jenkins_path=jenkins_job_path, local_path=local_job_path)

    return status.HTTP_201_CREATED, 'request accepted. sync initiated. check status (complete/start/failed) later'
