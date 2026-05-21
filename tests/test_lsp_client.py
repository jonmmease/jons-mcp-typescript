"""Unit tests for LSP client functionality."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jons_mcp_typescript.exceptions import LSPRequestError
from jons_mcp_typescript.lsp_client import ProcessWatchdog, VtslsClient


class TestProcessWatchdog:
    """Test suite for ProcessWatchdog class."""

    def test_initialization(self):
        """Test ProcessWatchdog initialization."""
        watchdog = ProcessWatchdog(max_restarts=5, restart_delay=2.0)
        assert watchdog.max_restarts == 5
        assert watchdog.restart_delay == 2.0
        assert watchdog.restart_count == 0
        assert watchdog._stopped is False

    def test_reset(self):
        """Test resetting the restart counter."""
        watchdog = ProcessWatchdog()
        watchdog.restart_count = 3
        watchdog.reset()
        assert watchdog.restart_count == 0

    def test_stop(self):
        """Test stopping the watchdog."""
        watchdog = ProcessWatchdog()
        assert watchdog._stopped is False
        watchdog.stop()
        assert watchdog._stopped is True

    @pytest.mark.asyncio
    async def test_monitor_process_alive(self, mock_vtsls_process: MagicMock):
        """Test monitoring a running process."""
        watchdog = ProcessWatchdog(max_restarts=3, restart_delay=0.1)
        mock_vtsls_process.poll = MagicMock(return_value=None)
        restart_fn = AsyncMock(return_value=mock_vtsls_process)

        # Stop the watchdog after a short time
        async def stop_after_delay():
            await asyncio.sleep(0.05)
            watchdog.stop()

        asyncio.create_task(stop_after_delay())

        result = await watchdog.monitor(mock_vtsls_process, restart_fn)
        assert result == mock_vtsls_process
        assert restart_fn.call_count == 0  # Process was alive

    @pytest.mark.asyncio
    async def test_monitor_process_crash_and_restart(self, mock_vtsls_process: MagicMock):
        """Test watchdog restarts a crashed process and returns the replacement."""
        watchdog = ProcessWatchdog(max_restarts=1, restart_delay=0.01)
        restarted_process = MagicMock()
        mock_vtsls_process.poll = MagicMock(return_value=1)
        mock_vtsls_process.returncode = 1
        restarted_process.poll = MagicMock(return_value=None)

        async def restart() -> MagicMock:
            watchdog.stop()
            return restarted_process

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await watchdog.monitor(mock_vtsls_process, restart)

        assert result == restarted_process
        assert watchdog.restart_count == 1

    @pytest.mark.asyncio
    async def test_monitor_max_restarts_exceeded(self, mock_vtsls_process: MagicMock):
        """Test ProcessCrashError when max restarts exceeded."""
        from jons_mcp_typescript.exceptions import ProcessCrashError

        watchdog = ProcessWatchdog(max_restarts=1, restart_delay=0.01)
        mock_vtsls_process.poll = MagicMock(return_value=1)  # Always crashed
        mock_vtsls_process.returncode = 1
        restart_fn = AsyncMock(side_effect=ProcessCrashError("Simulated crash"))

        with pytest.raises(ProcessCrashError):
            await watchdog.monitor(mock_vtsls_process, restart_fn)


class TestVtslsClientInitialization:
    """Test suite for VtslsClient initialization."""

    def test_initialization_with_defaults(self, temp_project: Path):
        """Test VtslsClient initialization with default values."""
        with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
            client = VtslsClient(temp_project)
            assert client.project_root == temp_project
            assert client.config == {}
            assert client.vtsls_path == "vtsls"
            assert client.process is None
            assert client.request_id == 0
            assert client.pending_requests == {}
            assert client.notification_handlers == {}
            assert client._initialized is False

    def test_initialization_with_custom_config(self, temp_project: Path):
        """Test VtslsClient initialization with custom configuration."""
        custom_config = {"typescript": {"preferences": {"quotePreference": "double"}}}
        with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
            client = VtslsClient(temp_project, config=custom_config)
            assert client.config == custom_config

    def test_initialization_with_custom_vtsls_path(self, temp_project: Path):
        """Test VtslsClient initialization with custom vtsls path."""
        custom_path = "/custom/path/to/vtsls"
        client = VtslsClient(temp_project, vtsls_path=custom_path)
        assert client.vtsls_path == custom_path

    def test_is_initialized_false_by_default(self, temp_project: Path):
        """Test that is_initialized returns False by default."""
        with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
            client = VtslsClient(temp_project)
            assert client.is_initialized() is False

    def test_find_vtsls_checks_alternate_npm_global_path(self, temp_project: Path):
        """Test npm global fallback checks both common install layouts."""
        npm_prefix = temp_project / "npm-prefix"
        alt_vtsls = (
            npm_prefix
            / "node_modules"
            / "@vtsls"
            / "language-server"
            / "bin"
            / "vtsls.js"
        )
        alt_vtsls.parent.mkdir(parents=True)
        alt_vtsls.write_text("", encoding="utf-8")

        client = VtslsClient.__new__(VtslsClient)
        client.project_root = temp_project

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("shutil.which", return_value=None),
            patch("subprocess.run", return_value=MagicMock(stdout=str(npm_prefix))),
        ):
            assert client._find_vtsls() == f"node {alt_vtsls}"

    def test_find_vtsls_falls_back_to_project_local_path(self, temp_project: Path):
        """Test a missing npm-global install does not block local vtsls discovery."""
        npm_prefix = temp_project / "empty-npm-prefix"
        npm_prefix.mkdir()
        local_vtsls = (
            temp_project
            / "node_modules"
            / "@vtsls"
            / "language-server"
            / "bin"
            / "vtsls.js"
        )
        local_vtsls.parent.mkdir(parents=True)
        local_vtsls.write_text("", encoding="utf-8")

        client = VtslsClient.__new__(VtslsClient)
        client.project_root = temp_project

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("shutil.which", return_value=None),
            patch("subprocess.run", return_value=MagicMock(stdout=str(npm_prefix))),
        ):
            assert client._find_vtsls() == f"node {local_vtsls}"


class TestVtslsClientMessageHandling:
    """Test suite for message handling in VtslsClient."""

    @pytest.mark.asyncio
    async def test_request_response_correlation(self):
        """Test that requests are properly correlated with responses."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "package.json").write_text('{"name": "test"}')

            with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
                client = VtslsClient(project_root)

                # Create a mock future for a request
                request_id = 1
                future = asyncio.Future()
                client.pending_requests[request_id] = future

                # Simulate receiving a response
                response_message = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"success": True},
                }

                await client._handle_message(response_message)

                # Verify the future was resolved
                assert future.done()
                assert future.result() == {"success": True}

    @pytest.mark.asyncio
    async def test_error_response_handling(self):
        """Test handling of error responses."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "package.json").write_text('{"name": "test"}')

            with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
                client = VtslsClient(project_root)

                request_id = 1
                future = asyncio.Future()
                client.pending_requests[request_id] = future

                error_message = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": "Server error"},
                }

                await client._handle_message(error_message)

                assert future.done()
                with pytest.raises(LSPRequestError):
                    future.result()

    @pytest.mark.asyncio
    async def test_notification_handler_registration(self):
        """Test registering and handling notifications."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "package.json").write_text('{"name": "test"}')

            with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
                client = VtslsClient(project_root)

                # Register a handler
                handler = AsyncMock()
                client.on_notification("textDocument/publishDiagnostics", handler)

                # Send a notification
                notification_message = {
                    "jsonrpc": "2.0",
                    "method": "textDocument/publishDiagnostics",
                    "params": {"uri": "file:///test.ts", "diagnostics": []},
                }

                await client._handle_message(notification_message)
                handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_notification_handler(self):
        """Test handling sync notification handlers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "package.json").write_text('{"name": "test"}')

            with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
                client = VtslsClient(project_root)

                # Register a synchronous handler
                handler = MagicMock()
                client.on_notification("custom/event", handler)

                notification_message = {
                    "jsonrpc": "2.0",
                    "method": "custom/event",
                    "params": {"data": "test"},
                }

                await client._handle_message(notification_message)
                handler.assert_called_once_with({"data": "test"})

    @pytest.mark.asyncio
    async def test_unhandled_notification(self):
        """Test that unhandled notifications don't cause errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "package.json").write_text('{"name": "test"}')

            with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
                client = VtslsClient(project_root)

                notification_message = {
                    "jsonrpc": "2.0",
                    "method": "unknown/method",
                    "params": {},
                }

                # Should not raise
                await client._handle_message(notification_message)


class TestVtslsClientTimeout:
    """Test suite for timeout handling in VtslsClient."""

    @pytest.mark.asyncio
    async def test_request_timeout(self):
        """Test that requests timeout properly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "package.json").write_text('{"name": "test"}')

            with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
                client = VtslsClient(project_root)
                client.request_timeout = 0.01

                # Mock the process
                client.process = MagicMock()
                client.process.stdin = MagicMock()
                client._loop = asyncio.get_running_loop()

                with patch.object(client, "_send_message", new_callable=AsyncMock):
                    with pytest.raises(LSPRequestError) as exc_info:
                        await client.request("test/method", {})

                    assert exc_info.value.is_retryable is True
                    assert "timed out" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_timeout_cleans_up_pending(self):
        """Test that timed out requests are cleaned from pending."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "package.json").write_text('{"name": "test"}')

            with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
                client = VtslsClient(project_root)
                client.request_timeout = 0.01
                client.process = MagicMock()
                client.process.stdin = MagicMock()
                client._loop = asyncio.get_running_loop()

                with patch.object(client, "_send_message", new_callable=AsyncMock):
                    try:
                        await client.request("test/method", {})
                    except LSPRequestError:
                        pass

                    # Verify the pending request was cleaned up
                    assert 0 not in client.pending_requests


class TestVtslsClientWorkspaceConfiguration:
    """Test suite for workspace configuration handling."""

    @pytest.mark.asyncio
    async def test_handle_workspace_configuration(self):
        """Test handling workspace/configuration requests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "package.json").write_text('{"name": "test"}')

            with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
                client = VtslsClient(project_root)

                params = {
                    "items": [
                        {"section": "typescript"},
                        {"section": "javascript"},
                        {"section": "vtsls"},
                    ]
                }

                result = await client._handle_workspace_configuration(params)

                assert len(result) == 3
                assert "preferences" in result[0]  # TypeScript config
                assert "preferences" in result[1]  # JavaScript config
                assert "experimental" in result[2]  # vtsls config

    @pytest.mark.asyncio
    async def test_workspace_configuration_with_custom_config(self):
        """Test workspace configuration with user overrides."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "package.json").write_text('{"name": "test"}')

            custom_config = {
                "typescript": {"customKey": "customValue"}
            }

            with patch.object(VtslsClient, "_find_vtsls", return_value="vtsls"):
                client = VtslsClient(project_root, config=custom_config)

                params = {"items": [{"section": "typescript"}]}

                result = await client._handle_workspace_configuration(params)

                # The config is merged (updated) with the base config
                assert result[0]["customKey"] == "customValue"
                assert "preferences" in result[0]  # Base preferences still there
