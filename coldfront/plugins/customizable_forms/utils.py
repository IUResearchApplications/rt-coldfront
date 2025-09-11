import re


def standardize_resource_name(resource_name):
    return re.sub('[^A-Za-z0-9]+', '', resource_name)
