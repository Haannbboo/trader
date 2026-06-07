# ADR-0007: MCP Tool Integration in ToolLayer

## Context

The `trader` system needs to integrate external tools from Model Context Protocol (MCP) servers (e.g., `sellthenews` for real-time options and market sentiment analysis) into the tool layer. The integration must allow whitelisting specific tools, support dynamic loading at runtime, avoid external library dependencies where simple HTTP protocols suffice, and translate MCP schemas to conform to the native ToolLayer's JSON schema format.

## Decision

We adopt a lightweight, configuration-driven MCP integration approach:
- **Lightweight client**: Implement a custom `McpClient` in `packages/agent/tools` utilizing `httpx` to interact with stateless HTTP-based MCP servers over JSON-RPC. It parses SSE framing (`event: message\ndata: ...`) dynamically for response payloads.
- **Dynamic Whitelisting**: Support whitelisting specific tools in the `agent.mcp_servers` configuration block, or using `"*"` to allow all tools from that server.
- **Schema Mapping**: Map MCP `inputSchema` to the native `parameters` field dynamically inside `ToolLayer.tool_specs()` so that the TypeScript agent's schema parser does not crash.
- **Unified Dispatch**: Intercept and route tool executions matching MCP tool names to the appropriate `McpClient` inside `ToolLayer.dispatch()`.
- **Architectural Boundary / Integration Choice**:
  - **Direct MCP Client Routing**: Best suited for single-use, stateless query/response tools (e.g., searching news, options calculations, lookups) consumed *only* by the agent. These are integrated by updating config files directly without writing code.
  - **Adapter & Bus Pipeline**: Best suited for tools/sources whose data needs to be consumed by components other than the agent (e.g., algorithmic trading strategies, risk controls) or require local database persistence. These must instead be implemented as first-class adapters, wrapped in domain services, and pumped to the shared event bus (`Bus`).

## Consequences

- External MCP tools are treated identically to native Python tools by the TypeScript agent, keeping the cross-language HTTP gateway extremely thin.
- Administrators can safely whitelist only a subset of sensitive tools from third-party MCP servers.
- Adding a new tool from an existing MCP server only requires updating the YAML configuration and restarting the live gateway (no code modifications).
- Dependency bloat is minimized by implementing the JSON-RPC stateless HTTP client directly.
- Clear guidelines establish when to use configuration-based MCP routing versus writing a first-class Python adapter.
