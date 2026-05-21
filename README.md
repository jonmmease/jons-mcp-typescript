# jons-mcp-typescript

MCP server providing TypeScript development capabilities via vtsls, Prettier, and ESLint.

## Features

- **TypeScript Language Server Integration** - Full LSP support via vtsls for code navigation, symbol information, and type checking
- **Prettier Formatting** - Code formatting with automatic config resolution
- **ESLint Linting** - Code linting with auto-fix support
- **Unified Operations** - Combined check and fix tools for efficient workflows

## Installation

### Prerequisites

1. **Python 3.10+** with uv or pip
2. **Node.js 18+**
3. **vtsls** - TypeScript language server:
   ```bash
   npm install -g @vtsls/language-server
   ```
4. **Project-local Prettier and ESLint** in the TypeScript project you run the server against:
   ```bash
   cd /path/to/typescript/project
   npm install -D prettier eslint
   ```

### Install the Package

```bash
# Using uv (recommended)
uv pip install jons-mcp-typescript

# Using pip
pip install jons-mcp-typescript
```

The Python package includes the small Node daemon source. The daemon intentionally
uses Prettier and ESLint from your target project's `node_modules` so formatting
and linting match that project.

## Usage

### Running the Server

```bash
# Use current directory as project root
jons-mcp-typescript

# Specify project root as argument
jons-mcp-typescript /path/to/typescript/project
```

### Local Development (Running from Source)

To run the server locally during development:

```bash
# Clone and setup
git clone https://github.com/jonmmease/jons-mcp-typescript
cd jons-mcp-typescript
uv sync --dev

# Install TypeScript project formatting/linting dependencies
cd /path/to/your/typescript/project && npm install -D prettier eslint

# Run against current directory
uv run jons-mcp-typescript

# Run against a specific TypeScript project
uv run jons-mcp-typescript /path/to/your/typescript/project

# Run from anywhere using uv's --project flag (uses cwd as TypeScript project)
cd /path/to/your/typescript/project
uv run --project /path/to/jons-mcp-typescript jons-mcp-typescript
```

### MCP Client Configuration

#### Claude Desktop / Claude Code

Add to your MCP settings (e.g., `~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "typescript": {
      "command": "jons-mcp-typescript",
      "args": []
    }
  }
}
```

This runs the server in the current working directory (your TypeScript project).

#### Local Development Configuration

When running from source, use `uv run --project` to point to the MCP server repo.
The TypeScript project defaults to the current working directory:

```json
{
  "mcpServers": {
    "typescript": {
      "command": "uv",
      "args": [
        "run",
        "--project", "/path/to/jons-mcp-typescript",
        "jons-mcp-typescript"
      ]
    }
  }
}
```

This tells uv to use the Python environment from `/path/to/jons-mcp-typescript` and run `jons-mcp-typescript` against the current working directory (your TypeScript project).

## Available Tools

### Navigation & Discovery

| Tool | Purpose |
|------|---------|
| `workspace_symbols` | Search for types/functions across the project by name |
| `document_symbols` | List all symbols defined in a file |
| `definition` | Jump to where a symbol is defined |
| `type_definition` | Jump to the type definition of a symbol |
| `implementation` | Find implementations of interfaces/abstract classes |
| `references` | Find all usages of a symbol |

### Understanding Code

| Tool | Purpose |
|------|---------|
| `type_info` | Get type name, fields, and methods for a value |
| `symbol_info` | Get type signature and docs for any symbol |

### Type Checking

| Tool | Purpose |
|------|---------|
| `diagnostics` | Get type errors and warnings |

### Refactoring

| Tool | Purpose |
|------|---------|
| `rename` | Safely rename a symbol across the project |

### Formatting & Linting

| Tool | Purpose |
|------|---------|
| `format_code` | Format code using Prettier |
| `check_formatting` | Check if code is formatted correctly |
| `lint_code` | Lint code using ESLint |
| `get_prettier_config` | Get resolved Prettier configuration |
| `get_eslint_config` | Get resolved ESLint configuration |

### Unified Operations

| Tool | Purpose |
|------|---------|
| `check_all` | Run all checks (formatting, linting, types) on a file |
| `fix_all` | Apply all automatic fixes to a file |

### Server Management

| Tool | Purpose |
|------|---------|
| `restart_server` | Restart TypeScript language server and daemon |

## Tool Examples

### Navigate to Definition

```python
# Find where a function is defined
result = await definition(
    file_path="/project/src/index.ts",
    line=10,
    character=15
)
# Returns: {"uri": "file:///project/src/utils.ts", "range": {...}}
```

### Get Type Information

```python
# Get fields and methods of a variable's type
result = await type_info(
    file_path="/project/src/app.ts",
    line=5,
    character=8
)
# Returns: {"typeName": "User", "fields": [...], "methods": {...}}
```

### Format Code

```python
# Format a TypeScript file
result = await format_code(
    file_path="/project/src/messy.ts"
)
# Returns: {"formatted": true, "code": "...", "changed": true}
```

### Lint and Fix

```python
# Lint with auto-fix
result = await lint_code(
    file_path="/project/src/app.ts",
    fix=True
)
# Returns: {"issues": [...], "fixedCode": "..."}
```

### Run All Checks

```python
# Check formatting, linting, and types
result = await check_all(
    file_path="/project/src/index.ts"
)
# Returns: {"checks": {...}, "overallPassed": false, "summary": "..."}
```

### Apply All Fixes

```python
# Fix all auto-fixable issues and write to file
result = await fix_all(
    file_path="/project/src/index.ts",
    write=True
)
# Returns: {"fixes": {...}, "written": true, "totalChanges": 5}
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        MCP Client                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastMCP Server (Python)                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    Tool Handlers                          │   │
│  │  language.py  intelligence.py  formatting.py  linting.py │   │
│  └──────────────────────────────────────────────────────────┘   │
│                   │                    │                        │
│                   ▼                    ▼                        │
│  ┌─────────────────────┐   ┌─────────────────────────────────┐ │
│  │    VtslsClient      │   │   FormatterLinterDaemon        │  │
│  │  (LSP over stdio)   │   │   (JSON Lines over stdio)      │  │
│  └─────────────────────┘   └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
           │                            │
           ▼                            ▼
┌─────────────────────┐   ┌─────────────────────────────────────┐
│       vtsls         │   │         Node.js Daemon              │
│  (TypeScript LSP)   │   │  ┌─────────────────────────────┐    │
│                     │   │  │   PrettierManager          │    │
│                     │   │  │   ESLintManager            │    │
│                     │   │  └─────────────────────────────┘    │
└─────────────────────┘   └─────────────────────────────────────┘
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `VTSLS_PATH` | Path to vtsls executable | Auto-detected |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, etc.) | INFO |

### Config Resolution

- **Prettier**: Resolves `.prettierrc`, `.prettierrc.json`, `.prettierrc.js`, etc.
- **ESLint**: Resolves `eslint.config.js` (flat config) or `.eslintrc.*`
- **TypeScript**: Resolves `tsconfig.json`

## Development

### Setup

```bash
git clone https://github.com/jonmmease/jons-mcp-typescript
cd jons-mcp-typescript
uv sync --dev
```

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=jons_mcp_typescript

# Run specific test file
uv run pytest tests/test_utils.py -v
```

### Test Requirements

Integration tests require:
- Node.js installed
- vtsls installed globally
- Prettier and ESLint available to the temporary test project

Tests will skip gracefully if dependencies are missing.

## License

MIT
