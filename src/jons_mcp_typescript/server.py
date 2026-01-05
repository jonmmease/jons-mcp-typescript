"""FastMCP server for TypeScript development capabilities."""

import asyncio
import hashlib
import logging
import signal
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from fastmcp import FastMCP

from .constants import DIAGNOSTICS_TIMEOUT, REQUEST_TIMEOUT
from .daemon_client import FormatterLinterDaemon
from .exceptions import VtslsNotInitializedError
from .lsp_client import ProcessWatchdog, VtslsClient

logger = logging.getLogger(__name__)


@dataclass
class DocumentState:
    """Track state of a document opened in vtsls.

    Used to detect content changes and manage document versions for proper
    LSP synchronization. This enables sending didChange instead of reopening
    when content changes, which is more efficient and LSP-compliant.
    """

    version: int
    content_hash: str
    is_open: bool = False


def compute_content_hash(content: str) -> str:
    """Compute a hash of file content for change detection.

    Uses SHA256 truncated to 16 chars (64 bits) - sufficient for
    detecting changes with negligible collision risk.

    Args:
        content: The file content to hash.

    Returns:
        A 16-character hex digest.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# Global state
vtsls: VtslsClient | None = None
vtsls_watchdog: ProcessWatchdog | None = None
daemon: FormatterLinterDaemon | None = None
daemon_watchdog: ProcessWatchdog | None = None
current_diagnostics: dict[str, list] = {}  # uri -> diagnostics
document_states: dict[str, DocumentState] = {}  # uri -> document state for version tracking
pending_diagnostics_events: dict[str, asyncio.Event] = {}  # uri -> event for waiting
initialization_complete = False
_project_root: Path | None = None


def handle_diagnostics(params: dict[str, Any]) -> None:
    """Handle publishDiagnostics notification from vtsls.

    Args:
        params: Notification parameters containing URI and diagnostics.
    """
    uri = params.get("uri", "")
    diags = params.get("diagnostics", [])
    logger.info(f"Received {len(diags)} diagnostics for {uri}")

    # Store diagnostics in global state
    current_diagnostics[uri] = diags

    # Signal any pending waiters for this URI
    if uri in pending_diagnostics_events:
        pending_diagnostics_events[uri].set()


def clear_diagnostics_for_uri(uri: str) -> None:
    """Clear cached diagnostics for a URI before requesting fresh ones.

    Args:
        uri: The file URI to clear diagnostics for.
    """
    if uri in current_diagnostics:
        del current_diagnostics[uri]
        logger.debug(f"Cleared cached diagnostics for {uri}")


def register_diagnostics_event(uri: str) -> asyncio.Event:
    """Register an event to wait for diagnostics for a URI.

    Args:
        uri: The file URI to wait for diagnostics.

    Returns:
        An asyncio.Event that will be set when diagnostics arrive.
    """
    event = asyncio.Event()
    pending_diagnostics_events[uri] = event
    return event


async def wait_for_diagnostics(uri: str, timeout: float = DIAGNOSTICS_TIMEOUT) -> list:
    """Wait for diagnostics to arrive for a URI.

    Args:
        uri: The file URI to wait for diagnostics.
        timeout: Maximum time to wait in seconds.

    Returns:
        List of diagnostics, or empty list if timeout or no diagnostics.
    """
    event = pending_diagnostics_events.get(uri)
    if not event:
        # No event registered - return cached or empty
        return current_diagnostics.get(uri, [])

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        logger.debug(f"Received diagnostics event for {uri}")
    except asyncio.TimeoutError:
        logger.warning(f"Timeout waiting for diagnostics for {uri}")
    finally:
        # Clean up the event
        pending_diagnostics_events.pop(uri, None)

    return current_diagnostics.get(uri, [])


async def ensure_vtsls_indexed(file_path: str | None = None) -> VtslsClient:
    """Ensure vtsls is initialized and return the client.

    Args:
        file_path: Optional file path for better error messages.

    Returns:
        The initialized VtslsClient.

    Raises:
        VtslsNotInitializedError: If vtsls is not initialized or still initializing.
    """
    if not vtsls:
        raise VtslsNotInitializedError("vtsls is not initialized")
    if not vtsls.is_initialized():
        raise VtslsNotInitializedError("vtsls is still initializing")
    return vtsls


def get_daemon() -> FormatterLinterDaemon:
    """Get the daemon instance for formatting/linting tools.

    Returns:
        The initialized FormatterLinterDaemon instance.

    Raises:
        RuntimeError: If daemon is not initialized.
    """
    if not daemon:
        raise RuntimeError("Daemon not initialized")
    return daemon


async def open_file(client: VtslsClient, file_path: str, file_uri: str) -> bool:
    """Open or sync a file in vtsls with current disk content.

    Uses version tracking to properly notify vtsls of changes:
    - If file is new: sends textDocument/didOpen with version 1
    - If file is open and content changed: sends textDocument/didChange with incremented version
    - If file is open and content unchanged: no notification needed
    - If file was closed but we have state: sends didOpen with incremented version

    Args:
        client: The VtslsClient instance.
        file_path: Absolute path to the file.
        file_uri: URI of the file (file://<path>).

    Returns:
        True if file was synced successfully, False on error.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        content_hash = compute_content_hash(content)
        state = document_states.get(file_uri)

        # Determine language ID
        lang_id = "typescript"
        if file_path.endswith(".tsx"):
            lang_id = "typescriptreact"
        elif file_path.endswith(".js"):
            lang_id = "javascript"
        elif file_path.endswith(".jsx"):
            lang_id = "javascriptreact"

        if state is None:
            # New file - send didOpen with version 1
            version = 1
            await client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": file_uri,
                        "languageId": lang_id,
                        "version": version,
                        "text": content,
                    }
                },
            )
            document_states[file_uri] = DocumentState(
                version=version, content_hash=content_hash, is_open=True
            )
            logger.debug(f"Opened new file in vtsls: {file_path} (v{version})")

        elif state.is_open:
            # File is already open - check if content changed
            if state.content_hash != content_hash:
                # Content changed - send didChange with incremented version
                version = state.version + 1
                await client.notify(
                    "textDocument/didChange",
                    {
                        "textDocument": {"uri": file_uri, "version": version},
                        "contentChanges": [{"text": content}],
                    },
                )
                document_states[file_uri] = DocumentState(
                    version=version, content_hash=content_hash, is_open=True
                )
                logger.debug(f"Synced changed file in vtsls: {file_path} (v{version})")
            else:
                # Content unchanged - no notification needed
                logger.debug(f"File unchanged, skipping sync: {file_path}")

        else:
            # File was closed but we have previous state
            # Check if content changed since last time
            if state.content_hash != content_hash:
                version = state.version + 1
            else:
                version = state.version

            await client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": file_uri,
                        "languageId": lang_id,
                        "version": version,
                        "text": content,
                    }
                },
            )
            document_states[file_uri] = DocumentState(
                version=version, content_hash=content_hash, is_open=True
            )
            logger.debug(f"Reopened file in vtsls: {file_path} (v{version})")

        return True
    except Exception as e:
        logger.error(f"Failed to open file {file_path}: {e}")
        return False


async def close_file(client: VtslsClient, file_uri: str) -> None:
    """Close a file in vtsls.

    Sends textDocument/didClose and updates document state tracking.
    Keeps version and content_hash for detecting changes on next open.

    Args:
        client: The VtslsClient instance.
        file_uri: URI of the file (file://<path>).
    """
    try:
        await client.notify(
            "textDocument/didClose",
            {"textDocument": {"uri": file_uri}},
        )
        # Update state to mark as closed, but keep version/hash for next open
        if file_uri in document_states:
            state = document_states[file_uri]
            document_states[file_uri] = DocumentState(
                version=state.version,
                content_hash=state.content_hash,
                is_open=False,
            )
        logger.debug(f"Closed file in vtsls: {file_uri}")
    except Exception as e:
        logger.warning(f"Failed to close file {file_uri}: {e}")


@asynccontextmanager
async def lifespan(mcp: FastMCP) -> AsyncIterator[None]:
    """Lifespan context manager for the MCP server.

    Handles:
    - Starting VtslsClient and FormatterLinterDaemon
    - Registering publishDiagnostics notification handler
    - Starting watchdog monitoring tasks
    - Yielding for server operation
    - Graceful shutdown on exit

    Args:
        mcp: The FastMCP instance.

    Yields:
        None during server operation.
    """
    global vtsls, daemon, vtsls_watchdog, daemon_watchdog, initialization_complete

    # Get project root from global state or use current directory
    project_root = _project_root or Path.cwd()
    logger.info(f"Starting MCP server for project: {project_root}")

    # Initialize vtsls client
    vtsls = VtslsClient(project_root)

    # Register diagnostics notification handler
    vtsls.on_notification("textDocument/publishDiagnostics", handle_diagnostics)

    # Start the vtsls client
    await vtsls.start()

    # Start daemon for formatting and linting
    daemon = FormatterLinterDaemon.create(project_root)
    await daemon.start()
    logger.info("Formatter/Linter daemon started")

    # Wait for initial analysis to complete
    # This gives vtsls time to scan the project and generate initial diagnostics
    logger.info("Waiting for initial TypeScript analysis...")
    await asyncio.sleep(2.0)

    initialization_complete = True
    logger.info("MCP server initialization complete")

    # Yield control to the server
    yield

    # Shutdown
    logger.info("Shutting down MCP server...")
    initialization_complete = False

    # Shutdown daemon first
    if daemon:
        await daemon.shutdown()
        daemon = None

    # Then shutdown vtsls
    if vtsls:
        await vtsls.shutdown()
        vtsls = None

    logger.info("MCP server shutdown complete")


# Server instructions for MCP clients
SERVER_INSTRUCTIONS = """
MCP server providing TypeScript development capabilities via vtsls, Prettier, and ESLint.

## Navigation & Discovery
| Tool | Purpose |
|------|---------|
| workspace_symbols | Search for types/functions across the project by name |
| document_symbols | List all symbols defined in a file |
| definition | Jump to where a symbol is defined |
| type_definition | Jump to the type definition of a symbol |
| implementation | Find implementations of interfaces/abstract classes |
| references | Find all usages of a symbol |

## Understanding Code
| Tool | Purpose |
|------|---------|
| type_info | Get type name, fields, and methods for a value |
| symbol_info | Get type signature and docs for any symbol |

## Type Checking
| Tool | Purpose |
|------|---------|
| diagnostics | Get type errors and warnings |

## Refactoring
| Tool | Purpose |
|------|---------|
| rename | Safely rename a symbol across the project |

## Formatting & Linting
| Tool | Purpose |
|------|---------|
| format_code | Format code using Prettier |
| check_formatting | Check if code is formatted |
| lint_code | Lint code using ESLint |
| check_all | Run all checks on a file |
| fix_all | Apply all automatic fixes |

## Server Management
| Tool | Purpose |
|------|---------|
| restart_server | Restart TypeScript language server |
"""

# Create FastMCP instance
mcp = FastMCP(
    name="jons-mcp-typescript",
    lifespan=lifespan,
    instructions=SERVER_INSTRUCTIONS.strip(),
)


def signal_handler(signum: int, frame: Any) -> None:
    """Handle SIGINT and SIGTERM signals.

    Args:
        signum: Signal number.
        frame: Current stack frame.
    """
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)


def run_server() -> None:
    """Main entry point for the MCP server.

    Sets up signal handlers and runs the FastMCP server.
    """
    import argparse

    global _project_root

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="MCP server for TypeScript development via vtsls, Prettier, and ESLint"
    )
    parser.add_argument(
        "project_path",
        nargs="?",
        type=str,
        default=None,
        help="Path to the TypeScript project root (defaults to current directory)",
    )

    args = parser.parse_args()

    # Determine project root
    if args.project_path:
        _project_root = Path(args.project_path).absolute()
        logger.info(f"Using project root: {_project_root}")
    else:
        _project_root = Path.cwd()
        logger.info(f"Using current directory as project root: {_project_root}")

    # Validate project root exists
    if not _project_root.exists():
        logger.error(f"Project root does not exist: {_project_root}")
        sys.exit(1)

    if not _project_root.is_dir():
        logger.error(f"Project root is not a directory: {_project_root}")
        sys.exit(1)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run the server
    logger.info("Starting jons-mcp-typescript server...")
    mcp.run()
