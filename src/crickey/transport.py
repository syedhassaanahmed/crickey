from __future__ import annotations

import socket
from contextlib import closing
from typing import Final

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from crickey.settings import Settings

MCP_PATH: Final = "/mcp"
NATIVE_HOST: Final = "127.0.0.1"
CONTAINER_HOST: Final = "0.0.0.0"
_ALLOWED_HOSTS: Final = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_ALLOWED_ORIGINS: Final = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
]


class TransportError(RuntimeError):
    """Raised when the transport cannot be started."""


def bind_host(settings: Settings, requested_host: str | None = None) -> str:
    expected = CONTAINER_HOST if settings.in_container else NATIVE_HOST
    if requested_host is not None and requested_host != expected:
        mode = "container" if settings.in_container else "native"
        raise TransportError(f"crickey {mode} mode must bind to {expected}, not {requested_host}.")
    return expected


def transport_security_settings() -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(_ALLOWED_HOSTS),
        allowed_origins=list(_ALLOWED_ORIGINS),
    )


def streamable_http_app(mcp: MCPServer, *, host: str = NATIVE_HOST):
    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        transport_security=transport_security_settings(),
        host=host,
    )


def run_stdio(mcp: MCPServer) -> None:
    mcp.run()


def serve_http(mcp: MCPServer, settings: Settings, *, host: str | None = None) -> None:
    bind = bind_host(settings, host)
    _ensure_port_available(bind, settings.port)
    uvicorn.run(
        streamable_http_app(mcp, host=bind),
        host=bind,
        port=settings.port,
        log_level="warning",
    )


def _ensure_port_available(host: str, port: int) -> None:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        try:
            sock.bind((host, port))
        except OSError as error:
            raise TransportError(
                f"Port {port} is already in use; choose another port with --port or CRICKEY_PORT."
            ) from error
