"""Unified tools for checking and fixing code with multiple tools.

Provides combined check_all and fix_all tools that run multiple checks and fixes
in parallel for efficiency.
"""

import asyncio
from typing import Any

from fastmcp import Context

from ..schemas import CheckAllResult, FixAllResult
from ..server import (
    clear_diagnostics_for_uri,
    close_file,
    ensure_vtsls_indexed,
    get_daemon,
    mcp,
    open_file,
    register_diagnostics_event,
    resolve_project_file,
    wait_for_diagnostics,
)
from ..utils import count_eslint_messages, lsp_result_to_public


@mcp.tool()
async def check_all(
    file_path: str,
    include_prettier: bool = True,
    include_eslint: bool = True,
    include_typescript: bool = True,
    ctx: Context | None = None,
) -> CheckAllResult:
    """Run the preferred combined quality check on a file.

    Checks code formatting, linting, and types in parallel and aggregates results.

    Args:
        file_path: Path to the file
        include_prettier: Check formatting with Prettier
        include_eslint: Check linting with ESLint
        include_typescript: Check types with TypeScript

    Returns:
        CheckAllResult with:
            - checks: Dict containing results for each check (prettier, eslint, typescript)
            - overallPassed: Whether all checks passed
            - summary: Human-readable summary of check results
    """
    project_file = resolve_project_file(file_path)
    daemon = get_daemon()

    # Read file content
    code = project_file.path.read_text(encoding="utf-8")

    checks: dict[str, Any] = {}
    tasks: list[Any] = []
    task_names: list[str] = []

    # Build check tasks
    if include_prettier:
        tasks.append(daemon.check_formatting(str(project_file.path), code))
        task_names.append("prettier")

    if include_eslint:
        tasks.append(daemon.lint(str(project_file.path), code, fix=False))
        task_names.append("eslint")

    if include_typescript:
        # For TypeScript, ensure vtsls is initialized and file is open
        try:
            client = await ensure_vtsls_indexed(file_path)
            file_uri = project_file.uri

            try:
                # Clear cached diagnostics and register event BEFORE opening file
                clear_diagnostics_for_uri(file_uri)
                register_diagnostics_event(file_uri)

                # Open/sync file with fresh content from disk
                await open_file(client, project_file.path, file_uri)

                # Wait for diagnostics to arrive via event (with timeout)
                ts_diags = await wait_for_diagnostics(file_uri)
            finally:
                # Close file so vtsls reads from disk next time
                await close_file(client, file_uri)

            checks["typescript"] = {
                "passed": len(ts_diags) == 0,
                "errorCount": len([d for d in ts_diags if d.get("severity", 1) == 1]),
                "warningCount": len([d for d in ts_diags if d.get("severity", 1) == 2]),
                "diagnostics": lsp_result_to_public(ts_diags),
            }
        except Exception as e:
            checks["typescript"] = {
                "passed": False,
                "error": str(e),
            }

    # Run checks in parallel
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for name, result in zip(task_names, results, strict=True):
            if isinstance(result, Exception):
                checks[name] = {"passed": False, "error": str(result)}
            elif not isinstance(result, dict):
                checks[name] = {"passed": False, "error": "Invalid check result"}
            elif name == "prettier":
                is_formatted = result.get("isFormatted", False)
                checks["prettier"] = {
                    "passed": is_formatted,
                    "message": "Formatted correctly"
                    if is_formatted
                    else "Needs formatting",
                }
            elif name == "eslint":
                messages = result.get("messages", [])
                error_count = int(result.get("errorCount", 0))
                warning_count = int(result.get("warningCount", 0))
                if error_count == 0 and warning_count == 0:
                    error_count, warning_count = count_eslint_messages(messages)
                checks["eslint"] = {
                    "passed": error_count == 0,
                    "errorCount": error_count,
                    "warningCount": warning_count,
                    "issues": messages,
                }

    # Calculate overall result
    overall_passed = all(c.get("passed", False) for c in checks.values())

    # Generate summary
    summary_parts = []
    for name, check in checks.items():
        if check.get("passed"):
            summary_parts.append(f"{name}: passed")
        else:
            summary_parts.append(f"{name}: failed")
    summary = "; ".join(summary_parts)

    return CheckAllResult.model_validate(
        {
            "checks": checks,
            "overallPassed": overall_passed,
            "summary": summary,
        }
    )


@mcp.tool()
async def fix_all(
    file_path: str,
    write: bool = False,
    include_prettier: bool = True,
    include_eslint: bool = True,
    ctx: Context | None = None,
) -> FixAllResult:
    """Run the preferred automatic fix workflow on a file.

    Applies ESLint fixes first, then Prettier formatting. Both are applied
    in sequence to the code. Optionally writes the fixed code back to the file.

    Args:
        file_path: Path to the file
        write: If True, write changes back to the file
        include_prettier: Apply Prettier formatting
        include_eslint: Apply ESLint fixes

    Returns:
        FixAllResult with:
            - fixes: Dict with status of each fix (prettier, eslint)
            - finalCode: The final fixed code
            - totalChanges: Number of changes made
            - written: Whether changes were written to file
    """
    project_file = resolve_project_file(file_path)
    daemon = get_daemon()

    # Read original file content
    original_code = project_file.path.read_text(encoding="utf-8")

    current_code = original_code
    fixes: dict[str, Any] = {}
    total_changes = 0

    # Run ESLint fix first (often adds imports, fixes logic, etc.)
    if include_eslint:
        result = await daemon.lint(str(project_file.path), current_code, fix=True)
        fixed_content = result.get("fixedContent")
        messages = result.get("messages", [])

        if fixed_content and fixed_content != current_code:
            current_code = fixed_content
            error_count = int(result.get("errorCount", 0))
            warning_count = int(result.get("warningCount", 0))
            if error_count == 0 and warning_count == 0:
                error_count, warning_count = count_eslint_messages(messages)
            total_changes += error_count + warning_count
            fixes["eslint"] = {
                "applied": True,
                "issuesFixed": len(messages),
            }
        else:
            fixes["eslint"] = {"applied": False}

    # Run Prettier on potentially ESLint-fixed code
    if include_prettier:
        result = await daemon.format(str(project_file.path), current_code)
        formatted_code = result.get("formatted")

        if formatted_code and formatted_code != current_code:
            current_code = formatted_code
            total_changes += 1
            fixes["prettier"] = {"applied": True}
        else:
            fixes["prettier"] = {"applied": False}

    # Write to file if requested and code changed
    written = False
    if write and current_code != original_code:
        project_file.path.write_text(current_code, encoding="utf-8")
        written = True

    return FixAllResult.model_validate(
        {
            "fixes": fixes,
            "finalCode": current_code,
            "totalChanges": total_changes,
            "written": written,
        }
    )
