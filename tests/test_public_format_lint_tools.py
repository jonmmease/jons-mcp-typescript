"""Public formatting and linting tool behavior tests."""

from pathlib import Path
from typing import Any

import pytest

from jons_mcp_typescript.exceptions import (
    ESLintConfigError,
    ESLintPluginError,
    PrettierConfigError,
    PrettierParseError,
)
from jons_mcp_typescript.tools import formatting, linting


class RecordingDaemon:
    """Configurable fake formatter/linter daemon."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.format_result: dict[str, Any] = {"formatted": "formatted\n"}
        self.check_result: dict[str, Any] = {"isFormatted": True}
        self.prettier_config_result: dict[str, Any] = {"config": {}}
        self.lint_result: dict[str, Any] = {"messages": []}
        self.eslint_config_result: dict[str, Any] = {"config": {}}
        self.error: Exception | None = None

    def _maybe_raise(self) -> None:
        if self.error:
            raise self.error

    async def format(self, filepath: str, content: str) -> dict[str, Any]:
        self.calls.append(("format", (filepath, content)))
        self._maybe_raise()
        return self.format_result

    async def check_formatting(self, filepath: str, content: str) -> dict[str, Any]:
        self.calls.append(("check_formatting", (filepath, content)))
        self._maybe_raise()
        return self.check_result

    async def get_prettier_config(self, filepath: str) -> dict[str, Any]:
        self.calls.append(("get_prettier_config", (filepath,)))
        self._maybe_raise()
        return self.prettier_config_result

    async def lint(
        self, filepath: str, content: str, fix: bool = False
    ) -> dict[str, Any]:
        self.calls.append(("lint", (filepath, content, fix)))
        self._maybe_raise()
        return self.lint_result

    async def get_eslint_config(self, filepath: str) -> dict[str, Any]:
        self.calls.append(("get_eslint_config", (filepath,)))
        self._maybe_raise()
        return self.eslint_config_result


@pytest.fixture
def daemon(monkeypatch: pytest.MonkeyPatch) -> RecordingDaemon:
    fake = RecordingDaemon()
    monkeypatch.setattr(formatting, "get_daemon", lambda: fake)
    monkeypatch.setattr(linting, "get_daemon", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_format_code_reads_file_and_normalizes_result(
    tool_project: Path, daemon: RecordingDaemon
):
    source = tool_project / "src" / "main.ts"
    source.write_text("const value=1;\n", encoding="utf-8")
    daemon.format_result = {"formatted": "const value = 1;\n"}

    result = await formatting.format_code("src/main.ts")

    assert result == {
        "formatted": True,
        "code": "const value = 1;\n",
        "changed": True,
    }
    assert daemon.calls == [("format", (str(source.resolve()), "const value=1;\n"))]


@pytest.mark.asyncio
async def test_format_code_accepts_explicit_empty_code_for_missing_file(
    tool_project: Path, daemon: RecordingDaemon
):
    target = tool_project / "src" / "generated.ts"
    daemon.format_result = {"formatted": ""}

    result = await formatting.format_code("src/generated.ts", code="")

    assert result["code"] == ""
    assert result["changed"] is False
    assert daemon.calls == [("format", (str(target.resolve()), ""))]


@pytest.mark.asyncio
async def test_check_formatting_returns_status_message(
    tool_project: Path, daemon: RecordingDaemon
):
    source = tool_project / "src" / "main.ts"
    daemon.check_result = {"isFormatted": False}

    result = await formatting.check_formatting("src/main.ts", code="let x=1;")

    assert result == {"formatted": False, "message": "Code needs formatting"}
    assert daemon.calls == [("check_formatting", (str(source.resolve()), "let x=1;"))]


@pytest.mark.asyncio
async def test_get_prettier_config_includes_config_file(daemon: RecordingDaemon):
    daemon.prettier_config_result = {
        "config": {"singleQuote": True},
        "configPath": "/project/.prettierrc",
    }

    result = await formatting.get_prettier_config("src/main.ts")

    assert result == {"singleQuote": True, "configFile": "/project/.prettierrc"}


@pytest.mark.parametrize(
    ("message", "expected_error"),
    [
        ("Parse error on line 1", PrettierParseError),
        ("Config file is invalid", PrettierConfigError),
    ],
)
@pytest.mark.asyncio
async def test_formatting_tools_map_prettier_errors(
    daemon: RecordingDaemon, message: str, expected_error: type[Exception]
):
    daemon.error = RuntimeError(message)

    with pytest.raises(expected_error):
        await formatting.format_code("src/main.ts", code="bad")


@pytest.mark.asyncio
async def test_lint_code_reads_file_and_counts_string_severities(
    tool_project: Path, daemon: RecordingDaemon
):
    source = tool_project / "src" / "main.ts"
    source.write_text("const unused = 1;\n", encoding="utf-8")
    daemon.lint_result = {
        "messages": [
            {"severity": "error", "message": "bad"},
            {"severity": "warning", "message": "heads up"},
        ],
        "fixed": True,
        "fixedContent": "const used = 1;\n",
    }

    result = await linting.lint_code("src/main.ts", fix=True)

    assert result["totalIssues"] == 2
    assert result["errors"] == 1
    assert result["warnings"] == 1
    assert result["fixed"] is True
    assert result["fixedCode"] == "const used = 1;\n"
    assert daemon.calls == [("lint", (str(source.resolve()), "const unused = 1;\n", True))]


@pytest.mark.asyncio
async def test_lint_code_uses_daemon_counts_when_present(daemon: RecordingDaemon):
    daemon.lint_result = {
        "messages": [{"severity": "warning", "message": "heads up"}],
        "errorCount": 3,
        "warningCount": 4,
        "fixed": False,
        "fixedContent": "ignored",
    }

    result = await linting.lint_code("src/new.ts", code="const x = 1;")

    assert result["errors"] == 3
    assert result["warnings"] == 4
    assert result["fixedCode"] is None


@pytest.mark.asyncio
async def test_get_eslint_config_returns_dict_only(daemon: RecordingDaemon):
    daemon.eslint_config_result = {"config": ["not", "a", "dict"]}

    result = await linting.get_eslint_config("src/main.ts")

    assert result == {}


@pytest.mark.parametrize(
    ("message", "expected_error"),
    [
        ("Plugin @typescript-eslint failed", ESLintPluginError),
        ("Config file is invalid", ESLintConfigError),
    ],
)
@pytest.mark.asyncio
async def test_linting_tools_map_eslint_errors(
    daemon: RecordingDaemon, message: str, expected_error: type[Exception]
):
    daemon.error = RuntimeError(message)

    with pytest.raises(expected_error):
        await linting.lint_code("src/main.ts", code="bad")
