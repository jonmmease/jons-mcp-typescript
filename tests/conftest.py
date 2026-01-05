"""Pytest configuration and fixtures for the TypeScript MCP server tests."""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

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
def temp_project_with_tsconfig(temp_project: Path) -> Path:
    """Create a temporary project with tsconfig.json.

    Args:
        temp_project: The temporary project directory.

    Returns:
        Path to the project root.
    """
    tsconfig = {
        "compilerOptions": {
            "target": "ES2020",
            "module": "commonjs",
            "strict": True,
            "esModuleInterop": True,
        },
        "include": ["src/**/*"],
        "exclude": ["node_modules", "dist"],
    }
    (temp_project / "tsconfig.json").write_text(json.dumps(tsconfig, indent=2))
    return temp_project


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
def mock_daemon_process() -> MagicMock:
    """Create a mock daemon subprocess.

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
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for async tests.

    Yields:
        asyncio.AbstractEventLoop instance.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture
def async_mock_stdin(mock_vtsls_process: MagicMock) -> MagicMock:
    """Configure mock stdin for LSP communication.

    Args:
        mock_vtsls_process: The mock process.

    Returns:
        Configured stdin mock.
    """
    stdin = AsyncMock()
    stdin.write = AsyncMock()
    stdin.flush = AsyncMock()
    mock_vtsls_process.stdin = stdin
    return stdin


@pytest.fixture
def async_mock_stdout(mock_vtsls_process: MagicMock) -> AsyncMock:
    """Configure mock stdout for LSP communication.

    Args:
        mock_vtsls_process: The mock process.

    Returns:
        Configured stdout mock.
    """
    stdout = AsyncMock()
    stdout.read = AsyncMock()
    mock_vtsls_process.stdout = stdout
    return stdout


@pytest.fixture
def sample_lsp_initialize_response() -> dict:
    """Create a sample LSP initialize response.

    Returns:
        Dictionary with vtsls initialization response.
    """
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "capabilities": {
                "textDocumentSync": 1,
                "completionProvider": {
                    "resolveProvider": True,
                    "triggerCharacters": ["."],
                },
                "hoverProvider": True,
                "definitionProvider": True,
                "implementationProvider": True,
                "referencesProvider": True,
                "renameProvider": {"prepareProvider": True},
                "codeActionProvider": True,
                "workspaceSymbolProvider": True,
                "documentSymbolProvider": True,
            },
            "serverInfo": {
                "name": "vtsls",
                "version": "0.2.0",
            },
        },
    }


@pytest.fixture
def sample_workspace_symbols() -> list[dict]:
    """Create sample workspace symbols response.

    Returns:
        List of workspace symbol information.
    """
    return [
        {
            "name": "MyClass",
            "kind": 5,  # Class
            "location": {
                "uri": "file:///workspace/src/index.ts",
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 10, "character": 0},
                },
            },
        },
        {
            "name": "myFunction",
            "kind": 12,  # Function
            "location": {
                "uri": "file:///workspace/src/utils.ts",
                "range": {
                    "start": {"line": 5, "character": 0},
                    "end": {"line": 15, "character": 0},
                },
            },
        },
    ]


@pytest.fixture
def sample_document_symbols() -> list[dict]:
    """Create sample document symbols response.

    Returns:
        List of document symbol information.
    """
    return [
        {
            "name": "MyInterface",
            "kind": 11,  # Interface
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 5, "character": 1},
            },
            "selectionRange": {
                "start": {"line": 0, "character": 10},
                "end": {"line": 0, "character": 21},
            },
        },
        {
            "name": "myMethod",
            "kind": 6,  # Method
            "range": {
                "start": {"line": 1, "character": 2},
                "end": {"line": 3, "character": 3},
            },
            "selectionRange": {
                "start": {"line": 1, "character": 2},
                "end": {"line": 1, "character": 10},
            },
        },
    ]


@pytest.fixture
def sample_references() -> list[dict]:
    """Create sample references response.

    Returns:
        List of reference locations.
    """
    return [
        {
            "uri": "file:///workspace/src/index.ts",
            "range": {
                "start": {"line": 10, "character": 5},
                "end": {"line": 10, "character": 15},
            },
        },
        {
            "uri": "file:///workspace/src/app.ts",
            "range": {
                "start": {"line": 3, "character": 2},
                "end": {"line": 3, "character": 12},
            },
        },
    ]


@pytest.fixture
def sample_hover_response() -> dict:
    """Create a sample hover response.

    Returns:
        Dictionary with hover information.
    """
    return {
        "contents": {
            "language": "typescript",
            "value": "(function) myFunction(param: string): void",
        },
    }


@pytest.fixture
def sample_diagnostics() -> list[dict]:
    """Create sample diagnostic messages.

    Returns:
        List of diagnostic information.
    """
    return [
        {
            "range": {
                "start": {"line": 5, "character": 10},
                "end": {"line": 5, "character": 15},
            },
            "severity": 1,  # Error
            "code": "TS2322",
            "source": "ts",
            "message": "Type 'string' is not assignable to type 'number'.",
        },
        {
            "range": {
                "start": {"line": 8, "character": 3},
                "end": {"line": 8, "character": 10},
            },
            "severity": 2,  # Warning
            "code": "TS6133",
            "source": "ts",
            "message": "'unusedVar' is declared but its value is never used.",
        },
    ]
