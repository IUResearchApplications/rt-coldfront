FROM python:3.10

ARG build_env=production
ENV BUILD_ENV=$build_env

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        libldap-dev \
        libsasl2-dev && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /usr/src/app

RUN pip3 install --upgrade pip

COPY . .

RUN pip3 install .
RUN pip3 install ./coldfront-custom-resources/
RUN pip3 install setuptools python-ldap ldap3 oracledb django-cas-ng mysqlclient
RUN if [ "${BUILD_ENV}" = "local" ]; then \
        pip3 install django-extensions Werkzeug pyOpenSSL django-debug-toolbar; \
    else \
        pip3 install gunicorn; \
    fi

ENV COLDFRONT_ENV=/opt/coldfront/coldfront.env
ENV COLDFRONT_CONFIG=/opt/coldfront/local_settings.py

EXPOSE 8000
CMD ["/bin/bash", "startup.sh"]
