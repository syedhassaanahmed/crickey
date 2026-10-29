from __future__ import annotations

import errno
import hmac
import ipaddress
import socket
from typing import Final

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from crickey.settings import LOCAL_HOSTS, LOCAL_ORIGINS, TOKEN_MIN_LENGTH, Settings

MCP_PATH: Final = "/mcp"
HEALTH_PATH: Final = "/health"
NATIVE_HOST: Final = "127.0.0.1"
CONTAINER_HOST: Final = "0.0.0.0"
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS: Final = 5.0
_ADDRESS_IN_USE: Final = {errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE)}


class TransportError(RuntimeError):
    """Raised when the transport cannot be started."""


class BearerTokenMiddleware:
    """Answers 401 to any request but /health without `Authorization: Bearer <token>` (D43)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != HEALTH_PATH:
            scheme, _, given = Headers(scope=scope).get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                given.strip().encode("latin-1"), self._token
            ):
                response = PlainTextResponse(
                    "Missing or wrong bearer token.",
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def bind_host(settings: Settings) -> str:
    if settings.host is not None:
        return settings.host
    return CONTAINER_HOST if settings.in_container else NATIVE_HOST


def transport_security_settings(settings: Settings) -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*LOCAL_HOSTS, *(_authority(url) for url in settings.urls)],
        allowed_origins=[*LOCAL_ORIGINS, *settings.urls],
    )


def streamable_http_app(mcp: MCPServer, settings: Settings) -> ASGIApp:
    @mcp.custom_route(HEALTH_PATH, methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        transport_security=transport_security_settings(settings),
        host=bind_host(settings),
    )
    return app if settings.token is None else BearerTokenMiddleware(app, settings.token)


def run_stdio(mcp: MCPServer) -> None:
    mcp.run()


def create_uvicorn_server(
    mcp: MCPServer, settings: Settings
) -> tuple[uvicorn.Server, socket.socket]:
    reach = _reach_beyond_this_machine(settings)
    if reach is not None and settings.token is None:
        raise TransportError(
            f"{reach} lets other machines reach crickey, so set CRICKEY_TOKEN to a secret of at "
            f'least {TOKEN_MIN_LENGTH} characters, which clients send as "Authorization: Bearer '
            '<token>".'
        )
    bind = bind_host(settings)
    sock = _bind_socket(bind, settings.port)
    app = streamable_http_app(mcp, settings)
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


def _reach_beyond_this_machine(settings: Settings) -> str | None:
    """The setting that lets other machines reach crickey, if any (D43).

    The image's 0.0.0.0 doesn't count: Docker publishes it on the host's loopback (D20).
    """
    if settings.host is not None and not _is_loopback(settings.host):
        return f"The bind address {settings.host}"
    for url in settings.urls:
        if not _is_loopback(_host_name(_authority(url))):
            return f"The URL {url}"
    return None


def _authority(url: str) -> str:
    """The Host value of a URL, such as crickey.example.com in "https://crickey.example.com"."""
    return url.split("://", 1)[1]


def _is_loopback(name: str) -> bool:
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _host_name(value: str) -> str:
    """The host of a Host value, such as localhost in "localhost:8765" or ::1 in "[::1]:*"."""
    if value.startswith("["):
        return value[1 : value.index("]")]
    return value.split(":", 1)[0]


def _bind_socket(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        return socket.create_server((host, port), family=family)
    except OSError as error:
        if error.errno in _ADDRESS_IN_USE:
            raise TransportError(
                f"Port {port} is already in use; choose another port with --port or CRICKEY_PORT."
            ) from error
        raise TransportError(
            f"crickey can't listen on {host}: {error.strerror}; check --host or CRICKEY_HOST."
        ) from error
