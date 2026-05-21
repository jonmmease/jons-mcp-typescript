"""Domain exceptions for the TypeScript MCP server."""


class LSPRequestError(Exception):
    """Exception raised when an LSP request fails.

    Attributes:
        message: Error message
        code: LSP error code
        is_retryable: Whether the request can be retried
    """

    def __init__(self, message: str, code: int, is_retryable: bool = False) -> None:
        """Initialize LSPRequestError.

        Args:
            message: Error message
            code: LSP error code (e.g., -32000 for InternalError)
            is_retryable: Whether the request can be retried
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.is_retryable = is_retryable


class VtslsNotInitializedError(Exception):
    """Exception raised when vtsls is not initialized."""
    pass


class VtslsNotFoundError(Exception):
    """Exception raised when vtsls executable is not found."""
    pass


class DaemonError(Exception):
    """Exception raised when daemon encounters an error.

    Attributes:
        message: Error message
        code: Error code from daemon response
    """

    def __init__(self, message: str, code: int) -> None:
        """Initialize DaemonError.

        Args:
            message: Error message
            code: Error code from daemon response
        """
        super().__init__(message)
        self.message = message
        self.code = code


class DaemonTimeoutError(Exception):
    """Exception raised when daemon request times out."""
    pass


class PathOutsideProjectError(ValueError):
    """Exception raised when a requested file is outside the project root."""
    pass


class PrettierConfigError(Exception):
    """Exception raised when Prettier configuration cannot be found or loaded."""
    pass


class PrettierParseError(Exception):
    """Exception raised when Prettier fails to parse code."""
    pass


class ESLintConfigError(Exception):
    """Exception raised when ESLint configuration cannot be found or loaded."""
    pass


class ESLintPluginError(Exception):
    """Exception raised when ESLint plugin cannot be loaded."""
    pass


class ProcessCrashError(Exception):
    """Exception raised when the LSP process crashes too many times."""
    pass
