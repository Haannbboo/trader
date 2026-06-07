"""
mcp — Model Context Protocol Client.

Provides the `McpClient` class to dynamically fetch, register, and invoke tools
exposed by external MCP servers.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx
from loguru import logger


class McpClient:
    """Client for talking to external Model Context Protocol (MCP) servers.
    Currently supports stateless HTTP transport (JSON-RPC POST with SSE framing).
    """

    def __init__(
        self, name: str, url: str, tools_filter: Optional[list[str]] = None
    ) -> None:
        self.name = name
        self.url = url
        self.tools_filter = tools_filter or []
        logger.info(
            "[MCP Client] Loading MCP server: name={} url={} tools={}",
            self.name,
            self.url,
            self.tools_filter,
        )
        self._client = httpx.AsyncClient(timeout=30.0)
        self.cached_specs: list[dict[str, Any]] = []

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def list_tools(self) -> list[dict[str, Any]]:
        """Fetch all tools from the MCP server, apply whitelisting/filtering, and cache schemas."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        try:
            response = await self._client.post(self.url, json=payload, headers=headers)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                data = self._parse_sse_data(response.text)
            else:
                data = response.json()

            if "error" in data:
                raise ValueError(f"MCP server error: {data['error']}")

            tools = data.get("result", {}).get("tools", [])

            # Whitelist filtering:
            # - If '*' is in the list, allow all tools.
            # - If specific tools are listed, allow only those.
            # - Otherwise, allow none.
            if "*" in self.tools_filter:
                filtered_tools = tools
            elif self.tools_filter:
                filtered_tools = [
                    t for t in tools if t.get("name") in self.tools_filter
                ]
            else:
                filtered_tools = []

            self.cached_specs = filtered_tools
            return filtered_tools

        except Exception as e:
            logger.exception(
                "Failed to list tools from MCP server {}: {}", self.name, e
            )
            return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool on the MCP server via JSON-RPC over HTTP."""
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        response = await self._client.post(self.url, json=payload, headers=headers)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            data = self._parse_sse_data(response.text)
        else:
            data = response.json()

        if "error" in data:
            raise ValueError(f"MCP tool execution failed: {data['error']}")

        return data.get("result", {})

    def _parse_sse_data(self, text: str) -> dict[str, Any]:
        """Parse SSE format ('event: message\\ndata: {...}') and extract the data payload."""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                json_str = line[len("data:") :].strip()
                return json.loads(json_str)
        raise ValueError("Failed to find data line in SSE response")
