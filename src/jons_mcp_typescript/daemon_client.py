"""Client for the FormatterLinter daemon process."""

import asyncio
import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any

from .constants import FORMAT_TIMEOUT, LINT_TIMEOUT
from .exceptions import DaemonError, DaemonTimeoutError

logger = logging.getLogger(__name__)


class FormatterLinterDaemon:
    """Client for communicating with the FormatterLinter daemon process.

    The daemon provides formatting and linting services via a JSON Lines protocol
    over stdin/stdout. It manages Prettier and ESLint configurations and provides
    a simple request/response interface.
    """

    def __init__(self, daemon_script: Path, project_root: Path):
        """Initialize the daemon client.

        Args:
            daemon_script: Path to the daemon's index.js file.
            project_root: Root directory of the project to format/lint.
        """
        self.daemon_script = daemon_script
        self.project_root = project_root
        self._process: subprocess.Popen | None = None
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._request_id = 0
        self._ready = asyncio.Event()
        self._shutting_down = False

        # Thread-based I/O
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._writer_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def create(cls, project_root: Path) -> "FormatterLinterDaemon":
        """Create a daemon client with automatic daemon script path resolution.

        Args:
            project_root: Root directory of the project to format/lint.

        Returns:
            A new FormatterLinterDaemon instance.
        """
        # Locate daemon script relative to this file
        daemon_script = Path(__file__).parent / "daemon" / "index.js"
        return cls(daemon_script, project_root)

    async def start(self):
        """Start the daemon process and wait for ready signal.

        Raises:
            RuntimeError: If daemon is already started or fails to start.
            DaemonTimeoutError: If ready signal not received within timeout.
        """
        if self._process:
            raise RuntimeError("Daemon already started")

        # Store event loop for thread-to-async communication
        self._loop = asyncio.get_running_loop()

        logger.info(f"Starting FormatterLinter daemon for project: {self.project_root}")
        logger.info(f"Using daemon script: {self.daemon_script}")

        try:
            self._process = subprocess.Popen(
                ['node', str(self.daemon_script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(self.project_root)
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start daemon: {e}")

        # Reset shutdown flag
        self._shutting_down = False

        # Start reader threads
        self._start_reader_thread()
        self._start_stderr_thread()

        # Wait for ready signal with timeout
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=10.0)
            logger.info("Daemon ready")
        except asyncio.TimeoutError:
            # Clean up on timeout
            await self.shutdown()
            raise DaemonTimeoutError("Daemon failed to send ready signal within 10s")

    def _start_reader_thread(self):
        """Start thread to read JSON Lines from daemon stdout."""
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name="daemon-reader"
        )
        self._reader_thread.start()

    def _start_stderr_thread(self):
        """Start thread to drain stderr and prevent deadlock."""
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop,
            daemon=True,
            name="daemon-stderr"
        )
        self._stderr_thread.start()

    def _reader_loop(self):
        """Read JSON Lines from stdout in a thread."""
        logger.debug("Reader thread started")

        while self._process and self._process.stdout and not self._shutting_down:
            try:
                line = self._process.stdout.readline()
                if not line:
                    logger.debug("Reader thread: EOF")
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    message = json.loads(line)
                    # Schedule async handling in the event loop
                    if self._loop:
                        asyncio.run_coroutine_threadsafe(
                            self._handle_message(message),
                            self._loop
                        )
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON line: {e}\nLine: {line}")

            except Exception as e:
                if not self._shutting_down:
                    logger.error(f"Error in reader thread: {e}")
                break

    def _stderr_loop(self):
        """Read stderr in a thread to prevent deadlock."""
        while self._process and self._process.stderr and not self._shutting_down:
            try:
                line = self._process.stderr.readline()
                if line:
                    decoded = line.strip()
                    if decoded:
                        if "error" in decoded.lower():
                            logger.error(f"Daemon stderr: {decoded}")
                        else:
                            logger.debug(f"Daemon stderr: {decoded}")
                else:
                    break
            except Exception:
                break

    async def _handle_message(self, message: dict[str, Any]):
        """Handle incoming message from daemon.

        Args:
            message: Parsed JSON message from daemon.
        """
        logger.debug(f"Received message: {json.dumps(message)[:200]}")

        # Check for ready event
        if message.get("event") == "ready":
            version = message.get("version")
            logger.info(f"Daemon ready (protocol version {version})")
            self._ready.set()
            return

        # Check for response (has 'id' field)
        if "id" in message:
            request_id = message["id"]
            future = self._pending_requests.pop(request_id, None)

            if future and not future.done():
                if "error" in message:
                    error = message["error"]
                    error_msg = error.get("message", "Unknown error")
                    error_code = error.get("code", -32000)
                    future.set_exception(DaemonError(error_msg, error_code))
                elif "result" in message:
                    future.set_result(message["result"])
                else:
                    logger.warning(f"Response {request_id} has no result or error")
                    future.set_exception(DaemonError("Invalid response format", -32000))
            elif not future:
                logger.warning(f"Received response for unknown request: {request_id}")
        else:
            logger.warning(f"Received message without id or event: {message}")

    async def send_request(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float = 30.0
    ) -> dict[str, Any]:
        """Send request to daemon and wait for response.

        Args:
            method: Method name (e.g., "format", "lint", "check", "getConfig").
            params: Method parameters.
            timeout: Request timeout in seconds.

        Returns:
            Response result dictionary.

        Raises:
            RuntimeError: If daemon not started.
            DaemonError: If daemon returns an error.
            DaemonTimeoutError: If request times out.
        """
        if not self._process or not self._process.stdin:
            raise RuntimeError("Daemon not started")

        request_id = f"req-{self._request_id}"
        self._request_id += 1

        # Create future for response
        future: asyncio.Future = asyncio.Future()
        self._pending_requests[request_id] = future

        # Build request
        request = {
            "id": request_id,
            "version": 1,
            "method": method,
            "params": params
        }

        # Write JSON line to stdin
        try:
            with self._writer_lock:
                json_line = json.dumps(request) + '\n'
                self._process.stdin.write(json_line)
                self._process.stdin.flush()

            logger.debug(f"Sent request {method} (id={request_id})")
        except Exception as e:
            del self._pending_requests[request_id]
            raise RuntimeError(f"Failed to send request: {e}")

        # Wait for response with timeout
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.debug(f"Got response for {method} (id={request_id})")
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise DaemonTimeoutError(
                f"Request {method} timed out after {timeout}s"
            )

    async def format(self, filepath: str, content: str) -> dict[str, Any]:
        """Format code using Prettier.

        Args:
            filepath: Path to the file (for config resolution).
            content: Code content to format.

        Returns:
            Dictionary with keys:
                - formatted: The formatted code (str)
                - configPath: Path to config used (str or None)

        Raises:
            DaemonError: If formatting fails.
            DaemonTimeoutError: If request times out.
        """
        return await self.send_request(
            "format",
            {
                "filepath": filepath,
                "content": content,
                "projectRoot": str(self.project_root)
            },
            timeout=FORMAT_TIMEOUT
        )

    async def check_formatting(self, filepath: str, content: str) -> dict[str, Any]:
        """Check if code is formatted correctly.

        Args:
            filepath: Path to the file (for config resolution).
            content: Code content to check.

        Returns:
            Dictionary with keys:
                - isFormatted: Whether code is already formatted (bool)
                - configPath: Path to config used (str or None)

        Raises:
            DaemonError: If check fails.
            DaemonTimeoutError: If request times out.
        """
        return await self.send_request(
            "check",
            {
                "filepath": filepath,
                "content": content,
                "projectRoot": str(self.project_root)
            },
            timeout=FORMAT_TIMEOUT
        )

    async def lint(
        self,
        filepath: str,
        content: str,
        fix: bool = False
    ) -> dict[str, Any]:
        """Lint code using ESLint.

        Args:
            filepath: Path to the file (for config resolution).
            content: Code content to lint.
            fix: Whether to apply auto-fixes.

        Returns:
            Dictionary with keys:
                - messages: List of lint messages (list)
                - fixedContent: Fixed content if fix=True (str or None)
                - configPath: Path to config used (str or None)

        Raises:
            DaemonError: If linting fails.
            DaemonTimeoutError: If request times out.
        """
        return await self.send_request(
            "lint",
            {
                "filepath": filepath,
                "content": content,
                "projectRoot": str(self.project_root),
                "fix": fix
            },
            timeout=LINT_TIMEOUT
        )

    async def get_prettier_config(self, filepath: str) -> dict[str, Any]:
        """Get Prettier configuration for a file.

        Args:
            filepath: Path to the file.

        Returns:
            Dictionary with keys:
                - config: The resolved Prettier config (dict or None)
                - configPath: Path to config file (str or None)

        Raises:
            DaemonError: If config resolution fails.
            DaemonTimeoutError: If request times out.
        """
        return await self.send_request(
            "getConfig",
            {
                "filepath": filepath,
                "tool": "prettier",
                "projectRoot": str(self.project_root)
            },
            timeout=10.0
        )

    async def get_eslint_config(self, filepath: str) -> dict[str, Any]:
        """Get ESLint configuration for a file.

        Args:
            filepath: Path to the file.

        Returns:
            Dictionary with keys:
                - config: The resolved ESLint config (dict or None)
                - configPath: Path to config file (str or None)

        Raises:
            DaemonError: If config resolution fails.
            DaemonTimeoutError: If request times out.
        """
        return await self.send_request(
            "getConfig",
            {
                "filepath": filepath,
                "tool": "eslint",
                "projectRoot": str(self.project_root)
            },
            timeout=10.0
        )

    async def restart(self):
        """Restart the daemon process.

        Useful for recovering from errors or applying configuration changes.

        Raises:
            RuntimeError: If restart fails.
            DaemonTimeoutError: If ready signal not received after restart.
        """
        logger.info("Restarting daemon...")

        # Shutdown existing process
        await self.shutdown()

        # Clear state
        self._request_id = 0
        self._pending_requests.clear()
        self._ready.clear()

        # Start new process
        await self.start()

        logger.info("Daemon restarted successfully")

    async def shutdown(self):
        """Shutdown the daemon process gracefully.

        Cancels all pending requests and terminates the subprocess.
        """
        if not self._process:
            return

        logger.debug("Starting daemon shutdown...")
        self._shutting_down = True

        # Cancel all pending requests
        for request_id, future in list(self._pending_requests.items()):
            if not future.done():
                future.set_exception(
                    DaemonError("Daemon shutting down", -32000)
                )
        self._pending_requests.clear()

        # Close stdin to signal daemon to exit
        if self._process.stdin:
            try:
                self._process.stdin.close()
            except Exception as e:
                logger.debug(f"Error closing stdin: {e}")

        # Give it a moment to exit cleanly
        try:
            await asyncio.sleep(0.2)
        except Exception:
            pass

        # Terminate process if still running
        if self._process.poll() is None:
            logger.debug("Process still running, terminating...")
            self._process.terminate()
            try:
                await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        None, self._process.wait
                    ),
                    timeout=5
                )
            except asyncio.TimeoutError:
                logger.debug("Process didn't terminate, killing...")
                self._process.kill()
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None, self._process.wait
                    )
                except Exception:
                    pass

        self._process = None
        self._ready.clear()
        logger.debug("Daemon shutdown complete")
