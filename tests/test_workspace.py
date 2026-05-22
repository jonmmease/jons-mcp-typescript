"""Workspace discovery and preloading behavior."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jons_mcp_typescript import server
from jons_mcp_typescript.workspace import (
    WorkspacePreloadStats,
    discover_workspace_projects,
    find_representative_source,
)


def write_package_project(
    package_dir: Path,
    *,
    source: bool = True,
    tsconfig: bool = True,
) -> None:
    package_dir.mkdir(parents=True)
    if tsconfig:
        (package_dir / "tsconfig.json").write_text("{}", encoding="utf-8")
    if source:
        src_dir = package_dir / "src"
        src_dir.mkdir()
        (src_dir / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")


def project_configs(project_root: Path) -> list[str]:
    return [
        project.tsconfig_path.relative_to(project_root).as_posix()
        for project in discover_workspace_projects(project_root)
    ]


def test_discovers_pnpm_workspace_globs_and_negations(tmp_path: Path):
    (tmp_path / "pnpm-workspace.yaml").write_text(
        """packages:
  - "packages/*"
  - "!packages/ignored"
""",
        encoding="utf-8",
    )
    write_package_project(tmp_path / "packages" / "included")
    write_package_project(tmp_path / "packages" / "ignored")

    assert project_configs(tmp_path) == ["packages/included/tsconfig.json"]


def test_discovers_package_json_workspace_array_and_object(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"workspaces": ["packages/*"]}',
        encoding="utf-8",
    )
    write_package_project(tmp_path / "packages" / "a")

    assert project_configs(tmp_path) == ["packages/a/tsconfig.json"]

    object_root = tmp_path / "object-root"
    object_root.mkdir()
    (object_root / "package.json").write_text(
        '{"workspaces": {"packages": ["modules/*"]}}',
        encoding="utf-8",
    )
    write_package_project(object_root / "modules" / "b")

    assert project_configs(object_root) == ["modules/b/tsconfig.json"]


def test_discovers_root_tsconfig_without_workspace_manifest(tmp_path: Path):
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const root = 1;\n",
        encoding="utf-8",
    )

    projects = discover_workspace_projects(tmp_path)

    assert [project.tsconfig_path.name for project in projects] == ["tsconfig.json"]
    assert projects[0].representative_file == tmp_path / "src" / "index.ts"


def test_root_tsconfig_representative_prunes_workspace_packages(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"workspaces": ["packages/*"]}',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    write_package_project(tmp_path / "packages" / "workspace-only")

    projects = discover_workspace_projects(tmp_path)
    representatives = {
        project.tsconfig_path.relative_to(tmp_path).as_posix(): project.representative_file
        for project in projects
    }

    assert representatives == {
        "packages/workspace-only/tsconfig.json": (
            tmp_path / "packages" / "workspace-only" / "src" / "index.ts"
        ),
        "tsconfig.json": None,
    }


def test_missing_manifests_and_no_root_tsconfig_return_no_projects(tmp_path: Path):
    assert discover_workspace_projects(tmp_path) == []


def test_skips_packages_without_tsconfig_and_marks_missing_source(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"workspaces": ["packages/*"]}',
        encoding="utf-8",
    )
    write_package_project(tmp_path / "packages" / "with-source")
    write_package_project(tmp_path / "packages" / "without-tsconfig", tsconfig=False)
    write_package_project(tmp_path / "packages" / "without-source", source=False)

    projects = discover_workspace_projects(tmp_path)

    assert [project.package_dir.name for project in projects] == [
        "with-source",
        "without-source",
    ]
    assert projects[0].representative_file is not None
    assert projects[1].representative_file is None


def test_ignores_outside_root_symlink_workspace(tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    write_package_project(outside)
    (tmp_path / "package.json").write_text(
        '{"workspaces": ["packages/*"]}',
        encoding="utf-8",
    )
    packages = tmp_path / "packages"
    packages.mkdir()
    (packages / "outside").symlink_to(outside, target_is_directory=True)
    write_package_project(packages / "inside")

    assert project_configs(tmp_path) == ["packages/inside/tsconfig.json"]


def test_representative_source_skips_build_outputs_and_declarations(tmp_path: Path):
    package_dir = tmp_path / "pkg"
    (package_dir / "src").mkdir(parents=True)
    (package_dir / "dist").mkdir()
    (package_dir / "src" / "types.d.ts").write_text(
        "export declare const value: number;\n",
        encoding="utf-8",
    )
    (package_dir / "dist" / "generated.ts").write_text(
        "export const generated = 1;\n",
        encoding="utf-8",
    )
    (package_dir / "src" / "index.ts").write_text(
        "export const value = 1;\n",
        encoding="utf-8",
    )

    assert find_representative_source(package_dir) == package_dir / "src" / "index.ts"


def test_representative_source_prefers_src_over_root_config(tmp_path: Path):
    package_dir = tmp_path / "pkg"
    (package_dir / "src").mkdir(parents=True)
    (package_dir / "jest.config.js").write_text(
        "export default {};\n",
        encoding="utf-8",
    )
    (package_dir / "src" / "index.ts").write_text(
        "export const value = 1;\n",
        encoding="utf-8",
    )
    tsconfig = package_dir / "tsconfig.json"
    tsconfig.write_text('{"include": ["src/**/*"]}', encoding="utf-8")

    assert (
        find_representative_source(package_dir, tsconfig_path=tsconfig)
        == package_dir / "src" / "index.ts"
    )


def test_representative_source_uses_tsconfig_include_without_src(tmp_path: Path):
    package_dir = tmp_path / "pkg"
    (package_dir / "lib").mkdir(parents=True)
    (package_dir / "jest.config.js").write_text(
        "export default {};\n",
        encoding="utf-8",
    )
    (package_dir / "lib" / "entry.ts").write_text(
        "export const value = 1;\n",
        encoding="utf-8",
    )
    tsconfig = package_dir / "tsconfig.json"
    tsconfig.write_text('{"include": ["lib/**/*"]}', encoding="utf-8")

    assert (
        find_representative_source(package_dir, tsconfig_path=tsconfig)
        == package_dir / "lib" / "entry.ts"
    )


def test_representative_source_respects_direct_tsconfig_exclude(tmp_path: Path):
    package_dir = tmp_path / "pkg"
    (package_dir / "lib").mkdir(parents=True)
    (package_dir / "lib" / "generated").mkdir()
    (package_dir / "lib" / "generated" / "a.ts").write_text(
        "export const generated = 1;\n",
        encoding="utf-8",
    )
    (package_dir / "lib" / "stable.ts").write_text(
        "export const stable = 1;\n",
        encoding="utf-8",
    )
    tsconfig = package_dir / "tsconfig.json"
    tsconfig.write_text(
        '{"include": ["lib/**/*"], "exclude": ["lib/generated"]}',
        encoding="utf-8",
    )

    assert (
        find_representative_source(package_dir, tsconfig_path=tsconfig)
        == package_dir / "lib" / "stable.ts"
    )


@pytest.mark.asyncio
async def test_preload_workspace_projects_records_loaded_skipped_and_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    (tmp_path / "package.json").write_text(
        '{"workspaces": ["packages/*"]}',
        encoding="utf-8",
    )
    write_package_project(tmp_path / "packages" / "loaded")
    write_package_project(tmp_path / "packages" / "failed")
    write_package_project(tmp_path / "packages" / "skipped", source=False)

    opened: list[str] = []
    closed: list[str] = []
    loaded: list[Path] = []

    async def open_file(client: Any, path: Path, uri: str) -> None:
        opened.append(uri)

    async def close_file(client: Any, uri: str) -> None:
        closed.append(uri)

    async def ensure_project_loaded(client: Any, path: Path) -> str:
        loaded.append(path)
        if "failed" in path.parts:
            raise RuntimeError("project boom")
        return str(path.parent.parent / "tsconfig.json")

    monkeypatch.setattr(server, "open_file", open_file)
    monkeypatch.setattr(server, "close_file", close_file)
    monkeypatch.setattr(server, "ensure_project_loaded", ensure_project_loaded)

    server.reset_workspace_preload_state()
    try:
        stats = await server.preload_workspace_projects(object(), tmp_path)  # type: ignore[arg-type]

        assert stats.discovered_projects == [
            "packages/failed/tsconfig.json",
            "packages/loaded/tsconfig.json",
            "packages/skipped/tsconfig.json",
        ]
        assert stats.loaded_projects == ["packages/loaded/tsconfig.json"]
        assert stats.skipped_projects == {
            "packages/skipped/tsconfig.json": "No representative source file found"
        }
        assert stats.failures == {"packages/failed/tsconfig.json": "project boom"}
        assert stats.representative_files == {
            "packages/failed/tsconfig.json": "packages/failed/src/index.ts",
            "packages/loaded/tsconfig.json": "packages/loaded/src/index.ts",
            "packages/skipped/tsconfig.json": None,
        }
        assert stats.loaded_config_keys == {
            "packages/loaded/tsconfig.json": str(
                tmp_path / "packages" / "loaded" / "tsconfig.json"
            )
        }
        assert opened == [
            (tmp_path / "packages" / "failed" / "src" / "index.ts").as_uri(),
            (tmp_path / "packages" / "loaded" / "src" / "index.ts").as_uri(),
        ]
        assert closed == [
            (tmp_path / "packages" / "failed" / "src" / "index.ts").as_uri()
        ]
        assert stats.held_open_uris == [
            (tmp_path / "packages" / "loaded" / "src" / "index.ts").as_uri()
        ]
        assert loaded == [
            tmp_path / "packages" / "failed" / "src" / "index.ts",
            tmp_path / "packages" / "loaded" / "src" / "index.ts",
        ]
        assert server.workspace_preload_stats == stats
    finally:
        server.reset_workspace_preload_state()


@pytest.mark.asyncio
async def test_preload_rejects_inferred_or_mismatched_project_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    (tmp_path / "package.json").write_text(
        '{"workspaces": ["packages/*"]}',
        encoding="utf-8",
    )
    write_package_project(tmp_path / "packages" / "inferred")
    write_package_project(tmp_path / "packages" / "mismatched")

    closed: list[str] = []

    async def open_file(client: Any, path: Path, uri: str) -> None:
        pass

    async def close_file(client: Any, uri: str) -> None:
        closed.append(uri)

    async def ensure_project_loaded(client: Any, path: Path) -> str:
        if "inferred" in path.parts:
            return f"inferred:{path}"
        return str(tmp_path / "other" / "tsconfig.json")

    monkeypatch.setattr(server, "open_file", open_file)
    monkeypatch.setattr(server, "close_file", close_file)
    monkeypatch.setattr(server, "ensure_project_loaded", ensure_project_loaded)

    server.reset_workspace_preload_state()
    try:
        stats = await server.preload_workspace_projects(object(), tmp_path)  # type: ignore[arg-type]

        assert stats.loaded_projects == []
        assert set(stats.failures) == {
            "packages/inferred/tsconfig.json",
            "packages/mismatched/tsconfig.json",
        }
        assert "inferred TypeScript project" in stats.failures[
            "packages/inferred/tsconfig.json"
        ]
        assert "but expected" in stats.failures[
            "packages/mismatched/tsconfig.json"
        ]
        assert sorted(closed) == sorted(
            [
                (tmp_path / "packages" / "inferred" / "src" / "index.ts").as_uri(),
                (tmp_path / "packages" / "mismatched" / "src" / "index.ts").as_uri(),
            ]
        )
        assert stats.held_open_uris == []
    finally:
        server.reset_workspace_preload_state()


@pytest.mark.asyncio
async def test_close_workspace_preload_files_closes_retained_representatives(
    monkeypatch: pytest.MonkeyPatch,
):
    closed: list[str] = []

    async def close_file(client: Any, uri: str) -> None:
        closed.append(uri)

    monkeypatch.setattr(server, "close_file", close_file)
    server.reset_workspace_preload_state()
    server.workspace_preload_state.open_file_uris.update(
        {"file:///project/a.ts", "file:///project/b.ts"}
    )
    server.workspace_preload_state.stats.held_open_uris = [
        "file:///project/a.ts",
        "file:///project/b.ts",
    ]

    await server.close_workspace_preload_files(object())  # type: ignore[arg-type]

    assert closed == ["file:///project/a.ts", "file:///project/b.ts"]
    assert server.workspace_preload_state.open_file_uris == set()
    assert server.workspace_preload_state.stats.held_open_uris == []


@pytest.mark.asyncio
async def test_background_preload_transitions_to_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    expected = WorkspacePreloadStats(
        discovered_projects=["packages/a/tsconfig.json"],
        loaded_projects=["packages/a/tsconfig.json"],
    )

    async def preload(client: Any, project_root: Path | None = None) -> WorkspacePreloadStats:
        return expected

    monkeypatch.setattr(server, "preload_workspace_projects", preload)
    server.reset_workspace_preload_state()
    try:
        state = await server.schedule_workspace_preload(object(), tmp_path)
        assert state.status == "running"
        assert state.task is not None

        await state.task

        assert server.workspace_preload_state.status == "complete"
        assert server.workspace_preload_state.task is None
        assert server.workspace_preload_state.stats == expected
        assert server.workspace_preload_warning() is None
    finally:
        await server.cancel_workspace_preload()
        server.reset_workspace_preload_state()


@pytest.mark.asyncio
async def test_background_preload_failure_and_cancellation_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def failing_preload(
        client: Any, project_root: Path | None = None
    ) -> WorkspacePreloadStats:
        raise RuntimeError("workspace boom")

    monkeypatch.setattr(server, "preload_workspace_projects", failing_preload)
    server.reset_workspace_preload_state()
    release: asyncio.Event | None = None
    try:
        failed_state = await server.schedule_workspace_preload(object(), tmp_path)
        assert failed_state.task is not None
        await failed_state.task

        assert server.workspace_preload_state.status == "failed"
        assert server.workspace_preload_state.error == "workspace boom"
        failed_warning = server.workspace_preload_warning()
        assert failed_warning is not None
        assert "workspace boom" in failed_warning["message"]

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_preload(
            client: Any, project_root: Path | None = None
        ) -> WorkspacePreloadStats:
            started.set()
            await release.wait()
            return WorkspacePreloadStats()

        monkeypatch.setattr(server, "preload_workspace_projects", slow_preload)
        cancelled_state = await server.schedule_workspace_preload(object(), tmp_path)
        assert cancelled_state.task is not None
        await started.wait()

        await server.cancel_workspace_preload()

        assert server.workspace_preload_state.status == "cancelled"
        cancelled_warning = server.workspace_preload_warning()
        assert cancelled_warning is not None
        assert "cancelled" in cancelled_warning["message"]
    finally:
        if release:
            release.set()
        await server.cancel_workspace_preload()
        server.reset_workspace_preload_state()
