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


def bind_host(settings: Settings) -> str:
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
    mcp: MCPServer, settings: Settings
) -> tuple[uvicorn.Server, socket.socket]:
    bind = bind_host(settings)
    sock = _bind_socket(bind, settings.port)
    config = uvicorn.Config(
        streamable_http_app(mcp, host=bind),
        host=bind,
        port=settings.port,
        log_level="warning",
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    )
    server = uvicorn.Server(config)
    _close_listen_streams_on_shutdown(server, mcp)
    return server, sock


def serve_http(mcp: MCPServer, settings: Settings) -> None:
    server, sock = create_uvicorn_server(mcp, settings)
    try:
        try:
            server.run(sockets=[sock])
        except KeyboardInterrupt:
            pass
    finally:
        sock.close()


def _close_listen_streams_on_shutdown(server: uvicorn.Server, mcp: MCPServer) -> None:
    original_shutdown = getattr(server, "shutdown", None)
    if original_shutdown is None:
        return

    async def shutdown(*args, **kwargs):
        _close_listen_streams(mcp)
        return await original_shutdown(*args, **kwargs)

    server.shutdown = shutdown


def _close_listen_streams(mcp: MCPServer) -> None:
    lowlevel = getattr(mcp, "_lowlevel_server", None)
    handlers = getattr(lowlevel, "_request_handlers", {})
    entry = handlers.get("subscriptions/listen")
    handler = getattr(entry, "handler", None)
    close = getattr(handler, "close", None)
    if close is not None:
        close()


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
