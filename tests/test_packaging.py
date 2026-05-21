"""Packaging behavior tests."""

import subprocess
import zipfile


def test_wheel_includes_daemon_sources_and_excludes_node_modules(tmp_path):
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    wheel_path = next(tmp_path.glob("*.whl"))

    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())

    assert "jons_mcp_typescript/daemon/index.js" in names
    assert "jons_mcp_typescript/daemon/prettier-manager.js" in names
    assert "jons_mcp_typescript/daemon/eslint-manager.js" in names
    assert "jons_mcp_typescript/daemon/package.json" in names
    assert not any("node_modules" in name for name in names)
