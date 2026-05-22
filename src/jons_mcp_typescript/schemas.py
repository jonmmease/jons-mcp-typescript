"""Public response schemas for MCP tools."""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PublicPosition(BaseModel):
    """One-based source position returned by public tools."""

    model_config = ConfigDict(extra="forbid")

    line: int = Field(..., ge=1, description="One-based line number.")
    character: int = Field(..., ge=1, description="One-based character offset.")


class PublicRange(BaseModel):
    """One-based source range returned by public tools."""

    model_config = ConfigDict(extra="forbid")

    start: PublicPosition
    end: PublicPosition | None = None


class PublicLocation(BaseModel):
    """File URI and optional one-based range."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    range: PublicRange | None = None


class NavigationLocation(BaseModel):
    """Normalized target returned by navigation tools."""

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(..., description="File URI for the navigation target.")
    range: PublicRange | dict[str, Any] | None = Field(
        default=None,
        description="One-based precise target range.",
    )
    fullRange: PublicRange | dict[str, Any] | None = Field(
        default=None,
        description="One-based full target range when the language server provides it.",
    )
    originRange: PublicRange | dict[str, Any] | None = Field(
        default=None,
        description="One-based range of the originating symbol when available.",
    )


class NavigationResult(BaseModel):
    """Result returned by definition, type_definition, and implementation."""

    model_config = ConfigDict(extra="forbid")

    items: list[NavigationLocation]
    totalItems: int = Field(..., ge=0)
    warnings: list[str] | None = None


class PaginatedResult(BaseModel, Generic[T]):
    """Common paginated result envelope."""

    model_config = ConfigDict(extra="forbid")

    items: list[T]
    totalItems: int = Field(..., ge=0)
    offset: int = Field(..., ge=0)
    limit: int = Field(..., ge=0)
    hasMore: bool
    nextOffset: int | None = None


class FormatCodeResult(BaseModel):
    """Result returned by format_code."""

    model_config = ConfigDict(extra="forbid")

    formatted: bool
    code: str
    changed: bool


class CheckFormattingResult(BaseModel):
    """Result returned by check_formatting."""

    model_config = ConfigDict(extra="forbid")

    formatted: bool
    message: str


class LintCodeResult(BaseModel):
    """Result returned by lint_code."""

    model_config = ConfigDict(extra="forbid")

    issues: list[dict[str, Any]]
    totalIssues: int = Field(..., ge=0)
    errors: int = Field(..., ge=0)
    warnings: int = Field(..., ge=0)
    fixed: bool
    fixedCode: str | None = None


class PublicLocationItem(PublicLocation):
    """Paginated source location item."""

    offset: int = Field(..., ge=0)


class ReferencesResult(PaginatedResult[PublicLocationItem]):
    """Result returned by references."""

    warnings: list[str] | None = None


class DiagnosticItem(BaseModel):
    """One TypeScript diagnostic item."""

    model_config = ConfigDict(extra="allow")

    severity: int | str | None = None
    message: str | None = None
    range: PublicRange | dict[str, Any] | None = None
    uri: str | None = None
    offset: int | None = Field(default=None, ge=0)


class DiagnosticsResult(PaginatedResult[DiagnosticItem]):
    """Result returned by diagnostics."""


class DocumentSymbolItem(BaseModel):
    """One document symbol item."""

    model_config = ConfigDict(extra="allow")

    name: str
    kind: int
    range: PublicRange | dict[str, Any] | None = None
    selectionRange: PublicRange | dict[str, Any] | None = None
    containerName: str | None = None
    offset: int | None = Field(default=None, ge=0)


class DocumentSymbolsResult(PaginatedResult[DocumentSymbolItem]):
    """Result returned by document_symbols."""


class SymbolInfoResult(BaseModel):
    """Result returned by symbol_info."""

    model_config = ConfigDict(extra="forbid")

    content: str | None
    range: PublicRange | dict[str, Any] | None = None


class TypeField(BaseModel):
    """Field discovered by type_info_of_reference."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    documentation: str | None = None


class TypeMethod(BaseModel):
    """Method discovered by type_info_of_reference."""

    model_config = ConfigDict(extra="allow")

    name: str
    signature: str
    documentation: str | None = None


class TypeMethodsResult(PaginatedResult[TypeMethod]):
    """Paginated methods in a type_info_of_reference result."""


class TypeInfoResult(BaseModel):
    """Result returned by type_info_of_reference."""

    model_config = ConfigDict(extra="forbid")

    displayString: str
    kind: str | None = None
    fields: list[TypeField]
    methods: TypeMethodsResult
    sourceLocation: PublicLocation | None = None


class CheckError(BaseModel):
    """Failed check with an error string."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    error: str


class PrettierCheck(BaseModel):
    """Prettier check result inside check_all."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    message: str


class ESLintCheck(BaseModel):
    """ESLint check result inside check_all."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    errorCount: int = Field(..., ge=0)
    warningCount: int = Field(..., ge=0)
    issues: list[dict[str, Any]]


class TypeScriptCheck(BaseModel):
    """TypeScript check result inside check_all."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    errorCount: int = Field(..., ge=0)
    warningCount: int = Field(..., ge=0)
    diagnostics: list[DiagnosticItem]


CheckResult = PrettierCheck | ESLintCheck | TypeScriptCheck | CheckError


class CheckAllResult(BaseModel):
    """Result returned by check_all."""

    model_config = ConfigDict(extra="forbid")

    checks: dict[str, CheckResult]
    overallPassed: bool
    summary: str


class BasicFixStatus(BaseModel):
    """Simple fix step status inside fix_all."""

    model_config = ConfigDict(extra="forbid")

    applied: bool


class ESLintFixStatus(BaseModel):
    """ESLint fix step status inside fix_all."""

    model_config = ConfigDict(extra="forbid")

    applied: bool
    issuesFixed: int = Field(..., ge=0)


FixResult = BasicFixStatus | ESLintFixStatus


class FixAllResult(BaseModel):
    """Result returned by fix_all."""

    model_config = ConfigDict(extra="forbid")

    fixes: dict[str, FixResult]
    finalCode: str
    totalChanges: int = Field(..., ge=0)
    written: bool


class RenamePreviewEdit(BaseModel):
    """One file edit returned by preview_rename."""

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(..., description="File URI for the file to edit.")
    range: PublicRange = Field(..., description="One-based replacement range.")
    newText: str = Field(..., description="Replacement text for the range.")


class RenamePreviewResult(BaseModel):
    """Normalized preview_rename result."""

    model_config = ConfigDict(extra="forbid")

    edits: list[RenamePreviewEdit]
    totalEdits: int = Field(..., ge=0, description="Total number of edits.")


class RenamePreviewError(BaseModel):
    """Error returned by preview_rename when no valid preview is available."""

    model_config = ConfigDict(extra="forbid")

    error: str
