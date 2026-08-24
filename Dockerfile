FROM ubuntu:24.04 AS base

FROM base AS cfimage

RUN apt update && DEBIAN_FRONTEND=noninteractive apt install -y --no-install-recommends \
        mariadb-client

FROM cfimage AS builder

RUN DEBIAN_FRONTEND=noninteractive apt install -y \
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


FROM cfimage

RUN apt-get clean && rm -rf /var/lib/apt/lists/*
RUN groupadd -g 1001 coldfrontgroup && useradd -u 1001 -g coldfrontgroup -d /app -s /bin/false coldfrontuser
COPY --from=builder --chown=1001:1001 /python /python
COPY --from=builder --chown=1001:1001 /app /app
ENV PATH="/app/.venv/bin:$PATH"
ENV HOME=/app
RUN mkdir -p /data
RUN mkdir -p /data/static
RUN mkdir -p /data/slurm/slurm_dump
RUN mkdir -p /data/slate_projects/incoming
EXPOSE 8000
USER coldfrontuser
CMD ["gunicorn", "--workers", "3", "--bind", ":8000", "coldfront.config.wsgi"]
