"""Navigation and information tools for TypeScript development.

Navigation tools: definition, type_definition, implementation, references
Information tools: symbol_info, type_info
"""

import re
from typing import Any

from fastmcp import Context

from ..constants import DEFAULT_LIMIT, DEFAULT_OFFSET
from ..server import (
    close_file,
    ensure_project_loaded,
    ensure_vtsls_indexed,
    is_project_file_uri,
    mcp,
    open_file,
    resolve_project_file,
    sync_open_file_content,
)
from ..utils import (
    apply_pagination,
    location_sort_key,
    lsp_result_to_public,
    public_position_to_lsp,
)


@mcp.tool()
async def definition(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict | list | None:
    """Jump to where a symbol is defined.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.

    Returns: File location dict, list of file location dicts, or None if not found
    """
    project_file = resolve_project_file(file_path)
    client = await ensure_vtsls_indexed(file_path)
    file_uri = project_file.uri
    position = public_position_to_lsp(line, character)
    await open_file(client, project_file.path, file_uri)

    try:
        await ensure_project_loaded(client, project_file.path)
        result = await client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": file_uri},
                "position": position,
            },
        )

        # Handle single location or list of linked locations
        if isinstance(result, dict):
            return lsp_result_to_public(result)
        elif isinstance(result, list):
            return lsp_result_to_public(result)
        return None
    finally:
        await close_file(client, file_uri)


@mcp.tool()
async def type_definition(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict | list | None:
    """Jump to the type definition of a symbol.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.

    Returns: File location dict, list of file location dicts, or None if not found
    """
    project_file = resolve_project_file(file_path)
    client = await ensure_vtsls_indexed(file_path)
    file_uri = project_file.uri
    position = public_position_to_lsp(line, character)
    await open_file(client, project_file.path, file_uri)

    try:
        await ensure_project_loaded(client, project_file.path)
        result = await client.request(
            "textDocument/typeDefinition",
            {
                "textDocument": {"uri": file_uri},
                "position": position,
            },
        )

        # Handle single location or list of linked locations
        if isinstance(result, dict):
            return lsp_result_to_public(result)
        elif isinstance(result, list):
            return lsp_result_to_public(result)
        return None
    finally:
        await close_file(client, file_uri)


@mcp.tool()
async def implementation(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict | list | None:
    """Find implementations of interfaces or abstract classes.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.

    Returns: File location dict, list of file location dicts, or None if not found
    """
    project_file = resolve_project_file(file_path)
    client = await ensure_vtsls_indexed(file_path)
    file_uri = project_file.uri
    position = public_position_to_lsp(line, character)
    await open_file(client, project_file.path, file_uri)

    try:
        await ensure_project_loaded(client, project_file.path)
        result = await client.request(
            "textDocument/implementation",
            {
                "textDocument": {"uri": file_uri},
                "position": position,
            },
        )

        # Handle single location or list of linked locations
        if isinstance(result, dict):
            return lsp_result_to_public(result)
        elif isinstance(result, list):
            return lsp_result_to_public(result)
        return None
    finally:
        await close_file(client, file_uri)


@mcp.tool()
async def references(
    file_path: str,
    line: int,
    character: int,
    include_declaration: bool = True,
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
    ctx: Context | None = None,
) -> dict:
    """Find all usages of a symbol.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.
        include_declaration: Whether to include the symbol declaration in results
        limit: Maximum results to return
        offset: Number of results to skip

    Returns: Paginated usages. Each item includes a file URI and one-based range.
    """
    project_file = resolve_project_file(file_path)
    client = await ensure_vtsls_indexed(file_path)
    file_uri = project_file.uri
    position = public_position_to_lsp(line, character)
    await open_file(client, project_file.path, file_uri)

    try:
        await ensure_project_loaded(client, project_file.path)
        result = await client.request(
            "textDocument/references",
            {
                "textDocument": {"uri": file_uri},
                "position": position,
                "context": {"includeDeclaration": include_declaration},
            },
        )

        if not result:
            return {
                "items": [],
                "totalItems": 0,
                "offset": offset,
                "limit": limit,
                "hasMore": False,
            }

        # Sort by location
        sorted_items = sorted(result, key=location_sort_key)

        # Apply pagination
        paginated, metadata = apply_pagination(sorted_items, offset, limit)
        return {"items": lsp_result_to_public(paginated), **metadata}
    finally:
        await close_file(client, file_uri)


# =============================================================================
# Symbol Tools
# =============================================================================


@mcp.tool()
async def document_symbols(
    file_path: str,
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
    ctx: Context | None = None,
) -> dict:
    """List all symbols defined in a file.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        limit: Maximum results to return
        offset: Number of results to skip

    Returns: Paginated symbols. Each item includes its name, kind, and one-based range.
    """
    project_file = resolve_project_file(file_path)
    client = await ensure_vtsls_indexed(file_path)
    file_uri = project_file.uri
    await open_file(client, project_file.path, file_uri)

    try:
        result = await client.request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": file_uri}},
        )

        if not result:
            return {
                "items": [],
                "totalItems": 0,
                "offset": offset,
                "limit": limit,
                "hasMore": False,
            }

        # Flatten nested symbols if DocumentSymbol format (has children)
        def flatten_symbols(symbols: list, parent_name: str = "") -> list:
            flattened = []
            for symbol in symbols:
                flat_symbol = {
                    "name": symbol.get("name", ""),
                    "kind": symbol.get("kind", 0),
                    "range": symbol.get("range") or symbol.get("location", {}).get("range"),
                    "selectionRange": symbol.get("selectionRange"),
                }
                if parent_name:
                    flat_symbol["containerName"] = parent_name
                flattened.append(flat_symbol)

                # Recurse into children
                children = symbol.get("children", [])
                if children:
                    flattened.extend(flatten_symbols(children, symbol.get("name", "")))
            return flattened

        # Check if result contains DocumentSymbol (has children field)
        if result and isinstance(result, list) and "children" in result[0]:
            flat_result = flatten_symbols(result)
        else:
            flat_result = result

        # Sort by line, then character, then name
        def symbol_sort_key(symbol: dict) -> tuple:
            range_obj = symbol.get("range") or symbol.get("location", {}).get("range", {})
            start = range_obj.get("start", {})
            line = start.get("line", 0)
            char = start.get("character", 0)
            name = symbol.get("name", "")
            return (line, char, name.lower())

        sorted_items = sorted(flat_result, key=symbol_sort_key)

        # Apply pagination
        paginated, metadata = apply_pagination(sorted_items, offset, limit)
        return {"items": lsp_result_to_public(paginated), **metadata}
    finally:
        await close_file(client, file_uri)


# =============================================================================
# Information Tools
# =============================================================================


@mcp.tool()
async def symbol_info(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get type signature and docs for any symbol.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.

    Returns:
        Dictionary with 'content' (type signature and docs) and 'range' (one-based source range)
    """
    project_file = resolve_project_file(file_path)
    client = await ensure_vtsls_indexed(file_path)
    file_uri = project_file.uri
    position = public_position_to_lsp(line, character)
    await open_file(client, project_file.path, file_uri)

    try:
        await ensure_project_loaded(client, project_file.path)
        result = await client.request(
            "textDocument/hover",
            {
                "textDocument": {"uri": file_uri},
                "position": position,
            },
        )

        if not result:
            return {"content": None, "range": None}

        content = _hover_contents_to_text(result.get("contents", {}))

        return {"content": content, "range": lsp_result_to_public(result.get("range"))}
    finally:
        await close_file(client, file_uri)


@mcp.tool()
async def type_info(
    file_path: str,
    line: int,
    character: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
    include_documentation: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get type name, fields, and methods for a value.

    This is the primary tool for understanding what operations are available on a value.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.
        limit: Maximum methods to return (fields always returned in full)
        offset: Offset for method pagination
        include_documentation: Include JSDoc for each member

    Returns:
        Dictionary with:
        - typeName: The inferred type name
        - fields: List of field definitions with name and type
        - methods: Paginated list of methods with signatures
        - sourceLocation: File URI and one-based range for the type definition (if available)
    """
    project_file = resolve_project_file(file_path)
    client = await ensure_vtsls_indexed(file_path)
    file_uri = project_file.uri
    position = public_position_to_lsp(line, character)
    await open_file(client, project_file.path, file_uri)

    # Track files we open so we can close them all
    opened_uris = [file_uri]

    try:
        await ensure_project_loaded(client, project_file.path)

        # Step 1: Get type info from hover
        hover_result = await client.request(
            "textDocument/hover",
            {
                "textDocument": {"uri": file_uri},
                "position": position,
            },
        )

        type_name = "unknown"
        if hover_result and hover_result.get("contents"):
            type_name = _infer_type_name_from_hover(hover_result["contents"])

        # Step 2: Get type definition location
        type_def_result = await client.request(
            "textDocument/typeDefinition",
            {
                "textDocument": {"uri": file_uri},
                "position": position,
            },
        )

        source_location: dict[str, Any] | None = None
        fields: list[dict[str, Any]] = []
        methods: list[dict[str, Any]] = []

        if type_def_result:
            # Handle array or single location
            type_loc = (
                type_def_result[0]
                if isinstance(type_def_result, list)
                else type_def_result
            )
            target_uri = type_loc.get("targetUri") or type_loc.get("uri")
            target_range = type_loc.get("targetRange") or type_loc.get("range")

            if target_uri:
                source_location = {
                    "uri": target_uri,
                    "range": lsp_result_to_public(target_range),
                }

                # Step 3: If local file, get document symbols for type members
                if target_uri.startswith("file://") and is_project_file_uri(target_uri):
                    type_file = resolve_project_file(target_uri)
                    await open_file(client, type_file.path, type_file.uri)
                    opened_uris.append(type_file.uri)

                    symbols_result = await client.request(
                        "textDocument/documentSymbol",
                        {"textDocument": {"uri": type_file.uri}},
                    )

                    if symbols_result:
                        # Find the type's symbols based on the target range
                        # and extract properties and methods
                        _extract_type_members(
                            symbols_result,
                            target_range,
                            fields,
                            methods,
                            include_documentation,
                        )

        # Step 4: Get completions after temporarily inserting a dot after the
        # identifier at the requested position.
        completion_items = await _get_member_completion_items(
            client,
            file_uri,
            project_file.path.read_text(encoding="utf-8"),
            position["line"],
            position["character"],
        )

        if completion_items:
            for item in completion_items:
                kind = item.get("kind", 0)
                name = item.get("label", "")
                detail = item.get("detail", "")
                documentation = ""

                if include_documentation:
                    doc = item.get("documentation")
                    if doc:
                        if isinstance(doc, dict):
                            documentation = doc.get("value", "")
                        else:
                            documentation = str(doc)

                # LSP CompletionItemKind: 2 = Method, 3 = Function, 5 = Field, 6 = Variable
                # 10 = Property
                if kind in (2, 3):  # Method or Function
                    method_entry: dict[str, Any] = {"name": name, "signature": detail}
                    if include_documentation and documentation:
                        method_entry["documentation"] = documentation
                    # Avoid duplicates
                    if not any(m["name"] == name for m in methods):
                        methods.append(method_entry)
                elif kind in (5, 6, 10):  # Field, Variable, or Property
                    field_entry: dict[str, Any] = {"name": name, "type": detail}
                    if include_documentation and documentation:
                        field_entry["documentation"] = documentation
                    # Avoid duplicates
                    if not any(f["name"] == name for f in fields):
                        fields.append(field_entry)

        # Sort fields by name for consistent output
        fields.sort(key=lambda f: f.get("name", ""))

        # Sort methods by name before pagination
        methods.sort(key=lambda m: m.get("name", ""))

        # Paginate methods (fields are always returned in full)
        paginated_methods, method_metadata = apply_pagination(
            methods, offset, limit, add_offset_field=False
        )

        return {
            "typeName": type_name,
            "fields": fields,
            "methods": {"items": paginated_methods, **method_metadata},
            "sourceLocation": source_location,
        }
    finally:
        # Close all files we opened
        for uri in opened_uris:
            await close_file(client, uri)


def _extract_type_members(
    symbols: list[dict[str, Any]],
    target_range: dict[str, Any] | None,
    fields: list[dict[str, Any]],
    methods: list[dict[str, Any]],
    include_documentation: bool,
) -> None:
    """Extract fields and methods from document symbols.

    Recursively walks the symbol tree to find members of the target type.

    Args:
        symbols: List of document symbols from LSP
        target_range: The range of the target type definition
        fields: List to populate with field definitions
        methods: List to populate with method definitions
        include_documentation: Whether to include documentation strings
    """
    for symbol in symbols:
        kind = symbol.get("kind", 0)
        name = symbol.get("name", "")
        detail = symbol.get("detail", "")
        sym_range = symbol.get("range", {})

        # Check if this symbol is within the target range (if specified)
        if target_range:
            target_start = target_range.get("start", {})
            target_end = target_range.get("end", {})
            sym_start = sym_range.get("start", {})

            # Only include symbols within the target range
            if sym_start.get("line", 0) < target_start.get("line", 0):
                continue
            if sym_start.get("line", 0) > target_end.get("line", float("inf")):
                continue

        # LSP SymbolKind: 6 = Method, 7 = Property, 8 = Field, 9 = Constructor
        # 12 = Function, 13 = Variable
        if kind in (6, 9, 12):  # Method, Constructor, Function
            method_entry: dict[str, Any] = {
                "name": name,
                "signature": detail or "unknown",
            }
            if not any(m["name"] == name for m in methods):
                methods.append(method_entry)
        elif kind in (7, 8, 13):  # Property, Field, Variable
            field_entry: dict[str, Any] = {
                "name": name,
                "type": detail or "unknown",
            }
            if not any(f["name"] == name for f in fields):
                fields.append(field_entry)

        # Recurse into children
        children = symbol.get("children", [])
        if children:
            _extract_type_members(
                children, None, fields, methods, include_documentation
            )


def _hover_contents_to_text(contents: Any) -> str:
    if isinstance(contents, dict):
        return str(contents.get("value", ""))
    if isinstance(contents, list):
        parts = []
        for item in contents:
            if isinstance(item, dict):
                value = item.get("value")
                if value:
                    parts.append(str(value))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts)
    if contents is None:
        return ""
    return str(contents)


def _infer_type_name_from_hover(contents: Any) -> str:
    text = _hover_contents_to_text(contents).strip()
    if not text:
        return "unknown"

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("```")
    ]
    if not lines:
        return "unknown"

    signature = lines[0]
    declaration_match = re.search(
        r"\b(?:interface|class|type|enum)\s+([A-Za-z_$][\w$]*)",
        signature,
    )
    if declaration_match:
        return declaration_match.group(1)

    colon_index = signature.rfind(":")
    if colon_index != -1:
        return signature[colon_index + 1 :].strip().rstrip(";")

    return signature or "unknown"


def _is_identifier_char(value: str) -> bool:
    return value.isalnum() or value in "_$"


def _member_completion_insertion(
    content: str, line: int, character: int
) -> tuple[int, int] | None:
    """Return (absolute insertion offset, completion character) for dot completion."""
    line_offsets = [0]
    for index, char in enumerate(content):
        if char == "\n":
            line_offsets.append(index + 1)

    if line < 0 or line >= len(line_offsets):
        return None

    line_start = line_offsets[line]
    newline_index = content.find("\n", line_start)
    line_end = len(content) if newline_index == -1 else newline_index
    line_text = content[line_start:line_end]
    char_index = min(max(character, 0), len(line_text))

    if char_index >= len(line_text) or not _is_identifier_char(line_text[char_index]):
        if char_index > 0 and _is_identifier_char(line_text[char_index - 1]):
            char_index -= 1
        else:
            return None

    identifier_end = char_index
    while (
        identifier_end < len(line_text)
        and _is_identifier_char(line_text[identifier_end])
    ):
        identifier_end += 1

    return line_start + identifier_end, identifier_end + 1


async def _get_member_completion_items(
    client: Any,
    file_uri: str,
    original_content: str,
    line: int,
    character: int,
) -> list[dict[str, Any]]:
    insertion = _member_completion_insertion(original_content, line, character)
    if insertion is None:
        return []

    insert_offset, completion_character = insertion
    temporary_content = (
        original_content[:insert_offset] + "." + original_content[insert_offset:]
    )

    try:
        await sync_open_file_content(client, file_uri, temporary_content)
        completions = await client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": completion_character},
                "context": {"triggerKind": 2, "triggerCharacter": "."},
            },
        )
    finally:
        await sync_open_file_content(client, file_uri, original_content)

    if not completions:
        return []
    if isinstance(completions, dict):
        items = completions.get("items", [])
    else:
        items = completions
    return [item for item in items if isinstance(item, dict)]
