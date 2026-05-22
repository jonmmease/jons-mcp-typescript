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
    representative_files: dict[str, str | None] = field(default_factory=dict)
    loaded_config_keys: dict[str, str] = field(default_factory=dict)
    held_open_uris: list[str] = field(default_factory=list)


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
                tsconfig_path=root_tsconfig.resolve(strict=True),
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
            representative_file=find_representative_source(
                package_dir,
                tsconfig_path=resolved_config,
            ),
        )

    return sorted(
        projects_by_config.values(),
        key=lambda project: project.tsconfig_path.as_posix(),
    )


def find_representative_source(
    package_dir: Path,
    *,
    tsconfig_path: Path | None = None,
    excluded_roots: set[Path] | None = None,
) -> Path | None:
    """Return a stable representative source file for a package project."""
    root = package_dir.resolve(strict=True)
    excluded = {
        excluded_root.resolve(strict=True)
        for excluded_root in excluded_roots or set()
    }

    src_dir = root / "src"
    if src_dir.is_dir():
        candidate = _first_source_file([src_dir], root, excluded)
        if candidate is not None:
            return candidate

    if tsconfig_path is not None:
        candidate = _first_tsconfig_source_file(
            tsconfig_path,
            root,
            excluded,
        )
        if candidate is not None:
            return candidate

    return _first_source_file([root], root, excluded)


def _first_tsconfig_source_file(
    tsconfig_path: Path,
    root: Path,
    excluded_roots: set[Path],
) -> Path | None:
    config = _read_tsconfig(tsconfig_path)
    if not config:
        return None

    files = config.get("files")
    if isinstance(files, list):
        file_candidates = [
            root / item
            for item in files
            if isinstance(item, str)
        ]
        candidate = _first_valid_source_path(file_candidates, root, excluded_roots)
        if candidate is not None:
            return candidate

    include = config.get("include")
    include_globs = (
        [item for item in include if isinstance(item, str)]
        if isinstance(include, list)
        else ["**/*"]
    )
    exclude = config.get("exclude")
    exclude_globs = (
        [item for item in exclude if isinstance(item, str)]
        if isinstance(exclude, list)
        else []
    )
    excluded = excluded_roots | _expanded_excluded_paths(root, exclude_globs)
    include_candidates: list[Path] = []
    for pattern in include_globs:
        try:
            include_candidates.extend(root.glob(pattern))
        except (NotImplementedError, OSError, RuntimeError, ValueError):
            continue

    return _first_valid_source_path(include_candidates, root, excluded)


def _first_source_file(
    roots: list[Path],
    package_root: Path,
    excluded_roots: set[Path],
) -> Path | None:
    candidates: list[Path] = []
    for search_root in roots:
        try:
            resolved_search_root = search_root.resolve(strict=True)
            resolved_search_root.relative_to(package_root)
        except (OSError, RuntimeError, ValueError):
            continue
        for current_root, dirnames, filenames in os.walk(resolved_search_root):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if _should_walk_source_dir(Path(current_root) / name, excluded_roots)
            )
            current_path = Path(current_root)
            for filename in sorted(filenames):
                candidate = _valid_source_path(
                    current_path / filename,
                    package_root,
                    excluded_roots,
                )
                if candidate is not None:
                    candidates.append(candidate)
    return candidates[0] if candidates else None


def _first_valid_source_path(
    paths: list[Path],
    package_root: Path,
    excluded_roots: set[Path],
) -> Path | None:
    candidates: list[Path] = []
    for path in paths:
        if path.is_dir():
            candidate = _first_source_file([path], package_root, excluded_roots)
            if candidate is not None:
                candidates.append(candidate)
            continue
        candidate = _valid_source_path(path, package_root, excluded_roots)
        if candidate is not None:
            candidates.append(candidate)
    return sorted(candidates, key=lambda candidate: candidate.as_posix())[0] if candidates else None


def _valid_source_path(
    path: Path,
    package_root: Path,
    excluded_roots: set[Path],
) -> Path | None:
    if path.suffix not in SOURCE_EXTENSIONS:
        return None
    if path.name.endswith(DECLARATION_SUFFIXES):
        return None
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(package_root)
    except (OSError, RuntimeError, ValueError):
        return None
    if any(resolved == excluded or excluded in resolved.parents for excluded in excluded_roots):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _expanded_excluded_paths(root: Path, globs: list[str]) -> set[Path]:
    excluded: set[Path] = set()
    for pattern in globs:
        try:
            candidates = root.glob(pattern)
        except (NotImplementedError, OSError, RuntimeError, ValueError):
            continue
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, RuntimeError, ValueError):
                continue
            excluded.add(resolved)
    return excluded


def _read_tsconfig(tsconfig_path: Path) -> dict[str, Any]:
    try:
        text = tsconfig_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(_strip_trailing_commas(_strip_json_comments(text)))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _strip_json_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    string_quote = ""
    escaped = False
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == string_quote:
                in_string = False
            index += 1
            continue

        if char in ("'", '"'):
            in_string = True
            string_quote = char
            result.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and text[index:index + 2] != "*/":
                index += 1
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


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
