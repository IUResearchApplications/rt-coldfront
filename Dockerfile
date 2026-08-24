FROM ubuntu:24.04 AS base

FROM base AS cfimage

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        mariadb-client \
        libldap2 \
        libsasl2-2 \
        libmariadb3 \
        libkrb5-3 \
        libk5crypto3 \
        libgssapi-krb5-2 \
        libglib2.0-0 \
        libdbus-1-3

FROM cfimage AS builder

RUN DEBIAN_FRONTEND=noninteractive apt-get install -y \
        gcc \
        cmake \
        pkg-config \
        build-essential \
        libmariadb-dev \
        libssl-dev \
        libdbus-1-dev \
        libldap2-dev \
        libkrb5-dev \
        libglib2.0-dev \
        libsasl2-dev

COPY --from=ghcr.io/astral-sh/uv:0.9.13 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_INSTALL_DIR=/python
ENV UV_PYTHON_PREFERENCE=only-managed

WORKDIR /app

# Install Python before the project for caching
RUN --mount=type=bind,source=.python-version,target=.python-version \
  uv python install

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=.python-version,target=.python-version \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=coldfront-custom-resources,target=coldfront-custom-resources \
  uv sync \
        --locked \
        --no-install-project \
        --no-dev \
        --extra ldap \
        --extra oidc \
        --extra mysql \
        --extra aa \
        --extra cas \
        --extra ccr
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync \
        --locked \
        --no-dev \
        --extra ldap \
        --extra oidc \
        --extra mysql \
        --extra aa \
        --extra cas \
        --extra ccr


FROM base AS oracle-client

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        unzip \
        wget && \
    wget -q https://download.oracle.com/otn_software/linux/instantclient/2326200v2/instantclient-basic-linux.x64-23.26.2.0.0.zip -O /tmp/oracle.zip && \
    unzip /tmp/oracle.zip -d /opt/oracle && \
    rm /tmp/oracle.zip && \
    apt-get clean && rm -rf /var/lib/apt/lists/*


FROM cfimage

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libaio1t64 && \
    ln -s /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=oracle-client /opt/oracle/instantclient_23_26 /opt/oracle/instantclient_23_26
RUN sh -c "echo /opt/oracle/instantclient_23_26 > /etc/ld.so.conf.d/oracle-instantclient.conf" && ldconfig
RUN groupadd -g 1001 coldfrontgroup && useradd -u 1001 -g coldfrontgroup -d /app -s /bin/false coldfrontuser
COPY --from=builder --chown=1001:1001 /python /python
COPY --from=builder --chown=1001:1001 /app /app
ENV PATH="/app/.venv/bin:$PATH"
ENV HOME=/app
RUN mkdir -p /data/static /data/slurm/slurm_dump /data/slate_projects/incoming
EXPOSE 8000
USER coldfrontuser
CMD ["gunicorn", "--workers", "3", "--bind", ":8000", "--control-socket", "/tmp/gunicorn.ctl", "coldfront.config.wsgi"]
