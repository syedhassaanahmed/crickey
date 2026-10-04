from __future__ import annotations

import socket
import sys
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
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS: Final = 5.0
_ALLOWED_HOSTS: Final = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_ALLOWED_ORIGINS: Final = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
]


class TransportError(RuntimeError):
    """Raised when the transport cannot be started."""


def bind_host(settings: Settings, requested_host: str | None = None) -> str:
    del requested_host
    return CONTAINER_HOST if settings.in_container else NATIVE_HOST


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


def create_uvicorn_server(
    mcp: MCPServer, settings: Settings, *, host: str | None = None
) -> tuple[uvicorn.Server, socket.socket]:
    bind = bind_host(settings, host)
    sock = _bind_socket(bind, settings.port)
    config = uvicorn.Config(
        streamable_http_app(mcp, host=bind),
        host=bind,
        port=settings.port,
        log_level="warning",
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    )
    return uvicorn.Server(config), sock


def serve_http(mcp: MCPServer, settings: Settings, *, host: str | None = None) -> None:
    server, sock = create_uvicorn_server(mcp, settings, host=host)
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()


def _bind_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError as error:
        sock.close()
        raise TransportError(
            f"Port {port} is already in use; choose another port with --port or CRICKEY_PORT."
        ) from error
    return sock
