# syntax=docker/dockerfile:1.10

FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.14.8-slim-trixie AS build
COPY --from=uv /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN --mount=type=secret,id=uv_index_url,env=UV_DEFAULT_INDEX,required=false \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable \
    && rm -f /app/.venv/.lock \
    && rm -f uv.lock

FROM python:3.14.8-slim-trixie AS final

LABEL org.opencontainers.image.source="https://github.com/syedhassaanahmed/crickey" \
      org.opencontainers.image.description="crickey is for personal use at the user's own risk; Cricinfo's terms ban data-extraction tools, and crickey isn't affiliated with Cricinfo, ESPNcricinfo or ESPN." \
      org.opencontainers.image.licenses="MIT"

ENV CRICKEY_IN_CONTAINER=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 10001 crickey \
    && useradd --uid 10001 --gid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin --no-create-home crickey

WORKDIR /app
COPY --from=build /app/.venv /app/.venv

USER 10001:10001
EXPOSE 8765
ENTRYPOINT ["crickey"]
CMD ["serve"]
