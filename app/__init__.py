from app.core.config import APP_PATH, DEBUG
import logging

if DEBUG:
    log_level = logging.DEBUG
else:
    log_level = logging.WARNING

logging.basicConfig(
    filename='{}/logs/brinks.log'.format(APP_PATH),
    level=log_level,
    format='[%(asctime)s] [%(name)s] [%(levelname)s]: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S%z'
)
