"""Integration tests for the TypeScript MCP server.

These tests exercise the full workflow with real TypeScript projects,
including vtsls, Prettier, and ESLint integration.

Tests require:
- Node.js installed
- vtsls installed (npm install -g @vtsls/language-server)
- Prettier installed in the target project
- ESLint installed in the target project

Tests are marked with appropriate skip conditions if dependencies are missing.
"""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from jons_mcp_typescript import server as server_state
from jons_mcp_typescript.daemon_client import FormatterLinterDaemon
from jons_mcp_typescript.exceptions import (
    DaemonError,
    ProjectLoadError,
    VtslsNotFoundError,
)
from jons_mcp_typescript.lsp_client import VtslsClient
from jons_mcp_typescript.server import (
    clear_diagnostics_for_uri,
    close_file,
    compute_content_hash,
    current_diagnostics,
    document_states,
    open_file,
    pending_diagnostics_events,
    register_diagnostics_event,
    wait_for_diagnostics,
)
from jons_mcp_typescript.tools import intelligence, language
from jons_mcp_typescript.utils import path_from_file_uri

# =============================================================================
# Helpers to check for dependencies
# =============================================================================


def is_node_available() -> bool:
    """Check if Node.js is available."""
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def is_vtsls_available() -> bool:
    """Check if vtsls is available."""
    try:
        # Check environment variable
        if os.environ.get("VTSLS_PATH"):
            return Path(os.environ["VTSLS_PATH"]).exists()

        # Check PATH
        if shutil.which("vtsls"):
            return True

        # Check npm global installation
        result = subprocess.run(
            ["npm", "prefix", "-g"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            npm_prefix = result.stdout.strip()
            vtsls_path = (
                Path(npm_prefix)
                / "lib"
                / "node_modules"
                / "@vtsls"
                / "language-server"
                / "bin"
                / "vtsls.js"
            )
            if vtsls_path.exists():
                return True

            # Also check without 'lib'
            vtsls_path = (
                Path(npm_prefix)
                / "node_modules"
                / "@vtsls"
                / "language-server"
                / "bin"
                / "vtsls.js"
            )
            return vtsls_path.exists()
        return False
    except Exception:
        return False


def is_prettier_available() -> bool:
    """Check if Prettier is available in daemon node_modules."""
    daemon_dir = Path(__file__).parent.parent / "src" / "jons_mcp_typescript" / "daemon"
    return (daemon_dir / "node_modules" / "prettier").exists()


def is_eslint_available() -> bool:
    """Check if ESLint is available in daemon node_modules."""
    daemon_dir = Path(__file__).parent.parent / "src" / "jons_mcp_typescript" / "daemon"
    return (daemon_dir / "node_modules" / "eslint").exists()


NODE_AVAILABLE = is_node_available()
VTSLS_AVAILABLE = is_vtsls_available()
PRETTIER_AVAILABLE = is_prettier_available()
ESLINT_AVAILABLE = is_eslint_available()


DAEMON_DIR = Path(__file__).parent.parent / "src" / "jons_mcp_typescript" / "daemon"


def start_raw_daemon(project_root: Path) -> subprocess.Popen:
    """Start the daemon directly so tests can inspect raw JSON-lines output."""
    return subprocess.Popen(
        ["node", str(DAEMON_DIR / "index.js")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(project_root),
    )


def read_json_line(process: subprocess.Popen) -> dict:
    """Read one daemon stdout line and assert it is valid JSON."""
    assert process.stdout is not None
    line = process.stdout.readline()
    assert line
    return json.loads(line)


def write_daemon_line(process: subprocess.Popen, line: str) -> None:
    """Write one raw JSON-lines protocol line to daemon stdin."""
    assert process.stdin is not None
    process.stdin.write(line + "\n")
    process.stdin.flush()


def stop_raw_daemon(process: subprocess.Popen) -> None:
    """Terminate a raw daemon process created by start_raw_daemon."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def link_project_node_modules(project_root: Path) -> None:
    """Give a temp project local Node deps by linking the daemon dev install."""
    source = DAEMON_DIR / "node_modules"
    target = project_root / "node_modules"
    if source.exists() and not target.exists():
        target.symlink_to(source, target_is_directory=True)


def position_of(text: str, needle: str, occurrence: int = 1) -> dict[str, int]:
    """Return a one-based public tool position for the requested occurrence."""
    start = -1
    cursor = 0
    for _ in range(occurrence):
        start = text.index(needle, cursor)
        cursor = start + len(needle)
    return {
        "line": text.count("\n", 0, start) + 1,
        "character": start - (text.rfind("\n", 0, start) + 1) + 1,
    }


def location_basenames(result: object) -> set[str]:
    """Extract file basenames from normalized locations or raw LSP locations."""
    if hasattr(result, "model_dump"):
        result = result.model_dump(exclude_none=True)

    if isinstance(result, dict) and isinstance(result.get("items"), list):
        items = result["items"]
    elif isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        items = [result]
    else:
        items = []

    basenames = set()
    for item in items:
        if isinstance(item, dict):
            uri = item.get("targetUri") or item.get("uri")
            if isinstance(uri, str):
                basenames.add(Path(uri.removeprefix("file://")).name)
    return basenames


def location_project_paths(result: object, project_root: Path) -> set[str]:
    """Extract project-relative paths from normalized locations or raw LSP locations."""
    project_root = project_root.resolve(strict=False)
    if hasattr(result, "model_dump"):
        result = result.model_dump(exclude_none=True)

    if isinstance(result, dict) and isinstance(result.get("items"), list):
        items = result["items"]
    elif isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        items = [result]
    else:
        items = []

    paths = set()
    for item in items:
        if isinstance(item, dict):
            uri = item.get("targetUri") or item.get("uri")
            if isinstance(uri, str):
                path = path_from_file_uri(uri).resolve(strict=False)
                try:
                    paths.add(path.relative_to(project_root).as_posix())
                except ValueError:
                    paths.add(path.as_posix())
    return paths


def rename_preview_basenames(preview: object) -> set[str]:
    """Extract file basenames from a normalized rename preview."""
    if hasattr(preview, "model_dump"):
        preview = preview.model_dump()
    if not isinstance(preview, dict):
        return set()

    basenames = set()
    edits = preview.get("edits", [])
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                uri = edit.get("uri")
                if isinstance(uri, str):
                    basenames.add(Path(uri.removeprefix("file://")).name)
    return basenames


def rename_preview_project_paths(preview: object, project_root: Path) -> set[str]:
    """Extract project-relative paths from a normalized rename preview."""
    project_root = project_root.resolve(strict=False)
    if hasattr(preview, "model_dump"):
        preview = preview.model_dump()
    if not isinstance(preview, dict):
        return set()

    paths = set()
    edits = preview.get("edits", [])
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                uri = edit.get("uri")
                if isinstance(uri, str):
                    path = path_from_file_uri(uri).resolve(strict=False)
                    try:
                        paths.add(path.relative_to(project_root).as_posix())
                    except ValueError:
                        paths.add(path.as_posix())
    return paths


def assert_monorepo_paths_include(
    actual: set[str],
    expected: set[str],
    tool_name: str,
) -> None:
    """Assert desired monorepo coverage with observed paths in the failure."""
    missing = expected - actual
    assert not missing, (
        f"{tool_name} did not include all referenced-package files; "
        f"missing={sorted(missing)}, actual={sorted(actual)}"
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def simple_ts_project() -> Generator[Path, None, None]:
    """Create a simple TypeScript project for testing.

    Structure:
        - package.json
        - tsconfig.json
        - src/index.ts (with type error)
        - src/utils.ts (utility module)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)

        # Create package.json
        package_json = {
            "name": "test-ts-project",
            "version": "1.0.0",
            "type": "module",
            "dependencies": {},
        }
        (project_root / "package.json").write_text(json.dumps(package_json, indent=2))

        # Create tsconfig.json
        tsconfig = {
            "compilerOptions": {
                "target": "ES2020",
                "module": "ESNext",
                "moduleResolution": "node",
                "strict": True,
                "esModuleInterop": True,
                "outDir": "./dist",
                "rootDir": "./src",
            },
            "include": ["src/**/*"],
            "exclude": ["node_modules", "dist"],
        }
        (project_root / "tsconfig.json").write_text(json.dumps(tsconfig, indent=2))

        # Create src directory
        src_dir = project_root / "src"
        src_dir.mkdir()

        # Create index.ts with a type error
        index_ts = '''// Main entry point
import { greet } from "./utils";

interface User {
    name: string;
    age: number;
}

const user: User = {
    name: "Alice",
    age: "thirty"  // Type error: string not assignable to number
};

console.log(greet(user.name));
'''
        (src_dir / "index.ts").write_text(index_ts)

        # Create utils.ts
        utils_ts = '''// Utility functions
export function greet(name: string): string {
    return `Hello, ${name}!`;
}

export function add(a: number, b: number): number {
    return a + b;
}
'''
        (src_dir / "utils.ts").write_text(utils_ts)

        yield project_root


@pytest.fixture
def cross_file_ts_project() -> Generator[Path, None, None]:
    """Create a project where project-wide LSP answers require unopened files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        (project_root / "package.json").write_text(
            json.dumps(
                {
                    "name": "cross-file-project",
                    "version": "1.0.0",
                    "type": "module",
                },
                indent=2,
            )
        )
        (project_root / "tsconfig.json").write_text(
            json.dumps(
                {
                    "compilerOptions": {
                        "target": "ES2020",
                        "module": "ESNext",
                        "moduleResolution": "node",
                        "strict": True,
                    },
                    "include": ["src/**/*"],
                },
                indent=2,
            )
        )

        src_dir = project_root / "src"
        src_dir.mkdir()
        (src_dir / "types.ts").write_text(
            """export interface Service {
  run(): string;
}

export class ImplA implements Service {
  run(): string { return target(); }
}

export function target(): string {
  return "ok";
}

export interface Box {
  value: string;
  method(): number;
}

export const box: Box = { value: "x", method: () => 1 };
""",
            encoding="utf-8",
        )
        (src_dir / "a.ts").write_text(
            """import { target, Service, box } from "./types";

export const fromA = target();
export const serviceA: Service | null = null;
export const value = box.value;
""",
            encoding="utf-8",
        )
        (src_dir / "b.ts").write_text(
            """import { target, Service } from "./types";

export class ImplB implements Service {
  run(): string { return target(); }
}
""",
            encoding="utf-8",
        )
        yield project_root


@pytest.fixture
def monorepo_ts_project() -> Generator[Path, None, None]:
    """Create a referenced-package monorepo for cross-project LSP probes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        (project_root / "package.json").write_text(
            json.dumps(
                {
                    "name": "monorepo-project",
                    "version": "1.0.0",
                    "private": True,
                    "type": "module",
                    "workspaces": ["packages/*"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (project_root / "tsconfig.json").write_text(
            json.dumps(
                {
                    "files": [],
                    "references": [
                        {"path": "./packages/common"},
                        {"path": "./packages/server"},
                        {"path": "./packages/worker"},
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        common_options = {
            "composite": True,
            "declaration": True,
            "target": "ES2020",
            "module": "ESNext",
            "moduleResolution": "node",
            "strict": True,
            "rootDir": "src",
            "outDir": "dist",
            "skipLibCheck": True,
        }
        package_options = {
            "composite": True,
            "declaration": True,
            "target": "ES2020",
            "module": "ESNext",
            "moduleResolution": "node",
            "strict": True,
            "baseUrl": ".",
            "paths": {"@fixture/common": ["../common/src/index.ts"]},
            "outDir": "dist",
            "skipLibCheck": True,
        }

        for package_name in ("common", "server", "worker"):
            package_dir = project_root / "packages" / package_name
            (package_dir / "src").mkdir(parents=True)
            tsconfig = {
                "compilerOptions": (
                    common_options
                    if package_name == "common"
                    else package_options
                ),
                "include": ["src/**/*"],
            }
            if package_name != "common":
                tsconfig["references"] = [{"path": "../common"}]
            (package_dir / "tsconfig.json").write_text(
                json.dumps(tsconfig, indent=2),
                encoding="utf-8",
            )
            (package_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": f"@fixture/{package_name}",
                        "version": "1.0.0",
                        "type": "module",
                        "exports": {"./package.json": "./package.json"},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        (project_root / "packages" / "common" / "src" / "errors.ts").write_text(
            """export interface TraceIdError {
  traceId: string;
}

export function createTraceIdError(message: string): TraceIdError {
  return { traceId: `trace:${message}` };
}
""",
            encoding="utf-8",
        )
        (project_root / "packages" / "common" / "src" / "index.ts").write_text(
            """export { createTraceIdError, type TraceIdError } from "./errors";
""",
            encoding="utf-8",
        )
        (project_root / "packages" / "server" / "src" / "handler.ts").write_text(
            """import { createTraceIdError, type TraceIdError } from "@fixture/common";

export class ServerTraceError extends Error implements TraceIdError {
  traceId = "server";
}

export function handleServer(): TraceIdError {
  return createTraceIdError("server");
}
""",
            encoding="utf-8",
        )
        (project_root / "packages" / "worker" / "src" / "worker.ts").write_text(
            """import { createTraceIdError, type TraceIdError } from "@fixture/common";

export class WorkerTraceError extends Error implements TraceIdError {
  traceId = "worker";
}

export function handleWorker(): TraceIdError {
  return createTraceIdError("worker");
}
""",
            encoding="utf-8",
        )

        yield project_root


@pytest.fixture
def project_with_prettier_config() -> Generator[Path, None, None]:
    """Create a TypeScript project with Prettier configuration.

    Structure:
        - package.json
        - .prettierrc (custom config)
        - src/index.ts (needs formatting)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)

        # Create package.json
        package_json = {
            "name": "test-prettier-project",
            "version": "1.0.0",
            "type": "module",
        }
        (project_root / "package.json").write_text(json.dumps(package_json, indent=2))

        # Create .prettierrc with custom config
        prettierrc = {
            "semi": False,
            "singleQuote": True,
            "tabWidth": 4,
            "trailingComma": "all",
        }
        (project_root / ".prettierrc").write_text(json.dumps(prettierrc, indent=2))

        # Create src directory
        src_dir = project_root / "src"
        src_dir.mkdir()

        # Create index.ts that needs formatting
        index_ts = '''const foo   =   "bar";const baz  =  123;
function hello(     name:string){return "Hello, "+name}
'''
        (src_dir / "index.ts").write_text(index_ts)
        link_project_node_modules(project_root)

        yield project_root


@pytest.fixture
def project_with_eslint_config() -> Generator[Path, None, None]:
    """Create a TypeScript project with ESLint configuration.

    Structure:
        - package.json
        - eslint.config.js (flat config)
        - src/index.ts (with lint issues)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)

        # Create package.json
        package_json = {
            "name": "test-eslint-project",
            "version": "1.0.0",
            "type": "module",
        }
        (project_root / "package.json").write_text(json.dumps(package_json, indent=2))

        # Create eslint.config.js (flat config format)
        eslint_config = '''export default [
    {
        files: ["**/*.ts", "**/*.tsx"],
        rules: {
            "no-unused-vars": "warn",
            "no-console": "warn",
        }
    }
];
'''
        (project_root / "eslint.config.js").write_text(eslint_config)

        # Create src directory
        src_dir = project_root / "src"
        src_dir.mkdir()

        # Create index.ts with lint issues
        index_ts = '''const unusedVariable = 42;

function greet(name: string): void {
    console.log("Hello, " + name);
}

greet("World");
'''
        (src_dir / "index.ts").write_text(index_ts)
        link_project_node_modules(project_root)

        yield project_root


@pytest.fixture
def project_with_full_config() -> Generator[Path, None, None]:
    """Create a TypeScript project with both Prettier and ESLint config.

    Structure:
        - package.json
        - tsconfig.json
        - .prettierrc
        - eslint.config.js
        - src/index.ts (with multiple issues)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)

        # Create package.json
        package_json = {
            "name": "test-full-config-project",
            "version": "1.0.0",
            "type": "module",
        }
        (project_root / "package.json").write_text(json.dumps(package_json, indent=2))

        # Create tsconfig.json
        tsconfig = {
            "compilerOptions": {
                "target": "ES2020",
                "module": "ESNext",
                "strict": True,
            },
            "include": ["src/**/*"],
        }
        (project_root / "tsconfig.json").write_text(json.dumps(tsconfig, indent=2))

        # Create .prettierrc
        prettierrc = {
            "semi": True,
            "singleQuote": True,
            "tabWidth": 2,
        }
        (project_root / ".prettierrc").write_text(json.dumps(prettierrc, indent=2))

        # Create eslint.config.js
        eslint_config = '''export default [
    {
        files: ["**/*.ts"],
        rules: {
            "no-unused-vars": "warn",
        }
    }
];
'''
        (project_root / "eslint.config.js").write_text(eslint_config)

        # Create src directory
        src_dir = project_root / "src"
        src_dir.mkdir()

        # Create index.ts with issues
        index_ts = '''const   unused   =   42
function greet(name:string){return "Hello, "+name}
greet("World")
'''
        (src_dir / "index.ts").write_text(index_ts)
        link_project_node_modules(project_root)

        yield project_root


# =============================================================================
# Daemon Integration Tests
# =============================================================================


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js not available")
class TestDaemonProtocolIntegration:
    """Raw daemon protocol tests backed by the real JavaScript process."""

    def test_ready_signal_is_json_on_stdout(self, simple_ts_project: Path):
        """The daemon should emit a JSON ready event as its first stdout line."""
        process = start_raw_daemon(simple_ts_project)
        try:
            assert read_json_line(process) == {"event": "ready", "version": 1}
        finally:
            stop_raw_daemon(process)

    def test_malformed_json_returns_protocol_error(self, simple_ts_project: Path):
        """Malformed input should produce a JSONParseError response."""
        process = start_raw_daemon(simple_ts_project)
        try:
            assert read_json_line(process)["event"] == "ready"

            write_daemon_line(process, "not json")
            response = read_json_line(process)

            assert response["id"] == "unknown"
            assert response["error"]["code"] == -32700
            assert response["error"]["data"]["type"] == "JSONParseError"
            assert response["error"]["data"]["retryable"] is False
        finally:
            stop_raw_daemon(process)

    def test_missing_params_returns_json_error(self, simple_ts_project: Path):
        """Request validation errors should stay on the JSON-lines channel."""
        process = start_raw_daemon(simple_ts_project)
        try:
            assert read_json_line(process)["event"] == "ready"

            request = {
                "id": "req-1",
                "version": 1,
                "method": "format",
                "params": {},
            }
            write_daemon_line(process, json.dumps(request))
            response = read_json_line(process)

            assert response["id"] == "req-1"
            assert response["error"]["code"] == -32000
            assert response["error"]["data"]["type"] == "InternalError"
            assert "Missing required params" in response["error"]["message"]
        finally:
            stop_raw_daemon(process)

    def test_dependency_missing_response_is_actionable(self, simple_ts_project: Path):
        """Missing project dependencies should identify the package and install."""
        process = start_raw_daemon(simple_ts_project)
        try:
            assert read_json_line(process)["event"] == "ready"

            file_path = simple_ts_project / "src" / "index.ts"
            request = {
                "id": "req-2",
                "version": 1,
                "method": "format",
                "params": {
                    "projectRoot": str(simple_ts_project),
                    "filepath": str(file_path),
                    "content": "",
                },
            }
            write_daemon_line(process, json.dumps(request))
            response = read_json_line(process)

            error = response["error"]
            assert response["id"] == "req-2"
            assert error["code"] == -32005
            assert error["data"]["type"] == "DependencyMissing"
            assert error["data"]["packageName"] == "prettier"
            assert error["data"]["installCommand"] == "npm install -D prettier"
        finally:
            stop_raw_daemon(process)


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js not available")
class TestDaemonIntegration:
    """Integration tests for the FormatterLinter daemon."""

    @pytest.mark.asyncio
    async def test_daemon_start_and_ready(self, simple_ts_project: Path):
        """Test that daemon starts and sends ready signal."""
        daemon = FormatterLinterDaemon.create(simple_ts_project)

        try:
            await daemon.start()
            assert daemon._ready.is_set()
        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    async def test_daemon_ping(self, simple_ts_project: Path):
        """Test ping request to daemon."""
        daemon = FormatterLinterDaemon.create(simple_ts_project)

        try:
            await daemon.start()

            result = await daemon.send_request("ping", {})
            assert result == {"ok": True}
        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not PRETTIER_AVAILABLE, reason="Prettier not installed in daemon")
    async def test_daemon_accepts_empty_format_content(
        self, project_with_prettier_config: Path
    ):
        """Test empty content is valid request content."""
        daemon = FormatterLinterDaemon.create(project_with_prettier_config)

        try:
            await daemon.start()

            file_path = str(project_with_prettier_config / "src" / "index.ts")
            result = await daemon.format(file_path, "")

            assert result["formatted"] == ""

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not PRETTIER_AVAILABLE, reason="Prettier not installed in daemon")
    async def test_daemon_format_code(self, project_with_prettier_config: Path):
        """Test formatting code with Prettier via daemon."""
        daemon = FormatterLinterDaemon.create(project_with_prettier_config)

        try:
            await daemon.start()

            file_path = str(project_with_prettier_config / "src" / "index.ts")
            code = 'const foo="bar";const baz=123;'

            result = await daemon.format(file_path, code)

            # Should return formatted code
            assert "formatted" in result
            formatted = result["formatted"]
            # Prettier with our config (no semi, single quote, tabWidth 4) should:
            # - Remove semicolons
            # - Use single quotes
            assert formatted != code  # Code was changed

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not PRETTIER_AVAILABLE, reason="Prettier not installed in daemon")
    async def test_daemon_check_formatting(self, project_with_prettier_config: Path):
        """Test checking if code is formatted."""
        daemon = FormatterLinterDaemon.create(project_with_prettier_config)

        try:
            await daemon.start()

            file_path = str(project_with_prettier_config / "src" / "index.ts")

            # Unformatted code
            unformatted = 'const foo="bar";const baz=123;'
            result = await daemon.check_formatting(file_path, unformatted)
            assert result.get("isFormatted") is False

            # Format it first
            format_result = await daemon.format(file_path, unformatted)
            formatted = format_result["formatted"]

            # Now check formatted code
            result = await daemon.check_formatting(file_path, formatted)
            assert result.get("isFormatted") is True

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not PRETTIER_AVAILABLE, reason="Prettier not installed in daemon")
    async def test_daemon_get_prettier_config(self, project_with_prettier_config: Path):
        """Test getting Prettier config."""
        daemon = FormatterLinterDaemon.create(project_with_prettier_config)

        try:
            await daemon.start()

            file_path = str(project_with_prettier_config / "src" / "index.ts")
            result = await daemon.get_prettier_config(file_path)

            assert "config" in result or "configPath" in result

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not ESLINT_AVAILABLE, reason="ESLint not installed in daemon")
    async def test_daemon_accepts_empty_lint_content(
        self, project_with_eslint_config: Path
    ):
        """Test empty content is valid lint request content."""
        daemon = FormatterLinterDaemon.create(project_with_eslint_config)

        try:
            await daemon.start()

            file_path = str(project_with_eslint_config / "src" / "index.ts")
            result = await daemon.lint(file_path, "", fix=False)

            assert result["messages"] == []

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not ESLINT_AVAILABLE, reason="ESLint not installed in daemon")
    async def test_daemon_lint_code(self, project_with_eslint_config: Path):
        """Test linting code with ESLint via daemon."""
        daemon = FormatterLinterDaemon.create(project_with_eslint_config)

        try:
            await daemon.start()

            file_path = str(project_with_eslint_config / "src" / "index.ts")
            code = '''const unused = 42;
console.log("hello");
'''

            result = await daemon.lint(file_path, code, fix=False)

            # Should return lint messages
            assert "messages" in result
            messages = result["messages"]
            # Our eslint config warns on no-unused-vars and no-console
            # So we should have warnings
            assert isinstance(messages, list)

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not ESLINT_AVAILABLE, reason="ESLint not installed in daemon")
    async def test_daemon_lint_with_fix(self, project_with_eslint_config: Path):
        """Test linting with auto-fix."""
        daemon = FormatterLinterDaemon.create(project_with_eslint_config)

        try:
            await daemon.start()

            file_path = str(project_with_eslint_config / "src" / "index.ts")
            code = '''const unused = 42;
console.log("hello");
'''

            result = await daemon.lint(file_path, code, fix=True)

            # Should return lint messages and potentially fixed content
            assert "messages" in result
            # fixedContent may be present if there were fixable issues
            # (no-unused-vars and no-console are typically not auto-fixable)

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    async def test_daemon_restart(self, simple_ts_project: Path):
        """Test daemon restart functionality."""
        daemon = FormatterLinterDaemon.create(simple_ts_project)

        try:
            await daemon.start()

            # Verify it works
            result = await daemon.send_request("ping", {})
            assert result == {"ok": True}

            # Restart
            await daemon.restart()

            # Verify it still works after restart
            result = await daemon.send_request("ping", {})
            assert result == {"ok": True}

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    async def test_daemon_error_handling(self, simple_ts_project: Path):
        """Test daemon error handling for invalid requests."""
        daemon = FormatterLinterDaemon.create(simple_ts_project)

        try:
            await daemon.start()

            # Unknown method
            with pytest.raises(DaemonError) as exc_info:
                await daemon.send_request("unknown_method", {})

            assert "Unknown method" in str(exc_info.value)

        finally:
            await daemon.shutdown()


# =============================================================================
# VtslsClient Integration Tests
# =============================================================================


@pytest.mark.skipif(not VTSLS_AVAILABLE, reason="vtsls not available")
class TestVtslsIntegration:
    """Integration tests for VtslsClient with real vtsls."""

    @pytest.mark.asyncio
    async def test_vtsls_start_and_initialize(self, simple_ts_project: Path):
        """Test that vtsls starts and initializes."""
        client = VtslsClient(simple_ts_project)

        try:
            await client.start()
            assert client.is_initialized()
        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    async def test_vtsls_document_open(self, simple_ts_project: Path):
        """Test opening a document in vtsls."""
        client = VtslsClient(simple_ts_project)

        try:
            await client.start()

            file_path = simple_ts_project / "src" / "index.ts"
            file_uri = f"file://{file_path}"
            content = file_path.read_text()

            # Open document
            await client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": file_uri,
                        "languageId": "typescript",
                        "version": 1,
                        "text": content,
                    }
                },
            )

            # Give vtsls time to process
            await asyncio.sleep(1.0)

        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    async def test_vtsls_hover(self, simple_ts_project: Path):
        """Test hover request for type information."""
        client = VtslsClient(simple_ts_project)

        try:
            await client.start()

            file_path = simple_ts_project / "src" / "utils.ts"
            file_uri = f"file://{file_path}"
            content = file_path.read_text()

            # Open document
            await client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": file_uri,
                        "languageId": "typescript",
                        "version": 1,
                        "text": content,
                    }
                },
            )

            # Wait for indexing
            await asyncio.sleep(1.0)

            # Hover over 'greet' function (line 1, character 16)
            result = await client.request(
                "textDocument/hover",
                {
                    "textDocument": {"uri": file_uri},
                    "position": {"line": 1, "character": 16},
                },
            )

            # Should get hover info
            assert result is not None
            assert "contents" in result

        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    async def test_vtsls_document_symbols(self, simple_ts_project: Path):
        """Test getting document symbols."""
        client = VtslsClient(simple_ts_project)

        try:
            await client.start()

            file_path = simple_ts_project / "src" / "utils.ts"
            file_uri = f"file://{file_path}"
            content = file_path.read_text()

            # Open document
            await client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": file_uri,
                        "languageId": "typescript",
                        "version": 1,
                        "text": content,
                    }
                },
            )

            # Wait for indexing
            await asyncio.sleep(1.0)

            # Get document symbols
            result = await client.request(
                "textDocument/documentSymbol",
                {"textDocument": {"uri": file_uri}},
            )

            # Should get symbols (greet, add functions)
            assert result is not None
            assert isinstance(result, list)
            assert len(result) >= 2  # At least greet and add

        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    async def test_vtsls_definition(self, simple_ts_project: Path):
        """Test go-to-definition."""
        client = VtslsClient(simple_ts_project)

        try:
            await client.start()

            # Open both files
            index_path = simple_ts_project / "src" / "index.ts"
            index_uri = f"file://{index_path}"
            index_content = index_path.read_text()

            utils_path = simple_ts_project / "src" / "utils.ts"
            utils_uri = f"file://{utils_path}"
            utils_content = utils_path.read_text()

            await client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": index_uri,
                        "languageId": "typescript",
                        "version": 1,
                        "text": index_content,
                    }
                },
            )

            await client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": utils_uri,
                        "languageId": "typescript",
                        "version": 1,
                        "text": utils_content,
                    }
                },
            )

            # Wait for indexing
            await asyncio.sleep(2.0)

            # Go to definition of 'greet' in index.ts (line 12, after import)
            # Looking for usage of greet in: console.log(greet(user.name));
            result = await client.request(
                "textDocument/definition",
                {
                    "textDocument": {"uri": index_uri},
                    "position": {"line": 12, "character": 13},  # Position of 'greet'
                },
            )

            # Should find definition
            assert result is not None

        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    async def test_vtsls_diagnostics_notification(self, simple_ts_project: Path):
        """Test receiving diagnostics for type errors."""
        client = VtslsClient(simple_ts_project)
        received_diagnostics = []

        def on_diagnostics(params):
            received_diagnostics.append(params)

        try:
            await client.start()

            # Register handler
            client.on_notification(
                "textDocument/publishDiagnostics", on_diagnostics
            )

            # Open index.ts which has a type error
            file_path = simple_ts_project / "src" / "index.ts"
            file_uri = f"file://{file_path}"
            content = file_path.read_text()

            await client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": file_uri,
                        "languageId": "typescript",
                        "version": 1,
                        "text": content,
                    }
                },
            )

            # Wait for diagnostics
            await asyncio.sleep(3.0)

            # Should have received diagnostics
            # The file has a type error: age: "thirty" (string not assignable to number)
            assert len(received_diagnostics) > 0

        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    async def test_vtsls_restart(self, simple_ts_project: Path):
        """Test vtsls restart functionality."""
        client = VtslsClient(simple_ts_project)

        try:
            await client.start()
            assert client.is_initialized()

            # Restart
            await client.restart()

            # Should still be initialized after restart
            assert client.is_initialized()

        finally:
            await client.shutdown()


@pytest.mark.skipif(not VTSLS_AVAILABLE, reason="vtsls not available")
class TestProjectGraphReadiness:
    """Tests that public project-wide tools force tsserver project loading."""

    @pytest.mark.asyncio
    async def test_project_wide_navigation_loads_unopened_files(
        self, cross_file_ts_project: Path
    ):
        client = VtslsClient(cross_file_ts_project)
        server_state._project_root = cross_file_ts_project
        server_state.vtsls = client
        server_state.clear_project_load_cache()
        server_state.document_states.clear()
        try:
            await client.start()

            a_text = (cross_file_ts_project / "src" / "a.ts").read_text()
            definition_result = await language.definition(
                "src/a.ts",
                **position_of(a_text, "target", occurrence=2),
            )
            assert location_basenames(definition_result) == {"types.ts"}

            references_result = await language.references(
                "src/a.ts",
                include_declaration=True,
                **position_of(a_text, "target", occurrence=2),
            )
            references_dict = references_result.model_dump(exclude_none=True)
            assert location_basenames(references_dict["items"]) == {
                "a.ts",
                "b.ts",
                "types.ts",
            }
            assert references_dict["totalItems"] == 6

            implementation_result = await language.implementation(
                "src/a.ts",
                **position_of(a_text, "Service", occurrence=2),
            )
            assert location_basenames(implementation_result) == {"b.ts", "types.ts"}

            symbol_info = await language.symbol_info(
                "src/a.ts",
                **position_of(a_text, "box", occurrence=2),
            )
            assert "Box" in str(symbol_info.content)
            assert "(loading...)" not in str(symbol_info.content)
        finally:
            await client.shutdown()
            server_state._project_root = None
            server_state.vtsls = None
            server_state.document_states.clear()
            server_state.clear_project_load_cache()

    @pytest.mark.asyncio
    async def test_preview_rename_includes_unopened_project_files(
        self, cross_file_ts_project: Path
    ):
        client = VtslsClient(cross_file_ts_project)
        server_state._project_root = cross_file_ts_project
        server_state.vtsls = client
        server_state.clear_project_load_cache()
        server_state.document_states.clear()
        try:
            await client.start()

            types_text = (cross_file_ts_project / "src" / "types.ts").read_text()
            rename_result = await intelligence.preview_rename(
                "src/types.ts",
                new_name="renamedTarget",
                **position_of(types_text, "target"),
            )

            assert rename_preview_basenames(rename_result) == {
                "a.ts",
                "b.ts",
                "types.ts",
            }
        finally:
            await client.shutdown()
            server_state._project_root = None
            server_state.vtsls = None
            server_state.document_states.clear()
            server_state.clear_project_load_cache()


@pytest.mark.skipif(not VTSLS_AVAILABLE, reason="vtsls not available")
class TestMonorepoProjectReferences:
    """Characterize public tool behavior across referenced package projects."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_monorepo_references_include_unopened_package_callers(
        self, monorepo_ts_project: Path
    ):
        client = VtslsClient(monorepo_ts_project)
        server_state._project_root = monorepo_ts_project
        server_state.vtsls = client
        server_state.clear_project_load_cache()
        server_state.document_states.clear()
        try:
            await client.start()

            common_text = (
                monorepo_ts_project / "packages" / "common" / "src" / "errors.ts"
            ).read_text()
            result = await language.references(
                "packages/common/src/errors.ts",
                include_declaration=True,
                **position_of(common_text, "createTraceIdError"),
            )

            actual = location_project_paths(result, monorepo_ts_project)
            assert_monorepo_paths_include(
                actual,
                {
                    "packages/common/src/errors.ts",
                    "packages/common/src/index.ts",
                    "packages/server/src/handler.ts",
                    "packages/worker/src/worker.ts",
                },
                "references",
            )
        finally:
            await client.shutdown()
            server_state._project_root = None
            server_state.vtsls = None
            server_state.document_states.clear()
            server_state.clear_project_load_cache()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_monorepo_implementation_includes_unopened_package_implementors(
        self, monorepo_ts_project: Path
    ):
        client = VtslsClient(monorepo_ts_project)
        server_state._project_root = monorepo_ts_project
        server_state.vtsls = client
        server_state.clear_project_load_cache()
        server_state.document_states.clear()
        try:
            await client.start()

            common_text = (
                monorepo_ts_project / "packages" / "common" / "src" / "errors.ts"
            ).read_text()
            result = await language.implementation(
                "packages/common/src/errors.ts",
                **position_of(common_text, "TraceIdError"),
            )

            actual = location_project_paths(result, monorepo_ts_project)
            assert_monorepo_paths_include(
                actual,
                {
                    "packages/server/src/handler.ts",
                    "packages/worker/src/worker.ts",
                },
                "implementation",
            )
        finally:
            await client.shutdown()
            server_state._project_root = None
            server_state.vtsls = None
            server_state.document_states.clear()
            server_state.clear_project_load_cache()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_monorepo_preview_rename_includes_unopened_package_callers(
        self, monorepo_ts_project: Path
    ):
        client = VtslsClient(monorepo_ts_project)
        server_state._project_root = monorepo_ts_project
        server_state.vtsls = client
        server_state.clear_project_load_cache()
        server_state.document_states.clear()
        try:
            await client.start()

            common_text = (
                monorepo_ts_project / "packages" / "common" / "src" / "errors.ts"
            ).read_text()
            result = await intelligence.preview_rename(
                "packages/common/src/errors.ts",
                new_name="createRenamedTraceIdError",
                **position_of(common_text, "createTraceIdError"),
            )

            actual = rename_preview_project_paths(result, monorepo_ts_project)
            assert_monorepo_paths_include(
                actual,
                {
                    "packages/common/src/errors.ts",
                    "packages/common/src/index.ts",
                    "packages/server/src/handler.ts",
                    "packages/worker/src/worker.ts",
                },
                "preview_rename",
            )
        finally:
            await client.shutdown()
            server_state._project_root = None
            server_state.vtsls = None
            server_state.document_states.clear()
            server_state.clear_project_load_cache()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_monorepo_package_root_references_include_workspace_callers(
        self, monorepo_ts_project: Path
    ):
        package_root = monorepo_ts_project / "packages" / "server"
        client = VtslsClient(package_root)
        server_state._project_root = package_root
        server_state.vtsls = client
        server_state.clear_project_load_cache()
        server_state.document_states.clear()
        try:
            await client.start()

            server_text = (package_root / "src" / "handler.ts").read_text()
            try:
                result = await language.references(
                    "src/handler.ts",
                    include_declaration=True,
                    **position_of(server_text, "createTraceIdError", occurrence=2),
                )
            except ProjectLoadError as exc:
                pytest.xfail(f"package-root projectInfo failed: {exc}")

            actual = location_project_paths(result, monorepo_ts_project)
            assert_monorepo_paths_include(
                actual,
                {
                    "packages/common/src/errors.ts",
                    "packages/common/src/index.ts",
                    "packages/server/src/handler.ts",
                    "packages/worker/src/worker.ts",
                },
                "references from package cwd",
            )
        finally:
            await client.shutdown()
            server_state._project_root = None
            server_state.vtsls = None
            server_state.document_states.clear()
            server_state.clear_project_load_cache()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_monorepo_package_root_preview_rename_includes_workspace_callers(
        self, monorepo_ts_project: Path
    ):
        package_root = monorepo_ts_project / "packages" / "server"
        client = VtslsClient(package_root)
        server_state._project_root = package_root
        server_state.vtsls = client
        server_state.clear_project_load_cache()
        server_state.document_states.clear()
        try:
            await client.start()

            server_text = (package_root / "src" / "handler.ts").read_text()
            try:
                result = await intelligence.preview_rename(
                    "src/handler.ts",
                    new_name="createServerScopedTraceIdError",
                    **position_of(server_text, "createTraceIdError", occurrence=2),
                )
            except ProjectLoadError as exc:
                pytest.xfail(f"package-root projectInfo failed: {exc}")

            actual = rename_preview_project_paths(result, monorepo_ts_project)
            assert actual == {"packages/server/src/handler.ts"}
        finally:
            await client.shutdown()
            server_state._project_root = None
            server_state.vtsls = None
            server_state.document_states.clear()
            server_state.clear_project_load_cache()


# =============================================================================
# Error Scenario Tests
# =============================================================================


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js not available")
class TestErrorScenarios:
    """Test error handling scenarios."""

    @pytest.mark.asyncio
    async def test_daemon_missing_params(self, simple_ts_project: Path):
        """Test error handling when required params are missing."""
        daemon = FormatterLinterDaemon.create(simple_ts_project)

        try:
            await daemon.start()

            # Format without required params
            with pytest.raises(DaemonError) as exc_info:
                await daemon.send_request("format", {})

            assert "Missing required params" in str(exc_info.value)

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    async def test_daemon_missing_project_dependency(self, simple_ts_project: Path):
        """Test missing project-local Prettier reports an actionable error."""
        daemon = FormatterLinterDaemon.create(simple_ts_project)

        try:
            await daemon.start()

            file_path = str(simple_ts_project / "src" / "index.ts")
            with pytest.raises(DaemonError) as exc_info:
                await daemon.format(file_path, "")

            assert exc_info.value.code == -32005
            assert "npm install -D prettier" in str(exc_info.value)

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    async def test_vtsls_not_found(self, simple_ts_project: Path):
        """Test handling when vtsls is not found."""
        # Save original environment
        original_vtsls_path = os.environ.get("VTSLS_PATH")
        original_path = os.environ.get("PATH")

        try:
            # Set VTSLS_PATH to invalid location and clear PATH to prevent finding vtsls
            os.environ["VTSLS_PATH"] = "/nonexistent/vtsls"
            os.environ["PATH"] = "/nonexistent"

            with pytest.raises(VtslsNotFoundError):
                VtslsClient(simple_ts_project)
                # _find_vtsls is called in __init__

        finally:
            # Restore environment
            if original_vtsls_path:
                os.environ["VTSLS_PATH"] = original_vtsls_path
            else:
                os.environ.pop("VTSLS_PATH", None)
            if original_path:
                os.environ["PATH"] = original_path

    @pytest.mark.asyncio
    async def test_daemon_timeout(self, simple_ts_project: Path):
        """Test request timeout handling."""
        daemon = FormatterLinterDaemon.create(simple_ts_project)

        try:
            await daemon.start()

            # This is a valid request, shouldn't timeout
            # We'd need a way to simulate slow responses to properly test timeout
            # For now, just verify short timeout parameter works
            result = await daemon.send_request("ping", {}, timeout=30.0)
            assert result == {"ok": True}

        finally:
            await daemon.shutdown()


# =============================================================================
# Full Workflow Tests
# =============================================================================


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js required")
class TestFullWorkflow:
    """Test complete workflows combining multiple components."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not PRETTIER_AVAILABLE, reason="Prettier not installed in daemon")
    async def test_format_and_check_workflow(self, project_with_prettier_config: Path):
        """Test format code then check formatting workflow."""
        daemon = FormatterLinterDaemon.create(project_with_prettier_config)

        try:
            await daemon.start()

            file_path = str(project_with_prettier_config / "src" / "index.ts")
            code = 'const foo="bar";  const baz=123;'

            # Check initial formatting (should fail)
            check_result = await daemon.check_formatting(file_path, code)
            assert check_result.get("isFormatted") is False

            # Format
            format_result = await daemon.format(file_path, code)
            formatted = format_result["formatted"]

            # Check again (should pass)
            check_result = await daemon.check_formatting(file_path, formatted)
            assert check_result.get("isFormatted") is True

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not ESLINT_AVAILABLE, reason="ESLint not installed in daemon")
    async def test_lint_and_fix_workflow(self, project_with_eslint_config: Path):
        """Test lint code then apply fixes workflow."""
        daemon = FormatterLinterDaemon.create(project_with_eslint_config)

        try:
            await daemon.start()

            file_path = str(project_with_eslint_config / "src" / "index.ts")
            code = '''const unused = 42;
console.log("hello");
export {};
'''

            # Initial lint
            await daemon.lint(file_path, code, fix=False)

            # Lint with fix
            fix_result = await daemon.lint(file_path, code, fix=True)

            # Verify we got results
            assert "messages" in fix_result

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not VTSLS_AVAILABLE, reason="vtsls not available")
    async def test_vtsls_with_diagnostics_workflow(self, simple_ts_project: Path):
        """Test opening file and receiving type error diagnostics."""
        client = VtslsClient(simple_ts_project)
        diagnostics_received = {}

        def on_diagnostics(params):
            uri = params.get("uri", "")
            diags = params.get("diagnostics", [])
            diagnostics_received[uri] = diags

        try:
            await client.start()
            client.on_notification("textDocument/publishDiagnostics", on_diagnostics)

            # Open file with type error
            file_path = simple_ts_project / "src" / "index.ts"
            file_uri = f"file://{file_path}"
            content = file_path.read_text()

            await client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": file_uri,
                        "languageId": "typescript",
                        "version": 1,
                        "text": content,
                    }
                },
            )

            # Wait for diagnostics
            await asyncio.sleep(3.0)

            # Should have received diagnostics with type error
            assert file_uri in diagnostics_received
            diags = diagnostics_received[file_uri]

            # Note: may vary based on vtsls version
            # Just verify we got some diagnostics
            assert isinstance(diags, list)

        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not (PRETTIER_AVAILABLE and ESLINT_AVAILABLE),
        reason="Prettier and ESLint required"
    )
    async def test_combined_daemon_workflow(self, project_with_full_config: Path):
        """Test combined format and lint workflow."""
        daemon = FormatterLinterDaemon.create(project_with_full_config)

        try:
            await daemon.start()

            file_path = str(project_with_full_config / "src" / "index.ts")
            original_code = (project_with_full_config / "src" / "index.ts").read_text()

            # Run lint with fix
            lint_result = await daemon.lint(file_path, original_code, fix=True)
            # fixedContent may be None if no fixes were applied
            fixed_code = lint_result.get("fixedContent") or original_code

            # Run format on (potentially) fixed code
            format_result = await daemon.format(file_path, fixed_code)
            final_code = format_result["formatted"]

            # Verify final code is formatted
            check_result = await daemon.check_formatting(file_path, final_code)
            assert check_result.get("isFormatted") is True

        finally:
            await daemon.shutdown()


# =============================================================================
# Concurrent Operations Tests
# =============================================================================


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js not available")
class TestConcurrentOperations:
    """Test concurrent operation handling."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not PRETTIER_AVAILABLE, reason="Prettier not installed in daemon")
    async def test_daemon_concurrent_requests(self, project_with_prettier_config: Path):
        """Test multiple concurrent requests to daemon."""
        daemon = FormatterLinterDaemon.create(project_with_prettier_config)

        try:
            await daemon.start()

            file_path = str(project_with_prettier_config / "src" / "index.ts")

            # Send multiple format requests concurrently
            codes = [
                f'const x{i} = "value{i}";' for i in range(5)
            ]

            tasks = [daemon.format(file_path, code) for code in codes]
            results = await asyncio.gather(*tasks)

            # All should succeed
            assert len(results) == 5
            for result in results:
                assert "formatted" in result

        finally:
            await daemon.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not PRETTIER_AVAILABLE, reason="Prettier not installed in daemon")
    async def test_daemon_mixed_operations(self, project_with_prettier_config: Path):
        """Test mixed format and check operations."""
        daemon = FormatterLinterDaemon.create(project_with_prettier_config)

        try:
            await daemon.start()

            file_path = str(project_with_prettier_config / "src" / "index.ts")
            code = 'const foo="bar";'

            # Run format and check concurrently
            format_task = daemon.format(file_path, code)
            check_task = daemon.check_formatting(file_path, code)

            results = await asyncio.gather(format_task, check_task)

            # Both should complete
            format_result, check_result = results
            assert "formatted" in format_result
            assert "isFormatted" in check_result

        finally:
            await daemon.shutdown()


# =============================================================================
# Stale Diagnostics Fix Tests
# =============================================================================


@pytest.mark.skipif(not VTSLS_AVAILABLE, reason="vtsls not available")
class TestStaleDiagnosticsFix:
    """Integration tests for the stale diagnostics fix.

    These tests verify that diagnostics are properly refreshed when file
    content changes on disk, addressing the stale cache issue.
    """

    @pytest.fixture(autouse=True)
    def reset_global_state(self):
        """Reset global state before each test."""
        current_diagnostics.clear()
        document_states.clear()
        pending_diagnostics_events.clear()
        yield
        # Cleanup after test
        current_diagnostics.clear()
        document_states.clear()
        pending_diagnostics_events.clear()

    @pytest.mark.asyncio
    async def test_diagnostics_detect_errors_in_fresh_file(self, simple_ts_project: Path):
        """Test that diagnostics correctly detect type errors in a fresh file."""
        client = VtslsClient(simple_ts_project)

        def on_diagnostics(params):
            uri = params.get("uri", "")
            diags = params.get("diagnostics", [])
            current_diagnostics[uri] = diags
            if uri in pending_diagnostics_events:
                pending_diagnostics_events[uri].set()

        try:
            await client.start()
            client.on_notification("textDocument/publishDiagnostics", on_diagnostics)

            file_path = simple_ts_project / "src" / "index.ts"
            file_uri = f"file://{file_path}"

            # Clear any stale diagnostics and register event
            clear_diagnostics_for_uri(file_uri)
            register_diagnostics_event(file_uri)

            # Open file
            await open_file(client, str(file_path), file_uri)

            # Wait for diagnostics with timeout
            diags = await wait_for_diagnostics(file_uri, timeout=5.0)

            # Should have diagnostics for the type error (age: "thirty")
            assert len(diags) > 0, "Expected diagnostics for type error"

            # Close file
            await close_file(client, file_uri)

        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    async def test_diagnostics_detect_fix_after_error_corrected(self, simple_ts_project: Path):
        """Test that diagnostics refresh when file error is corrected on disk."""
        client = VtslsClient(simple_ts_project)

        def on_diagnostics(params):
            uri = params.get("uri", "")
            diags = params.get("diagnostics", [])
            current_diagnostics[uri] = diags
            if uri in pending_diagnostics_events:
                pending_diagnostics_events[uri].set()

        try:
            await client.start()
            client.on_notification("textDocument/publishDiagnostics", on_diagnostics)

            file_path = simple_ts_project / "src" / "index.ts"
            file_uri = f"file://{file_path}"

            # First, open file with type error
            clear_diagnostics_for_uri(file_uri)
            register_diagnostics_event(file_uri)
            await open_file(client, str(file_path), file_uri)
            initial_diags = await wait_for_diagnostics(file_uri, timeout=5.0)

            # Should have errors initially
            assert len(initial_diags) > 0, "Expected initial diagnostics"
            await close_file(client, file_uri)

            # Now fix the file on disk (change age: "thirty" to age: 30)
            original_content = file_path.read_text()
            fixed_content = original_content.replace('age: "thirty"', "age: 30")
            file_path.write_text(fixed_content)

            # Open file again - should get fresh diagnostics
            clear_diagnostics_for_uri(file_uri)
            register_diagnostics_event(file_uri)
            await open_file(client, str(file_path), file_uri)
            new_diags = await wait_for_diagnostics(file_uri, timeout=5.0)

            # Should have no type errors now (or fewer)
            # Note: may have other diagnostics like unused imports
            type_errors = [d for d in new_diags if d.get("severity", 1) == 1]
            assert len(type_errors) < len(initial_diags), \
                f"Expected fewer errors after fix. Initial: {len(initial_diags)}, New: {len(type_errors)}"

            await close_file(client, file_uri)

        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    async def test_version_increments_on_content_change(self, simple_ts_project: Path):
        """Test that document version increments when content changes."""
        client = VtslsClient(simple_ts_project)

        def on_diagnostics(params):
            uri = params.get("uri", "")
            diags = params.get("diagnostics", [])
            current_diagnostics[uri] = diags
            if uri in pending_diagnostics_events:
                pending_diagnostics_events[uri].set()

        try:
            await client.start()
            client.on_notification("textDocument/publishDiagnostics", on_diagnostics)

            file_path = simple_ts_project / "src" / "utils.ts"
            file_uri = f"file://{file_path}"

            # First open
            await open_file(client, str(file_path), file_uri)
            assert file_uri in document_states
            assert document_states[file_uri].version == 1
            initial_hash = document_states[file_uri].content_hash

            # Modify file on disk
            original_content = file_path.read_text()
            modified_content = original_content + "\n// Added comment\n"
            file_path.write_text(modified_content)

            # Open again - should detect change and increment version
            await open_file(client, str(file_path), file_uri)
            assert document_states[file_uri].version == 2
            assert document_states[file_uri].content_hash != initial_hash

            # Open again without changing - should keep same version
            await open_file(client, str(file_path), file_uri)
            assert document_states[file_uri].version == 2

            await close_file(client, file_uri)

        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_rapid_calls_same_file(self, simple_ts_project: Path):
        """Test that multiple rapid calls to the same file work correctly."""
        client = VtslsClient(simple_ts_project)

        def on_diagnostics(params):
            uri = params.get("uri", "")
            diags = params.get("diagnostics", [])
            current_diagnostics[uri] = diags
            if uri in pending_diagnostics_events:
                pending_diagnostics_events[uri].set()

        try:
            await client.start()
            client.on_notification("textDocument/publishDiagnostics", on_diagnostics)

            file_path = simple_ts_project / "src" / "index.ts"
            file_uri = f"file://{file_path}"

            # Make multiple rapid calls
            for _i in range(3):
                clear_diagnostics_for_uri(file_uri)
                register_diagnostics_event(file_uri)
                await open_file(client, str(file_path), file_uri)
                diags = await wait_for_diagnostics(file_uri, timeout=5.0)
                await close_file(client, file_uri)

                # Each call should return consistent results
                assert isinstance(diags, list)

            # After 3 opens/closes of same file, version should still be 1
            # (content hasn't changed)
            # Note: file was closed so check it was tracked
            assert file_uri in document_states

        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    async def test_timeout_handling_returns_gracefully(self, simple_ts_project: Path):
        """Test that timeout on diagnostics returns gracefully."""
        client = VtslsClient(simple_ts_project)

        # Don't register diagnostics handler - so event will never be set
        try:
            await client.start()

            file_path = simple_ts_project / "src" / "utils.ts"
            file_uri = f"file://{file_path}"

            # Register event but with no handler to signal it
            register_diagnostics_event(file_uri)

            # Wait with very short timeout - should timeout and return empty
            result = await wait_for_diagnostics(file_uri, timeout=0.1)

            # Should return empty list (or whatever is cached) gracefully
            assert isinstance(result, list)

            # Event should be cleaned up
            assert file_uri not in pending_diagnostics_events

        finally:
            await client.shutdown()

    @pytest.mark.asyncio
    async def test_content_hash_consistency(self):
        """Test that content hash is consistent and detects changes."""
        content1 = "const x = 1;"
        content2 = "const x = 2;"
        content3 = "const x = 1;"  # Same as content1

        hash1 = compute_content_hash(content1)
        hash2 = compute_content_hash(content2)
        hash3 = compute_content_hash(content3)

        # Same content should have same hash
        assert hash1 == hash3

        # Different content should have different hash
        assert hash1 != hash2

        # Hash should be 16 chars (truncated SHA256)
        assert len(hash1) == 16
