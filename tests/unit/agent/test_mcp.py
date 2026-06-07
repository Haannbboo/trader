from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from tools import ToolLayer
from tools.mcp import McpClient


# Mock account service to satisfy ToolLayer dependencies
class MockAccount:
    async def get_balance(self):
        return None

    async def get_positions(self):
        return []


@pytest.mark.asyncio
async def test_mcp_client_list_tools_whitelist() -> None:
    # Test list_tools with a specific whitelist
    client = McpClient(
        name="test_mcp", url="http://mock-mcp/mcp", tools_filter=["tool1"]
    )

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/event-stream"}
    mock_response.text = (
        "event: message\n"
        'data: {"jsonrpc": "2.0", "id": 1, "result": {"tools": ['
        '{"name": "tool1", "description": "Tool 1"},'
        '{"name": "tool2", "description": "Tool 2"}'
        "]}}"
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        tools = await client.list_tools()

        assert len(tools) == 1
        assert tools[0]["name"] == "tool1"
        assert len(client.cached_specs) == 1

        # Verify JSON-RPC payload sent
        mock_post.assert_called_once()
        kwargs = mock_post.call_args[1]
        assert kwargs["json"]["method"] == "tools/list"
    await client.close()


@pytest.mark.asyncio
async def test_mcp_client_list_tools_allow_all() -> None:
    # Test list_tools with wildcard "*" whitelist
    client = McpClient(name="test_mcp", url="http://mock-mcp/mcp", tools_filter=["*"])

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/event-stream"}
    mock_response.text = (
        "event: message\n"
        'data: {"jsonrpc": "2.0", "id": 1, "result": {"tools": ['
        '{"name": "tool1", "description": "Tool 1"},'
        '{"name": "tool2", "description": "Tool 2"}'
        "]}}"
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        tools = await client.list_tools()

        assert len(tools) == 2
        assert tools[0]["name"] == "tool1"
        assert tools[1]["name"] == "tool2"
    await client.close()


@pytest.mark.asyncio
async def test_mcp_client_call_tool() -> None:
    client = McpClient(name="test_mcp", url="http://mock-mcp/mcp")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/event-stream"}
    mock_response.text = (
        "event: message\n"
        'data: {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "success"}]}}'
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        res = await client.call_tool("tool1", {"arg1": "val1"})

        assert res["content"][0]["text"] == "success"

        # Verify call payload
        mock_post.assert_called_once()
        kwargs = mock_post.call_args[1]
        assert kwargs["json"]["method"] == "tools/call"
        assert kwargs["json"]["params"]["name"] == "tool1"
        assert kwargs["json"]["params"]["arguments"] == {"arg1": "val1"}
    await client.close()


@pytest.mark.asyncio
async def test_tool_layer_mcp_integration() -> None:
    # Verify ToolLayer handles MCP initialization, spec discovery, and routing
    mcp_configs = [
        {
            "name": "test_mcp",
            "url": "http://mock-mcp/mcp",
            "enabled": True,
            "tools": ["tool1"],
        }
    ]

    account = MockAccount()
    layer = ToolLayer(account=account, mcp_configs=mcp_configs)

    # Mocking client's HTTP calls inside initialize/dispatch
    mock_list_response = MagicMock(spec=httpx.Response)
    mock_list_response.status_code = 200
    mock_list_response.headers = {"content-type": "text/event-stream"}
    mock_list_response.text = (
        "event: message\n"
        'data: {"jsonrpc": "2.0", "id": 1, "result": {"tools": ['
        '{"name": "tool1", "description": "Tool 1", "inputSchema": {"type": "object"}}'
        "]}}"
    )

    mock_call_response = MagicMock(spec=httpx.Response)
    mock_call_response.status_code = 200
    mock_call_response.headers = {"content-type": "text/event-stream"}
    mock_call_response.text = (
        "event: message\n"
        'data: {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "result_from_mcp"}]}}'
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_list_response

        # 1. Initialize fetches tools
        await layer.initialize()

        assert len(layer._mcp_clients) == 1
        assert len(layer._mcp_clients[0].cached_specs) == 1

        # 2. Specs are advertised
        specs = layer.tool_specs()
        mcp_specs = [s for s in specs if s["name"] == "tool1"]
        assert len(mcp_specs) == 1
        assert mcp_specs[0]["description"] == "Tool 1"
        assert mcp_specs[0]["parameters"] == {"type": "object"}

        # 3. Dispatches call to client
        mock_post.return_value = mock_call_response
        dispatch_res = await layer.dispatch("tool1", {"arg": "val"})
        assert dispatch_res["content"][0]["text"] == "result_from_mcp"

        # Verify correct args routed
        mock_post.assert_called_with(
            "http://mock-mcp/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "tool1", "arguments": {"arg": "val"}},
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )

        # 4. Clean shutdown
        await layer.close()


def test_url_sanitization() -> None:
    from tools.mcp import _sanitize_url

    assert (
        _sanitize_url("https://mcp.sellthenews.org/mcp?key=123")
        == "https://mcp.sellthenews.org/mcp"
    )
    assert (
        _sanitize_url("https://user:pass@mcp.host.com:8080/mcp")
        == "https://***:***@mcp.host.com:8080/mcp"
    )
    assert _sanitize_url("invalid_url_###") == "invalid-url"


@pytest.mark.asyncio
async def test_tool_layer_duplicate_name_error() -> None:
    # 1. Native collision
    mcp_configs = [
        {
            "name": "test_mcp",
            "url": "http://mock-mcp/mcp",
            "enabled": True,
            "tools": ["get_balance"],  # Native tool name collision!
        }
    ]
    account = MockAccount()
    layer = ToolLayer(account=account, mcp_configs=mcp_configs)

    mock_list_response = MagicMock(spec=httpx.Response)
    mock_list_response.status_code = 200
    mock_list_response.headers = {"content-type": "text/event-stream"}
    mock_list_response.text = (
        "event: message\n"
        'data: {"jsonrpc": "2.0", "id": 1, "result": {"tools": ['
        '{"name": "get_balance", "description": "tool"}'
        "]}}"
    )
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_list_response
        with pytest.raises(ValueError) as exc:
            await layer.initialize()
        assert "Duplicate tool name 'get_balance' detected" in str(exc.value)
    await layer.close()

    # 2. Inter-MCP client collision
    mcp_configs2 = [
        {
            "name": "mcp1",
            "url": "http://mcp1/mcp",
            "enabled": True,
            "tools": ["toolA"],
        },
        {
            "name": "mcp2",
            "url": "http://mcp2/mcp",
            "enabled": True,
            "tools": ["toolA"],
        },
    ]
    layer2 = ToolLayer(account=account, mcp_configs=mcp_configs2)
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        # We need mock_list_response to return name: toolA for both
        mock_list_response.text = (
            "event: message\n"
            'data: {"jsonrpc": "2.0", "id": 1, "result": {"tools": ['
            '{"name": "toolA", "description": "tool"}'
            "]}}"
        )
        mock_post.return_value = mock_list_response
        with pytest.raises(ValueError) as exc:
            await layer2.initialize()
        assert "Duplicate tool name 'toolA' detected" in str(exc.value)
    await layer2.close()
