"""A bare MCPServer (mcp 2.3.0 defaults, one tool) over stdio, for the F-13/F-14 pins. Not a test module."""

from mcp.server import MCPServer

server = MCPServer("bare")


@server.tool()
def count(n: int) -> int:
    """Return n."""
    return n


if __name__ == "__main__":
    server.run("stdio")
