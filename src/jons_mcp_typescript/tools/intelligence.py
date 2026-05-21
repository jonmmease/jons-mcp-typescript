"""Intelligence tools for TypeScript development - diagnostics and rename."""

from fastmcp import Context

from .. import server as server_state
from ..constants import DEFAULT_LIMIT, DEFAULT_OFFSET
from ..server import (
    clear_diagnostics_for_uri,
    close_file,
    current_diagnostics,
    document_states,
    ensure_vtsls_indexed,
    get_daemon,
    mcp,
    open_file,
    pending_diagnostics_events,
    register_diagnostics_event,
    resolve_project_file,
    wait_for_diagnostics,
)
from ..utils import apply_pagination, diagnostic_sort_key


@mcp.tool()
async def diagnostics(
    file_path: str | None = None,
    scope: str = "file",
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
    ctx: Context | None = None,
) -> dict:
    """Get type errors and warnings.

    Args:
        file_path: Path to file (required if scope='file')
        scope: 'file' (single file) or 'workspace' (all cached diagnostics)
               Note: 'workspace' only shows diagnostics for files previously queried
        limit: Maximum results to return
        offset: Number of results to skip

    Returns: {items: [...], totalItems, offset, limit, hasMore, nextOffset}
    """
    all_diagnostics = []

    if scope == "file":
        if not file_path:
            return {"error": "file_path required when scope='file'"}
        project_file = resolve_project_file(file_path)
        client = await ensure_vtsls_indexed(file_path)
        file_uri = project_file.uri

        try:
            # Clear cached diagnostics and register event BEFORE opening file
            clear_diagnostics_for_uri(file_uri)
            register_diagnostics_event(file_uri)

            # Open/sync file with fresh content from disk
            await open_file(client, project_file.path, file_uri)

            # Wait for diagnostics to arrive via event (with timeout)
            all_diagnostics = await wait_for_diagnostics(file_uri)
        finally:
            # Close file so vtsls reads from disk next time
            await close_file(client, file_uri)

    elif scope == "workspace":
        # Return all cached diagnostics from previous queries
        for uri, diags in current_diagnostics.items():
            all_diagnostics.extend([{"uri": uri, **d} for d in diags])

    if not all_diagnostics:
        return {
            "items": [],
            "totalItems": 0,
            "offset": offset,
            "limit": limit,
            "hasMore": False,
        }

    sorted_items = sorted(all_diagnostics, key=diagnostic_sort_key)
    paginated, metadata = apply_pagination(sorted_items, offset, limit)
    return {"items": paginated, **metadata}


@mcp.tool()
async def rename(
    file_path: str,
    line: int,
    character: int,
    new_name: str,
    ctx: Context | None = None,
) -> dict:
    """Safely rename a symbol across the project.

    Args:
        file_path: Path to the file containing the symbol
        line: Line number (0-indexed)
        character: Column number (0-indexed)
        new_name: New name for the symbol

    Returns: WorkspaceEdit with all changes needed
    """
    project_file = resolve_project_file(file_path)
    client = await ensure_vtsls_indexed(file_path)
    file_uri = project_file.uri
    await open_file(client, project_file.path, file_uri)

    try:
        # Optional: Validate rename is possible
        try:
            prepare_result = await client.request(
                "textDocument/prepareRename",
                {
                    "textDocument": {"uri": file_uri},
                    "position": {"line": line, "character": character},
                },
            )
            if not prepare_result:
                return {"error": "Symbol cannot be renamed"}
        except Exception:
            # prepareRename is optional, continue with rename
            pass

        # Perform rename
        result = await client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
                "newName": new_name,
            },
        )

        if not result:
            return {"error": "Rename failed", "changes": {}}

        return result if isinstance(result, dict) else {"error": "Rename failed"}
    finally:
        await close_file(client, file_uri)


@mcp.tool()
async def restart_server(ctx: Context | None = None) -> str:
    """Restart TypeScript language server.

    Use this after making changes to tsconfig.json or when the server
    seems to be in a bad state.

    Returns: Status message
    """
    client = server_state.vtsls
    if not client:
        return "Error: TypeScript server not running"

    # Clear all cached state
    current_diagnostics.clear()
    document_states.clear()
    pending_diagnostics_events.clear()

    # Restart the language server and daemon.
    await client.restart()
    daemon = get_daemon()
    await daemon.restart()

    return "TypeScript language server and formatter/linter daemon restarted successfully"
