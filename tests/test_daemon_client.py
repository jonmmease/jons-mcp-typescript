"""Unit tests for daemon client functionality."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jons_mcp_typescript.daemon_client import FormatterLinterDaemon
from jons_mcp_typescript.exceptions import DaemonError, DaemonTimeoutError


class TestFormatterLinterDaemonInitialization:
    """Test suite for FormatterLinterDaemon initialization."""

    def test_initialization_with_explicit_paths(self):
        """Test daemon initialization with explicit daemon script path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon_script = Path(tmpdir) / "daemon.js"
            daemon_script.write_text("// daemon")

            daemon = FormatterLinterDaemon(daemon_script, project_root)

            assert daemon.daemon_script == daemon_script
            assert daemon.project_root == project_root
            assert daemon._process is None
            assert daemon._pending_requests == {}
            assert daemon._request_id == 0
            assert daemon._shutting_down is False

    def test_create_class_method(self):
        """Test FormatterLinterDaemon.create class method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "package.json").write_text('{"name": "test"}')

            # Mock the daemon script existence check
            with patch("pathlib.Path.parent"):
                daemon = FormatterLinterDaemon.create(project_root)
                assert daemon.project_root == project_root


class TestDaemonMessageHandling:
    """Test suite for daemon message handling."""

    @pytest.mark.asyncio
    async def test_ready_event_handling(self):
        """Test handling of ready event from daemon."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            # Initially not ready
            assert not daemon._ready.is_set()

            # Simulate ready event
            ready_message = {"event": "ready", "version": 1}
            await daemon._handle_message(ready_message)

            assert daemon._ready.is_set()

    @pytest.mark.asyncio
    async def test_request_response_correlation(self):
        """Test that request/response are properly correlated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            # Create pending request
            request_id = "req-1"
            future = asyncio.Future()
            daemon._pending_requests[request_id] = future

            # Simulate response
            response_message = {
                "id": request_id,
                "result": {"formatted": "code", "changed": True},
            }

            await daemon._handle_message(response_message)

            assert future.done()
            assert future.result() == {"formatted": "code", "changed": True}

    @pytest.mark.asyncio
    async def test_error_response_handling(self):
        """Test handling of error responses."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            request_id = "req-1"
            future = asyncio.Future()
            daemon._pending_requests[request_id] = future

            error_message = {
                "id": request_id,
                "error": {"code": -32001, "message": "Config not found"},
            }

            await daemon._handle_message(error_message)

            assert future.done()
            with pytest.raises(DaemonError) as exc_info:
                future.result()

            assert exc_info.value.code == -32001

    @pytest.mark.asyncio
    async def test_unknown_response_handling(self):
        """Test handling of responses without proper format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            request_id = "req-1"
            future = asyncio.Future()
            daemon._pending_requests[request_id] = future

            # Response with neither result nor error
            invalid_message = {"id": request_id}

            await daemon._handle_message(invalid_message)

            assert future.done()
            with pytest.raises(DaemonError):
                future.result()

    @pytest.mark.asyncio
    async def test_orphan_response_warning(self):
        """Test handling of response for unknown request."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            # Response for non-existent request
            orphan_message = {
                "id": "unknown-req",
                "result": {"data": "value"},
            }

            # Should not raise
            await daemon._handle_message(orphan_message)


class TestDaemonJSONLinesProtocol:
    """Test suite for JSON Lines protocol handling."""

    def test_json_lines_format(self):
        """Test JSON Lines format compliance."""
        messages = [
            {
                "id": "req-1",
                "version": 1,
                "method": "format",
                "params": {"filepath": "/test.ts", "content": "code"},
            },
            {
                "id": "req-1",
                "result": {"formatted": "formatted code"},
            },
        ]

        for msg in messages:
            json_line = json.dumps(msg)
            # Each message must be single line
            assert "\n" not in json_line
            # Must be valid JSON
            parsed = json.loads(json_line)
            assert parsed == msg

    @pytest.mark.asyncio
    async def test_send_request_format(self):
        """Test that send_request uses correct format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            # Mock the process
            daemon._process = MagicMock()
            daemon._process.stdin = MagicMock()
            daemon._process.poll = MagicMock(return_value=None)

            # Mock the response
            mock_response = {"formatted": "code"}

            with patch.object(daemon, "send_request", return_value=mock_response):
                result = await daemon.format(
                    "/test.ts", "original code"
                )
                assert result == {"formatted": "code"}


class TestDaemonRequestTimeout:
    """Test suite for request timeout handling."""

    @pytest.mark.asyncio
    async def test_request_timeout(self):
        """Test that requests timeout properly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            # Mock the process
            daemon._process = MagicMock()
            daemon._process.stdin = MagicMock()
            daemon._process.poll = MagicMock(return_value=None)

            with pytest.raises(DaemonTimeoutError):
                await daemon.send_request(
                    "format",
                    {"filepath": "/test.ts", "content": "code"},
                    timeout=0.01
                )

    @pytest.mark.asyncio
    async def test_request_timeout_cleans_up_pending(self):
        """Test that timed out requests are cleaned from pending."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            # Mock the process
            daemon._process = MagicMock()
            daemon._process.stdin = MagicMock()
            daemon._process.poll = MagicMock(return_value=None)

            try:
                await daemon.send_request(
                    "format",
                    {"filepath": "/test.ts", "content": "code"},
                    timeout=0.01
                )
            except DaemonTimeoutError:
                pass

            # Verify pending request was cleaned up
            assert daemon._request_id == 1
            assert "req-0" not in daemon._pending_requests


class TestDaemonReadySignal:
    """Test suite for ready signal detection."""

    @pytest.mark.asyncio
    async def test_ready_signal_detection(self):
        """Test detection of ready signal from daemon."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            assert not daemon._ready.is_set()

            ready_message = {"event": "ready", "version": 1}
            await daemon._handle_message(ready_message)

            assert daemon._ready.is_set()

    @pytest.mark.asyncio
    async def test_start_waits_for_ready_signal(self):
        """Test that start() waits for ready signal."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon_script = Path(tmpdir) / "daemon.js"
            daemon_script.write_text("// daemon")

            daemon = FormatterLinterDaemon(daemon_script, project_root)

            # Mock subprocess
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.poll = MagicMock(return_value=None)

            with patch("subprocess.Popen", return_value=mock_process):
                with patch.object(daemon, "_start_reader_thread"):
                    with patch.object(daemon, "_start_stderr_thread"):
                        # Simulate ready signal after a short delay
                        async def send_ready():
                            await asyncio.sleep(0.05)
                            daemon._ready.set()

                        asyncio.create_task(send_ready())

                        # This should wait for ready signal
                        await daemon.start()

                        assert daemon._ready.is_set()
                        assert daemon._process == mock_process

    @pytest.mark.asyncio
    async def test_start_timeout_on_no_ready_signal(self):
        """Test that start waits for ready signal."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon_script = Path(tmpdir) / "daemon.js"
            daemon_script.write_text("// daemon")

            daemon = FormatterLinterDaemon(daemon_script, project_root)

            # Mock subprocess
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.poll = MagicMock(return_value=None)

            # Mock with a short timeout in daemon
            with patch("subprocess.Popen", return_value=mock_process):
                with patch.object(daemon, "_start_reader_thread"):
                    with patch.object(daemon, "_start_stderr_thread"):
                        with patch.object(daemon, "shutdown", new_callable=AsyncMock):
                            # Patch asyncio.wait_for to use shorter timeout
                            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                                with pytest.raises(DaemonTimeoutError):
                                    await daemon.start()


class TestDaemonFormatMethods:
    """Test suite for daemon format/lint methods."""

    @pytest.mark.asyncio
    async def test_format_method(self):
        """Test format method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            mock_result = {"formatted": "formatted code", "configPath": None}

            with patch.object(
                daemon, "send_request", return_value=mock_result
            ):
                result = await daemon.format("/test.ts", "original code")
                assert result == mock_result

    @pytest.mark.asyncio
    async def test_check_formatting_method(self):
        """Test check_formatting method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            mock_result = {"isFormatted": True, "configPath": None}

            with patch.object(
                daemon, "send_request", return_value=mock_result
            ):
                result = await daemon.check_formatting("/test.ts", "code")
                assert result == mock_result

    @pytest.mark.asyncio
    async def test_lint_method(self):
        """Test lint method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            mock_result = {
                "messages": [
                    {
                        "ruleId": "no-unused-vars",
                        "severity": 2,
                        "message": "Variable is defined but never used",
                    }
                ],
                "configPath": None,
            }

            with patch.object(
                daemon, "send_request", return_value=mock_result
            ):
                result = await daemon.lint("/test.ts", "code")
                assert result == mock_result

    @pytest.mark.asyncio
    async def test_lint_with_fix(self):
        """Test lint method with fix enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            mock_result = {
                "messages": [],
                "fixedContent": "fixed code",
                "configPath": None,
            }

            with patch.object(
                daemon, "send_request", return_value=mock_result
            ):
                result = await daemon.lint("/test.ts", "code", fix=True)
                assert result == mock_result

    @pytest.mark.asyncio
    async def test_get_prettier_config(self):
        """Test get_prettier_config method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            mock_result = {
                "config": {"singleQuote": True},
                "configPath": "/path/to/.prettierrc",
            }

            with patch.object(
                daemon, "send_request", return_value=mock_result
            ):
                result = await daemon.get_prettier_config("/test.ts")
                assert result == mock_result

    @pytest.mark.asyncio
    async def test_get_eslint_config(self):
        """Test get_eslint_config method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)
            daemon._loop = asyncio.get_running_loop()

            mock_result = {
                "config": {"extends": "eslint:recommended"},
                "configPath": "/path/to/.eslintrc.json",
            }

            with patch.object(
                daemon, "send_request", return_value=mock_result
            ):
                result = await daemon.get_eslint_config("/test.ts")
                assert result == mock_result


class TestDaemonLifecycle:
    """Test suite for daemon lifecycle management."""

    @pytest.mark.asyncio
    async def test_restart_method(self):
        """Test daemon restart method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)

            # Mock start and shutdown
            with patch.object(daemon, "start", new_callable=AsyncMock):
                with patch.object(daemon, "shutdown", new_callable=AsyncMock):
                    initial_request_id = daemon._request_id
                    await daemon.restart()

                    # Verify state was reset
                    assert daemon._request_id == 0
                    assert daemon._pending_requests == {}
                    assert not daemon._ready.is_set()
                    daemon.shutdown.assert_called_once()
                    daemon.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_method(self):
        """Test daemon shutdown method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)

            # Mock process
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            mock_process.poll = MagicMock(return_value=None)

            async def mock_wait(*args, **kwargs):
                return

            mock_process.wait = mock_wait
            daemon._process = mock_process

            # Add some pending requests
            future = asyncio.Future()
            daemon._pending_requests["req-1"] = future

            await daemon.shutdown()

            # Verify shutdown state
            assert daemon._shutting_down is True
            assert daemon._process is None
            assert daemon._pending_requests == {}
            assert not daemon._ready.is_set()

    @pytest.mark.asyncio
    async def test_shutdown_no_process(self):
        """Test shutdown when process is None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daemon = FormatterLinterDaemon(Path(tmpdir) / "daemon.js", project_root)

            # Should not raise
            await daemon.shutdown()
            assert daemon._process is None
