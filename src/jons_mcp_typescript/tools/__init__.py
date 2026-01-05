"""TypeScript development tools for MCP.

This module provides tools for TypeScript development:

Navigation & Discovery:
    - definition: Jump to where a symbol is defined
    - type_definition: Jump to the type definition of a symbol
    - implementation: Find implementations of interfaces/abstract classes
    - references: Find all usages of a symbol
    - workspace_symbols: Search for types/functions across the project
    - document_symbols: List all symbols defined in a file

Information:
    - symbol_info: Get type signature and docs for any symbol
    - type_info: Get type name, fields, and methods for a value

Type Checking:
    - diagnostics: Get type errors and warnings

Refactoring:
    - rename: Safely rename a symbol across the project

Formatting:
    - format_code: Format code using Prettier
    - check_formatting: Check if code is formatted correctly
    - get_prettier_config: Get resolved Prettier configuration

Linting:
    - lint_code: Lint code using ESLint
    - get_eslint_config: Get resolved ESLint configuration

Unified:
    - check_all: Run all checks on a file
    - fix_all: Apply all automatic fixes

Server Management:
    - restart_server: Restart TypeScript language server and daemon
"""

from . import formatting, intelligence, language, linting, unified

# Re-export all tools for convenient access
from .formatting import check_formatting, format_code, get_prettier_config
from .intelligence import diagnostics, rename, restart_server
from .language import (
    definition,
    document_symbols,
    implementation,
    references,
    symbol_info,
    type_definition,
    type_info,
    workspace_symbols,
)
from .linting import get_eslint_config, lint_code
from .unified import check_all, fix_all

__all__ = [
    # Navigation
    "definition",
    "type_definition",
    "implementation",
    "references",
    "workspace_symbols",
    "document_symbols",
    # Information
    "symbol_info",
    "type_info",
    # Type checking
    "diagnostics",
    # Refactoring
    "rename",
    # Formatting
    "format_code",
    "check_formatting",
    "get_prettier_config",
    # Linting
    "lint_code",
    "get_eslint_config",
    # Unified
    "check_all",
    "fix_all",
    # Server
    "restart_server",
    # Modules
    "formatting",
    "intelligence",
    "language",
    "linting",
    "unified",
]
