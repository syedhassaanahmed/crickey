"""crickey's tools for Inspect agents, over Streamable HTTP.

Inspect's own MCP tools (`mcp_server_http`) would change what models see and what the eval can
measure, so the eval connects through this bridge instead:
- Inspect keeps only the JSON Schema keywords its `ToolParam` has, so `$defs`, `$ref`, `oneOf` and
  `const` go. `query_stats` would offer `query` as an empty object (21,851 characters of schema
  down to 193), and the answer tools' `period` would lose its kinds. MCP clients such as Copilot
  CLI send models the whole schema, so the bridge inlines it into keywords Inspect keeps.
- Inspect drops each result's `_meta`, where crickey reports the call's Statsguru requests.
- Inspect would hand a block to the model as an error; the bridge stops the run instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from inspect_ai.tool import Tool, ToolDef, ToolError, ToolParams, ToolSource
from inspect_ai.util import store
from mcp import Client
from mcp.types import TextContent
from mcp.types import Tool as McpTool

from crickey.server import STATSGURU_META_KEY

# Each sample's store lists its calls' Statsguru tallies under this key.
CALLS_KEY = "crickey_calls"
# What crickey says when Statsguru blocks it.
_BLOCK_MESSAGES = ("Statsguru blocked this request", "Statsguru access is paused until")
# Keywords Inspect's ToolParam doesn't keep, or that only label a schema.
_DROPPED = frozenset({"$defs", "$schema", "discriminator", "title"})


class StatsguruBlocked(RuntimeError):
    """Statsguru blocked crickey. The eval stops rather than ask again."""


class CrickeyTools(ToolSource):
    """crickey's tools, listed once and called over a fresh MCP session each time."""

    def __init__(self, server: Any, *, timeout: float = 600.0) -> None:
        # A URL such as http://127.0.0.1:8765/mcp, or an in-process server in tests.
        self._server = server
        self._timeout = timeout
        self._tools: list[Tool] | None = None
        self.instructions: str | None = None

    async def connect(self) -> None:
        if self._tools is not None:
            return
        async with self._client() as client:
            self.instructions = client.instructions
            listed = (await client.list_tools()).tools
        self._tools = [self._tool(mcp_tool) for mcp_tool in listed]

    async def tools(self) -> list[Tool]:
        await self.connect()
        assert self._tools is not None
        return list(self._tools)

    def _client(self) -> Client:
        return Client(self._server, read_timeout_seconds=self._timeout)

    def _tool(self, mcp_tool: McpTool) -> Tool:
        # Inspect passes a call's arguments through as they are only to `**kwargs: Any`.
        async def execute(**kwargs: Any) -> str:
            async with self._client() as client:
                result = await client.call_tool(mcp_tool.name, kwargs)
            text = "\n\n".join(
                item.text for item in result.content if isinstance(item, TextContent)
            )
            tally = (result.meta or {}).get(STATSGURU_META_KEY) or {}
            record_call(
                {
                    "tool": mcp_tool.name,
                    "requests": int(tally.get("requests", 0)),
                    "cached_pages": int(tally.get("cached_pages", 0)),
                    "error": result.is_error,
                }
            )
            if result.is_error:
                if any(message in text for message in _BLOCK_MESSAGES):
                    raise StatsguruBlocked(text)
                raise ToolError(text)
            return text

        parameters = ToolParams.model_validate(inline_schema(mcp_tool.input_schema))
        # Inspect needs every parameter described; its own MCP tools fall back to the name too.
        for name, parameter in parameters.properties.items():
            parameter.description = parameter.description or name
        return ToolDef(
            execute,
            name=mcp_tool.name,
            description=mcp_tool.description,
            parameters=parameters,
        ).as_tool()


def record_call(record: Mapping[str, Any]) -> None:
    current = store()
    current.set(CALLS_KEY, [*current.get(CALLS_KEY, []), dict(record)])


def inline_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The schema with `$ref`s inlined, in the JSON Schema keywords Inspect's ToolParam keeps.

    `oneOf` becomes `anyOf`, `const` a one-value `enum`, and an exclusive bound on an integer an
    inclusive one; labels and discriminators go, since the variants' `kind` enums say the same.
    """
    definitions = schema.get("$defs", {})

    def convert(node: Any, seen: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [convert(item, seen) for item in node]
        if not isinstance(node, Mapping):
            return node
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            if name in seen:
                raise ValueError(f"recursive schema: {name}")
            rest = {key: value for key, value in node.items() if key != "$ref"}
            return {**convert(definitions[name], (*seen, name)), **convert(rest, seen)}
        converted: dict[str, Any] = {}
        for key, value in node.items():
            if key in _DROPPED:
                continue
            if key == "properties":
                converted[key] = {name: convert(item, seen) for name, item in value.items()}
            elif key in {"anyOf", "oneOf"}:
                converted["anyOf"] = [*converted.get("anyOf", []), *convert(value, seen)]
            elif key == "const":
                converted["enum"] = [value]
            elif key in {"exclusiveMinimum", "exclusiveMaximum"}:
                bound = "minimum" if key == "exclusiveMinimum" else "maximum"
                step = 1 if key == "exclusiveMinimum" else -1
                integer = node.get("type") == "integer"
                converted[bound] = value + step if integer else value
            elif key in {"items", "additionalProperties", "not"}:
                converted[key] = convert(value, seen)
            else:
                converted[key] = value
        return converted

    return convert({key: value for key, value in schema.items() if key != "$defs"}, ())
