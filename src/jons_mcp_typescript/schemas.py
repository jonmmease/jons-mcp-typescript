"""Public response schemas for MCP tools."""

from pydantic import BaseModel, ConfigDict, Field


class PublicPosition(BaseModel):
    """One-based source position returned by public tools."""

    model_config = ConfigDict(extra="forbid")

    line: int = Field(..., ge=1, description="One-based line number.")
    character: int = Field(..., ge=1, description="One-based character offset.")


class PublicRange(BaseModel):
    """One-based source range returned by public tools."""

    model_config = ConfigDict(extra="forbid")

    start: PublicPosition
    end: PublicPosition


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
