"""Pytest configuration and fixtures for the TypeScript MCP server tests."""

import tempfile
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest


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
