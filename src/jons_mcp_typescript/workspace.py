"""Workspace discovery helpers for TypeScript monorepos."""

import ast
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}
DECLARATION_SUFFIXES = (".d.ts", ".d.tsx", ".d.mts", ".d.cts")
EXCLUDED_SOURCE_DIRS = {
    ".git",
    ".next",
    ".turbo",
    "build",
    "coverage",
    "dist",
    "node_modules",
}


@dataclass(frozen=True)
class WorkspaceProject:
    """A discovered TypeScript project in the configured workspace."""

    package_dir: Path
    tsconfig_path: Path
    representative_file: Path | None


@dataclass
class WorkspacePreloadStats:
    """Summary of workspace project preloading."""

    discovered_projects: list[str] = field(default_factory=list)
    loaded_projects: list[str] = field(default_factory=list)
    skipped_projects: dict[str, str] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)


def discover_workspace_projects(project_root: Path) -> list[WorkspaceProject]:
    """Discover TypeScript projects from supported workspace manifests."""
    root = project_root.expanduser().resolve(strict=True)
    package_dirs = _workspace_package_dirs(root)
    projects_by_config: dict[Path, WorkspaceProject] = {}

    root_tsconfig = root / "tsconfig.json"
    if root_tsconfig.exists():
        projects_by_config[root_tsconfig.resolve(strict=True)] = WorkspaceProject(
            package_dir=root,
            tsconfig_path=root_tsconfig.resolve(strict=True),
            representative_file=find_representative_source(
                root,
                excluded_roots=set(package_dirs),
            ),
        )

    for package_dir in package_dirs:
        tsconfig = package_dir / "tsconfig.json"
        if not tsconfig.exists():
            continue
        resolved_config = tsconfig.resolve(strict=True)
        projects_by_config[resolved_config] = WorkspaceProject(
            package_dir=package_dir,
            tsconfig_path=resolved_config,
            representative_file=find_representative_source(package_dir),
        )

    return sorted(
        projects_by_config.values(),
        key=lambda project: project.tsconfig_path.as_posix(),
    )


def find_representative_source(
    package_dir: Path,
    *,
    excluded_roots: set[Path] | None = None,
) -> Path | None:
    """Return a stable representative source file for a package project."""
    root = package_dir.resolve(strict=True)
    excluded = {
        excluded_root.resolve(strict=True)
        for excluded_root in excluded_roots or set()
    }
    candidates: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if _should_walk_source_dir(Path(current_root) / name, excluded)
        )
        current_path = Path(current_root)
        for filename in sorted(filenames):
            path = current_path / filename
            if path.suffix not in SOURCE_EXTENSIONS:
                continue
            if path.name.endswith(DECLARATION_SUFFIXES):
                continue
            candidates.append(path.resolve(strict=True))
    return candidates[0] if candidates else None


def _should_walk_source_dir(path: Path, excluded_roots: set[Path]) -> bool:
    if path.name in EXCLUDED_SOURCE_DIRS:
        return False
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    return resolved not in excluded_roots


def _workspace_package_dirs(root: Path) -> list[Path]:
    globs = _read_pnpm_workspace_globs(root) + _read_package_json_workspace_globs(root)
    if not globs:
        return []

    includes = [glob for glob in globs if not glob.startswith("!")]
    excludes = [glob[1:] for glob in globs if glob.startswith("!")]
    excluded_dirs = _expand_workspace_globs(root, excludes)
    included_dirs = _expand_workspace_globs(root, includes)

    return sorted(
        included_dirs - excluded_dirs,
        key=lambda path: path.as_posix(),
    )


def _expand_workspace_globs(root: Path, globs: list[str]) -> set[Path]:
    paths: set[Path] = set()
    for glob in globs:
        if not glob:
            continue
        try:
            candidates = root.glob(glob)
        except (NotImplementedError, OSError, RuntimeError, ValueError):
            continue
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, RuntimeError, ValueError):
                continue
            if resolved.is_dir():
                paths.add(resolved)
    return paths


def _read_pnpm_workspace_globs(root: Path) -> list[str]:
    manifest = root / "pnpm-workspace.yaml"
    if not manifest.exists():
        return []

    lines = manifest.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^(\s*)packages\s*:\s*(.*)$", line)
        if not match:
            continue

        base_indent = len(match.group(1))
        remainder = _strip_yaml_scalar(match.group(2))
        if remainder:
            parsed = _parse_inline_list(remainder)
            return parsed if parsed is not None else [remainder]

        globs: list[str] = []
        for item_line in lines[index + 1 :]:
            if not item_line.strip() or item_line.strip().startswith("#"):
                continue
            indent = len(item_line) - len(item_line.lstrip())
            if indent <= base_indent:
                break
            item = item_line.strip()
            if not item.startswith("-"):
                continue
            value = _strip_yaml_scalar(item[1:].strip())
            if value:
                globs.append(value)
        return globs

    return []


def _read_package_json_workspace_globs(root: Path) -> list[str]:
    manifest = root / "package.json"
    if not manifest.exists():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    workspaces = data.get("workspaces") if isinstance(data, dict) else None
    if isinstance(workspaces, list):
        return [item for item in workspaces if isinstance(item, str)]
    if isinstance(workspaces, dict):
        packages = workspaces.get("packages")
        if isinstance(packages, list):
            return [item for item in packages if isinstance(item, str)]
    return []


def _parse_inline_list(value: str) -> list[str] | None:
    if not (value.startswith("[") and value.endswith("]")):
        return None
    try:
        parsed: Any = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    return [item for item in parsed if isinstance(item, str)]


def _strip_yaml_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if "#" in value:
        value = value.split("#", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value
