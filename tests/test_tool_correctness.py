"""Unit tests for tool result normalization."""

import tempfile
from pathlib import Path
from typing import Any

import pytest

from jons_mcp_typescript import server
from jons_mcp_typescript.exceptions import DocumentSyncError
from jons_mcp_typescript.tools import linting, unified
from jons_mcp_typescript.tools.intelligence import restart_server
from jons_mcp_typescript.tools.language import type_info


class FakeDaemon:
    async def lint(
        self, filepath: str, content: str, fix: bool = False
    ) -> dict[str, Any]:
        return {
            "messages": [
                {"severity": "error", "message": "bad"},
                {"severity": "warning", "message": "heads up"},
            ],
            "fixed": False,
            "fixedContent": None,
        }

    async def check_formatting(self, filepath: str, content: str) -> dict[str, Any]:
        return {"isFormatted": True}


class Restartable:
    def __init__(self) -> None:
        self.restart_count = 0

    async def restart(self) -> None:
        self.restart_count += 1


class FakeVtslsLifecycle:
    instances: list["FakeVtslsLifecycle"] = []

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.shutdown_called = False
        FakeVtslsLifecycle.instances.append(self)

    def on_notification(self, method: str, handler: Any) -> None:
        return None

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        self.shutdown_called = True


class FailingDaemonLifecycle:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    @classmethod
    def create(cls, project_root: Path) -> "FailingDaemonLifecycle":
        return cls(project_root)

    async def start(self) -> None:
        raise RuntimeError("daemon boom")

    async def shutdown(self) -> None:
        return None


class FakeTypeInfoClient:
    def __init__(self) -> None:
        self.documents: dict[str, str] = {}

    def is_initialized(self) -> bool:
        return True

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        if method == "textDocument/didOpen":
            text_document = params["textDocument"]
            self.documents[text_document["uri"]] = text_document["text"]
        elif method == "textDocument/didChange":
            uri = params["textDocument"]["uri"]
            self.documents[uri] = params["contentChanges"][0]["text"]

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        if method == "textDocument/hover":
            return {"contents": {"value": "User"}}
        if method == "textDocument/typeDefinition":
            return None
        if method == "textDocument/completion":
            return {
                "items": [
                    {"kind": 10, "label": "name", "detail": "string"},
                    {"kind": 2, "label": "greet", "detail": "() => void"},
                ]
            }
        raise AssertionError(f"unexpected request: {method}")


@pytest.fixture
def project_file():
    with tempfile.TemporaryDirectory() as project_tmp:
        project_root = Path(project_tmp)
        source_file = project_root / "src" / "main.ts"
        source_file.parent.mkdir()
        source_file.write_text("const value = 1;")
        server._project_root = project_root
        try:
            yield source_file
        finally:
            server._project_root = None


@pytest.mark.asyncio
async def test_lint_code_counts_string_severities(monkeypatch, project_file):
    monkeypatch.setattr(linting, "get_daemon", lambda: FakeDaemon())

    result = await linting.lint_code(str(project_file))

    assert result["errors"] == 1
    assert result["warnings"] == 1


@pytest.mark.asyncio
async def test_check_all_fails_on_eslint_error(monkeypatch, project_file):
    monkeypatch.setattr(unified, "get_daemon", lambda: FakeDaemon())

    result = await unified.check_all(str(project_file), include_typescript=False)

    assert result["checks"]["eslint"]["passed"] is False
    assert result["overallPassed"] is False


@pytest.mark.asyncio
async def test_restart_server_restarts_language_server_and_daemon(monkeypatch):
    fake_vtsls = Restartable()
    fake_daemon = Restartable()
    server.vtsls = fake_vtsls  # type: ignore[assignment]
    server.current_diagnostics["file:///x.ts"] = []
    server.pending_diagnostics_events["file:///x.ts"] = object()  # type: ignore[assignment]
    try:
        monkeypatch.setattr(
            "jons_mcp_typescript.tools.intelligence.get_daemon",
            lambda: fake_daemon,
        )

        result = await restart_server()

        assert fake_vtsls.restart_count == 1
        assert fake_daemon.restart_count == 1
        assert server.current_diagnostics == {}
        assert server.pending_diagnostics_events == {}
        assert "restarted successfully" in result
    finally:
        server.vtsls = None
        server.current_diagnostics.clear()
        server.pending_diagnostics_events.clear()


@pytest.mark.asyncio
async def test_lifespan_shuts_down_vtsls_when_daemon_start_fails(monkeypatch):
    with tempfile.TemporaryDirectory() as project_tmp:
        server._project_root = Path(project_tmp)
        FakeVtslsLifecycle.instances = []
        monkeypatch.setattr(server, "VtslsClient", FakeVtslsLifecycle)
        monkeypatch.setattr(server, "FormatterLinterDaemon", FailingDaemonLifecycle)
        try:
            with pytest.raises(RuntimeError, match="daemon boom"):
                async with server.lifespan(None):  # type: ignore[arg-type]
                    pass

            assert FakeVtslsLifecycle.instances[0].shutdown_called is True
        finally:
            server._project_root = None
            server.vtsls = None
            server.daemon = None


@pytest.mark.asyncio
async def test_open_file_raises_when_disk_sync_fails(tmp_path):
    missing_file = tmp_path / "missing.ts"

    with pytest.raises(DocumentSyncError, match="Failed to sync"):
        await server.open_file(
            FakeTypeInfoClient(),
            missing_file,
            missing_file.as_uri(),
        )


@pytest.mark.asyncio
async def test_type_info_uses_temporary_dot_completion():
    with tempfile.TemporaryDirectory() as project_tmp:
        project_root = Path(project_tmp)
        source_file = project_root / "src" / "main.ts"
        source_file.parent.mkdir()
        source_file.write_text(
            'const user = { name: "Ada", greet() {} };\nuser;\n',
            encoding="utf-8",
        )
        fake_client = FakeTypeInfoClient()
        server._project_root = project_root
        server.vtsls = fake_client  # type: ignore[assignment]
        try:
            result = await type_info("src/main.ts", line=1, character=1)

            assert result["typeName"] == "User"
            assert result["fields"] == [{"name": "name", "type": "string"}]
            assert result["methods"]["items"] == [
                {"name": "greet", "signature": "() => void"}
            ]
        finally:
            server._project_root = None
            server.vtsls = None
