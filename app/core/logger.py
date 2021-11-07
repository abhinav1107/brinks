import logging


def get_logger(name):
    return logging.getLogger("brinks." + name)
