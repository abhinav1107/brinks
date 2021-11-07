import os
from starlette.config import Config


def ensure_dir(base_path):
    for each_dir in ['logs', 'downloads']:
        dir_path = '{}/{}'.format(base_path, each_dir)
        if os.path.exists(dir_path) and not os.path.isdir(dir_path):
            raise FileExistsError("path {} exists but it is not a directory".format(dir_path))
        if not os.path.exists(dir_path):
            os.mkdir(dir_path)


__app_base_dir = os.path.abspath(os.path.dirname(os.path.abspath(__file__)) + '/../../')
config = Config(".env")
VERSION = '0.1.0'
PROJECT_NAME = "Brinks - Using FastAPI"
APP_PATH = config("APP_PATH", default=__app_base_dir)
JENKINS_HOST: str = config("JENKINS_HOST")
JENKINS_USER: str = config("JENKINS_USER", default="root")
JENKINS_HOME: str = config("JENKINS_HOME", default='/var/lib/jenkins')
DEBUG: bool = config("DEBUG", cast=bool, default=False)
ensure_dir(APP_PATH)
