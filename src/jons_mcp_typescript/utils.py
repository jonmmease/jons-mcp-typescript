"""Utility functions for the TypeScript MCP server."""

from pathlib import Path
from typing import Any, TypeVar, cast
from urllib.parse import urlparse
from urllib.request import url2pathname

from .constants import DEFAULT_LIMIT, DEFAULT_OFFSET
from .exceptions import PathOutsideProjectError

T = TypeVar("T")


def path_from_file_uri(uri: str) -> Path:
    """Convert a file:// URI to a local path.

    Args:
        uri: A file URI.

    Returns:
        Local filesystem path.

    Raises:
        ValueError: If the URI is not a local file URI.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Unsupported URI scheme for file path: {parsed.scheme}")
    if parsed.netloc not in ("", "localhost"):
        raise ValueError(f"Unsupported non-local file URI: {uri}")
    return Path(url2pathname(parsed.path))


def resolve_project_path(
    file_path: str,
    project_root: Path,
    *,
    must_exist: bool = True,
) -> Path:
    """Resolve and validate a user-supplied path inside the project root.

    Relative paths are resolved against project_root. Existing symlinks are
    resolved before containment is checked, so symlink escapes are rejected.

    Args:
        file_path: Path string or file:// URI.
        project_root: Configured TypeScript project root.
        must_exist: Whether the final path must already exist.

    Returns:
        Canonical absolute path.

    Raises:
        FileNotFoundError: If must_exist is True and the path does not exist.
        PathOutsideProjectError: If the path escapes project_root.
        ValueError: If a URI cannot be converted to a local file path.
    """
    root = project_root.expanduser().resolve(strict=True)
    if file_path.startswith("file://"):
        path = path_from_file_uri(file_path)
    else:
        path = Path(file_path)
    if not path.is_absolute():
        path = root / path

    if must_exist:
        resolved = path.resolve(strict=True)
    else:
        resolved = path.resolve(strict=False)

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PathOutsideProjectError(
            f"Path is outside project root: {resolved} (root: {root})"
        ) from exc

    return resolved


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


def public_position_to_lsp(line: int, character: int) -> dict[str, int]:
    """Convert 1-indexed public tool positions to 0-indexed LSP positions."""
    return {
        "line": max(line - 1, 0),
        "character": max(character - 1, 0),
    }


def lsp_result_to_public(value: T) -> T:
    """Convert LSP position payloads to 1-indexed public tool results."""
    if isinstance(value, list):
        return cast(T, [lsp_result_to_public(item) for item in value])
    if isinstance(value, dict):
        if _is_lsp_position(value):
            converted = value.copy()
            converted["line"] = value["line"] + 1
            converted["character"] = value["character"] + 1
            return cast(T, converted)
        return cast(
            T, {key: lsp_result_to_public(item) for key, item in value.items()}
        )
    return value


def _is_lsp_position(value: dict[str, Any]) -> bool:
    return isinstance(value.get("line"), int) and isinstance(value.get("character"), int)


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


def count_eslint_messages(messages: list[dict[str, Any]]) -> tuple[int, int]:
    """Return (errors, warnings) for normalized ESLint messages."""
    error_count = 0
    warning_count = 0
    for message in messages:
        severity = message.get("severity")
        if severity in ("error", 2):
            error_count += 1
        elif severity in ("warning", 1):
            warning_count += 1
    return error_count, warning_count
