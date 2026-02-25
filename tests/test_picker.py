"""Tests for proton_to_icloud.picker — pure-function tests only."""

from proton_to_icloud.picker import (
    detect_proton_exports,
    list_directory_entries,
    pick_start_directory,
)


class TestDetectProtonExports:
    def test_single_export(self, tmp_path):
        export = tmp_path / "user@pm.me" / "mail_20260223_210229"
        export.mkdir(parents=True)
        result = detect_proton_exports(str(tmp_path))
        assert result == [str(export)]

    def test_multiple_exports_sorted_newest_first(self, tmp_path):
        old = tmp_path / "user@pm.me" / "mail_20250101_000000"
        new = tmp_path / "user@pm.me" / "mail_20260223_210229"
        old.mkdir(parents=True)
        new.mkdir(parents=True)
        result = detect_proton_exports(str(tmp_path))
        assert result[0] == str(new)
        assert result[1] == str(old)

    def test_no_exports(self, tmp_path):
        (tmp_path / "some_folder").mkdir()
        assert detect_proton_exports(str(tmp_path)) == []

    def test_non_matching_names(self, tmp_path):
        (tmp_path / "user@pm.me" / "not_a_mail_dir").mkdir(parents=True)
        (tmp_path / "user@pm.me" / "mail_short").mkdir(parents=True)
        assert detect_proton_exports(str(tmp_path)) == []

    def test_too_deep_nesting_ignored(self, tmp_path):
        deep = tmp_path / "a" / "b" / "mail_20260101_120000"
        deep.mkdir(parents=True)
        assert detect_proton_exports(str(tmp_path)) == []

    def test_nonexistent_base(self, tmp_path):
        assert detect_proton_exports(str(tmp_path / "nope")) == []


class TestListDirectoryEntries:
    def test_parent_always_first(self, tmp_path):
        (tmp_path / "Alpha").mkdir()
        entries = list_directory_entries(str(tmp_path))
        assert entries[0].name == "../"

    def test_subdirs_listed_alphabetically(self, tmp_path):
        (tmp_path / "Zebra").mkdir()
        (tmp_path / "Apple").mkdir()
        entries = list_directory_entries(str(tmp_path))
        names = [e.name for e in entries[1:]]
        assert names == ["Apple/", "Zebra/"]

    def test_files_excluded(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hi")
        (tmp_path / "SubDir").mkdir()
        entries = list_directory_entries(str(tmp_path))
        names = [e.name for e in entries]
        assert "readme.txt" not in names
        assert "SubDir/" in names

    def test_eml_counts(self, tmp_path):
        inbox = tmp_path / "Inbox"
        inbox.mkdir()
        (inbox / "msg1.eml").write_text("")
        (inbox / "msg2.eml").write_text("")
        (inbox / "msg1.metadata.json").write_text("")
        entries = list_directory_entries(str(tmp_path))
        inbox_entry = [e for e in entries if e.name == "Inbox/"][0]
        assert inbox_entry.eml_count == 2

    def test_empty_subdir_zero_count(self, tmp_path):
        (tmp_path / "Empty").mkdir()
        entries = list_directory_entries(str(tmp_path))
        empty_entry = [e for e in entries if e.name == "Empty/"][0]
        assert empty_entry.eml_count == 0

    def test_permission_denied(self, tmp_path):
        restricted = tmp_path / "Restricted"
        restricted.mkdir()
        restricted.chmod(0o000)
        try:
            entries = list_directory_entries(str(tmp_path))
            r = [e for e in entries if e.name == "Restricted/"][0]
            assert r.eml_count == -1
        finally:
            restricted.chmod(0o755)


class TestPickStartDirectory:
    def test_single_export_returns_it(self, tmp_path, monkeypatch):
        export = tmp_path / "user@pm.me" / "mail_20260223_210229"
        export.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        assert pick_start_directory() == str(export)

    def test_multiple_exports_returns_cwd(self, tmp_path, monkeypatch):
        (tmp_path / "user@pm.me" / "mail_20260101_000000").mkdir(parents=True)
        (tmp_path / "user@pm.me" / "mail_20260223_210229").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        assert pick_start_directory() == str(tmp_path)

    def test_no_exports_returns_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert pick_start_directory() == str(tmp_path)
