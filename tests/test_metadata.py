"""Tests for proton_to_icloud.metadata — label loading, routing, and plan building."""

import json
import os

from proton_to_icloud.metadata import (
    build_routing_plan,
    load_labels,
    print_routing_summary,
    read_label_ids,
    resolve_target_folder,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

SAMPLE_LABELS_PAYLOAD = {
    "Version": 1,
    "Payload": [
        {"ID": "0", "Name": "Inbox", "Path": "Inbox", "Color": "#8080FF", "Type": 1},
        {"ID": "7", "Name": "Sent", "Path": "Sent", "Color": "#8080FF", "Type": 1},
        {"ID": "8", "Name": "Drafts", "Path": "Drafts", "Color": "#8080FF", "Type": 1},
        {"ID": "4", "Name": "Spam", "Path": "Spam", "Color": "#8080FF", "Type": 1},
        {"ID": "3", "Name": "Trash", "Path": "Trash", "Color": "#8080FF", "Type": 1},
        {"ID": "6", "Name": "Archive", "Path": "Archive", "Color": "#8080FF", "Type": 1},
        {"ID": "5", "Name": "All Mail", "Path": "All Mail", "Color": "#8080FF", "Type": 1},
        {"ID": "15", "Name": "All Mail", "Path": "All Mail", "Color": "#8080FF", "Type": 1},
        {"ID": "10", "Name": "Starred", "Path": "Starred", "Color": "#8080FF", "Type": 1},
    ],
}

LABELS_MAP = {item["ID"]: item["Name"] for item in SAMPLE_LABELS_PAYLOAD["Payload"]}


def _write_labels(path, payload=None):
    with open(path, "w") as f:
        json.dump(payload or SAMPLE_LABELS_PAYLOAD, f)


def _write_metadata(path, label_ids):
    with open(path, "w") as f:
        json.dump({"Version": 1, "Payload": {"LabelIDs": label_ids}}, f)


# ── TestLoadLabels ───────────────────────────────────────────────────────────


class TestLoadLabels:
    def test_found_in_source_dir(self, tmp_path):
        _write_labels(tmp_path / "labels.json")
        result = load_labels(str(tmp_path))
        assert result is not None
        assert result["0"] == "Inbox"
        assert result["7"] == "Sent"

    def test_found_in_json_subdir(self, tmp_path):
        json_dir = tmp_path / "json"
        json_dir.mkdir()
        _write_labels(json_dir / "labels.json")
        result = load_labels(str(tmp_path))
        assert result is not None
        assert result["6"] == "Archive"

    def test_found_in_parent(self, tmp_path):
        child = tmp_path / "sub"
        child.mkdir()
        _write_labels(tmp_path / "labels.json")
        result = load_labels(str(child))
        assert result is not None
        assert result["0"] == "Inbox"

    def test_not_found(self, tmp_path):
        result = load_labels(str(tmp_path))
        assert result is None

    def test_malformed_json(self, tmp_path):
        (tmp_path / "labels.json").write_text("not json")
        result = load_labels(str(tmp_path))
        assert result is None

    def test_missing_payload(self, tmp_path):
        (tmp_path / "labels.json").write_text(json.dumps({"Version": 1}))
        # Missing Payload key → empty dict (Payload defaults to [])
        result = load_labels(str(tmp_path))
        assert result == {}


# ── TestReadLabelIds ─────────────────────────────────────────────────────────


class TestReadLabelIds:
    def test_sibling_metadata(self, tmp_path):
        (tmp_path / "msg.eml").write_text("fake")
        _write_metadata(tmp_path / "msg.metadata.json", ["0", "5", "15"])
        result = read_label_ids(str(tmp_path / "msg.eml"))
        assert result == ["0", "5", "15"]

    def test_split_layout(self, tmp_path):
        eml_dir = tmp_path / "eml"
        json_dir = tmp_path / "json"
        eml_dir.mkdir()
        json_dir.mkdir()
        (eml_dir / "msg.eml").write_text("fake")
        _write_metadata(json_dir / "msg.metadata.json", ["7", "2"])
        result = read_label_ids(str(eml_dir / "msg.eml"))
        assert result == ["7", "2"]

    def test_missing_metadata_file(self, tmp_path):
        (tmp_path / "msg.eml").write_text("fake")
        result = read_label_ids(str(tmp_path / "msg.eml"))
        assert result is None

    def test_malformed_metadata(self, tmp_path):
        (tmp_path / "msg.eml").write_text("fake")
        (tmp_path / "msg.metadata.json").write_text("not json")
        result = read_label_ids(str(tmp_path / "msg.eml"))
        assert result is None

    def test_missing_payload_key(self, tmp_path):
        (tmp_path / "msg.eml").write_text("fake")
        (tmp_path / "msg.metadata.json").write_text(json.dumps({"Version": 1}))
        result = read_label_ids(str(tmp_path / "msg.eml"))
        assert result is None


# ── TestResolveTargetFolder ──────────────────────────────────────────────────


class TestResolveTargetFolder:
    def test_inbox_prefixed(self):
        result = resolve_target_folder(
            ["0", "5", "15"], LABELS_MAP, direct=False, base_mailbox="Proton-Import"
        )
        assert result == "Proton-Import/Inbox"

    def test_inbox_direct(self):
        result = resolve_target_folder(
            ["0", "5", "15"], LABELS_MAP, direct=True, base_mailbox="Proton-Import"
        )
        assert result == "INBOX"

    def test_sent_prefixed(self):
        result = resolve_target_folder(
            ["7", "2", "5"], LABELS_MAP, direct=False, base_mailbox="Proton-Import"
        )
        assert result == "Proton-Import/Sent"

    def test_sent_direct(self):
        result = resolve_target_folder(
            ["7", "2", "5"], LABELS_MAP, direct=True, base_mailbox="Proton-Import"
        )
        assert result == "Sent Messages"

    def test_archive_prefixed(self):
        result = resolve_target_folder(
            ["6", "5", "15"], LABELS_MAP, direct=False, base_mailbox="Proton-Import"
        )
        assert result == "Proton-Import/Archive"

    def test_archive_direct(self):
        result = resolve_target_folder(
            ["6", "5", "15"], LABELS_MAP, direct=True, base_mailbox="Proton-Import"
        )
        assert result == "Archive"

    def test_priority_inbox_over_archive(self):
        result = resolve_target_folder(["6", "0", "5"], LABELS_MAP, direct=False, base_mailbox="X")
        assert result == "X/Inbox"

    def test_priority_sent_over_trash(self):
        result = resolve_target_folder(["3", "7", "5"], LABELS_MAP, direct=False, base_mailbox="X")
        assert result == "X/Sent"

    def test_unknown_label_fallback(self):
        result = resolve_target_folder(
            ["999"], LABELS_MAP, direct=False, base_mailbox="Proton-Import"
        )
        assert result == "Proton-Import"

    def test_only_meta_labels_fallback(self):
        result = resolve_target_folder(
            ["5", "15", "10"], LABELS_MAP, direct=False, base_mailbox="Proton-Import"
        )
        assert result == "Proton-Import"

    def test_none_label_ids_fallback(self):
        result = resolve_target_folder(None, LABELS_MAP, direct=False, base_mailbox="Proton-Import")
        assert result == "Proton-Import"

    def test_empty_label_ids_fallback(self):
        result = resolve_target_folder([], LABELS_MAP, direct=False, base_mailbox="Proton-Import")
        assert result == "Proton-Import"

    def test_none_labels_map_fallback(self):
        result = resolve_target_folder(["0", "5"], None, direct=False, base_mailbox="Proton-Import")
        assert result == "Proton-Import"

    def test_spam_direct(self):
        result = resolve_target_folder(
            ["4", "5"], LABELS_MAP, direct=True, base_mailbox="Proton-Import"
        )
        assert result == "Junk"

    def test_trash_direct(self):
        result = resolve_target_folder(
            ["3", "5"], LABELS_MAP, direct=True, base_mailbox="Proton-Import"
        )
        assert result == "Deleted Messages"

    def test_drafts_prefixed(self):
        result = resolve_target_folder(["8", "1", "5"], LABELS_MAP, direct=False, base_mailbox="X")
        assert result == "X/Drafts"


# ── TestBuildRoutingPlan ─────────────────────────────────────────────────────


class TestBuildRoutingPlan:
    def test_groups_by_folder(self, tmp_path):
        _write_labels(tmp_path / "labels.json")
        for name, labels in [("a", ["0", "5"]), ("b", ["6", "5"]), ("c", ["0", "5"])]:
            (tmp_path / f"{name}.eml").write_text("fake")
            _write_metadata(tmp_path / f"{name}.metadata.json", labels)

        eml_files = sorted(str(tmp_path / f"{n}.eml") for n in ["a", "b", "c"])
        routing = build_routing_plan(
            eml_files, str(tmp_path), direct=False, base_mailbox="Proton-Import"
        )
        assert len(routing["Proton-Import/Inbox"]) == 2
        assert len(routing["Proton-Import/Archive"]) == 1

    def test_missing_metadata_uses_fallback(self, tmp_path):
        _write_labels(tmp_path / "labels.json")
        (tmp_path / "no_meta.eml").write_text("fake")

        routing = build_routing_plan(
            [str(tmp_path / "no_meta.eml")],
            str(tmp_path),
            direct=False,
            base_mailbox="Proton-Import",
        )
        assert routing == {"Proton-Import": [str(tmp_path / "no_meta.eml")]}

    def test_missing_labels_json_all_fallback(self, tmp_path):
        (tmp_path / "a.eml").write_text("fake")
        (tmp_path / "b.eml").write_text("fake")

        eml_files = sorted(str(tmp_path / f"{n}.eml") for n in ["a", "b"])
        routing = build_routing_plan(
            eml_files, str(tmp_path), direct=False, base_mailbox="Proton-Import"
        )
        assert list(routing.keys()) == ["Proton-Import"]
        assert len(routing["Proton-Import"]) == 2

    def test_preserves_sort_order(self, tmp_path):
        _write_labels(tmp_path / "labels.json")
        for name in ["z", "a", "m"]:
            (tmp_path / f"{name}.eml").write_text("fake")
            _write_metadata(tmp_path / f"{name}.metadata.json", ["0", "5"])

        eml_files = sorted(str(tmp_path / f"{n}.eml") for n in ["z", "a", "m"])
        routing = build_routing_plan(eml_files, str(tmp_path), direct=False, base_mailbox="X")
        basenames = [os.path.basename(p) for p in routing["X/Inbox"]]
        assert basenames == ["a.eml", "m.eml", "z.eml"]

    def test_direct_mode(self, tmp_path):
        _write_labels(tmp_path / "labels.json")
        (tmp_path / "msg.eml").write_text("fake")
        _write_metadata(tmp_path / "msg.metadata.json", ["7", "2", "5"])

        routing = build_routing_plan(
            [str(tmp_path / "msg.eml")],
            str(tmp_path),
            direct=True,
            base_mailbox="Proton-Import",
        )
        assert "Sent Messages" in routing


# ── TestPrintRoutingSummary ──────────────────────────────────────────────────


class TestPrintRoutingSummary:
    def test_prints_without_error(self, capsys):
        routing = {"INBOX": ["a", "b", "c"], "Archive": ["d"]}
        print_routing_summary(routing)
        output = capsys.readouterr().out
        assert "INBOX" in output
        assert "Archive" in output
        assert "3" in output
