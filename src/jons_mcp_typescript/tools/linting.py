"""Linting tools for TypeScript development using ESLint."""

from typing import Any

from fastmcp import Context

from ..exceptions import ESLintConfigError, ESLintPluginError
from ..server import get_daemon, mcp


@mcp.tool()
async def lint_code(
    file_path: str,
    code: str | None = None,
    fix: bool = False,
    config_file: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Lint source code using ESLint.

    Args:
        file_path: Path to the file (used for config resolution)
        code: Code to lint (if None, reads from file_path)
        fix: Whether to apply auto-fixes
        config_file: Optional explicit config file path

    Returns:
        Dictionary with keys:
        - issues: List of lint issues
        - totalIssues: Total number of issues
        - errors: Number of errors
        - warnings: Number of warnings
        - fixed: Whether code was fixed
        - fixedCode: Fixed code if fix=True, otherwise None

    Raises:
        ESLintConfigError: If ESLint configuration cannot be found or loaded
        ESLintPluginError: If ESLint plugin cannot be loaded
    """
    daemon = get_daemon()

    # Read content if not provided
    if code is None:
        with open(file_path, "r", encoding="utf-8") as f:
            code = f.read()

    try:
        result = await daemon.lint(file_path, code, fix)

        # Extract issues from messages
        messages = result.get("messages", [])

        # Count errors and warnings
        error_count = sum(1 for msg in messages if msg.get("severity") == 2)
        warning_count = sum(1 for msg in messages if msg.get("severity") == 1)

        return {
            "issues": messages,
            "totalIssues": len(messages),
            "errors": error_count,
            "warnings": warning_count,
            "fixed": result.get("fixed", False),
            "fixedCode": result.get("fixedContent") if fix else None,
        }
    except Exception as e:
        error_msg = str(e)
        if "Plugin" in error_msg or "plugin" in error_msg:
            raise ESLintPluginError(f"ESLint plugin error: {error_msg}") from e
        elif "Config" in error_msg or "config" in error_msg:
            raise ESLintConfigError(f"ESLint config error: {error_msg}") from e
        raise


@mcp.tool()
async def get_eslint_config(
    file_path: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get ESLint configuration for a file.

    Args:
        file_path: Path to resolve config for

    Returns:
        Config object with rules and settings

    Raises:
        ESLintConfigError: If ESLint configuration cannot be found or loaded
        ESLintPluginError: If ESLint plugin cannot be loaded
    """
    daemon = get_daemon()

    try:
        result = await daemon.get_eslint_config(file_path)
        return result.get("config", {})
    except Exception as e:
        error_msg = str(e)
        if "Plugin" in error_msg or "plugin" in error_msg:
            raise ESLintPluginError(f"ESLint plugin error: {error_msg}") from e
        elif "Config" in error_msg or "config" in error_msg:
            raise ESLintConfigError(f"ESLint config error: {error_msg}") from e
        raise
