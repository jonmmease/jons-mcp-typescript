"""Security-focused public tool behavior tests."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from jons_mcp_typescript.exceptions import PathOutsideProjectError
from jons_mcp_typescript.tools import (
    formatting,
    intelligence,
    language,
    linting,
    unified,
)

ToolCall = Callable[[str], Awaitable[Any]]


def tool_calls() -> dict[str, ToolCall]:
    """Return public file-path tool invocations keyed by tool name."""
    return {
        "format_code": lambda path: formatting.format_code.fn(path, code=""),
        "check_formatting": lambda path: formatting.check_formatting.fn(path, code=""),
        "get_prettier_config": lambda path: formatting.get_prettier_config.fn(path),
        "lint_code": lambda path: linting.lint_code.fn(path, code=""),
        "get_eslint_config": lambda path: linting.get_eslint_config.fn(path),
        "diagnostics": lambda path: intelligence.diagnostics.fn(path, scope="file"),
        "rename": lambda path: intelligence.rename.fn(path, 0, 0, "renamed"),
        "definition": lambda path: language.definition.fn(path, 0, 0),
        "type_definition": lambda path: language.type_definition.fn(path, 0, 0),
        "implementation": lambda path: language.implementation.fn(path, 0, 0),
        "references": lambda path: language.references.fn(path, 0, 0),
        "document_symbols": lambda path: language.document_symbols.fn(path),
        "symbol_info": lambda path: language.symbol_info.fn(path, 0, 0),
        "type_info": lambda path: language.type_info.fn(path, 0, 0),
        "check_all": lambda path: unified.check_all.fn(path),
        "fix_all": lambda path: unified.fix_all.fn(path, write=True),
    }


@pytest.fixture
def escaped_paths(tool_project: Path) -> dict[str, str]:
    """Create one outside target for each supported escape shape."""
    outside_file = tool_project.parent / "outside.ts"
    outside_file.write_text("const outside = true;\n", encoding="utf-8")

    symlink_path = tool_project / "src" / "outside-link.ts"
    symlink_path.symlink_to(outside_file)

    return {
        "absolute": str(outside_file),
        "file_uri": outside_file.as_uri(),
        "parent": "../outside.ts",
        "symlink": "src/outside-link.ts",
    }


@pytest.fixture(autouse=True)
def fail_if_backends_are_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Path rejection should happen before daemon, LSP, reads, or writes."""

    def fail_backend(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("backend should not be reached for outside-root paths")

    async def fail_async_backend(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("backend should not be reached for outside-root paths")

    for module in (formatting, linting, unified, intelligence):
        if hasattr(module, "get_daemon"):
            monkeypatch.setattr(module, "get_daemon", fail_backend)

    for module in (language, intelligence, unified):
        monkeypatch.setattr(module, "ensure_vtsls_indexed", fail_async_backend)


@pytest.mark.parametrize("tool_name", sorted(tool_calls()))
@pytest.mark.parametrize("escape_name", ["absolute", "file_uri", "parent", "symlink"])
@pytest.mark.asyncio
async def test_file_path_tools_reject_project_root_escapes(
    escaped_paths: dict[str, str],
    tool_name: str,
    escape_name: str,
):
    call = tool_calls()[tool_name]

    with pytest.raises(PathOutsideProjectError):
        await call(escaped_paths[escape_name])
