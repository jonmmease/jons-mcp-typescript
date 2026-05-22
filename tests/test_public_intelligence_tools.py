"""Public diagnostics and rename-preview tool behavior tests."""

import inspect
from pathlib import Path
from typing import Any, get_type_hints

import pytest

from jons_mcp_typescript import semantic, server
from jons_mcp_typescript.schemas import (
    DiagnosticsResult,
    RenamePreviewError,
    RenamePreviewResult,
)
from jons_mcp_typescript.tools import intelligence
from jons_mcp_typescript.workspace import WorkspacePreloadStats


class FakeIntelligenceClient:
    """Fake vtsls client for diagnostics and rename-preview tests."""

    def __init__(self) -> None:
        self.responses: list[Any] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((method, params))
        response = self.responses.pop(0)
        if callable(response):
            response = response(method, params)
        if isinstance(response, Exception):
            raise response
        return response


def test_preview_rename_return_annotation_uses_public_models():
    rename_hints = get_type_hints(intelligence.preview_rename)
    diagnostics_hints = get_type_hints(intelligence.diagnostics)

    assert rename_hints["return"] == RenamePreviewResult | RenamePreviewError
    assert diagnostics_hints["return"] == DiagnosticsResult
    assert "scope" not in inspect.signature(intelligence.diagnostics).parameters


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch):
    fake = FakeIntelligenceClient()
    opened: list[str] = []
    closed: list[str] = []
    ensure_calls: list[str | None] = []
    project_loads: list[str] = []

    async def ensure(file_path: str | None = None) -> FakeIntelligenceClient:
        ensure_calls.append(file_path)
        return fake

    async def open_file(client: FakeIntelligenceClient, path: Path, uri: str) -> None:
        opened.append(uri)

    async def close_file(client: FakeIntelligenceClient, uri: str) -> None:
        closed.append(uri)

    async def ensure_project_loaded(client: FakeIntelligenceClient, path: Path) -> str:
        project_loads.append(str(path))
        return f"config:{path.name}"

    monkeypatch.setattr(intelligence, "ensure_vtsls_indexed", ensure)
    monkeypatch.setattr(intelligence, "ensure_project_loaded", ensure_project_loaded)
    monkeypatch.setattr(intelligence, "open_file", open_file)
    monkeypatch.setattr(intelligence, "close_file", close_file)
    monkeypatch.setattr(semantic, "ensure_project_loaded", ensure_project_loaded)
    monkeypatch.setattr(semantic, "open_file", open_file)
    monkeypatch.setattr(semantic, "close_file", close_file)
    return fake, opened, closed, ensure_calls, project_loads


@pytest.mark.asyncio
async def test_diagnostics_sorts_paginates_and_closes(
    tool_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness: tuple[
        FakeIntelligenceClient, list[str], list[str], list[str | None], list[str]
    ],
):
    _fake, opened, closed, ensure_calls, project_loads = harness

    async def wait(uri: str) -> list[dict[str, Any]]:
        return [
            {
                "severity": 2,
                "range": {"start": {"line": 3, "character": 0}},
                "message": "warn",
            },
            {
                "severity": 1,
                "range": {"start": {"line": 1, "character": 0}},
                "message": "err",
            },
        ]

    monkeypatch.setattr(intelligence, "wait_for_diagnostics", wait)

    result = await intelligence.diagnostics("src/main.ts", limit=1, offset=0)

    result_dict = result.model_dump(exclude_none=True)
    assert result_dict["totalItems"] == 2
    assert result_dict["items"] == [
        {
            "severity": 1,
            "range": {"start": {"line": 2, "character": 1}},
            "message": "err",
            "offset": 0,
        }
    ]
    assert result_dict["hasMore"] is True
    assert ensure_calls == ["src/main.ts"]
    assert project_loads == []
    assert sorted(opened) == sorted(closed)


@pytest.mark.asyncio
async def test_diagnostics_closes_file_when_wait_fails(
    tool_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness: tuple[
        FakeIntelligenceClient, list[str], list[str], list[str | None], list[str]
    ],
):
    _fake, opened, closed, _ensure_calls, project_loads = harness

    async def wait(uri: str) -> list[dict[str, Any]]:
        raise RuntimeError("diagnostics failed")

    monkeypatch.setattr(intelligence, "wait_for_diagnostics", wait)

    with pytest.raises(RuntimeError, match="diagnostics failed"):
        await intelligence.diagnostics("src/main.ts")

    assert sorted(opened) == sorted(closed)
    assert project_loads == []


@pytest.mark.asyncio
async def test_preview_rename_blocks_while_workspace_preload_is_incomplete(
    tool_project: Path,
    harness: tuple[
        FakeIntelligenceClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, opened, closed, ensure_calls, project_loads = harness
    server.reset_workspace_preload_state()
    server.workspace_preload_state.status = "running"
    server.workspace_preload_state.stats = WorkspacePreloadStats(
        discovered_projects=["packages/a/tsconfig.json"],
    )
    try:
        result = await intelligence.preview_rename(
            "src/main.ts",
            line=1,
            character=7,
            new_name="renamed",
        )

        assert isinstance(result, RenamePreviewError)
        assert "preview_rename is disabled" in result.error
        assert ensure_calls == ["src/main.ts"]
        assert fake.calls == []
        assert opened == []
        assert closed == []
        assert project_loads == []
    finally:
        server.reset_workspace_preload_state()


@pytest.mark.asyncio
async def test_preview_rename_returns_error_when_prepare_rename_rejects(
    tool_project: Path,
    harness: tuple[
        FakeIntelligenceClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    fake.responses = [None]

    result = await intelligence.preview_rename(
        "src/main.ts",
        line=1,
        character=7,
        new_name="renamed",
    )

    assert result == RenamePreviewError(error="Symbol cannot be renamed")
    assert project_loads == [str(source)]
    assert [call[0] for call in fake.calls] == ["textDocument/prepareRename"]
    assert sorted(opened) == sorted(closed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "edit",
    [
        {
            "changes": {
                "file:///project/src/main.ts": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 6},
                            "end": {"line": 0, "character": 12},
                        },
                        "newText": "renamed",
                    }
                ]
            }
        },
        {
            "documentChanges": [
                {
                    "textDocument": {"uri": "file:///project/src/main.ts"},
                    "edits": [
                        {
                            "range": {
                                "start": {"line": 0, "character": 6},
                                "end": {"line": 0, "character": 12},
                            },
                            "newText": "renamed",
                        }
                    ],
                }
            ]
        },
    ],
)
async def test_preview_rename_normalizes_edit_shapes(
    tool_project: Path,
    harness: tuple[
        FakeIntelligenceClient, list[str], list[str], list[str | None], list[str]
    ],
    edit: dict[str, Any],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    fake.responses = [RuntimeError("unsupported"), edit, []]

    result = await intelligence.preview_rename(
        "src/main.ts",
        line=1,
        character=7,
        new_name="renamed",
    )

    assert isinstance(result, RenamePreviewResult)
    assert result.model_dump() == {
        "edits": [
            {
                "uri": "file:///project/src/main.ts",
                "range": {
                    "start": {"line": 1, "character": 7},
                    "end": {"line": 1, "character": 13},
                },
                "newText": "renamed",
            }
        ],
        "totalEdits": 1,
    }
    assert project_loads == [str(source)]
    assert [call[0] for call in fake.calls] == [
        "textDocument/prepareRename",
        "textDocument/rename",
        "textDocument/references",
    ]
    assert sorted(opened) == sorted(closed)


@pytest.mark.parametrize(
    ("rename_result", "expected"),
    [
        (None, {"error": "Rename failed"}),
        ([], {"error": "Rename failed"}),
        ("bad", {"error": "Rename failed"}),
        ({"unexpected": []}, {"error": "Rename returned unsupported edit shape"}),
    ],
)
@pytest.mark.asyncio
async def test_preview_rename_normalizes_failed_results(
    tool_project: Path,
    harness: tuple[
        FakeIntelligenceClient, list[str], list[str], list[str | None], list[str]
    ],
    rename_result: Any,
    expected: dict[str, Any],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    fake.responses = [{"range": {}}, rename_result]

    result = await intelligence.preview_rename(
        "src/main.ts",
        line=1,
        character=7,
        new_name="renamed",
    )

    assert isinstance(result, RenamePreviewError)
    assert result.model_dump() == expected
    assert project_loads == [str(source)]
    assert sorted(opened) == sorted(closed)


@pytest.mark.asyncio
async def test_preview_rename_aggregates_reference_seeded_project_edits(
    tool_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness: tuple[
        FakeIntelligenceClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    alias = tool_project / "src" / "alias.ts"
    server_a = tool_project / "src" / "server_a.ts"
    server_b = tool_project / "src" / "server_b.ts"
    external = tmp_path / "external.ts"
    for path in (alias, server_a, server_b, external):
        path.write_text("export const value = 1;\n", encoding="utf-8")

    async def grouped_project_load(client: FakeIntelligenceClient, path: Path) -> str:
        project_loads.append(str(path))
        if path.name.startswith("server_"):
            return "server-config"
        return "main-config"

    monkeypatch.setattr(intelligence, "ensure_project_loaded", grouped_project_load)
    monkeypatch.setattr(semantic, "ensure_project_loaded", grouped_project_load)

    def edit(uri: str, line: int, character: int) -> dict[str, Any]:
        return {
            "changes": {
                uri: [
                    {
                        "range": {
                            "start": {"line": line, "character": character},
                            "end": {"line": line, "character": character + 5},
                        },
                        "newText": "renamed",
                    }
                ]
            }
        }

    direct_edit = {
        "changes": {
            source.as_uri(): edit(source.as_uri(), 0, 6)["changes"][source.as_uri()],
            alias.as_uri(): edit(alias.as_uri(), 0, 9)["changes"][alias.as_uri()],
        }
    }
    seed_edit = {
        "changes": {
            server_a.as_uri(): edit(server_a.as_uri(), 1, 2)["changes"][
                server_a.as_uri()
            ],
            source.as_uri(): edit(source.as_uri(), 0, 6)["changes"][source.as_uri()],
        }
    }
    fake.responses = [
        {"range": {}},
        direct_edit,
        [
            {
                "uri": source.as_uri(),
                "range": {"start": {"line": 0, "character": 6}},
            },
            {"uri": alias.as_uri(), "range": {"start": {"line": 0, "character": 9}}},
            {
                "uri": server_a.as_uri(),
                "range": {"start": {"line": 1, "character": 2}},
            },
            {
                "uri": server_b.as_uri(),
                "range": {"start": {"line": 1, "character": 2}},
            },
            {
                "uri": external.as_uri(),
                "range": {"start": {"line": 1, "character": 2}},
            },
        ],
        seed_edit,
    ]

    result = await intelligence.preview_rename(
        "src/main.ts",
        line=1,
        character=7,
        new_name="renamed",
    )

    assert isinstance(result, RenamePreviewResult)
    result_dict = result.model_dump()
    assert {edit["uri"] for edit in result_dict["edits"]} == {
        source.as_uri(),
        alias.as_uri(),
        server_a.as_uri(),
    }
    assert result_dict["totalEdits"] == 3
    rename_uris = [
        params["textDocument"]["uri"]
        for method, params in fake.calls
        if method == "textDocument/rename"
    ]
    assert rename_uris == [source.as_uri(), server_a.as_uri()]
    assert external.as_uri() not in opened
    assert str(external) not in project_loads
    assert sorted(opened) == sorted(closed)


@pytest.mark.asyncio
async def test_preview_rename_returns_error_for_bad_aggregation_result(
    tool_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness: tuple[
        FakeIntelligenceClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    server_file = tool_project / "src" / "server.ts"
    server_file.write_text("export const value = 1;\n", encoding="utf-8")

    async def grouped_project_load(client: FakeIntelligenceClient, path: Path) -> str:
        project_loads.append(str(path))
        return "server-config" if path.name == "server.ts" else "main-config"

    monkeypatch.setattr(intelligence, "ensure_project_loaded", grouped_project_load)
    monkeypatch.setattr(semantic, "ensure_project_loaded", grouped_project_load)

    fake.responses = [
        {"range": {}},
        {
            "changes": {
                source.as_uri(): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 6},
                            "end": {"line": 0, "character": 11},
                        },
                        "newText": "renamed",
                    }
                ]
            }
        },
        [
            {
                "uri": server_file.as_uri(),
                "range": {"start": {"line": 0, "character": 6}},
            }
        ],
        {"unexpected": []},
    ]

    result = await intelligence.preview_rename(
        "src/main.ts",
        line=1,
        character=7,
        new_name="renamed",
    )

    assert result == RenamePreviewError(error="Rename returned unsupported edit shape")
    assert sorted(opened) == sorted(closed)
