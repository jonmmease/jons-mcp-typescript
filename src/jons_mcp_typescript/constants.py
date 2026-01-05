"""Constants for the TypeScript MCP server."""

# LSP Method Names (TypeScript Tools)
# Navigation & Discovery
LSP_WORKSPACE_SYMBOLS = "workspace/symbol"
LSP_DOCUMENT_SYMBOLS = "textDocument/documentSymbol"

# Jump to Code
LSP_DEFINITION = "textDocument/definition"
LSP_TYPE_DEFINITION = "textDocument/typeDefinition"
LSP_IMPLEMENTATION = "textDocument/implementation"
LSP_REFERENCES = "textDocument/references"

# Understanding Code
LSP_HOVER = "textDocument/hover"
LSP_SEMANTIC_TOKENS = "textDocument/semanticTokens/full"

# Type Checking
LSP_DIAGNOSTIC = "textDocument/diagnostic"

# Refactoring
LSP_RENAME = "textDocument/rename"

# Server Management
LSP_SHUTDOWN = "shutdown"
LSP_INITIALIZE = "initialize"
LSP_INITIALIZED = "initialized"
LSP_TEXT_DOCUMENT_DID_OPEN = "textDocument/didOpen"
LSP_TEXT_DOCUMENT_DID_CLOSE = "textDocument/didClose"
LSP_TEXT_DOCUMENT_DID_CHANGE = "textDocument/didChange"

# Error Codes
ERROR_CODE_INTERNAL_ERROR = -32000
ERROR_CODE_CONFIG_NOT_FOUND = -32001
ERROR_CODE_PARSE_ERROR = -32002
ERROR_CODE_PLUGIN_MISSING = -32003
ERROR_CODE_TIMEOUT = -32004
ERROR_CODE_JSON_PARSE_ERROR = -32700

# Timeout Constants (in seconds)
REQUEST_TIMEOUT = 60
FORMAT_TIMEOUT = 30
LINT_TIMEOUT = 60
DIAGNOSTICS_TIMEOUT = 5.0  # Timeout for waiting for diagnostics notification

# Pagination Defaults
DEFAULT_LIMIT = 20
DEFAULT_OFFSET = 0
