"""Linting tools for TypeScript development using ESLint."""

from typing import Any

from fastmcp import Context

from ..exceptions import ESLintConfigError, ESLintPluginError
from ..schemas import LintCodeResult
from ..server import get_daemon, mcp, resolve_project_file
from ..utils import count_eslint_messages


@mcp.tool()
async def lint_code(
    file_path: str,
    code: str | None = None,
    fix: bool = False,
    config_file: str | None = None,
    ctx: Context | None = None,
) -> LintCodeResult:
    """Lower-level ESLint lint/fix operation only.

    Prefer check_all for normal checks and fix_all for normal automatic fixes.
    Use this when you specifically need only ESLint results or ESLint fixed
    code without Prettier.

    Args:
        file_path: Path to the file (used for config resolution)
        code: Code to lint (if None, reads from file_path)
        fix: Whether to apply auto-fixes
        config_file: Optional explicit config file path

    Returns:
        LintCodeResult with:
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
    project_file = resolve_project_file(file_path, must_exist=code is None)
    daemon = get_daemon()

    # Read content if not provided
    if code is None:
        code = project_file.path.read_text(encoding="utf-8")

    try:
        result = await daemon.lint(str(project_file.path), code, fix)

        # Extract issues from messages
        messages = result.get("messages", [])

        # Count errors and warnings
        error_count = int(result.get("errorCount", 0))
        warning_count = int(result.get("warningCount", 0))
        if error_count == 0 and warning_count == 0:
            error_count, warning_count = count_eslint_messages(messages)

        return LintCodeResult(
            issues=messages,
            totalIssues=len(messages),
            errors=error_count,
            warnings=warning_count,
            fixed=result.get("fixed", False),
            fixedCode=result.get("fixedContent") if fix else None,
        )
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
    project_file = resolve_project_file(file_path, must_exist=False)
    daemon = get_daemon()

    try:
        result = await daemon.get_eslint_config(str(project_file.path))
        config = result.get("config", {})
        return config if isinstance(config, dict) else {}
    except Exception as e:
        error_msg = str(e)
        if "Plugin" in error_msg or "plugin" in error_msg:
            raise ESLintPluginError(f"ESLint plugin error: {error_msg}") from e
        elif "Config" in error_msg or "config" in error_msg:
            raise ESLintConfigError(f"ESLint config error: {error_msg}") from e
        raise
