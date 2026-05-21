"""Public diagnostics and rename tool behavior tests."""

from pathlib import Path
from typing import Any

import pytest

from jons_mcp_typescript import server
from jons_mcp_typescript.tools import intelligence


class FakeIntelligenceClient:
    """Fake vtsls client for diagnostics and rename tests."""

    def __init__(self) -> None:
        self.responses: list[Any] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((method, params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch):
    fake = FakeIntelligenceClient()
    opened: list[str] = []
    closed: list[str] = []
    ensure_calls: list[str | None] = []

    async def ensure(file_path: str | None = None) -> FakeIntelligenceClient:
        ensure_calls.append(file_path)
        return fake

    async def open_file(client: FakeIntelligenceClient, path: Path, uri: str) -> None:
        opened.append(uri)

    async def close_file(client: FakeIntelligenceClient, uri: str) -> None:
        closed.append(uri)

    monkeypatch.setattr(intelligence, "ensure_vtsls_indexed", ensure)
    monkeypatch.setattr(intelligence, "open_file", open_file)
    monkeypatch.setattr(intelligence, "close_file", close_file)
    return fake, opened, closed, ensure_calls


@pytest.mark.asyncio
async def test_diagnostics_file_scope_requires_file_path():
    result = await intelligence.diagnostics.fn(scope="file")

    assert result == {"error": "file_path required when scope='file'"}


@pytest.mark.asyncio
async def test_diagnostics_file_scope_sorts_paginates_and_closes(
    tool_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness: tuple[FakeIntelligenceClient, list[str], list[str], list[str | None]],
):
    _fake, opened, closed, ensure_calls = harness

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

    result = await intelligence.diagnostics.fn("src/main.ts", limit=1, offset=0)

    assert result["totalItems"] == 2
    assert result["items"] == [
        {
            "severity": 1,
            "range": {"start": {"line": 1, "character": 0}},
            "message": "err",
            "offset": 0,
        }
    ]
    assert result["hasMore"] is True
    assert ensure_calls == ["src/main.ts"]
    assert opened == closed


@pytest.mark.asyncio
async def test_diagnostics_closes_file_when_wait_fails(
    tool_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness: tuple[FakeIntelligenceClient, list[str], list[str], list[str | None]],
):
    _fake, opened, closed, _ensure_calls = harness

    async def wait(uri: str) -> list[dict[str, Any]]:
        raise RuntimeError("diagnostics failed")

    monkeypatch.setattr(intelligence, "wait_for_diagnostics", wait)

    with pytest.raises(RuntimeError, match="diagnostics failed"):
        await intelligence.diagnostics.fn("src/main.ts")

    assert opened == closed


@pytest.mark.asyncio
async def test_diagnostics_workspace_scope_uses_cached_diagnostics():
    server.current_diagnostics.clear()
    server.current_diagnostics["file:///b.ts"] = [
        {
            "severity": 2,
            "range": {"start": {"line": 2, "character": 0}},
            "message": "warn",
        }
    ]
    server.current_diagnostics["file:///a.ts"] = [
        {
            "severity": 1,
            "range": {"start": {"line": 1, "character": 0}},
            "message": "err",
        }
    ]

    try:
        result = await intelligence.diagnostics.fn(scope="workspace")

        assert [item["uri"] for item in result["items"]] == [
            "file:///a.ts",
            "file:///b.ts",
        ]
    finally:
        server.current_diagnostics.clear()


@pytest.mark.asyncio
async def test_rename_returns_error_when_prepare_rename_rejects(
    tool_project: Path,
    harness: tuple[FakeIntelligenceClient, list[str], list[str], list[str | None]],
):
    fake, opened, closed, _ensure_calls = harness
    fake.responses = [None]

    result = await intelligence.rename.fn(
        "src/main.ts",
        line=0,
        character=6,
        new_name="renamed",
    )

    assert result == {"error": "Symbol cannot be renamed"}
    assert [call[0] for call in fake.calls] == ["textDocument/prepareRename"]
    assert opened == closed


@pytest.mark.asyncio
async def test_rename_continues_when_prepare_rename_is_unsupported(
    tool_project: Path,
    harness: tuple[FakeIntelligenceClient, list[str], list[str], list[str | None]],
):
    fake, opened, closed, _ensure_calls = harness
    edit = {"changes": {"file:///project/src/main.ts": []}}
    fake.responses = [RuntimeError("unsupported"), edit]

    result = await intelligence.rename.fn(
        "src/main.ts",
        line=0,
        character=6,
        new_name="renamed",
    )

    assert result == edit
    assert [call[0] for call in fake.calls] == [
        "textDocument/prepareRename",
        "textDocument/rename",
    ]
    assert opened == closed


@pytest.mark.parametrize(
    ("rename_result", "expected"),
    [
        (None, {"error": "Rename failed", "changes": {}}),
        ([], {"error": "Rename failed", "changes": {}}),
        ("bad", {"error": "Rename failed"}),
    ],
)
@pytest.mark.asyncio
async def test_rename_normalizes_failed_results(
    tool_project: Path,
    harness: tuple[FakeIntelligenceClient, list[str], list[str], list[str | None]],
    rename_result: Any,
    expected: dict[str, Any],
):
    fake, opened, closed, _ensure_calls = harness
    fake.responses = [{"range": {}}, rename_result]

    result = await intelligence.rename.fn(
        "src/main.ts",
        line=0,
        character=6,
        new_name="renamed",
    )

    assert result == expected
    assert opened == closed
