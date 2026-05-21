"""Pytest configuration and fixtures for the TypeScript MCP server tests."""

import tempfile
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jons_mcp_typescript import server


@pytest.fixture
def temp_project() -> Generator[Path, None, None]:
    """Create a temporary project directory with package.json.

    Yields:
        Path to the temporary project root.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        # Create package.json
        (project_root / "package.json").write_text('{"name": "test-project", "version": "1.0.0"}')
        yield project_root


@pytest.fixture
def mock_vtsls_process() -> MagicMock:
    """Create a mock vtsls subprocess.

    Returns:
        MagicMock configured as a subprocess.Popen instance.
    """
    process = MagicMock()
    process.stdin = MagicMock()
    process.stdout = MagicMock()
    process.stderr = MagicMock()
    process.poll = MagicMock(return_value=None)
    process.returncode = None
    return process


@pytest.fixture
def tool_project(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a project root and install it as the active server project."""
    project_root = tmp_path / "project"
    src_dir = project_root / "src"
    src_dir.mkdir(parents=True)
    (project_root / "package.json").write_text(
        '{"name": "tool-project", "version": "1.0.0"}',
        encoding="utf-8",
    )
    (src_dir / "main.ts").write_text("const value = 1;\n", encoding="utf-8")

    server._project_root = project_root
    server.current_diagnostics.clear()
    server.document_states.clear()
    server.pending_diagnostics_events.clear()
    try:
        yield project_root
    finally:
        server._project_root = None
        server.vtsls = None
        server.daemon = None
        server.current_diagnostics.clear()
        server.document_states.clear()
        server.pending_diagnostics_events.clear()
