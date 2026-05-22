"""Shared helpers for project-wide semantic aggregation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exceptions import PathOutsideProjectError
from .server import close_file, ensure_project_loaded, get_project_root, open_file
from .utils import resolve_project_path


@dataclass(frozen=True)
class ReferenceSeed:
    """An in-root reference location suitable for follow-up LSP requests."""

    uri: str
    path: Path
    position: dict[str, int]
    config_key: str


async def reference_seeds_by_project(
    client: Any,
    file_uri: str,
    position: dict[str, int],
) -> list[ReferenceSeed]:
    """Return one in-root semantic reference seed per TypeScript project config."""
    result = await client.request(
        "textDocument/references",
        {
            "textDocument": {"uri": file_uri},
            "position": position,
            "context": {"includeDeclaration": True},
        },
    )
    if not isinstance(result, list):
        return []

    root = get_project_root()
    seeds_by_uri: dict[str, ReferenceSeed] = {}
    seeds_by_config: dict[str, ReferenceSeed] = {}

    for reference in sorted(result, key=_reference_sort_key):
        uri = reference.get("uri") if isinstance(reference, dict) else None
        range_obj = reference.get("range") if isinstance(reference, dict) else None
        start = range_obj.get("start") if isinstance(range_obj, dict) else None
        if not isinstance(uri, str) or not isinstance(start, dict):
            continue
        line = start.get("line")
        character = start.get("character")
        if not isinstance(line, int) or not isinstance(character, int):
            continue
        if uri in seeds_by_uri:
            continue

        try:
            path = resolve_project_path(uri, root, must_exist=True)
        except (OSError, PathOutsideProjectError, ValueError):
            continue

        seed_uri = path.as_uri()
        if seed_uri == file_uri:
            config_key = await ensure_project_loaded(client, path)
        else:
            await open_file(client, path, seed_uri)
            try:
                config_key = await ensure_project_loaded(client, path)
            finally:
                await close_file(client, seed_uri)

        seed = ReferenceSeed(
            uri=seed_uri,
            path=path,
            position={"line": line, "character": character},
            config_key=config_key,
        )
        seeds_by_uri[uri] = seed
        seeds_by_config.setdefault(config_key, seed)

    return list(seeds_by_config.values())


def navigation_item_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    """Return a stable dedupe key for a normalized navigation item."""
    return (
        str(item.get("uri", "")),
        repr(item.get("range")),
        repr(item.get("fullRange")),
        repr(item.get("originRange")),
    )


def _reference_sort_key(item: Any) -> tuple[str, int, int]:
    if not isinstance(item, dict):
        return ("", 0, 0)
    uri = item.get("uri", "")
    range_obj = item.get("range") if isinstance(item.get("range"), dict) else {}
    start = range_obj.get("start", {}) if isinstance(range_obj, dict) else {}
    line = start.get("line", 0) if isinstance(start, dict) else 0
    character = start.get("character", 0) if isinstance(start, dict) else 0
    return (str(uri), line, character)
