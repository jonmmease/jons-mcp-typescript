"""LSP client for vtsls (Vue/TypeScript Language Server)."""

import asyncio
import json
import logging
import os
import queue
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable
from inspect import iscoroutinefunction
from pathlib import Path
from typing import Any

from .constants import REQUEST_TIMEOUT
from .exceptions import LSPRequestError, ProcessCrashError, VtslsNotFoundError

logger = logging.getLogger(__name__)


class ProcessWatchdog:
    """Monitor subprocess health, auto-restart on crash."""

    def __init__(self, max_restarts: int = 3, restart_delay: float = 1.0):
        """Initialize the watchdog.

        Args:
            max_restarts: Maximum number of restart attempts before giving up.
            restart_delay: Initial delay between restarts (exponential backoff).
        """
        self.max_restarts = max_restarts
        self.restart_delay = restart_delay
        self.restart_count = 0
        self._stopped = False

    def reset(self) -> None:
        """Reset the restart counter (call after successful operations)."""
        self.restart_count = 0

    def stop(self) -> None:
        """Stop monitoring."""
        self._stopped = True

    async def monitor(
        self, process: subprocess.Popen, restart_fn: Callable[[], Any]
    ) -> subprocess.Popen:
        """Monitor process, restart on unexpected exit.

        Args:
            process: The subprocess to monitor.
            restart_fn: Async function to call for restart, returns new process.

        Returns:
            The current or restarted process.

        Raises:
            ProcessCrashError: When max restarts exceeded.
        """
        while not self._stopped:
            if process.poll() is not None:  # Process died
                exit_code = process.returncode
                logger.warning(f"vtsls process died with exit code {exit_code}")

                if self.restart_count < self.max_restarts:
                    delay = self.restart_delay * (2**self.restart_count)
                    logger.info(
                        f"Restarting vtsls in {delay}s "
                        f"(attempt {self.restart_count + 1}/{self.max_restarts})"
                    )
                    await asyncio.sleep(delay)
                    process = await restart_fn()
                    self.restart_count += 1
                else:
                    raise ProcessCrashError(
                        f"vtsls crashed {self.max_restarts} times, giving up. "
                        f"Last exit code: {exit_code}"
                    )
            await asyncio.sleep(1.0)
        return process


class VtslsClient:
    """Thread-based LSP client for vtsls (Vue/TypeScript Language Server)."""

    def __init__(
        self,
        project_root: Path,
        config: dict[str, Any] | None = None,
        vtsls_path: str | None = None,
    ):
        """Initialize the vtsls client.

        Args:
            project_root: Root directory of the TypeScript project.
            config: Optional configuration overrides.
            vtsls_path: Optional explicit path to vtsls executable.
        """
        self.project_root = project_root
        self.config = config or {}
        self.vtsls_path = vtsls_path or self._find_vtsls()
        self.process: subprocess.Popen | None = None
        self.request_id = 0
        self.pending_requests: dict[int, asyncio.Future] = {}
        self.notification_handlers: dict[str, Callable] = {}
        self._initialized = False
        self._shutting_down = False
        self.request_timeout = REQUEST_TIMEOUT

        # Thread-based I/O
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._writer_lock = threading.Lock()
        self._message_queue: queue.Queue = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

        # Process watchdog
        self._watchdog: ProcessWatchdog | None = None
        self._watchdog_task: asyncio.Task | None = None

    def _find_vtsls(self) -> str:
        """Find vtsls executable.

        Discovery order:
        1. VTSLS_PATH environment variable
        2. vtsls in PATH (via which)
        3. npm global installation

        Returns:
            Path to vtsls executable.

        Raises:
            VtslsNotFoundError: If vtsls cannot be found.
        """
        # Check environment variable first
        if env_path := os.environ.get("VTSLS_PATH"):
            if Path(env_path).exists():
                logger.info(f"Using vtsls from VTSLS_PATH: {env_path}")
                return env_path
            else:
                logger.warning(f"VTSLS_PATH set but file not found: {env_path}")

        # Try to find vtsls in PATH
        if path := shutil.which("vtsls"):
            logger.info(f"Found vtsls in PATH: {path}")
            return path

        # Try npm global installation
        try:
            npm_prefix_result = subprocess.run(
                ["npm", "prefix", "-g"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            npm_prefix = npm_prefix_result.stdout.strip()

            if npm_prefix:
                # Standard npm global location
                node_path = (
                    Path(npm_prefix)
                    / "lib"
                    / "node_modules"
                    / "@vtsls"
                    / "language-server"
                    / "bin"
                    / "vtsls.js"
                )
                if node_path.exists():
                    logger.info(f"Found vtsls via npm global: {node_path}")
                    return f"node {shlex.quote(str(node_path))}"

                # Also check without 'lib' (some npm configurations)
                alt_path = (
                    Path(npm_prefix)
                    / "node_modules"
                    / "@vtsls"
                    / "language-server"
                    / "bin"
                    / "vtsls.js"
                )
                if alt_path.exists():
                    logger.info(f"Found vtsls via npm global (alt): {alt_path}")
                    return f"node {shlex.quote(str(alt_path))}"
        except subprocess.TimeoutExpired:
            logger.warning("npm prefix -g timed out")
        except FileNotFoundError:
            logger.debug("npm not found on PATH")
        except Exception as e:
            logger.warning(f"Error checking npm global: {e}")

        # Check project-local node_modules
        local_vtsls = (
            self.project_root
            / "node_modules"
            / "@vtsls"
            / "language-server"
            / "bin"
            / "vtsls.js"
        )
        if local_vtsls.exists():
            logger.info(f"Found vtsls in local node_modules: {local_vtsls}")
            return f"node {shlex.quote(str(local_vtsls))}"

        raise VtslsNotFoundError(
            "vtsls not found. Install it with: npm install -g @vtsls/language-server"
        )

    def is_initialized(self) -> bool:
        """Check if the client is initialized."""
        return self._initialized

    async def start(self) -> None:
        """Start the vtsls process and initialize communication."""
        if self.process:
            raise RuntimeError("Already started")

        # Store event loop for thread-to-async communication
        self._loop = asyncio.get_running_loop()

        logger.info(f"Starting vtsls for project: {self.project_root}")
        logger.info(f"Using vtsls command: {self.vtsls_path}")

        await self._start_process()

        # Initialize LSP connection
        await self._initialize()

        # Start watchdog
        self._watchdog = ProcessWatchdog(max_restarts=3, restart_delay=1.0)
        self._watchdog_task = asyncio.create_task(self._run_watchdog())

    async def _start_process(self) -> None:
        """Start the vtsls subprocess and reader threads."""
        # Start process with unbuffered output
        env = os.environ.copy()
        env["NODE_NO_WARNINGS"] = "1"  # Suppress Node.js warnings

        try:
            # Split command for subprocess while preserving quoted paths.
            if Path(self.vtsls_path).exists():
                cmd_parts = [self.vtsls_path]
            else:
                cmd_parts = shlex.split(self.vtsls_path)
            # Add --stdio flag if not already present
            if "--stdio" not in cmd_parts:
                cmd_parts.append("--stdio")

            self.process = subprocess.Popen(
                cmd_parts,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.project_root),
                env=env,
                bufsize=0,  # Unbuffered
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start vtsls: {e}") from e

        # Reset shutdown flag
        self._shutting_down = False

        # Start reader threads
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread.start()

        # Process messages from queue
        asyncio.create_task(self._process_messages())

    def _reader_loop(self) -> None:
        """Read messages from stdout in a thread."""
        buffer = b""
        logger.debug("Reader thread started")

        while self.process and not self._shutting_down:
            try:
                stdout = self.process.stdout
                if stdout is None:
                    break
                # Read one byte at a time to avoid blocking
                byte = stdout.read(1)
                if not byte:
                    logger.debug("Reader thread: EOF")
                    break

                buffer += byte

                # Check for complete message (LSP header ends with \r\n\r\n)
                header_end = buffer.find(b"\r\n\r\n")
                if header_end == -1:
                    continue

                # Parse header
                header = buffer[:header_end].decode("utf-8")
                content_length = None
                for line in header.split("\r\n"):
                    if line.startswith("Content-Length: "):
                        content_length = int(line[16:])
                        break

                if content_length is None:
                    logger.warning("No Content-Length in header, skipping")
                    buffer = buffer[header_end + 4 :]
                    continue

                # Read content body
                content_start = header_end + 4
                while len(buffer) < content_start + content_length:
                    chunk = stdout.read(
                        min(4096, content_start + content_length - len(buffer))
                    )
                    if not chunk:
                        logger.debug("Reader thread: EOF while reading content")
                        return
                    buffer += chunk

                # Extract message
                content = buffer[content_start : content_start + content_length]
                buffer = buffer[content_start + content_length :]

                try:
                    message = json.loads(content.decode("utf-8"))
                    # Put message in queue for async processing
                    method_or_id = message.get(
                        "method", f"response id={message.get('id')}"
                    )
                    logger.debug(f"Reader thread: queuing message {method_or_id}")
                    self._message_queue.put(message)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON: {e}")

            except Exception as e:
                if not self._shutting_down:
                    logger.error(f"Error in reader thread: {e}")
                break

    def _stderr_loop(self) -> None:
        """Read stderr in a thread to prevent deadlock."""
        while self.process and self.process.stderr and not self._shutting_down:
            try:
                line = self.process.stderr.readline()
                if line:
                    decoded = line.decode().strip()
                    if decoded:
                        if "error" in decoded.lower() or "panic" in decoded.lower():
                            logger.error(f"vtsls stderr: {decoded}")
                        else:
                            logger.debug(f"vtsls stderr: {decoded}")
                else:
                    break
            except Exception:
                break

    async def _process_messages(self) -> None:
        """Process messages from the queue."""
        logger.debug("Message processor started")
        while not self._shutting_down:
            try:
                # Check if queue has items
                if not self._message_queue.empty():
                    message = self._message_queue.get_nowait()
                    logger.debug(
                        f"Processing message from queue: {message.get('method', 'response')}"
                    )
                    await self._handle_message(message)
                else:
                    # Small sleep to avoid busy waiting
                    await asyncio.sleep(0.01)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing message: {e}")

    async def _handle_message(self, message: dict[str, Any]) -> None:
        """Handle incoming LSP message."""
        logger.debug(f"Received: {json.dumps(message)[:500]}")

        if "id" in message and "method" in message:
            # Request from server to client
            request_id = message["id"]
            method = message["method"]
            params = message.get("params", {})

            logger.debug(f"Server request: {method} (id={request_id})")

            # Handle workspace/configuration request
            if method == "workspace/configuration":
                result = await self._handle_workspace_configuration(params)
                await self._send_message(
                    {"jsonrpc": "2.0", "id": request_id, "result": result}
                )
            elif method == "client/registerCapability":
                # Accept dynamic capability registration
                await self._send_message(
                    {"jsonrpc": "2.0", "id": request_id, "result": None}
                )
            elif method == "window/workDoneProgress/create":
                # Accept progress creation
                await self._send_message(
                    {"jsonrpc": "2.0", "id": request_id, "result": None}
                )
            else:
                # Send error response for unsupported methods
                logger.debug(f"Unsupported server request: {method}")
                await self._send_message(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not supported: {method}",
                        },
                    }
                )
        elif "id" in message:
            # Response to our request
            request_id = message["id"]
            future = self.pending_requests.pop(request_id, None)

            if future and not future.done():
                if "error" in message:
                    error = message["error"]
                    future.set_exception(
                        LSPRequestError(
                            f"{error.get('message', 'Unknown error')}",
                            code=error.get("code", -32000),
                        )
                    )
                else:
                    future.set_result(message.get("result"))
        else:
            # Server notification
            method = message.get("method", "")
            params = message.get("params", {})

            handler = self.notification_handlers.get(method)
            if handler:
                try:
                    # Check if handler is async
                    if iscoroutinefunction(handler):
                        await handler(params)
                    else:
                        handler(params)
                except Exception as e:
                    logger.error(f"Error in notification handler for {method}: {e}")
            else:
                logger.debug(f"Unhandled notification: {method}")

    async def _handle_workspace_configuration(
        self, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Handle workspace/configuration request from server."""
        result = []
        items = params.get("items", [])

        for item in items:
            section = item.get("section", "")
            config_response: dict[str, Any] = {}

            if section == "typescript":
                # TypeScript configuration
                config_response = {
                    "preferences": {
                        "includeInlayParameterNameHints": "none",
                        "includeInlayPropertyDeclarationTypeHints": False,
                        "includeInlayVariableTypeHints": False,
                    },
                    "suggest": {
                        "completeFunctionCalls": True,
                    },
                }
            elif section == "javascript":
                # JavaScript configuration (similar to TypeScript)
                config_response = {
                    "preferences": {
                        "includeInlayParameterNameHints": "none",
                    },
                    "suggest": {
                        "completeFunctionCalls": True,
                    },
                }
            elif section == "vtsls":
                # vtsls-specific configuration
                config_response = {
                    "experimental": {
                        "completion": {
                            "enableServerSideFuzzyMatch": True,
                        }
                    }
                }

            # Merge with any user-provided config
            if section in self.config:
                config_response.update(self.config[section])

            result.append(config_response)

        return result

    async def _initialize(self) -> None:
        """Send LSP initialize request."""
        logger.debug("Sending initialize request...")

        result = await self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "clientInfo": {"name": "typescript-mcp", "version": "0.1.0"},
                "rootUri": self.project_root.absolute().as_uri(),
                "rootPath": str(self.project_root.absolute()),
                "workspaceFolders": [
                    {
                        "uri": self.project_root.absolute().as_uri(),
                        "name": self.project_root.name,
                    }
                ],
                "capabilities": {
                    "textDocument": {
                        "hover": {"contentFormat": ["plaintext", "markdown"]},
                        "completion": {
                            "completionItem": {
                                "snippetSupport": True,
                                "resolveSupport": {
                                    "properties": [
                                        "documentation",
                                        "detail",
                                        "additionalTextEdits",
                                    ]
                                },
                            }
                        },
                        "signatureHelp": {
                            "signatureInformation": {
                                "documentationFormat": ["plaintext", "markdown"],
                                "parameterInformation": {"labelOffsetSupport": True},
                            }
                        },
                        "definition": {"linkSupport": True},
                        "typeDefinition": {"linkSupport": True},
                        "implementation": {"linkSupport": True},
                        "references": {},
                        "documentHighlight": {},
                        "documentSymbol": {
                            "hierarchicalDocumentSymbolSupport": True,
                            "symbolKind": {
                                "valueSet": list(range(1, 27))  # All symbol kinds
                            },
                        },
                        "formatting": {},
                        "rangeFormatting": {},
                        "rename": {"prepareSupport": True},
                        "codeAction": {
                            "codeActionLiteralSupport": {
                                "codeActionKind": {
                                    "valueSet": [
                                        "quickfix",
                                        "refactor",
                                        "refactor.extract",
                                        "refactor.inline",
                                        "refactor.rewrite",
                                        "source",
                                        "source.organizeImports",
                                    ]
                                }
                            },
                            "resolveSupport": {"properties": ["edit"]},
                        },
                        "publishDiagnostics": {
                            "relatedInformation": True,
                            "tagSupport": {"valueSet": [1, 2]},  # Unnecessary, Deprecated
                        },
                        "callHierarchy": {},
                        "semanticTokens": {
                            "requests": {"full": True, "range": True},
                            "tokenTypes": [
                                "namespace",
                                "type",
                                "class",
                                "enum",
                                "interface",
                                "struct",
                                "typeParameter",
                                "parameter",
                                "variable",
                                "property",
                                "enumMember",
                                "event",
                                "function",
                                "method",
                                "macro",
                                "keyword",
                                "modifier",
                                "comment",
                                "string",
                                "number",
                                "regexp",
                                "operator",
                            ],
                            "tokenModifiers": [
                                "declaration",
                                "definition",
                                "readonly",
                                "static",
                                "deprecated",
                                "abstract",
                                "async",
                                "modification",
                                "documentation",
                                "defaultLibrary",
                            ],
                            "formats": ["relative"],
                        },
                    },
                    "workspace": {
                        "applyEdit": True,
                        "symbol": {
                            "symbolKind": {
                                "valueSet": list(range(1, 27))  # All symbol kinds
                            }
                        },
                        "executeCommand": {},
                        "workspaceFolders": True,
                        "configuration": True,
                        "didChangeConfiguration": {
                            "dynamicRegistration": True,
                        },
                    },
                    "window": {
                        "workDoneProgress": True,
                    },
                },
            },
        )

        logger.info(f"vtsls initialized successfully: {result.get('serverInfo', {})}")

        # Send initialized notification
        await self.notify("initialized", {})

        self._initialized = True

        # Reset watchdog on successful initialization
        if self._watchdog:
            self._watchdog.reset()

    async def request(self, method: str, params: Any = None) -> Any:
        """Send request and wait for response.

        Args:
            method: LSP method name.
            params: Request parameters.

        Returns:
            The result from the server.

        Raises:
            RuntimeError: If not started.
            LSPRequestError: If request fails or times out.
        """
        if not self.process:
            raise RuntimeError("Not started")

        request_id = self.request_id
        self.request_id += 1

        logger.debug(f"Creating request {method} with id {request_id}")

        # Create future for response
        future: asyncio.Future = asyncio.Future()
        self.pending_requests[request_id] = future

        # Send request
        await self._send_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )

        # Wait for response with timeout
        try:
            logger.debug(f"Waiting for response to {method} (id={request_id})")
            result = await asyncio.wait_for(future, timeout=self.request_timeout)
            logger.debug(f"Got response for {method} (id={request_id})")
            return result
        except asyncio.TimeoutError:
            self.pending_requests.pop(request_id, None)
            logger.error(
                f"Request {method} (id={request_id}) timed out. "
                f"Pending requests: {list(self.pending_requests.keys())}"
            )
            raise LSPRequestError(
                f"Request {method} timed out after {self.request_timeout}s",
                code=-32004,
                is_retryable=True,
            ) from None

    async def notify(self, method: str, params: Any = None) -> None:
        """Send notification (no response expected).

        Args:
            method: LSP method name.
            params: Notification parameters.
        """
        await self._send_message(
            {"jsonrpc": "2.0", "method": method, "params": params or {}}
        )

    async def _send_message(self, message: dict[str, Any]) -> None:
        """Send message to vtsls.

        Args:
            message: The JSON-RPC message to send.

        Raises:
            RuntimeError: If process not running.
        """
        if not self.process or not self.process.stdin:
            raise RuntimeError("Process not running")

        content = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(content)}\r\n\r\n".encode()

        # Thread-safe write
        with self._writer_lock:
            self.process.stdin.write(header + content)
            self.process.stdin.flush()

        description = message.get("method", f"response to {message.get('id')}")
        logger.debug(f"Sent: {description}")

    def on_notification(self, method: str, handler: Callable[..., Any]) -> None:
        """Register notification handler.

        Args:
            method: LSP notification method name.
            handler: Callback function (sync or async).
        """
        self.notification_handlers[method] = handler

    async def _run_watchdog(self) -> None:
        """Run the process watchdog."""
        if not self._watchdog or not self.process:
            return

        try:
            await self._watchdog.monitor(self.process, self.restart)
        except ProcessCrashError as e:
            logger.error(f"Watchdog detected fatal crash: {e}")
            # Mark as not initialized so callers know to handle the error
            self._initialized = False
        except asyncio.CancelledError:
            logger.debug("Watchdog task cancelled")
        except Exception as e:
            logger.error(f"Watchdog error: {e}")

    async def restart(self) -> subprocess.Popen:
        """Restart the vtsls process.

        Called by the watchdog when the process crashes.

        Returns:
            The new process.
        """
        logger.info("Restarting vtsls...")

        # Clean up old process state
        self._shutting_down = True
        self._initialized = False

        # Clear pending requests with error
        for _request_id, future in list(self.pending_requests.items()):
            if not future.done():
                future.set_exception(
                    LSPRequestError(
                        "Server restarted",
                        code=-32000,
                        is_retryable=True,
                    )
                )
        self.pending_requests.clear()

        # Clear message queue
        while not self._message_queue.empty():
            try:
                self._message_queue.get_nowait()
            except queue.Empty:
                break

        # Start new process
        await self._start_process()

        # Re-initialize
        await self._initialize()

        logger.info("vtsls restarted successfully")
        if self.process is None:
            raise RuntimeError("vtsls restart did not create a process")
        return self.process

    async def shutdown(self) -> None:
        """Shutdown the language server gracefully."""
        if not self.process:
            return

        logger.debug("Starting shutdown...")

        # Stop watchdog first
        if self._watchdog:
            self._watchdog.stop()
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

        try:
            # Send shutdown request first
            shutdown_result = await self.request("shutdown", {})
            logger.debug(f"Shutdown response: {shutdown_result}")

            # Then mark as shutting down to stop message processing
            self._shutting_down = True

            # Send exit notification
            await self.notify("exit", {})

            # Give it a moment to exit cleanly
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            self._shutting_down = True

        # Terminate process if still running
        if self.process.poll() is None:
            logger.debug("Process still running, terminating...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.debug("Process didn't terminate, killing...")
                self.process.kill()
                self.process.wait()

        self.process = None
        self._initialized = False
        logger.debug("Shutdown complete")
