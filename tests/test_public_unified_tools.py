"""Public unified check/fix tool behavior tests."""

from pathlib import Path
from typing import Any

import pytest

from jons_mcp_typescript.tools import unified


class UnifiedDaemon:
    """Fake daemon for check_all and fix_all tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, bool | None]] = []
        self.check_result: dict[str, Any] = {"isFormatted": True}
        self.lint_result: dict[str, Any] = {"messages": []}
        self.format_result: dict[str, Any] = {"formatted": None}
        self.lint_error: Exception | None = None

    async def check_formatting(self, filepath: str, content: str) -> dict[str, Any]:
        self.calls.append(("check_formatting", filepath, content, None))
        return self.check_result

    async def lint(
        self, filepath: str, content: str, fix: bool = False
    ) -> dict[str, Any]:
        self.calls.append(("lint", filepath, content, fix))
        if self.lint_error:
            raise self.lint_error
        return self.lint_result

    async def format(self, filepath: str, content: str) -> dict[str, Any]:
        self.calls.append(("format", filepath, content, None))
        return self.format_result


@pytest.fixture
def daemon(monkeypatch: pytest.MonkeyPatch) -> UnifiedDaemon:
    fake = UnifiedDaemon()
    monkeypatch.setattr(unified, "get_daemon", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_check_all_prettier_only_reports_formatting_failure(
    tool_project: Path,
    daemon: UnifiedDaemon,
):
    daemon.check_result = {"isFormatted": False}

    result = await unified.check_all(
        "src/main.ts",
        include_eslint=False,
        include_typescript=False,
    )

    assert result["checks"] == {
        "prettier": {"passed": False, "message": "Needs formatting"}
    }
    assert result["overallPassed"] is False
    assert result["summary"] == "prettier: failed"


@pytest.mark.asyncio
async def test_check_all_eslint_only_counts_string_severities(
    tool_project: Path, daemon: UnifiedDaemon
):
    daemon.lint_result = {
        "messages": [
            {"severity": "error", "message": "bad"},
            {"severity": "warning", "message": "heads up"},
        ]
    }

    result = await unified.check_all(
        "src/main.ts",
        include_prettier=False,
        include_typescript=False,
    )

    assert result["checks"]["eslint"]["passed"] is False
    assert result["checks"]["eslint"]["errorCount"] == 1
    assert result["checks"]["eslint"]["warningCount"] == 1
    assert result["overallPassed"] is False


@pytest.mark.asyncio
async def test_check_all_normalizes_daemon_exceptions(
    tool_project: Path, daemon: UnifiedDaemon
):
    daemon.lint_error = RuntimeError("eslint exploded")

    result = await unified.check_all(
        "src/main.ts",
        include_prettier=False,
        include_typescript=False,
    )

    assert result["checks"]["eslint"] == {
        "passed": False,
        "error": "eslint exploded",
    }
    assert result["summary"] == "eslint: failed"


@pytest.mark.asyncio
async def test_check_all_typescript_branch_uses_fresh_diagnostics(
    tool_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    daemon: UnifiedDaemon,
):
    events: list[str] = []
    diagnostics = [
        {
            "severity": 2,
            "message": "warn",
            "range": {"start": {"line": 2, "character": 0}},
        },
        {
            "severity": 1,
            "message": "err",
            "range": {"start": {"line": 0, "character": 4}},
        },
    ]

    async def ensure(file_path: str) -> object:
        events.append(f"ensure:{file_path}")
        return object()

    async def open_file(client: object, path: Path, uri: str) -> None:
        events.append(f"open:{path.name}:{uri.startswith('file://')}")

    async def wait(uri: str) -> list[dict[str, Any]]:
        events.append(f"wait:{uri.startswith('file://')}")
        return diagnostics

    async def close_file(client: object, uri: str) -> None:
        events.append(f"close:{uri.startswith('file://')}")

    monkeypatch.setattr(unified, "ensure_vtsls_indexed", ensure)
    monkeypatch.setattr(unified, "open_file", open_file)
    monkeypatch.setattr(unified, "wait_for_diagnostics", wait)
    monkeypatch.setattr(unified, "close_file", close_file)

    result = await unified.check_all(
        "src/main.ts",
        include_prettier=False,
        include_eslint=False,
        include_typescript=True,
    )

    assert result["checks"]["typescript"]["passed"] is False
    assert result["checks"]["typescript"]["errorCount"] == 1
    assert result["checks"]["typescript"]["warningCount"] == 1
    assert result["checks"]["typescript"]["diagnostics"] == [
        {
            "severity": 2,
            "message": "warn",
            "range": {"start": {"line": 3, "character": 1}},
        },
        {
            "severity": 1,
            "message": "err",
            "range": {"start": {"line": 1, "character": 5}},
        },
    ]
    assert events == [
        "ensure:src/main.ts",
        "open:main.ts:True",
        "wait:True",
        "close:True",
    ]


@pytest.mark.asyncio
async def test_fix_all_runs_eslint_then_prettier_without_writing(
    tool_project: Path,
    daemon: UnifiedDaemon,
):
    source = tool_project / "src" / "main.ts"
    source.write_text("const value=1;\n", encoding="utf-8")
    daemon.lint_result = {
        "messages": [
            {"severity": "error", "message": "bad"},
            {"severity": "warning", "message": "heads up"},
        ],
        "fixedContent": "const value = 1;\n",
    }
    daemon.format_result = {"formatted": "const value = 1;\n"}

    result = await unified.fix_all("src/main.ts", write=False)

    assert result["finalCode"] == "const value = 1;\n"
    assert result["totalChanges"] == 2
    assert result["written"] is False
    assert source.read_text(encoding="utf-8") == "const value=1;\n"
    assert [call[0] for call in daemon.calls] == ["lint", "format"]
    assert daemon.calls[1][2] == "const value = 1;\n"


@pytest.mark.asyncio
async def test_fix_all_writes_changed_content(tool_project: Path, daemon: UnifiedDaemon):
    source = tool_project / "src" / "main.ts"
    source.write_text("const value=1;\n", encoding="utf-8")
    daemon.lint_result = {"messages": [], "fixedContent": None}
    daemon.format_result = {"formatted": "const value = 1;\n"}

    result = await unified.fix_all(
        "src/main.ts",
        write=True,
        include_eslint=False,
    )

    assert result["fixes"] == {"prettier": {"applied": True}}
    assert result["totalChanges"] == 1
    assert result["written"] is True
    assert source.read_text(encoding="utf-8") == "const value = 1;\n"


@pytest.mark.asyncio
async def test_fix_all_respects_include_flags_and_unchanged_content(
    tool_project: Path,
    daemon: UnifiedDaemon,
):
    source = tool_project / "src" / "main.ts"
    source.write_text("const value = 1;\n", encoding="utf-8")
    daemon.format_result = {"formatted": "const value = 1;\n"}

    result = await unified.fix_all(
        "src/main.ts",
        write=True,
        include_eslint=False,
    )

    assert result["fixes"] == {"prettier": {"applied": False}}
    assert result["totalChanges"] == 0
    assert result["written"] is False
    assert [call[0] for call in daemon.calls] == ["format"]
