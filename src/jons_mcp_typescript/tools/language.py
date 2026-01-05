"""Navigation and information tools for TypeScript development.

Navigation tools: definition, type_definition, implementation, references
Information tools: symbol_info, type_info
"""

from typing import Any

from fastmcp import Context

from ..constants import DEFAULT_LIMIT, DEFAULT_OFFSET
from ..server import close_file, ensure_vtsls_indexed, mcp, open_file
from ..utils import apply_pagination, ensure_file_uri, location_sort_key


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
        line: Line number (0-indexed)
        character: Column number (0-indexed)

    Returns: Location or LocationLink, or None if not found
    """
    client = await ensure_vtsls_indexed(file_path)
    file_uri = ensure_file_uri(file_path)
    await open_file(client, file_path, file_uri)

    try:
        result = await client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
            },
        )

        # Handle single location or LocationLink array
        if isinstance(result, dict):
            return result
        elif isinstance(result, list):
            return result
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
        line: Line number (0-indexed)
        character: Column number (0-indexed)

    Returns: Location or LocationLink, or None if not found
    """
    client = await ensure_vtsls_indexed(file_path)
    file_uri = ensure_file_uri(file_path)
    await open_file(client, file_path, file_uri)

    try:
        result = await client.request(
            "textDocument/typeDefinition",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
            },
        )

        # Handle single location or LocationLink array
        if isinstance(result, dict):
            return result
        elif isinstance(result, list):
            return result
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
        line: Line number (0-indexed)
        character: Column number (0-indexed)

    Returns: Location or LocationLink array, or None if not found
    """
    client = await ensure_vtsls_indexed(file_path)
    file_uri = ensure_file_uri(file_path)
    await open_file(client, file_path, file_uri)

    try:
        result = await client.request(
            "textDocument/implementation",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
            },
        )

        # Handle single location or LocationLink array
        if isinstance(result, dict):
            return result
        elif isinstance(result, list):
            return result
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
        line: Line number (0-indexed)
        character: Column number (0-indexed)
        include_declaration: Whether to include the symbol declaration in results
        limit: Maximum results to return
        offset: Number of results to skip

    Returns: {items: [...], totalItems, offset, limit, hasMore, nextOffset}
    """
    client = await ensure_vtsls_indexed(file_path)
    file_uri = ensure_file_uri(file_path)
    await open_file(client, file_path, file_uri)

    try:
        result = await client.request(
            "textDocument/references",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
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
        return {"items": paginated, **metadata}
    finally:
        await close_file(client, file_uri)


# =============================================================================
# Symbol Tools
# =============================================================================


@mcp.tool()
async def workspace_symbols(
    query: str,
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
    ctx: Context | None = None,
) -> dict:
    """Search for types/functions across the project by name.

    Args:
        query: Search query string
        limit: Maximum results to return
        offset: Number of results to skip

    Returns: {items: [...], totalItems, offset, limit, hasMore, nextOffset}
    """
    client = await ensure_vtsls_indexed()

    result = await client.request(
        "workspace/symbol",
        {"query": query},
    )

    if not result:
        return {
            "items": [],
            "totalItems": 0,
            "offset": offset,
            "limit": limit,
            "hasMore": False,
        }

    # Sort by name, then URI, then line
    def workspace_symbol_sort_key(symbol: dict) -> tuple:
        name = symbol.get("name", "")
        location = symbol.get("location", {})
        uri = location.get("uri", "")
        start = location.get("range", {}).get("start", {})
        line = start.get("line", 0)
        return (name.lower(), uri, line)

    sorted_items = sorted(result, key=workspace_symbol_sort_key)

    # Apply pagination
    paginated, metadata = apply_pagination(sorted_items, offset, limit)
    return {"items": paginated, **metadata}


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

    Returns: {items: [...], totalItems, offset, limit, hasMore, nextOffset}
    """
    client = await ensure_vtsls_indexed(file_path)
    file_uri = ensure_file_uri(file_path)
    await open_file(client, file_path, file_uri)

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
        return {"items": paginated, **metadata}
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
    """Get type signature and docs for any symbol (via hover).

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: Line number (0-indexed)
        character: Column number (0-indexed)

    Returns:
        Dictionary with 'content' (type signature and docs) and 'range' (source range)
    """
    client = await ensure_vtsls_indexed(file_path)
    file_uri = ensure_file_uri(file_path)
    await open_file(client, file_path, file_uri)

    try:
        result = await client.request(
            "textDocument/hover",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
            },
        )

        if not result:
            return {"content": None, "range": None}

        # Extract content from hover response
        contents = result.get("contents", {})
        if isinstance(contents, dict):
            # MarkupContent
            content = contents.get("value", "")
        elif isinstance(contents, list):
            # Array of MarkedString
            content = "\n".join(
                c.get("value", str(c)) if isinstance(c, dict) else str(c) for c in contents
            )
        else:
            content = str(contents)

        return {"content": content, "range": result.get("range")}
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
        line: Line number (0-indexed)
        character: Column number (0-indexed)
        limit: Maximum methods to return (fields always returned in full)
        offset: Offset for method pagination
        include_documentation: Include JSDoc for each member

    Returns:
        Dictionary with:
        - typeName: The inferred type name
        - fields: List of field definitions with name and type
        - methods: Paginated list of methods with signatures
        - sourceLocation: Location of the type definition (if available)
    """
    client = await ensure_vtsls_indexed(file_path)
    file_uri = ensure_file_uri(file_path)
    await open_file(client, file_path, file_uri)

    # Track files we open so we can close them all
    opened_uris = [file_uri]

    try:
        # Step 1: Get type info from hover
        hover_result = await client.request(
            "textDocument/hover",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
            },
        )

        type_name = "unknown"
        if hover_result and hover_result.get("contents"):
            contents = hover_result["contents"]
            if isinstance(contents, dict):
                type_name = contents.get("value", "unknown")
            elif isinstance(contents, list) and contents:
                first = contents[0]
                type_name = (
                    first.get("value", str(first)) if isinstance(first, dict) else str(first)
                )

        # Step 2: Get type definition location
        type_def_result = await client.request(
            "textDocument/typeDefinition",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
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
                source_location = {"uri": target_uri, "range": target_range}

                # Step 3: If local file, get document symbols for type members
                if target_uri.startswith("file://"):
                    type_file_path = target_uri.replace("file://", "")
                    await open_file(client, type_file_path, target_uri)
                    opened_uris.append(target_uri)

                    symbols_result = await client.request(
                        "textDocument/documentSymbol",
                        {"textDocument": {"uri": target_uri}},
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

        # Step 4: Get completions to discover methods (dot completion trick)
        # Request completions as if a dot was typed after the symbol
        completions = await client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character + 1},
                "context": {"triggerKind": 2, "triggerCharacter": "."},
            },
        )

        if completions:
            items = (
                completions.get("items", completions)
                if isinstance(completions, dict)
                else completions
            )
            for item in items:
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
