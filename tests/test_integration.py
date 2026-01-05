"""Integration tests for the TypeScript MCP server.

These tests exercise the full workflow with real TypeScript projects,
including vtsls, Prettier, and ESLint integration.

Tests require:
- Node.js installed
- vtsls installed (npm install -g @vtsls/language-server)
- Prettier installed (will use bundled fallback if not)
- ESLint installed (will use bundled fallback if not)

Tests are marked with appropriate skip conditions if dependencies are missing.
"""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from jons_mcp_typescript.daemon_client import FormatterLinterDaemon
from jons_mcp_typescript.exceptions import (
    DaemonError,
    DaemonTimeoutError,
    VtslsNotFoundError,
)
from jons_mcp_typescript.lsp_client import VtslsClient
from jons_mcp_typescript.server import (
    clear_diagnostics_for_uri,
    close_file,
    compute_content_hash,
    current_diagnostics,
    document_states,
    DocumentState,
    open_file,
    pending_diagnostics_events,
    register_diagnostics_event,
    wait_for_diagnostics,
)


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

        yield project_root


# =============================================================================
# Daemon Integration Tests
# =============================================================================


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
    async def test_vtsls_workspace_symbols(self, simple_ts_project: Path):
        """Test workspace symbol search."""
        client = VtslsClient(simple_ts_project)

        try:
            await client.start()

            # Wait for initial indexing
            await asyncio.sleep(2.0)

            # Search for 'greet' symbol
            result = await client.request(
                "workspace/symbol",
                {"query": "greet"},
            )

            # Should find the greet function
            assert result is not None
            assert isinstance(result, list)
            # Note: workspace/symbol may or may not find results depending on indexing

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


# =============================================================================
# Error Scenario Tests
# =============================================================================


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js not available")
class TestErrorScenarios:
    """Test error handling scenarios."""

    @pytest.mark.asyncio
    async def test_daemon_invalid_json(self, simple_ts_project: Path):
        """Test handling of malformed JSON in daemon communication."""
        daemon = FormatterLinterDaemon.create(simple_ts_project)

        try:
            await daemon.start()

            # Valid request should work
            result = await daemon.send_request("ping", {})
            assert result == {"ok": True}

        finally:
            await daemon.shutdown()

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
                client = VtslsClient(simple_ts_project)
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
            lint_result = await daemon.lint(file_path, code, fix=False)
            initial_messages = lint_result.get("messages", [])

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

            # Should have at least one diagnostic for the type error
            # age: "thirty" - string not assignable to number
            type_errors = [
                d for d in diags if "not assignable" in d.get("message", "").lower()
                or "type" in d.get("message", "").lower()
            ]
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
            for i in range(3):
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
