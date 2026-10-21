# syntax=docker/dockerfile:1

FROM docker:28-cli AS docker-cli

FROM ghcr.io/astral-sh/uv:0.11 AS uv

# --- 建置相依套件 ---
FROM python:3.13-slim-trixie AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- 執行環境 ---
FROM python:3.13-slim-trixie

# systemctl / journalctl 用來透過 D-Bus 與 journal 檔案操作主機的 systemd（容器內不執行 systemd）
RUN apt-get update \
    && apt-get install -y --no-install-recommends systemd \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker-cli /usr/local/bin/docker /usr/bin/docker

# 固定 UID，主機的 polkit 規則以此 UID 對應的使用者授權
ARG APP_UID=10001
RUN groupadd --system --gid ${APP_UID} svcbot \
    && useradd --system --uid ${APP_UID} --gid ${APP_UID} --no-create-home --shell /usr/sbin/nologin svcbot \
    && mkdir -p /app/database \
    && chown svcbot:svcbot /app/database

COPY --from=builder /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app
USER svcbot

VOLUME ["/app/database"]

CMD ["system-controller"]
