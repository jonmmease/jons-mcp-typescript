# jons-mcp-typescript

MCP server providing TypeScript development capabilities via vtsls, Prettier, and ESLint.

## Features

- **TypeScript Language Server Integration** - Full LSP support via vtsls for code navigation, symbol information, and type checking
- **Prettier Formatting** - Code formatting with automatic config resolution
- **ESLint Linting** - Code linting with auto-fix support
- **Unified Operations** - Combined check and fix tools for efficient workflows

## Installation

This project is not published to PyPI yet. Install or run it from GitHub:

```bash
uvx --from git+https://github.com/jonmmease/jons-mcp-typescript.git \
  jons-mcp-typescript /path/to/typescript/project
```

### Prerequisites

1. **Python 3.10+** with `uv`
2. **Node.js 18.18+**, Node.js 20.9+, or a newer supported Node.js release
3. **vtsls** - TypeScript language server, installed globally or in the TypeScript project:
   ```bash
   # Global install
   npm install -g @vtsls/language-server

   # Or project-local install
   cd /path/to/typescript/project
   npm install -D @vtsls/language-server
   ```
4. **Project-local Prettier and ESLint** in the TypeScript project you run the server against:
   ```bash
   cd /path/to/typescript/project
   npm install -D prettier eslint
   ```

The Python package includes the small Node daemon source. The daemon intentionally uses Prettier and ESLint from your target project's `node_modules` so formatting and linting match that project.

## Usage

### Running the Server

```bash
# Run from GitHub against the current directory
uvx --from git+https://github.com/jonmmease/jons-mcp-typescript.git \
  jons-mcp-typescript .

# Run from GitHub against a specific project root
uvx --from git+https://github.com/jonmmease/jons-mcp-typescript.git \
  jons-mcp-typescript /path/to/typescript/project
```

### Local Development (Running from Source)

To run the server locally during development:

```bash
# Clone and setup
git clone https://github.com/jonmmease/jons-mcp-typescript.git
cd jons-mcp-typescript
uv sync --dev

# Install TypeScript project dependencies used by the server
cd /path/to/your/typescript/project
npm install -D @vtsls/language-server prettier eslint

# Run against current directory
uv run jons-mcp-typescript

# Run against a specific TypeScript project
uv run jons-mcp-typescript /path/to/your/typescript/project

# Run from anywhere using uv's --project flag (uses cwd as TypeScript project)
cd /path/to/your/typescript/project
uv run --project /path/to/jons-mcp-typescript jons-mcp-typescript .
```

### MCP Client Configuration

#### Claude Code

From the TypeScript project root, add the server to Claude Code:

```bash
cd /path/to/typescript/project
claude mcp add typescript --scope local -- \
  uvx --from git+https://github.com/jonmmease/jons-mcp-typescript.git \
  jons-mcp-typescript .
```

Use `--scope project` instead of `--scope local` if you want Claude Code to write a shared `.mcp.json` file in the project. A shared project config should look like this:

```json
{
  "mcpServers": {
    "typescript": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/jonmmease/jons-mcp-typescript.git",
        "jons-mcp-typescript",
        "."
      ]
    }
  }
}
```

Verify it with:

```bash
claude mcp get typescript
```

#### Codex CLI

From the TypeScript project root, add the server to Codex:

```bash
cd /path/to/typescript/project
codex mcp add typescript -- \
  uvx --from git+https://github.com/jonmmease/jons-mcp-typescript.git \
  jons-mcp-typescript .
```

This writes an MCP server entry to Codex's config. The equivalent TOML is:

```toml
[mcp_servers.typescript]
command = "uvx"
args = [
  "--from",
  "git+https://github.com/jonmmease/jons-mcp-typescript.git",
  "jons-mcp-typescript",
  ".",
]
```

Verify it with:

```bash
codex mcp get typescript
```

If you want the MCP server to always target one specific TypeScript project, replace the final `"."` with an absolute project path in the CLI command or config.

#### Local Checkout Configuration

For active development on this MCP server, clone the repo and point your MCP client at the checkout:

```bash
git clone https://github.com/jonmmease/jons-mcp-typescript.git
cd jons-mcp-typescript
uv sync --dev
```

Then configure your MCP client to run:

```json
{
  "mcpServers": {
    "typescript": {
      "command": "uv",
      "args": [
        "run",
        "--project", "/path/to/jons-mcp-typescript",
        "jons-mcp-typescript",
        "."
      ]
    }
  }
}
```

This tells `uv` to use the Python environment from `/path/to/jons-mcp-typescript` and run `jons-mcp-typescript` against the current working directory, which should be your TypeScript project.

## Available Tools

### Navigation & Discovery

| Tool | Purpose |
|------|---------|
| `document_symbols` | List all symbols defined in a file |
| `definition` | Jump to where a symbol is defined |
| `type_definition` | Jump to the type definition of a symbol |
| `implementation` | Find implementations of interfaces/abstract classes |
| `references` | Find all usages of a symbol |

### Understanding Code

| Tool | Purpose |
|------|---------|
| `type_info_of_reference` | Get TypeScript display info and accessible members for a value reference |
| `symbol_info` | Get type signature and docs for any symbol |

### Type Checking

| Tool | Purpose |
|------|---------|
| `diagnostics` | Get fresh type errors and warnings for one file |

### Refactoring

| Tool | Purpose |
|------|---------|
| `preview_rename` | Preview a symbol rename across the project without writing files |

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
| `restart_server` | Restart TypeScript language server and daemon, then reload workspace projects |

## Tool Examples

### Recommended Workflow

For most single-file quality checks, start with `check_all`. It runs TypeScript
diagnostics, Prettier, and ESLint together and returns one combined summary.
Use `fix_all` when you want automatic ESLint fixes followed by Prettier
formatting, optionally writing the result back to disk.

`format_code`, `check_formatting`, and `lint_code` remain available as lower
level tools when you need only one formatter or linter operation. For
project-wide symbol-name discovery, start with text search to find candidate
files, then use `document_symbols` or the semantic position-based tools.

`preview_rename` is safe to inspect: it returns `edits`, a flat list of file
URI, one-based replacement range, and `newText` values, plus `totalEdits`. It
does not write to disk by itself.

For monorepos, start the server at the workspace root. The server auto-discovers
`pnpm-workspace.yaml` and `package.json` workspaces, then preloads package
`tsconfig.json` projects so `references`, `implementation`, and `preview_rename`
can see across loaded packages. Re-run `restart_server` after changing
workspace manifests or package `tsconfig.json` files. Repos without supported
workspace manifests or a root `tsconfig.json` may under-report cross-package
semantic results.

### Position Inputs And Results

Tools such as `definition`, `references`, `symbol_info`,
`type_info_of_reference`, and `preview_rename` use one-based positions for both
inputs and returned ranges. If
your editor, terminal listing, or agent `Read` output shows line 28, pass
`line=28`; returned ranges also use line 28 for that same source line. When you
do not already know a position, `document_symbols` returns one-based ranges for
the symbols in a file.

### Navigate to Definition

```python
# Find where a function is defined
result = await definition(
    file_path="/project/src/index.ts",
    line=10,
    character=15,
)
# Returns: {"items": [{"uri": "file:///project/src/utils.ts", "range": {...}}], "totalItems": 1}
```

### Get Type Information

```python
# Get TypeScript display info plus fields and methods of a value reference
result = await type_info_of_reference(
    file_path="/project/src/app.ts",
    line=5,
    character=8,
)
# Returns: {"displayString": "const user: User", "kind": "const", "fields": [...], "methods": {...}}
```

### Check Everything

```python
# Run TypeScript, Prettier, and ESLint checks for one file
result = await check_all(
    file_path="/project/src/app.ts"
)
# Returns: {"overallPassed": false, "checks": {...}, "summary": "..."}
```

### Fix Everything

```python
# Run ESLint fixes, then Prettier formatting
result = await fix_all(
    file_path="/project/src/app.ts",
    write=True,
)
# Returns: {"finalCode": "...", "totalChanges": 2, "written": true}
```

### Preview Rename

```python
# Preview a cross-project symbol rename without writing files
result = await preview_rename(
    file_path="/project/src/app.ts",
    line=12,
    character=8,
    new_name="newName",
)
# Returns:
# {
#   "edits": [{"uri": "file:///project/src/app.ts", "range": {...}, "newText": "newName"}],
#   "totalEdits": 1,
# }
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
- **TypeScript**: Resolves `tsconfig.json`; monorepos with `pnpm-workspace.yaml`
  or `package.json` workspaces preload package projects automatically

## Development

### Setup

```bash
git clone https://github.com/jonmmease/jons-mcp-typescript.git
cd jons-mcp-typescript
uv sync --dev
```

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/jons_mcp_typescript

# Run specific test file
uv run pytest tests/test_utils.py -v
```

### Test Requirements

Integration tests require:
- Node.js 18.18+, Node.js 20.9+, or a newer supported Node.js release
- vtsls installed globally or in the temporary test project
- Prettier and ESLint available to the temporary test project

Tests will skip gracefully if dependencies are missing.

## License

MIT
