"""Utility functions for the TypeScript MCP server."""

from pathlib import Path
from typing import Any, TypeVar

from .constants import DEFAULT_LIMIT, DEFAULT_OFFSET

T = TypeVar("T")


def ensure_file_uri(file_path: str) -> str:
    """Convert file path to proper file URI.

    Args:
        file_path: Path to the file (absolute, relative, or already a URI)

    Returns:
        Properly formatted file:// URI
    """
    if file_path.startswith("file://"):
        return file_path

    path = Path(file_path)
    if not path.is_absolute():
        path = Path.cwd() / path

    return f"file://{path.absolute()}"


def apply_pagination(
    items: list[T],
    offset: int = DEFAULT_OFFSET,
    limit: int = DEFAULT_LIMIT,
    add_offset_field: bool = True,
) -> tuple[list[T | dict[str, Any]], dict[str, Any]]:
    """Apply pagination to a list of items.

    Args:
        items: The full list of items to paginate
        offset: Number of items to skip
        limit: Maximum number of items to return
        add_offset_field: Whether to add an 'offset' field to each item

    Returns:
        Tuple of (paginated_items, metadata_dict)
    """
    total_items = len(items)
    start_idx = min(offset, total_items)
    end_idx = min(start_idx + limit, total_items)
    paginated = items[start_idx:end_idx]

    # Add offset field to each item if requested
    result_items: list[T | dict[str, Any]]
    if add_offset_field:
        processed_items: list[dict[str, Any]] = []
        for i, item in enumerate(paginated):
            if isinstance(item, dict):
                processed_item = item.copy()
            else:
                processed_item = {"item": item}
            processed_item["offset"] = start_idx + i
            processed_items.append(processed_item)
        result_items = processed_items  # type: ignore[assignment]
    else:
        result_items = list(paginated)

    has_more = end_idx < total_items

    metadata = {
        "totalItems": total_items,
        "offset": offset,
        "limit": limit,
        "hasMore": has_more,
        "nextOffset": end_idx if has_more else None,
    }

    return result_items, metadata


# Sort key functions for consistent pagination ordering


def location_sort_key(item: dict[str, Any]) -> tuple[str, int, int]:
    """Sort key for items with location info (references, etc.).

    Sorts by URI, then by line, then by character.
    """
    uri = item.get("uri", "")
    start = item.get("range", {}).get("start", {})
    line = start.get("line", 0)
    char = start.get("character", 0)
    return (uri, line, char)


def symbol_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    """Sort key for document symbols.

    Sorts by line number, then character, then by name.
    """
    # For DocumentSymbol format
    if "range" in item:
        start = item.get("range", {}).get("start", {})
        line = start.get("line", 0)
        char = start.get("character", 0)
    # For SymbolInformation format
    else:
        location = item.get("location", {})
        start = location.get("range", {}).get("start", {})
        line = start.get("line", 0)
        char = start.get("character", 0)

    name = item.get("fullName", item.get("name", ""))
    return (line, char, name)


def workspace_symbol_sort_key(item: dict[str, Any]) -> tuple[str, str, int]:
    """Sort key for workspace symbols.

    Sorts by name, then by URI, then by line.
    """
    name = item.get("name", "")
    location = item.get("location", {})
    uri = location.get("uri", "")
    line = location.get("range", {}).get("start", {}).get("line", 0)
    return (name, uri, line)


def diagnostic_sort_key(item: dict[str, Any]) -> tuple[int, str, int, int]:
    """Sort key for diagnostics.

    Sorts by severity (errors first), then by URI, then by position.
    """
    severity = item.get("severity", 999)  # Lower is more severe
    uri = item.get("uri", "")
    start = item.get("range", {}).get("start", {})
    line = start.get("line", 0)
    char = start.get("character", 0)
    return (severity, uri, line, char)


def find_package_root(file_path: str) -> str:
    """Find the nearest package.json by walking up the directory tree.

    Args:
        file_path: Path to start searching from

    Returns:
        Path to the directory containing package.json, or parent directory if not found
    """
    current = Path(file_path).parent if Path(file_path).is_file() else Path(file_path)
    while current != current.parent:
        if (current / "package.json").exists():
            return str(current)
        current = current.parent
    return str(Path(file_path).parent)
