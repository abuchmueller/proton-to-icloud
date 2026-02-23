"""Tests for proton_to_icloud.upload — pure-function tests only (no IMAP)."""

import os

from proton_to_icloud.upload import collect_eml_files, parse_date_from_eml


class TestCollectEmlFiles:
    def test_finds_eml_files(self, tmp_path):
        (tmp_path / "a.eml").write_text("fake")
        (tmp_path / "b.eml").write_text("fake")
        (tmp_path / "c.json").write_text("fake")

        result = collect_eml_files(str(tmp_path))
        assert len(result) == 2
        assert all(f.endswith(".eml") for f in result)

    def test_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "a.eml").write_text("fake")
        (sub / "b.eml").write_text("fake")

        result = collect_eml_files(str(tmp_path))
        assert len(result) == 2

    def test_excludes_directory(self, tmp_path):
        output = tmp_path / "batches"
        output.mkdir()
        (tmp_path / "a.eml").write_text("fake")
        (output / "b.eml").write_text("fake")

        result = collect_eml_files(str(tmp_path), exclude_dir=str(output))
        assert len(result) == 1
        assert result[0].endswith("a.eml")

    def test_empty_directory(self, tmp_path):
        result = collect_eml_files(str(tmp_path))
        assert result == []

    def test_sorted(self, tmp_path):
        (tmp_path / "z.eml").write_text("fake")
        (tmp_path / "a.eml").write_text("fake")
        (tmp_path / "m.eml").write_text("fake")

        result = collect_eml_files(str(tmp_path))
        basenames = [os.path.basename(f) for f in result]
        assert basenames == ["a.eml", "m.eml", "z.eml"]

    def test_case_insensitive_extension(self, tmp_path):
        (tmp_path / "a.EML").write_text("fake")
        (tmp_path / "b.Eml").write_text("fake")

        result = collect_eml_files(str(tmp_path))
        assert len(result) == 2


class TestParseDateFromEml:
    def test_valid_date(self):
        raw = b"From: test@test.com\nDate: Mon, 15 Jan 2024 10:30:45 +0000\nSubject: Test\n\nBody"
        result = parse_date_from_eml(raw)
        assert result is not None
        assert "2024" in result

    def test_no_date_header(self):
        raw = b"From: test@test.com\nSubject: No date\n\nBody"
        result = parse_date_from_eml(raw)
        assert result is None

    def test_malformed_date(self):
        raw = b"Date: not-a-real-date\n\nBody"
        result = parse_date_from_eml(raw)
        assert result is None

    def test_empty_bytes(self):
        result = parse_date_from_eml(b"")
        assert result is None
