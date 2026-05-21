"""Intelligence tools for TypeScript development - diagnostics and rename preview."""

from typing import Any

from fastmcp import Context
from pydantic import ValidationError

from .. import server as server_state
from ..constants import DEFAULT_LIMIT, DEFAULT_OFFSET
from ..schemas import (
    DiagnosticsResult,
    RenamePreviewEdit,
    RenamePreviewError,
    RenamePreviewResult,
    ToolError,
)
from ..server import (
    clear_diagnostics_for_uri,
    close_file,
    current_diagnostics,
    document_states,
    ensure_project_loaded,
    ensure_vtsls_indexed,
    get_daemon,
    mcp,
    open_file,
    pending_diagnostics_events,
    register_diagnostics_event,
    resolve_project_file,
    wait_for_diagnostics,
)
from ..utils import (
    apply_pagination,
    diagnostic_sort_key,
    lsp_result_to_public,
    public_position_to_lsp,
)


@mcp.tool()
async def diagnostics(
    file_path: str | None = None,
    scope: str = "file",
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
    ctx: Context | None = None,
) -> DiagnosticsResult | ToolError:
    """Get type errors and warnings.

    Args:
        file_path: Path to file (required if scope='file')
        scope: 'file' (single file) or 'workspace' (all cached diagnostics)
               Note: 'workspace' only shows diagnostics for files previously queried
        limit: Maximum results to return
        offset: Number of results to skip

    Returns: DiagnosticsResult, or ToolError when file_path is missing.
    """
    all_diagnostics = []

    if scope == "file":
        if not file_path:
            return ToolError(error="file_path required when scope='file'")
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
        return DiagnosticsResult(
            items=[],
            totalItems=0,
            offset=offset,
            limit=limit,
            hasMore=False,
        )

    sorted_items = sorted(all_diagnostics, key=diagnostic_sort_key)
    paginated, metadata = apply_pagination(sorted_items, offset, limit)
    return DiagnosticsResult.model_validate(
        {"items": lsp_result_to_public(paginated), **metadata}
    )


@mcp.tool()
async def preview_rename(
    file_path: str,
    line: int,
    character: int,
    new_name: str,
    ctx: Context | None = None,
) -> RenamePreviewResult | RenamePreviewError:
    """Preview a symbol rename across the project without writing files.

    Returns a normalized preview with:
    - edits: Flat list of file edits to apply
    - edits[].uri: File URI to edit
    - edits[].range: One-based replacement range with start/end line/character
    - edits[].newText: Replacement text for that range
    - totalEdits: Total number of replacement edits

    The caller must apply the edits separately; this tool never modifies files.

    Args:
        file_path: Path to the file containing the symbol
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.
        new_name: New name for the symbol

    Returns: RenamePreviewResult, or RenamePreviewError.
    """
    project_file = resolve_project_file(file_path)
    client = await ensure_vtsls_indexed(file_path)
    file_uri = project_file.uri
    position = public_position_to_lsp(line, character)
    await open_file(client, project_file.path, file_uri)

    try:
        await ensure_project_loaded(client, project_file.path)

        # Optional: Validate rename is possible
        try:
            prepare_result = await client.request(
                "textDocument/prepareRename",
                {
                    "textDocument": {"uri": file_uri},
                    "position": position,
                },
            )
            if not prepare_result:
                return RenamePreviewError(error="Symbol cannot be renamed")
        except Exception:
            # prepareRename is optional, continue with rename
            pass

        # Perform rename
        result = await client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": file_uri},
                "position": position,
                "newName": new_name,
            },
        )

        if not result:
            return RenamePreviewError(error="Rename failed")

        if not isinstance(result, dict):
            return RenamePreviewError(error="Rename failed")

        try:
            return _normalize_rename_preview(result)
        except (TypeError, ValidationError):
            return RenamePreviewError(error="Rename returned unsupported edit shape")
    finally:
        await close_file(client, file_uri)


def _normalize_rename_preview(result: dict[str, Any]) -> RenamePreviewResult:
    edits: list[RenamePreviewEdit] = []
    found_edit_container = False

    changes = result.get("changes")
    if isinstance(changes, dict):
        found_edit_container = True
        for uri, uri_edits in changes.items():
            if isinstance(uri, str) and isinstance(uri_edits, list):
                edits.extend(_rename_edits_for_uri(uri, uri_edits))

    document_changes = result.get("documentChanges")
    if isinstance(document_changes, list):
        found_edit_container = True
        for document_change in document_changes:
            if not isinstance(document_change, dict):
                continue
            text_document = document_change.get("textDocument")
            uri = (
                text_document.get("uri")
                if isinstance(text_document, dict)
                else None
            )
            uri_edits = document_change.get("edits")
            if isinstance(uri, str) and isinstance(uri_edits, list):
                edits.extend(_rename_edits_for_uri(uri, uri_edits))

    if not found_edit_container:
        raise TypeError("Rename result did not contain edits")

    edits.sort(
        key=lambda edit: (
            edit.uri,
            edit.range.start.line,
            edit.range.start.character,
            edit.range.end.line if edit.range.end else edit.range.start.line,
            edit.range.end.character
            if edit.range.end
            else edit.range.start.character,
            edit.newText,
        )
    )
    return RenamePreviewResult(edits=edits, totalEdits=len(edits))


def _rename_edits_for_uri(
    uri: str, raw_edits: list[Any]
) -> list[RenamePreviewEdit]:
    edits: list[RenamePreviewEdit] = []
    for raw_edit in raw_edits:
        if not isinstance(raw_edit, dict):
            continue
        raw_range = raw_edit.get("range")
        new_text = raw_edit.get("newText")
        if not isinstance(raw_range, dict) or not isinstance(new_text, str):
            continue
        edits.append(
            RenamePreviewEdit.model_validate(
                {
                    "uri": uri,
                    "range": lsp_result_to_public(raw_range),
                    "newText": new_text,
                }
            )
        )
    return edits


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
    server_state.clear_project_load_cache()

    # Restart the language server and daemon.
    await client.restart()
    daemon = get_daemon()
    await daemon.restart()

    return "TypeScript language server and formatter/linter daemon restarted successfully"
