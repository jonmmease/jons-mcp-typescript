"""Prettier formatting tools for TypeScript development.

Provides code formatting, formatting checks, and config resolution using Prettier.
"""

from typing import Any

from fastmcp import Context

from ..exceptions import PrettierConfigError, PrettierParseError
from ..schemas import CheckFormattingResult, FormatCodeResult
from ..server import get_daemon, mcp, resolve_project_file


@mcp.tool()
async def format_code(
    file_path: str,
    code: str | None = None,
    config_file: str | None = None,
    ctx: Context | None = None,
) -> FormatCodeResult:
    """Lower-level Prettier formatting only.

    Prefer fix_all for the normal agent workflow because it runs ESLint fixes
    before Prettier. Use this when you specifically need only Prettier output.

    Args:
        file_path: Path to the file (used for config resolution)
        code: Code to format (if None, reads from file_path)
        config_file: Optional explicit config file path (currently unused but provided for compatibility)

    Returns:
        FormatCodeResult with:
            - formatted: True if code was successfully formatted
            - code: The formatted code
            - changed: Whether the code changed from the input

    Raises:
        PrettierConfigError: If Prettier configuration cannot be found or loaded
        PrettierParseError: If the code cannot be parsed
    """
    project_file = resolve_project_file(file_path, must_exist=code is None)
    daemon = get_daemon()

    # Read content if not provided
    if code is None:
        try:
            code = project_file.path.read_text(encoding="utf-8")
        except OSError as e:
            raise PrettierConfigError(
                f"Failed to read file {project_file.path}: {e}"
            ) from e

    try:
        result = await daemon.format(str(project_file.path), code)

        # Extract the formatted code and determine if it changed
        formatted_code = result.get("formatted", code)
        changed = formatted_code != code

        return FormatCodeResult(formatted=True, code=formatted_code, changed=changed)
    except Exception as e:
        error_msg = str(e)
        if "Parse error" in error_msg or "Parsing" in error_msg:
            raise PrettierParseError(f"Failed to parse code: {error_msg}") from e
        elif "Config" in error_msg or "config" in error_msg:
            raise PrettierConfigError(f"Config error: {error_msg}") from e
        raise


@mcp.tool()
async def check_formatting(
    file_path: str,
    code: str | None = None,
    config_file: str | None = None,
    ctx: Context | None = None,
) -> CheckFormattingResult:
    """Lower-level Prettier formatting check only.

    Prefer check_all for the normal agent workflow because it combines
    TypeScript diagnostics, Prettier, and ESLint. Use this when you specifically
    need only the Prettier formatting status.

    Args:
        file_path: Path to the file
        code: Code to check (if None, reads from file_path)
        config_file: Optional explicit config file path (currently unused but provided for compatibility)

    Returns:
        CheckFormattingResult with:
            - formatted: Whether code is properly formatted
            - message: Human-readable status message

    Raises:
        PrettierConfigError: If Prettier configuration cannot be found or loaded
        PrettierParseError: If the code cannot be parsed
    """
    project_file = resolve_project_file(file_path, must_exist=code is None)
    daemon = get_daemon()

    # Read content if not provided
    if code is None:
        try:
            code = project_file.path.read_text(encoding="utf-8")
        except OSError as e:
            raise PrettierConfigError(
                f"Failed to read file {project_file.path}: {e}"
            ) from e

    try:
        result = await daemon.check_formatting(str(project_file.path), code)
        is_formatted = result.get("isFormatted", False)

        return CheckFormattingResult(
            formatted=is_formatted,
            message="Code is properly formatted"
            if is_formatted
            else "Code needs formatting",
        )
    except Exception as e:
        error_msg = str(e)
        if "Parse error" in error_msg or "Parsing" in error_msg:
            raise PrettierParseError(f"Failed to parse code: {error_msg}") from e
        elif "Config" in error_msg or "config" in error_msg:
            raise PrettierConfigError(f"Config error: {error_msg}") from e
        raise


@mcp.tool()
async def get_prettier_config(
    file_path: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get Prettier configuration for a file.

    Resolves the Prettier configuration that would be used for the given file,
    including all options and the path to the configuration file.

    Args:
        file_path: Path to resolve config for

    Returns:
        Dictionary with the resolved Prettier configuration including:
            - The full config options object
            - configFile: Path to the configuration file (or None if using defaults)

    Raises:
        PrettierConfigError: If Prettier configuration cannot be found or loaded
    """
    project_file = resolve_project_file(file_path, must_exist=False)
    daemon = get_daemon()

    try:
        result = await daemon.get_prettier_config(str(project_file.path))
        config = result.get("config", {})

        # Ensure config is returned with proper structure
        if not isinstance(config, dict):
            config = {}

        # Include the configPath from the result
        config_path = result.get("configPath")
        if config_path:
            config["configFile"] = config_path

        return config
    except Exception as e:
        error_msg = str(e)
        if "Config" in error_msg or "config" in error_msg:
            raise PrettierConfigError(f"Config error: {error_msg}") from e
        raise
