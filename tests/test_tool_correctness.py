"""Unit tests for tool result normalization."""

import tempfile
from pathlib import Path
from typing import Any

import pytest

from jons_mcp_typescript import server
from jons_mcp_typescript.exceptions import DocumentSyncError, ProjectLoadError
from jons_mcp_typescript.tools import linting, unified
from jons_mcp_typescript.tools.intelligence import restart_server
from jons_mcp_typescript.tools.language import type_info_of_reference


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
        if method == "workspace/executeCommand":
            request_name = params["arguments"][0]
            filepath = params["arguments"][1]["file"]
            if request_name == "quickinfo":
                return {
                    "success": True,
                    "body": {
                        "displayString": (
                            "const user: { name: string; greet(): void; }"
                        ),
                        "kind": "const",
                    },
                }
            return {
                "success": True,
                "body": {
                    "configFileName": "/project/tsconfig.json",
                    "languageServiceDisabled": False,
                    "fileNames": [filepath],
                },
            }
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


class FakeProjectInfoClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((method, params))
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response


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

    assert result.errors == 1
    assert result.warnings == 1


@pytest.mark.asyncio
async def test_check_all_fails_on_eslint_error(monkeypatch, project_file):
    monkeypatch.setattr(unified, "get_daemon", lambda: FakeDaemon())

    result = await unified.check_all(str(project_file), include_typescript=False)

    result_dict = result.model_dump(exclude_none=True)
    assert result_dict["checks"]["eslint"]["passed"] is False
    assert result_dict["overallPassed"] is False


@pytest.mark.asyncio
async def test_restart_server_restarts_language_server_and_daemon(monkeypatch):
    fake_vtsls = Restartable()
    fake_daemon = Restartable()
    preload_calls = []
    server.vtsls = fake_vtsls  # type: ignore[assignment]
    server.current_diagnostics["file:///x.ts"] = []
    server.pending_diagnostics_events["file:///x.ts"] = object()  # type: ignore[assignment]
    server.loaded_project_configs.add("/project/tsconfig.json")
    server.project_file_configs["/project/src/main.ts"] = "/project/tsconfig.json"
    try:
        monkeypatch.setattr(
            "jons_mcp_typescript.tools.intelligence.get_daemon",
            lambda: fake_daemon,
        )

        async def preload(client):
            preload_calls.append(client)
            return None

        monkeypatch.setattr(server, "preload_workspace_projects", preload)

        result = await restart_server()

        assert fake_vtsls.restart_count == 1
        assert fake_daemon.restart_count == 1
        assert preload_calls == [fake_vtsls]
        assert server.current_diagnostics == {}
        assert server.pending_diagnostics_events == {}
        assert server.loaded_project_configs == set()
        assert server.project_file_configs == {}
        assert "restarted successfully" in result
    finally:
        server.vtsls = None
        server.current_diagnostics.clear()
        server.pending_diagnostics_events.clear()
        server.clear_project_load_cache()


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
            server.clear_project_load_cache()
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
async def test_ensure_project_loaded_requests_project_info_and_caches(tmp_path):
    source = tmp_path / "src" / "main.ts"
    sibling = tmp_path / "src" / "other.ts"
    source.parent.mkdir()
    source.write_text("export const value = 1;", encoding="utf-8")
    sibling.write_text("export const other = 2;", encoding="utf-8")
    config = tmp_path / "tsconfig.json"
    config.write_text("{}", encoding="utf-8")
    client = FakeProjectInfoClient(
        {
            "success": True,
            "body": {
                "configFileName": str(config),
                "languageServiceDisabled": False,
                "fileNames": [str(source), str(sibling)],
            },
        }
    )

    server.clear_project_load_cache()
    try:
        first_config = await server.ensure_project_loaded(
            client, source  # type: ignore[arg-type]
        )
        second_config = await server.ensure_project_loaded(
            client, sibling  # type: ignore[arg-type]
        )

        assert client.calls == [
            (
                "workspace/executeCommand",
                {
                    "command": "typescript.tsserverRequest",
                    "arguments": [
                        "projectInfo",
                        {"file": str(source.resolve()), "needFileNameList": True},
                    ],
                },
            )
        ]
        assert first_config == str(config)
        assert second_config == str(config)
        assert server.loaded_project_configs == {str(config)}
        assert server.project_file_configs[str(source.resolve())] == str(config)
        assert server.project_file_configs[str(sibling.resolve())] == str(config)
    finally:
        server.clear_project_load_cache()


@pytest.mark.asyncio
async def test_ensure_project_loaded_preserves_original_file_config(tmp_path):
    common_source = tmp_path / "packages" / "common" / "src" / "main.ts"
    server_source = tmp_path / "packages" / "server" / "src" / "main.ts"
    common_source.parent.mkdir(parents=True)
    server_source.parent.mkdir(parents=True)
    common_source.write_text("export const value = 1;", encoding="utf-8")
    server_source.write_text("export const serverValue = 2;", encoding="utf-8")
    common_config = tmp_path / "packages" / "common" / "tsconfig.json"
    server_config = tmp_path / "packages" / "server" / "tsconfig.json"
    common_config.write_text("{}", encoding="utf-8")
    server_config.write_text("{}", encoding="utf-8")

    client = FakeProjectInfoClient(
        [
            {
                "success": True,
                "body": {
                    "configFileName": str(common_config),
                    "languageServiceDisabled": False,
                    "fileNames": [str(common_source)],
                },
            },
            {
                "success": True,
                "body": {
                    "configFileName": str(server_config),
                    "languageServiceDisabled": False,
                    "fileNames": [str(server_source), str(common_source)],
                },
            },
        ]
    )

    server.clear_project_load_cache()
    try:
        common_key = await server.ensure_project_loaded(
            client, common_source  # type: ignore[arg-type]
        )
        server_key = await server.ensure_project_loaded(
            client, server_source  # type: ignore[arg-type]
        )

        assert common_key == str(common_config)
        assert server_key == str(server_config)
        assert server.project_file_configs[str(common_source.resolve())] == str(
            common_config
        )
        assert server.project_file_configs[str(server_source.resolve())] == str(
            server_config
        )
    finally:
        server.clear_project_load_cache()


@pytest.mark.asyncio
async def test_ensure_project_loaded_rejects_bad_project_info(tmp_path):
    source = tmp_path / "src" / "main.ts"
    source.parent.mkdir()
    source.write_text("export const value = 1;", encoding="utf-8")
    client = FakeProjectInfoClient({"success": False})

    server.clear_project_load_cache()
    try:
        with pytest.raises(ProjectLoadError, match="invalid projectInfo response"):
            await server.ensure_project_loaded(client, source)  # type: ignore[arg-type]
    finally:
        server.clear_project_load_cache()


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
            result = await type_info_of_reference("src/main.ts", line=2, character=2)

            result_dict = result.model_dump(exclude_none=True)
            assert result_dict["displayString"] == (
                "const user: { name: string; greet(): void; }"
            )
            assert result_dict["kind"] == "const"
            assert result_dict["fields"] == [{"name": "name", "type": "string"}]
            assert result_dict["methods"]["items"] == [
                {"name": "greet", "signature": "() => void"}
            ]
        finally:
            server._project_root = None
            server.vtsls = None
