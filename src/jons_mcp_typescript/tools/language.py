"""Navigation and information tools for TypeScript development.

Navigation tools: definition, type_definition, implementation, references
Information tools: symbol_info, type_info_of_reference
"""

from typing import Any

from fastmcp import Context
from pydantic import ValidationError

from ..constants import DEFAULT_LIMIT, DEFAULT_OFFSET
from ..schemas import (
    DocumentSymbolsResult,
    NavigationResult,
    PublicLocation,
    PublicRange,
    ReferencesResult,
    SymbolInfoResult,
    TypeInfoResult,
)
from ..server import (
    close_file,
    ensure_project_loaded,
    ensure_vtsls_indexed,
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


def _normalize_navigation_result(result: Any) -> NavigationResult:
    """Normalize LSP Location/LocationLink results for public tool responses."""
    if not result:
        return NavigationResult(items=[], totalItems=0)

    raw_items = result if isinstance(result, list) else [result]
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for raw_item in raw_items:
        item = _normalize_navigation_item(raw_item)
        if item is None:
            continue

        key = (
            item["uri"],
            repr(item.get("range")),
            repr(item.get("fullRange")),
            repr(item.get("originRange")),
        )
        if key in seen:
            continue
        seen.add(key)
        items.append(item)

    return NavigationResult.model_validate({"items": items, "totalItems": len(items)})


def _normalize_navigation_item(raw_item: Any) -> dict[str, Any] | None:
    """Normalize one LSP Location or LocationLink item."""
    if not isinstance(raw_item, dict):
        return None

    target_uri = raw_item.get("targetUri")
    if isinstance(target_uri, str):
        target_range = raw_item.get("targetRange")
        selection_range = raw_item.get("targetSelectionRange") or target_range
        item: dict[str, Any] = {"uri": target_uri}
        if selection_range is not None:
            item["range"] = lsp_result_to_public(selection_range)
        if target_range is not None and target_range != selection_range:
            item["fullRange"] = lsp_result_to_public(target_range)
        origin_range = raw_item.get("originSelectionRange")
        if origin_range is not None:
            item["originRange"] = lsp_result_to_public(origin_range)
        return item

    uri = raw_item.get("uri")
    if isinstance(uri, str):
        item = {"uri": uri}
        range_obj = raw_item.get("range")
        if range_obj is not None:
            item["range"] = lsp_result_to_public(range_obj)
        return item

    return None


def _type_source_location(
    type_location: Any,
) -> PublicLocation | None:
    """Return a normalized public source location."""
    if not isinstance(type_location, dict):
        return None

    target_uri = type_location.get("targetUri") or type_location.get("uri")
    if not isinstance(target_uri, str):
        return None

    target_range = type_location.get("targetRange") or type_location.get("range")
    source_data: dict[str, Any] = {"uri": target_uri}
    if isinstance(target_range, dict):
        try:
            source_data["range"] = PublicRange.model_validate(
                lsp_result_to_public(target_range)
            )
        except ValidationError:
            pass

    return PublicLocation.model_validate(source_data)


@mcp.tool()
async def definition(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> NavigationResult:
    """Jump to where a symbol is defined.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.

    Returns: NavigationResult with normalized target locations and one-based ranges.
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

        return _normalize_navigation_result(result)
    finally:
        await close_file(client, file_uri)


@mcp.tool()
async def type_definition(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> NavigationResult:
    """Jump to the type definition of a symbol.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.

    Returns: NavigationResult with normalized target locations and one-based ranges.
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

        return _normalize_navigation_result(result)
    finally:
        await close_file(client, file_uri)


@mcp.tool()
async def implementation(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> NavigationResult:
    """Find implementations of interfaces or abstract classes.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.

    Returns: NavigationResult with normalized target locations and one-based ranges.
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

        return _normalize_navigation_result(result)
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
) -> ReferencesResult:
    """Find all usages of a symbol.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.
        include_declaration: Whether to include the symbol declaration in results
        limit: Maximum results to return
        offset: Number of results to skip

    Returns: ReferencesResult. Each item includes a file URI and one-based range.
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
            return ReferencesResult(
                items=[],
                totalItems=0,
                offset=offset,
                limit=limit,
                hasMore=False,
            )

        # Sort by location
        sorted_items = sorted(result, key=location_sort_key)

        # Apply pagination
        paginated, metadata = apply_pagination(sorted_items, offset, limit)
        return ReferencesResult.model_validate(
            {"items": lsp_result_to_public(paginated), **metadata}
        )
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
) -> DocumentSymbolsResult:
    """List all symbols defined in a file.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        limit: Maximum results to return
        offset: Number of results to skip

    Returns: DocumentSymbolsResult with symbol names, kinds, and one-based ranges.
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
            return DocumentSymbolsResult(
                items=[],
                totalItems=0,
                offset=offset,
                limit=limit,
                hasMore=False,
            )

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
        return DocumentSymbolsResult.model_validate(
            {"items": lsp_result_to_public(paginated), **metadata}
        )
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
) -> SymbolInfoResult:
    """Get type signature and docs for any symbol.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.

    Returns:
        SymbolInfoResult with content (type signature and docs) and one-based source range.
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
            return SymbolInfoResult(content=None, range=None)

        content = _hover_contents_to_text(result.get("contents", {}))

        return SymbolInfoResult.model_validate(
            {"content": content, "range": lsp_result_to_public(result.get("range"))}
        )
    finally:
        await close_file(client, file_uri)


@mcp.tool()
async def type_info_of_reference(
    file_path: str,
    line: int,
    character: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
    include_documentation: bool = False,
    ctx: Context | None = None,
) -> TypeInfoResult:
    """Get TypeScript display info and accessible members for a value reference.

    For best results, call this on a reference/use of a value at the probe
    location, such as `user` in `user.name` or a standalone `user;`, not on the
    declaration name in `const user = ...`. Returned fields and methods are the
    members available on that reference at the probe location.

    Args:
        file_path: Path to the TypeScript/JavaScript file
        line: One-based line number, matching editor/Read output.
        character: One-based column on that line.
        limit: Maximum methods to return (fields always returned in full)
        offset: Offset for method pagination
        include_documentation: Include JSDoc for each member

    Returns:
        TypeInfoResult with:
        - displayString: Exact TypeScript quickinfo display string
        - kind: TypeScript quickinfo kind, such as const, function, or class
        - fields: List of field definitions with name and type
        - methods: Paginated list of methods with signatures
        - sourceLocation: File URI and normalized one-based range for the type
          definition (if available)
    """
    project_file = resolve_project_file(file_path)
    client = await ensure_vtsls_indexed(file_path)
    file_uri = project_file.uri
    position = public_position_to_lsp(line, character)
    await open_file(client, project_file.path, file_uri)

    try:
        await ensure_project_loaded(client, project_file.path)

        quickinfo = await _get_quickinfo(
            client,
            project_file.path,
            line,
            character,
        )

        type_def_result = await client.request(
            "textDocument/typeDefinition",
            {
                "textDocument": {"uri": file_uri},
                "position": position,
            },
        )

        source_location: PublicLocation | None = None
        fields: list[dict[str, Any]] = []
        methods: list[dict[str, Any]] = []

        if type_def_result:
            type_loc = (
                type_def_result[0]
                if isinstance(type_def_result, list)
                else type_def_result
            )
            source_location = _type_source_location(type_loc)

        # Get completions after temporarily inserting a dot after the identifier
        # at the requested position.
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

        return TypeInfoResult.model_validate(
            {
                **quickinfo,
                "fields": fields,
                "methods": {"items": paginated_methods, **method_metadata},
                "sourceLocation": source_location,
            }
        )
    finally:
        await close_file(client, file_uri)


async def _get_quickinfo(
    client: Any,
    file_path: Any,
    line: int,
    character: int,
) -> dict[str, str | None]:
    """Return exact tsserver quickinfo display fields for a public position."""
    response = await client.request(
        "workspace/executeCommand",
        {
            "command": "typescript.tsserverRequest",
            "arguments": [
                "quickinfo",
                {
                    "file": str(file_path),
                    "line": max(line, 1),
                    "offset": max(character, 1),
                },
            ],
        },
    )
    body = response.get("body") if isinstance(response, dict) else None
    if not isinstance(body, dict):
        return {"displayString": "", "kind": None}

    display_string = body.get("displayString")
    kind = body.get("kind")
    return {
        "displayString": display_string if isinstance(display_string, str) else "",
        "kind": kind if isinstance(kind, str) else None,
    }


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
