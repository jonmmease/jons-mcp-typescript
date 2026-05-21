"""Public language/navigation tool behavior tests."""

from pathlib import Path
from typing import Any

import pytest

from jons_mcp_typescript.tools import language


class FakeLanguageClient:
    """Fake vtsls client with configurable request responses."""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.notifications: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((method, params))
        response = self.responses.get(method)
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

    async def ensure(file_path: str | None = None) -> FakeLanguageClient:
        ensure_calls.append(file_path)
        return fake

    async def open_file(client: FakeLanguageClient, path: Path, uri: str) -> None:
        opened.append(uri)

    async def close_file(client: FakeLanguageClient, uri: str) -> None:
        closed.append(uri)

    monkeypatch.setattr(language, "ensure_vtsls_indexed", ensure)
    monkeypatch.setattr(language, "open_file", open_file)
    monkeypatch.setattr(language, "close_file", close_file)
    return fake, opened, closed, ensure_calls


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
    harness: tuple[FakeLanguageClient, list[str], list[str], list[str | None]],
    tool: Any,
    lsp_method: str,
):
    fake, opened, closed, ensure_calls = harness
    location = {
        "uri": "file:///project/src/main.ts",
        "range": {"start": {"line": 2, "character": 4}},
    }
    fake.responses[lsp_method] = location

    result = await tool("src/main.ts", line=1, character=2)

    assert result == location
    assert ensure_calls == ["src/main.ts"]
    assert opened == closed
    assert fake.calls == [
        (
            lsp_method,
            {
                "textDocument": {"uri": opened[0]},
                "position": {"line": 1, "character": 2},
            },
        )
    ]


@pytest.mark.asyncio
async def test_references_sorts_and_paginates(
    tool_project: Path,
    harness: tuple[FakeLanguageClient, list[str], list[str], list[str | None]],
):
    fake, opened, closed, _ensure_calls = harness
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
        line=0,
        character=1,
        include_declaration=False,
        limit=2,
        offset=1,
    )

    assert [item["uri"] for item in result["items"]] == [
        "file:///project/a.ts",
        "file:///project/b.ts",
    ]
    assert [item["offset"] for item in result["items"]] == [1, 2]
    assert result["hasMore"] is False
    assert opened == closed
    assert fake.calls[0][1]["context"] == {"includeDeclaration": False}


@pytest.mark.asyncio
async def test_workspace_symbols_sorts_and_paginates(
    harness: tuple[FakeLanguageClient, list[str], list[str], list[str | None]],
):
    fake, opened, closed, ensure_calls = harness
    fake.responses["workspace/symbol"] = [
        {
            "name": "Zoo",
            "location": {
                "uri": "file:///z.ts",
                "range": {"start": {"line": 0}},
            },
        },
        {
            "name": "Alpha",
            "location": {
                "uri": "file:///b.ts",
                "range": {"start": {"line": 2}},
            },
        },
        {
            "name": "Alpha",
            "location": {
                "uri": "file:///a.ts",
                "range": {"start": {"line": 1}},
            },
        },
    ]

    result = await language.workspace_symbols("a", limit=2, offset=0)

    assert [(item["name"], item["location"]["uri"]) for item in result["items"]] == [
        ("Alpha", "file:///a.ts"),
        ("Alpha", "file:///b.ts"),
    ]
    assert result["hasMore"] is True
    assert ensure_calls == [None]
    assert opened == []
    assert closed == []


@pytest.mark.asyncio
async def test_document_symbols_flattens_sorts_and_closes(
    tool_project: Path,
    harness: tuple[FakeLanguageClient, list[str], list[str], list[str | None]],
):
    fake, opened, closed, _ensure_calls = harness
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

    assert [(item["name"], item.get("containerName")) for item in result["items"]] == [
        ("User", None),
        ("name", "User"),
    ]
    assert opened == closed


@pytest.mark.asyncio
async def test_symbol_info_normalizes_hover_content(
    tool_project: Path,
    harness: tuple[FakeLanguageClient, list[str], list[str], list[str | None]],
):
    fake, opened, closed, _ensure_calls = harness
    fake.responses["textDocument/hover"] = {
        "contents": [{"value": "const value: number"}, "docs"],
        "range": {"start": {"line": 0, "character": 0}},
    }

    result = await language.symbol_info("src/main.ts", line=0, character=6)

    assert result == {
        "content": "const value: number\ndocs",
        "range": {"start": {"line": 0, "character": 0}},
    }
    assert opened == closed


@pytest.mark.asyncio
async def test_type_info_extracts_in_root_members_and_restores_document(
    tool_project: Path,
    harness: tuple[FakeLanguageClient, list[str], list[str], list[str | None]],
):
    fake, opened, closed, _ensure_calls = harness
    source = tool_project / "src" / "main.ts"
    type_file = tool_project / "src" / "types.ts"
    source.write_text("const user = makeUser();\nuser;\n", encoding="utf-8")
    type_file.write_text("export interface User {}\n", encoding="utf-8")
    type_range = {"start": {"line": 0, "character": 0}, "end": {"line": 10}}
    fake.responses = {
        "textDocument/hover": {"contents": {"value": "User"}},
        "textDocument/typeDefinition": {
            "uri": type_file.resolve().as_uri(),
            "range": type_range,
        },
        "textDocument/documentSymbol": [
            {
                "name": "name",
                "kind": 7,
                "detail": "string",
                "range": {"start": {"line": 1, "character": 2}},
            },
            {
                "name": "save",
                "kind": 6,
                "detail": "() => void",
                "range": {"start": {"line": 2, "character": 2}},
            },
        ],
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

    result = await language.type_info(
        "src/main.ts",
        line=1,
        character=1,
        include_documentation=True,
    )

    assert result["typeName"] == "User"
    assert result["sourceLocation"] == {"uri": type_file.as_uri(), "range": type_range}
    assert result["fields"] == [
        {"name": "email", "type": "string", "documentation": "Email docs"},
        {"name": "name", "type": "string"},
    ]
    assert result["methods"]["items"] == [
        {"name": "greet", "signature": "() => string", "documentation": "Greet docs"},
        {"name": "save", "signature": "() => void"},
    ]
    assert [call[0] for call in fake.calls] == [
        "textDocument/hover",
        "textDocument/typeDefinition",
        "textDocument/documentSymbol",
        "textDocument/completion",
    ]
    changed_texts = [
        params["contentChanges"][0]["text"]
        for method, params in fake.notifications
        if method == "textDocument/didChange"
    ]
    assert changed_texts == [
        "const user = makeUser();\nuser.;\n",
        "const user = makeUser();\nuser;\n",
    ]
    assert opened == [source.as_uri(), type_file.as_uri()]
    assert closed == opened


@pytest.mark.asyncio
async def test_type_info_returns_external_source_without_opening_it(
    tool_project: Path,
    tmp_path: Path,
    harness: tuple[FakeLanguageClient, list[str], list[str], list[str | None]],
):
    fake, opened, closed, _ensure_calls = harness
    source = tool_project / "src" / "main.ts"
    external = tmp_path / "external.ts"
    source.write_text("user;\n", encoding="utf-8")
    external.write_text("export interface External {}\n", encoding="utf-8")
    fake.responses = {
        "textDocument/hover": {"contents": {"value": "External"}},
        "textDocument/typeDefinition": {"uri": external.as_uri(), "range": {}},
        "textDocument/completion": {"items": []},
    }

    result = await language.type_info("src/main.ts", line=0, character=1)

    assert result["sourceLocation"] == {"uri": external.as_uri(), "range": {}}
    assert [call[0] for call in fake.calls] == [
        "textDocument/hover",
        "textDocument/typeDefinition",
        "textDocument/completion",
    ]
    assert opened == [source.as_uri()]
    assert closed == opened


@pytest.mark.asyncio
async def test_type_info_skips_completion_when_identifier_is_not_safe(
    tool_project: Path,
    harness: tuple[FakeLanguageClient, list[str], list[str], list[str | None]],
):
    fake, _opened, _closed, _ensure_calls = harness
    source = tool_project / "src" / "main.ts"
    source.write_text(";\n", encoding="utf-8")
    fake.responses = {
        "textDocument/hover": None,
        "textDocument/typeDefinition": None,
        "textDocument/completion": AssertionError("completion should not be requested"),
    }

    result = await language.type_info("src/main.ts", line=0, character=0)

    assert result["typeName"] == "unknown"
    assert result["fields"] == []
    assert result["methods"]["items"] == []
    assert [call[0] for call in fake.calls] == [
        "textDocument/hover",
        "textDocument/typeDefinition",
    ]
    assert fake.notifications == []
