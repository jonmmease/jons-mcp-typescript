"""MCP server for TypeScript development via vtsls, Prettier, and ESLint."""

from . import tools  # noqa: F401 - Register tools with MCP

__version__ = "0.1.0"


def main() -> None:
    """Entry point for the MCP server."""
    from .server import run_server

    run_server()


__all__ = ["main", "__version__"]
