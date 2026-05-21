"""Security-focused tool behavior tests."""

import tempfile
from pathlib import Path

import pytest

from jons_mcp_typescript import server
from jons_mcp_typescript.exceptions import PathOutsideProjectError
from jons_mcp_typescript.tools.unified import fix_all


@pytest.mark.asyncio
async def test_fix_all_rejects_write_outside_project_root():
    with tempfile.TemporaryDirectory() as project_tmp:
        with tempfile.TemporaryDirectory() as outside_tmp:
            server._project_root = Path(project_tmp)
            outside_file = Path(outside_tmp) / "outside.ts"
            outside_file.write_text("const x = 1;")
            try:
                with pytest.raises(PathOutsideProjectError):
                    await fix_all.fn(str(outside_file), write=True)
            finally:
                server._project_root = None
