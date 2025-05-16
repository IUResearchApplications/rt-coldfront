ARG build_env=production

FROM python:3.10 AS build

ARG build_env
ENV BUILD_ENV=$build_env

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        libldap-dev \
        libsasl2-dev && \
    apt-get clean -y

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /usr/src/app
RUN pip3 install --upgrade pip

COPY . .

RUN mkdir /opt/coldfront/
RUN cp -r ./coldfront/components/* /opt/coldfront/

RUN pip3 install .
RUN pip3 install ./coldfront-custom-resources/
RUN pip3 install setuptools python-ldap ldap3 oracledb django-cas-ng mysqlclient
RUN if [ "${BUILD_ENV}" = "local" ]; then \
        pip3 install django-extensions Werkzeug pyOpenSSL django-debug-toolbar; \
    else \
        pip3 install gunicorn; \
    fi

FROM python:3.10-slim AS app
COPY --from=build --chown=1001:0 /opt/venv /opt/venv
COPY --from=build --chown=1001:0 /usr/src/app/startup.sh /opt/venv/bin
COPY --from=build --chown=1001:0 /usr/src/app/coldfront/components /usr/src/app/coldfront/components 

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        default-mysql-client \
        libldap-dev \
        libsasl2-dev && \
    apt-get clean -y

ARG build_env
ENV BUILD_ENV=$build_env

WORKDIR /usr/src/app
ENV PATH="/opt/venv/bin:$PATH"

ENV COLDFRONT_ENV=/opt/coldfront/coldfront.env
ENV COLDFRONT_CONFIG=/opt/coldfront/local_settings.py
ENV SITE_STATIC=/usr/src/app/coldfront/components/site/static
ENV SITE_TEMPLATES=/usr/src/app/coldfront/components/site/templates

EXPOSE 8000
EXPOSE 3306

USER 1001

CMD ["startup.sh"]
