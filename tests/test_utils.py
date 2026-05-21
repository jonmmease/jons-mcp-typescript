"""Unit tests for utility functions."""

import tempfile
from pathlib import Path

import pytest

from jons_mcp_typescript.exceptions import PathOutsideProjectError
from jons_mcp_typescript.utils import (
    apply_pagination,
    diagnostic_sort_key,
    location_sort_key,
    resolve_project_path,
)


class TestResolveProjectPath:
    """Test project-root scoped path resolution."""

    def test_relative_path_resolves_against_project_root(self, monkeypatch):
        with tempfile.TemporaryDirectory() as project_tmp:
            with tempfile.TemporaryDirectory() as cwd_tmp:
                project_root = Path(project_tmp)
                source_file = project_root / "src" / "main.ts"
                source_file.parent.mkdir()
                source_file.write_text("export const value = 1;")
                monkeypatch.chdir(cwd_tmp)

                result = resolve_project_path("src/main.ts", project_root)

                assert result == source_file.resolve()

    def test_file_uri_inside_project_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            source_file = project_root / "main.ts"
            source_file.write_text("export {};")

            result = resolve_project_path(source_file.as_uri(), project_root)

            assert result == source_file.resolve()

    def test_absolute_outside_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            with tempfile.TemporaryDirectory() as outside_tmp:
                outside_file = Path(outside_tmp) / "outside.ts"
                outside_file.write_text("export {};")

                with pytest.raises(PathOutsideProjectError):
                    resolve_project_path(str(outside_file), Path(project_tmp))

    def test_parent_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            project_root = Path(project_tmp)
            outside_file = project_root.parent / "outside.ts"
            outside_file.write_text("export {};")
            try:
                with pytest.raises(PathOutsideProjectError):
                    resolve_project_path("../outside.ts", project_root)
            finally:
                outside_file.unlink()

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            with tempfile.TemporaryDirectory() as outside_tmp:
                project_root = Path(project_tmp)
                outside_dir = Path(outside_tmp)
                outside_file = outside_dir / "outside.ts"
                outside_file.write_text("export {};")
                link = project_root / "link"
                link.symlink_to(outside_dir, target_is_directory=True)

                with pytest.raises(PathOutsideProjectError):
                    resolve_project_path("link/outside.ts", project_root)


class TestApplyPagination:
    """Test suite for apply_pagination function."""

    def test_empty_list(self):
        """Test pagination on empty list."""
        items, meta = apply_pagination([], 0, 20)
        assert items == []
        assert meta["totalItems"] == 0
        assert meta["hasMore"] is False
        assert meta["offset"] == 0
        assert meta["limit"] == 20

    def test_items_within_limit(self):
        """Test when all items fit within limit."""
        data = [1, 2, 3, 4, 5]
        items, meta = apply_pagination(data, 0, 20)
        assert len(items) == 5
        assert meta["totalItems"] == 5
        assert meta["hasMore"] is False
        assert meta["nextOffset"] is None

    def test_items_with_offset(self):
        """Test pagination with offset."""
        data = [1, 2, 3, 4, 5]
        items, meta = apply_pagination(data, 2, 20)
        assert items == [{"item": 3, "offset": 2}, {"item": 4, "offset": 3}, {"item": 5, "offset": 4}]
        assert meta["offset"] == 2
        assert meta["totalItems"] == 5

    def test_items_with_limit(self):
        """Test pagination with limit."""
        data = list(range(10))
        items, meta = apply_pagination(data, 0, 3)
        assert len(items) == 3
        assert meta["hasMore"] is True
        assert meta["nextOffset"] == 3
        assert meta["limit"] == 3

    def test_items_with_offset_and_limit(self):
        """Test pagination with both offset and limit."""
        data = list(range(20))
        items, meta = apply_pagination(data, 5, 5)
        assert len(items) == 5
        assert meta["offset"] == 5
        assert meta["hasMore"] is True
        assert meta["nextOffset"] == 10

    def test_offset_beyond_list_length(self):
        """Test when offset exceeds list length."""
        data = [1, 2, 3]
        items, meta = apply_pagination(data, 10, 20)
        assert items == []
        assert meta["hasMore"] is False
        assert meta["offset"] == 10

    def test_pagination_without_offset_field(self):
        """Test pagination without adding offset field."""
        data = [1, 2, 3, 4, 5]
        items, meta = apply_pagination(data, 0, 3, add_offset_field=False)
        assert items == [1, 2, 3]
        assert len(items) == 3

    def test_pagination_with_dict_items(self):
        """Test pagination with dictionary items."""
        data = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 3, "name": "Charlie"},
        ]
        items, meta = apply_pagination(data, 0, 2)
        assert len(items) == 2
        assert items[0]["id"] == 1
        assert items[0]["offset"] == 0
        assert items[1]["offset"] == 1
        assert meta["hasMore"] is True

    def test_pagination_metadata(self):
        """Test that pagination metadata is correct."""
        data = list(range(100))
        items, meta = apply_pagination(data, 10, 20)
        assert meta["totalItems"] == 100
        assert meta["offset"] == 10
        assert meta["limit"] == 20
        assert meta["hasMore"] is True
        assert meta["nextOffset"] == 30


class TestLocationSortKey:
    """Test suite for location_sort_key function."""

    def test_sort_by_uri(self):
        """Test sorting by URI."""
        items = [
            {"uri": "file://z.ts", "range": {"start": {"line": 0, "character": 0}}},
            {"uri": "file://a.ts", "range": {"start": {"line": 0, "character": 0}}},
        ]
        sorted_items = sorted(items, key=location_sort_key)
        assert sorted_items[0]["uri"] == "file://a.ts"
        assert sorted_items[1]["uri"] == "file://z.ts"

    def test_sort_by_line_when_same_uri(self):
        """Test sorting by line number when URIs are the same."""
        items = [
            {"uri": "file://a.ts", "range": {"start": {"line": 10, "character": 0}}},
            {"uri": "file://a.ts", "range": {"start": {"line": 5, "character": 0}}},
        ]
        sorted_items = sorted(items, key=location_sort_key)
        assert sorted_items[0]["range"]["start"]["line"] == 5
        assert sorted_items[1]["range"]["start"]["line"] == 10

    def test_sort_by_character_when_same_line(self):
        """Test sorting by character when line is the same."""
        items = [
            {"uri": "file://a.ts", "range": {"start": {"line": 5, "character": 10}}},
            {"uri": "file://a.ts", "range": {"start": {"line": 5, "character": 3}}},
        ]
        sorted_items = sorted(items, key=location_sort_key)
        assert sorted_items[0]["range"]["start"]["character"] == 3
        assert sorted_items[1]["range"]["start"]["character"] == 10

    def test_missing_uri_defaults_to_empty_string(self):
        """Test that missing URI defaults to empty string."""
        item = {"range": {"start": {"line": 0, "character": 0}}}
        key = location_sort_key(item)
        assert key[0] == ""

    def test_missing_range_defaults_to_zero(self):
        """Test that missing range information defaults to 0."""
        item = {"uri": "file://test.ts"}
        key = location_sort_key(item)
        assert key[1] == 0  # line
        assert key[2] == 0  # character


class TestDiagnosticSortKey:
    """Test suite for diagnostic_sort_key function."""

    def test_sort_by_severity(self):
        """Test primary sort by severity (errors first)."""
        items = [
            {
                "severity": 3,  # Hint
                "uri": "file://a.ts",
                "range": {"start": {"line": 0, "character": 0}},
            },
            {
                "severity": 1,  # Error
                "uri": "file://a.ts",
                "range": {"start": {"line": 0, "character": 0}},
            },
        ]
        sorted_items = sorted(items, key=diagnostic_sort_key)
        assert sorted_items[0]["severity"] == 1

    def test_sort_by_uri_secondary(self):
        """Test secondary sort by URI."""
        items = [
            {
                "severity": 1,
                "uri": "file://z.ts",
                "range": {"start": {"line": 0, "character": 0}},
            },
            {
                "severity": 1,
                "uri": "file://a.ts",
                "range": {"start": {"line": 0, "character": 0}},
            },
        ]
        sorted_items = sorted(items, key=diagnostic_sort_key)
        assert sorted_items[0]["uri"] == "file://a.ts"

    def test_sort_by_position_tertiary(self):
        """Test tertiary sort by position."""
        items = [
            {
                "severity": 1,
                "uri": "file://a.ts",
                "range": {"start": {"line": 10, "character": 0}},
            },
            {
                "severity": 1,
                "uri": "file://a.ts",
                "range": {"start": {"line": 5, "character": 0}},
            },
        ]
        sorted_items = sorted(items, key=diagnostic_sort_key)
        assert sorted_items[0]["range"]["start"]["line"] == 5

    def test_missing_severity_defaults_to_999(self):
        """Test that missing severity defaults to 999 (least severe)."""
        items = [
            {
                "severity": 1,
                "uri": "file://a.ts",
                "range": {"start": {"line": 0, "character": 0}},
            },
            {
                "uri": "file://a.ts",
                "range": {"start": {"line": 0, "character": 0}},
            },
        ]
        sorted_items = sorted(items, key=diagnostic_sort_key)
        assert sorted_items[0]["severity"] == 1
