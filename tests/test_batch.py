"""Tests for proton_to_icloud.batch — pure-function tests only."""

from proton_to_icloud.batch import detect_highest_batch_index


class TestDetectHighestBatchIndex:
    def test_no_batches(self, tmp_path):
        assert detect_highest_batch_index(str(tmp_path)) == 0

    def test_nonexistent_dir(self, tmp_path):
        assert detect_highest_batch_index(str(tmp_path / "nope")) == 0

    def test_single_batch(self, tmp_path):
        (tmp_path / "batch_001").mkdir()
        assert detect_highest_batch_index(str(tmp_path)) == 1

    def test_multiple_batches(self, tmp_path):
        (tmp_path / "batch_001").mkdir()
        (tmp_path / "batch_005").mkdir()
        (tmp_path / "batch_012").mkdir()
        assert detect_highest_batch_index(str(tmp_path)) == 12

    def test_ignores_non_batch_dirs(self, tmp_path):
        (tmp_path / "batch_003").mkdir()
        (tmp_path / "other_folder").mkdir()
        (tmp_path / "batch_nope").mkdir()
        assert detect_highest_batch_index(str(tmp_path)) == 3

    def test_ignores_files_named_batch(self, tmp_path):
        (tmp_path / "batch_010").write_text("I am a file, not a dir")
        (tmp_path / "batch_002").mkdir()
        assert detect_highest_batch_index(str(tmp_path)) == 2
