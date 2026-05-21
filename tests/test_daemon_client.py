"""Unit tests for daemon client functionality."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jons_mcp_typescript.constants import FORMAT_TIMEOUT, LINT_TIMEOUT
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
                            async def timeout_wait_for(awaitable, timeout):
                                awaitable.close()
                                raise asyncio.TimeoutError

                            with patch("asyncio.wait_for", side_effect=timeout_wait_for):
                                with pytest.raises(DaemonTimeoutError):
                                    await daemon.start()


@pytest.mark.parametrize(
    ("call_name", "args", "expected_method", "expected_params", "expected_timeout"),
    [
        (
            "format",
            ("/test.ts", "original code"),
            "format",
            {
                "filepath": "/test.ts",
                "content": "original code",
                "projectRoot": "/project",
            },
            FORMAT_TIMEOUT,
        ),
        (
            "check_formatting",
            ("/test.ts", "code"),
            "check",
            {
                "filepath": "/test.ts",
                "content": "code",
                "projectRoot": "/project",
            },
            FORMAT_TIMEOUT,
        ),
        (
            "lint",
            ("/test.ts", "code", True),
            "lint",
            {
                "filepath": "/test.ts",
                "content": "code",
                "projectRoot": "/project",
                "fix": True,
            },
            LINT_TIMEOUT,
        ),
        (
            "get_prettier_config",
            ("/test.ts",),
            "getConfig",
            {
                "filepath": "/test.ts",
                "tool": "prettier",
                "projectRoot": "/project",
            },
            10.0,
        ),
        (
            "get_eslint_config",
            ("/test.ts",),
            "getConfig",
            {
                "filepath": "/test.ts",
                "tool": "eslint",
                "projectRoot": "/project",
            },
            10.0,
        ),
    ],
)
@pytest.mark.asyncio
async def test_daemon_public_methods_send_expected_requests(
    call_name: str,
    args: tuple,
    expected_method: str,
    expected_params: dict,
    expected_timeout: float,
):
    """Public wrapper methods should preserve the daemon wire contract."""
    daemon = FormatterLinterDaemon(Path("/daemon.js"), Path("/project"))
    mock_result = {"ok": True}

    with patch.object(
        daemon,
        "send_request",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as send_request:
        result = await getattr(daemon, call_name)(*args)

    assert result == mock_result
    send_request.assert_awaited_once_with(
        expected_method,
        expected_params,
        timeout=expected_timeout,
    )


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

            def mock_wait(*_args, **_kwargs):
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
