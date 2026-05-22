"""Public language/navigation tool behavior tests."""

from pathlib import Path
from typing import Any, get_type_hints

import pytest

from jons_mcp_typescript import semantic, server
from jons_mcp_typescript.schemas import (
    DocumentSymbolsResult,
    NavigationResult,
    ReferencesResult,
    SymbolInfoResult,
    TypeInfoResult,
)
from jons_mcp_typescript.tools import language
from jons_mcp_typescript.workspace import WorkspacePreloadStats


class FakeLanguageClient:
    """Fake vtsls client with configurable request responses."""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.notifications: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((method, params))
        response = self.responses.get(method)
        if callable(response):
            response = response(method, params)
        if isinstance(response, Exception):
            raise response
        return response

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        self.notifications.append((method, params))


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch):
    fake = FakeLanguageClient()
    opened: list[str] = []
    closed: list[str] = []
    ensure_calls: list[str | None] = []
    project_loads: list[str] = []

    async def ensure(file_path: str | None = None) -> FakeLanguageClient:
        ensure_calls.append(file_path)
        return fake

    async def open_file(client: FakeLanguageClient, path: Path, uri: str) -> None:
        opened.append(uri)

    async def close_file(client: FakeLanguageClient, uri: str) -> None:
        closed.append(uri)

    async def ensure_project_loaded(client: FakeLanguageClient, path: Path) -> str:
        project_loads.append(str(path))
        return f"config:{path.name}"

    monkeypatch.setattr(language, "ensure_vtsls_indexed", ensure)
    monkeypatch.setattr(language, "ensure_project_loaded", ensure_project_loaded)
    monkeypatch.setattr(language, "open_file", open_file)
    monkeypatch.setattr(language, "close_file", close_file)
    monkeypatch.setattr(semantic, "ensure_project_loaded", ensure_project_loaded)
    monkeypatch.setattr(semantic, "open_file", open_file)
    monkeypatch.setattr(semantic, "close_file", close_file)
    return fake, opened, closed, ensure_calls, project_loads


def test_language_return_annotations_use_public_models():
    assert get_type_hints(language.definition)["return"] == NavigationResult
    assert get_type_hints(language.type_definition)["return"] == NavigationResult
    assert get_type_hints(language.implementation)["return"] == NavigationResult
    assert get_type_hints(language.references)["return"] == ReferencesResult
    assert get_type_hints(language.document_symbols)["return"] == DocumentSymbolsResult
    assert get_type_hints(language.symbol_info)["return"] == SymbolInfoResult
    assert get_type_hints(language.type_info_of_reference)["return"] == TypeInfoResult


@pytest.mark.parametrize(
    ("tool", "lsp_method"),
    [
        (language.definition, "textDocument/definition"),
        (language.type_definition, "textDocument/typeDefinition"),
        (language.implementation, "textDocument/implementation"),
    ],
)
@pytest.mark.asyncio
async def test_navigation_tools_open_request_and_close(
    tool_project: Path,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
    tool: Any,
    lsp_method: str,
):
    fake, opened, closed, ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    location = {
        "uri": "file:///project/src/main.ts",
        "range": {"start": {"line": 2, "character": 4}},
    }
    fake.responses[lsp_method] = location
    fake.responses["textDocument/references"] = []

    result = await tool("src/main.ts", line=2, character=3)

    assert result.model_dump(exclude_none=True) == {
        "items": [
            {
                "uri": "file:///project/src/main.ts",
                "range": {"start": {"line": 3, "character": 5}},
            }
        ],
        "totalItems": 1,
    }
    assert ensure_calls == ["src/main.ts"]
    assert project_loads == [str(source)]
    assert sorted(opened) == sorted(closed)
    expected_calls = [
        (
            lsp_method,
            {
                "textDocument": {"uri": opened[0]},
                "position": {"line": 1, "character": 2},
            },
        )
    ]
    if tool is language.implementation:
        expected_calls.append(
            (
                "textDocument/references",
                {
                    "textDocument": {"uri": opened[0]},
                    "position": {"line": 1, "character": 2},
                    "context": {"includeDeclaration": True},
                },
            )
        )
    assert fake.calls == expected_calls


@pytest.mark.parametrize(
    ("tool", "lsp_method"),
    [
        (language.definition, "textDocument/definition"),
        (language.type_definition, "textDocument/typeDefinition"),
        (language.implementation, "textDocument/implementation"),
    ],
)
@pytest.mark.asyncio
async def test_navigation_tools_normalize_location_links(
    tool_project: Path,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
    tool: Any,
    lsp_method: str,
):
    fake, _opened, _closed, _ensure_calls, _project_loads = harness
    fake.responses[lsp_method] = [
        {
            "originSelectionRange": {
                "start": {"line": 1, "character": 2},
                "end": {"line": 1, "character": 8},
            },
            "targetUri": "file:///project/src/types.ts",
            "targetRange": {
                "start": {"line": 4, "character": 0},
                "end": {"line": 6, "character": 1},
            },
            "targetSelectionRange": {
                "start": {"line": 4, "character": 16},
                "end": {"line": 4, "character": 22},
            },
        },
        {
            "originSelectionRange": {
                "start": {"line": 1, "character": 2},
                "end": {"line": 1, "character": 8},
            },
            "targetUri": "file:///project/src/types.ts",
            "targetRange": {
                "start": {"line": 4, "character": 0},
                "end": {"line": 6, "character": 1},
            },
            "targetSelectionRange": {
                "start": {"line": 4, "character": 16},
                "end": {"line": 4, "character": 22},
            },
        },
    ]

    result = await tool("src/main.ts", line=2, character=3)

    assert result.model_dump(exclude_none=True) == {
        "items": [
            {
                "uri": "file:///project/src/types.ts",
                "range": {
                    "start": {"line": 5, "character": 17},
                    "end": {"line": 5, "character": 23},
                },
                "fullRange": {
                    "start": {"line": 5, "character": 1},
                    "end": {"line": 7, "character": 2},
                },
                "originRange": {
                    "start": {"line": 2, "character": 3},
                    "end": {"line": 2, "character": 9},
                },
            }
        ],
        "totalItems": 1,
    }


@pytest.mark.parametrize(
    ("tool", "lsp_method"),
    [
        (language.definition, "textDocument/definition"),
        (language.type_definition, "textDocument/typeDefinition"),
        (language.implementation, "textDocument/implementation"),
    ],
)
@pytest.mark.asyncio
async def test_navigation_tools_return_empty_result_for_missing_targets(
    tool_project: Path,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
    tool: Any,
    lsp_method: str,
):
    fake, _opened, _closed, _ensure_calls, _project_loads = harness
    fake.responses[lsp_method] = None

    result = await tool("src/main.ts", line=2, character=3)

    assert result == NavigationResult(items=[], totalItems=0)


@pytest.mark.asyncio
async def test_implementation_aggregates_reference_seeded_projects(
    tool_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
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

    async def grouped_project_load(client: FakeLanguageClient, path: Path) -> str:
        project_loads.append(str(path))
        if path.name.startswith("server_"):
            return "server-config"
        return "main-config"

    monkeypatch.setattr(language, "ensure_project_loaded", grouped_project_load)
    monkeypatch.setattr(semantic, "ensure_project_loaded", grouped_project_load)

    main_location = {
        "uri": source.as_uri(),
        "range": {"start": {"line": 0, "character": 0}},
    }
    server_location = {
        "uri": server_a.as_uri(),
        "range": {"start": {"line": 2, "character": 4}},
    }

    def implementation_response(method: str, params: dict[str, Any]) -> Any:
        uri = params["textDocument"]["uri"]
        if uri == source.as_uri():
            return [main_location]
        if uri == server_a.as_uri():
            return [server_location, main_location]
        raise AssertionError(f"unexpected implementation seed: {uri}")

    fake.responses["textDocument/implementation"] = implementation_response
    fake.responses["textDocument/references"] = [
        {"uri": source.as_uri(), "range": {"start": {"line": 0, "character": 6}}},
        {"uri": alias.as_uri(), "range": {"start": {"line": 0, "character": 6}}},
        {"uri": server_a.as_uri(), "range": {"start": {"line": 1, "character": 8}}},
        {"uri": server_b.as_uri(), "range": {"start": {"line": 1, "character": 8}}},
        {"uri": external.as_uri(), "range": {"start": {"line": 1, "character": 8}}},
    ]

    result = await language.implementation("src/main.ts", line=1, character=7)

    result_dict = result.model_dump(exclude_none=True)
    assert {(item["uri"], item["range"]["start"]["line"]) for item in result_dict["items"]} == {
        (source.as_uri(), 1),
        (server_a.as_uri(), 3),
    }
    assert result_dict["totalItems"] == 2

    implementation_uris = [
        params["textDocument"]["uri"]
        for method, params in fake.calls
        if method == "textDocument/implementation"
    ]
    assert implementation_uris == [source.as_uri(), server_a.as_uri()]
    assert external.as_uri() not in opened
    assert str(external) not in project_loads
    assert sorted(opened) == sorted(closed)


@pytest.mark.asyncio
async def test_semantic_tools_warn_while_workspace_preload_is_incomplete(
    tool_project: Path,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, _opened, _closed, _ensure_calls, _project_loads = harness
    source = tool_project / "src" / "main.ts"
    fake.responses["textDocument/references"] = [
        {
            "uri": source.as_uri(),
            "range": {"start": {"line": 0, "character": 6}},
        }
    ]
    fake.responses["textDocument/implementation"] = []
    server.reset_workspace_preload_state()
    server.workspace_preload_state.status = "running"
    server.workspace_preload_state.stats = WorkspacePreloadStats(
        discovered_projects=["packages/a/tsconfig.json"],
    )
    try:
        references_result = await language.references(
            "src/main.ts",
            line=1,
            character=7,
        )
        implementation_result = await language.implementation(
            "src/main.ts",
            line=1,
            character=7,
        )

        assert references_result.warnings
        assert references_result.warnings[0].code == "WORKSPACE_PRELOAD_INCOMPLETE"
        assert "still running" in references_result.warnings[0].message
        assert references_result.warnings[0].detailsTool == "workspace_status"
        assert implementation_result.warnings
        assert implementation_result.warnings[0].code == "WORKSPACE_PRELOAD_INCOMPLETE"
        assert "still running" in implementation_result.warnings[0].message

        server.workspace_preload_state.status = "complete"
        server.workspace_preload_state.stats = WorkspacePreloadStats()
        fake.responses["textDocument/references"] = []

        complete_result = await language.references(
            "src/main.ts",
            line=1,
            character=7,
        )

        assert complete_result.warnings is None
    finally:
        server.reset_workspace_preload_state()


@pytest.mark.asyncio
async def test_implementation_aggregation_errors_for_in_root_seed(
    tool_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    server_file = tool_project / "src" / "server.ts"
    server_file.write_text("export const value = 1;\n", encoding="utf-8")

    async def grouped_project_load(client: FakeLanguageClient, path: Path) -> str:
        project_loads.append(str(path))
        return "server-config" if path.name == "server.ts" else "main-config"

    monkeypatch.setattr(language, "ensure_project_loaded", grouped_project_load)
    monkeypatch.setattr(semantic, "ensure_project_loaded", grouped_project_load)

    def implementation_response(method: str, params: dict[str, Any]) -> Any:
        uri = params["textDocument"]["uri"]
        if uri == source.as_uri():
            return []
        raise RuntimeError("seed boom")

    fake.responses["textDocument/implementation"] = implementation_response
    fake.responses["textDocument/references"] = [
        {
            "uri": server_file.as_uri(),
            "range": {"start": {"line": 0, "character": 6}},
        }
    ]

    with pytest.raises(RuntimeError, match="seed boom"):
        await language.implementation("src/main.ts", line=1, character=7)

    assert sorted(opened) == sorted(closed)


@pytest.mark.asyncio
async def test_references_sorts_and_paginates(
    tool_project: Path,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    fake.responses["textDocument/references"] = [
        {
            "uri": "file:///project/b.ts",
            "range": {"start": {"line": 3, "character": 0}},
        },
        {
            "uri": "file:///project/a.ts",
            "range": {"start": {"line": 1, "character": 5}},
        },
        {
            "uri": "file:///project/a.ts",
            "range": {"start": {"line": 1, "character": 1}},
        },
    ]

    result = await language.references(
        "src/main.ts",
        line=1,
        character=2,
        include_declaration=False,
        limit=2,
        offset=1,
    )

    result_dict = result.model_dump(exclude_none=True)
    assert [item["uri"] for item in result_dict["items"]] == [
        "file:///project/a.ts",
        "file:///project/b.ts",
    ]
    assert [item["range"]["start"] for item in result_dict["items"]] == [
        {"line": 2, "character": 6},
        {"line": 4, "character": 1},
    ]
    assert [item["offset"] for item in result_dict["items"]] == [1, 2]
    assert result_dict["hasMore"] is False
    assert project_loads == [str(source)]
    assert opened == closed
    assert fake.calls[0][1]["context"] == {"includeDeclaration": False}


@pytest.mark.asyncio
async def test_document_symbols_flattens_sorts_and_closes(
    tool_project: Path,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    fake.responses["textDocument/documentSymbol"] = [
        {
            "name": "User",
            "kind": 5,
            "range": {"start": {"line": 5, "character": 0}},
            "selectionRange": {},
            "children": [
                {
                    "name": "name",
                    "kind": 7,
                    "range": {"start": {"line": 6, "character": 2}},
                    "selectionRange": {},
                }
            ],
        }
    ]

    result = await language.document_symbols("src/main.ts")

    result_dict = result.model_dump(exclude_none=True)
    assert [
        (item["name"], item.get("containerName")) for item in result_dict["items"]
    ] == [
        ("User", None),
        ("name", "User"),
    ]
    assert [item["range"]["start"] for item in result_dict["items"]] == [
        {"line": 6, "character": 1},
        {"line": 7, "character": 3},
    ]
    assert project_loads == []
    assert opened == closed


@pytest.mark.asyncio
async def test_symbol_info_normalizes_hover_content(
    tool_project: Path,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    fake.responses["textDocument/hover"] = {
        "contents": [{"value": "const value: number"}, "docs"],
        "range": {"start": {"line": 0, "character": 0}},
    }

    result = await language.symbol_info("src/main.ts", line=1, character=7)

    assert result.model_dump(exclude_none=True) == {
        "content": "const value: number\ndocs",
        "range": {"start": {"line": 1, "character": 1}},
    }
    assert project_loads == [str(source)]
    assert opened == closed


@pytest.mark.asyncio
async def test_type_info_uses_completion_members_and_restores_document(
    tool_project: Path,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    type_file = tool_project / "src" / "types.ts"
    source.write_text("const user = makeUser();\nuser;\n", encoding="utf-8")
    type_file.write_text("export interface User {}\n", encoding="utf-8")
    type_range = {
        "start": {"line": 0, "character": 0},
        "end": {"line": 10, "character": 1},
    }
    public_type_range = {
        "start": {"line": 1, "character": 1},
        "end": {"line": 11, "character": 2},
    }
    fake.responses = {
        "workspace/executeCommand": {
            "success": True,
            "body": {"displayString": "const user: User", "kind": "const"},
        },
        "textDocument/typeDefinition": {
            "uri": type_file.resolve().as_uri(),
            "range": type_range,
        },
        "textDocument/completion": {
            "items": [
                {"label": "name", "kind": 10, "detail": "string"},
                {
                    "label": "email",
                    "kind": 10,
                    "detail": "string",
                    "documentation": {"value": "Email docs"},
                },
                {
                    "label": "greet",
                    "kind": 2,
                    "detail": "() => string",
                    "documentation": "Greet docs",
                },
            ]
        },
    }

    result = await language.type_info_of_reference(
        "src/main.ts",
        line=2,
        character=2,
        include_documentation=True,
    )

    result_dict = result.model_dump(exclude_none=True)
    assert result_dict["displayString"] == "const user: User"
    assert result_dict["kind"] == "const"
    assert result_dict["sourceLocation"] == {
        "uri": type_file.as_uri(),
        "range": public_type_range,
    }
    assert result_dict["fields"] == [
        {"name": "email", "type": "string", "documentation": "Email docs"},
        {"name": "name", "type": "string"},
    ]
    assert result_dict["methods"]["items"] == [
        {"name": "greet", "signature": "() => string", "documentation": "Greet docs"},
    ]
    assert [call[0] for call in fake.calls] == [
        "workspace/executeCommand",
        "textDocument/typeDefinition",
        "textDocument/completion",
    ]
    assert fake.calls[0][1] == {
        "command": "typescript.tsserverRequest",
        "arguments": [
            "quickinfo",
            {"file": str(source), "line": 2, "offset": 2},
        ],
    }
    assert project_loads == [str(source)]
    changed_texts = [
        params["contentChanges"][0]["text"]
        for method, params in fake.notifications
        if method == "textDocument/didChange"
    ]
    assert changed_texts == [
        "const user = makeUser();\nuser.;\n",
        "const user = makeUser();\nuser;\n",
    ]
    assert opened == [source.as_uri()]
    assert closed == opened


@pytest.mark.asyncio
async def test_type_info_returns_quickinfo_display_string_and_kind(
    tool_project: Path,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    source.write_text("err;\n", encoding="utf-8")
    fake.responses = {
        "workspace/executeCommand": {
            "success": True,
            "body": {"displayString": "let err: Error", "kind": "let"},
        },
        "textDocument/typeDefinition": None,
        "textDocument/completion": {"items": []},
    }

    result = await language.type_info_of_reference("src/main.ts", line=1, character=2)

    result_dict = result.model_dump(exclude_none=True)
    assert result_dict["displayString"] == "let err: Error"
    assert result_dict["kind"] == "let"
    assert result_dict["fields"] == []
    assert result_dict["methods"]["items"] == []
    assert fake.calls[0][1]["arguments"] == [
        "quickinfo",
        {"file": str(source), "line": 1, "offset": 2},
    ]
    assert project_loads == [str(source)]
    assert opened == [source.as_uri()]
    assert closed == opened


@pytest.mark.asyncio
async def test_type_info_returns_external_source_without_opening_it(
    tool_project: Path,
    tmp_path: Path,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, opened, closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    external = tmp_path / "external.ts"
    source.write_text("user;\n", encoding="utf-8")
    external.write_text("export interface External {}\n", encoding="utf-8")
    fake.responses = {
        "workspace/executeCommand": {
            "success": True,
            "body": {"displayString": "const user: External", "kind": "const"},
        },
        "textDocument/typeDefinition": {"uri": external.as_uri(), "range": {}},
        "textDocument/completion": {"items": []},
    }

    result = await language.type_info_of_reference("src/main.ts", line=1, character=2)

    result_dict = result.model_dump(exclude_none=True)
    assert result_dict["displayString"] == "const user: External"
    assert result_dict["sourceLocation"] == {"uri": external.as_uri()}
    assert [call[0] for call in fake.calls] == [
        "workspace/executeCommand",
        "textDocument/typeDefinition",
        "textDocument/completion",
    ]
    assert project_loads == [str(source)]
    assert opened == [source.as_uri()]
    assert closed == opened


@pytest.mark.asyncio
async def test_type_info_skips_completion_when_identifier_is_not_safe(
    tool_project: Path,
    harness: tuple[
        FakeLanguageClient, list[str], list[str], list[str | None], list[str]
    ],
):
    fake, _opened, _closed, _ensure_calls, project_loads = harness
    source = tool_project / "src" / "main.ts"
    source.write_text(";\n", encoding="utf-8")
    fake.responses = {
        "workspace/executeCommand": {"success": False},
        "textDocument/typeDefinition": None,
        "textDocument/completion": AssertionError("completion should not be requested"),
    }

    result = await language.type_info_of_reference("src/main.ts", line=1, character=1)

    result_dict = result.model_dump(exclude_none=True)
    assert result_dict["displayString"] == ""
    assert result_dict["fields"] == []
    assert result_dict["methods"]["items"] == []
    assert [call[0] for call in fake.calls] == [
        "workspace/executeCommand",
        "textDocument/typeDefinition",
    ]
    assert project_loads == [str(source)]
    assert fake.notifications == []
