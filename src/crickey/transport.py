from __future__ import annotations

import os
import socket
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
    app = streamable_http_app(mcp, host=bind)
    close_listen_streams = _listen_stream_closer(mcp)
    config = uvicorn.Config(
        app,
        host=bind,
        port=settings.port,
        log_level="warning",
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    )
    server = uvicorn.Server(config)
    _close_listen_streams_on_shutdown(server, close_listen_streams)
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


def _close_listen_streams_on_shutdown(server: uvicorn.Server, close_listen_streams) -> None:
    original_shutdown = server.shutdown

    async def shutdown(*args, **kwargs):
        close_listen_streams()
        return await original_shutdown(*args, **kwargs)

    server.shutdown = shutdown


def _listen_stream_closer(mcp: MCPServer):
    handler_entry = mcp.session_manager.app.get_request_handler("subscriptions/listen")
    return handler_entry.handler.close


def _bind_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "posix":
            # As asyncio does: lets a restart reuse a port still in TIME_WAIT. Elsewhere the
            # option would let a second server share the port, and the default already refuses.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError as error:
        sock.close()
        raise TransportError(
            f"Port {port} is already in use; choose another port with --port or CRICKEY_PORT."
        ) from error
    return sock
