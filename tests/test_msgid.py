"""Tests for proton_to_icloud.msgid — Message-ID matching, no IMAP."""

from proton_to_icloud.msgid import (
    classify_eml_files,
    collect_existing_message_ids,
    extract_message_id_from_eml,
    normalize_message_id,
    parse_imap_list_line,
    parse_message_ids_from_fetch,
)


class TestNormalizeMessageId:
    def test_strips_brackets_and_lowercases(self):
        assert normalize_message_id("<ABC@Example.COM>") == "abc@example.com"

    def test_already_bare(self):
        assert normalize_message_id("abc@example.com") == "abc@example.com"

    def test_collapses_whitespace(self):
        assert normalize_message_id("  <id@host>  ") == "id@host"

    def test_none_and_empty(self):
        assert normalize_message_id(None) is None
        assert normalize_message_id("") is None
        assert normalize_message_id("   ") is None
        assert normalize_message_id("<>") is None


class TestExtractMessageIdFromEml:
    def test_standard_header(self):
        raw = b"From: a@b.com\nMessage-ID: <one@host>\nSubject: Hi\n\nBody"
        assert extract_message_id_from_eml(raw) == "one@host"

    def test_case_insensitive_header_name(self):
        raw = b"From: a@b.com\nMessage-Id: <two@host>\n\nBody"
        assert extract_message_id_from_eml(raw) == "two@host"

    def test_folded_header(self):
        raw = b"From: a@b.com\nMessage-ID:\n <folded@host>\n\nBody"
        assert extract_message_id_from_eml(raw) == "folded@host"

    def test_missing_header(self):
        raw = b"From: a@b.com\nSubject: Hi\n\nBody"
        assert extract_message_id_from_eml(raw) is None

    def test_header_fragment_from_imap_fetch(self):
        raw = b"Message-ID: <imap@host>\r\n\r\n"
        assert extract_message_id_from_eml(raw) == "imap@host"


class TestParseImapListLine:
    def test_inbox_unquoted(self):
        mb = parse_imap_list_line('(\\HasNoChildren) "/" INBOX')
        assert mb.wire == mb.display == "INBOX"
        assert "\\HasNoChildren" in mb.flags

    def test_quoted_with_space(self):
        mb = parse_imap_list_line('(\\HasNoChildren \\Sent) "/" "Sent Messages"')
        assert mb.wire == "Sent Messages"
        assert "\\Sent" in mb.flags

    def test_nested_folder(self):
        mb = parse_imap_list_line('(\\HasNoChildren) "/" "Proton-Import/Inbox"')
        assert mb.wire == "Proton-Import/Inbox"

    def test_noselect(self):
        mb = parse_imap_list_line('(\\Noselect \\HasChildren) "/" "[Mail]"')
        assert mb.wire == "[Mail]"
        assert "\\Noselect" in mb.flags

    def test_non_ascii_keeps_wire_name_for_select(self):
        mb = parse_imap_list_line('(\\HasNoChildren) "/" "B&APw-ro"')
        assert mb.wire == "B&APw-ro"
        assert mb.display == "Büro"

    def test_garbage(self):
        assert parse_imap_list_line("not a list line") is None


class TestParseMessageIdsFromFetch:
    def test_tuple_payloads(self):
        fetched = [
            (b"1 (UID 12 BODY[HEADER.FIELDS (MESSAGE-ID)] {23}", b"Message-ID: <a@h>\r\n\r\n"),
            b")",
            (b"2 (UID 13 BODY[HEADER.FIELDS (MESSAGE-ID)] {23}", b"Message-ID: <B@H>\r\n\r\n"),
            b")",
        ]
        assert parse_message_ids_from_fetch(fetched) == {"a@h", "b@h"}

    def test_empty_and_none(self):
        assert parse_message_ids_from_fetch(None) == set()
        assert parse_message_ids_from_fetch([]) == set()


class TestClassifyEmlFiles:
    def _write(self, path, msgid: str | None):
        if msgid is None:
            path.write_bytes(b"From: a@b.com\nSubject: Hi\n\nBody")
        else:
            path.write_bytes(f"From: a@b.com\nMessage-ID: <{msgid}>\nSubject: Hi\n\nBody".encode())

    def test_skips_existing_and_intra_export_dupes(self, tmp_path):
        a = tmp_path / "a.eml"
        b = tmp_path / "b.eml"
        c = tmp_path / "c.eml"
        d = tmp_path / "d.eml"
        self._write(a, "already@host")
        self._write(b, "new@host")
        self._write(c, "new@host")  # duplicate of b
        self._write(d, None)

        upload, skip, missing = classify_eml_files(
            [str(a), str(b), str(c), str(d)],
            existing_ids={"already@host"},
        )
        assert skip == 2  # a (in iCloud) + c (dup of b)
        assert missing == 1
        assert upload == 2  # b and d

    def test_all_new(self, tmp_path):
        a = tmp_path / "a.eml"
        self._write(a, "only@host")
        upload, skip, missing = classify_eml_files([str(a)], existing_ids=set())
        assert (upload, skip, missing) == (1, 0, 0)


class _FakeConn:
    """Mimics imaplib: arguments are sent as ASCII."""

    def __init__(self, lines):
        self.lines = lines
        self.selected = []

    def list(self):
        return "OK", self.lines

    def select(self, name, readonly=False):
        name.encode("ascii")
        self.selected.append(name)
        return "OK", [b"0"]

    def uid(self, *args):
        return "OK", [b""]


class TestCollectExistingMessageIds:
    def test_non_ascii_and_special_use_folders(self, capsys):
        conn = _FakeConn(
            [
                b'(\\HasNoChildren) "/" INBOX',
                b'(\\HasNoChildren) "/" "B&APw-ro"',
                b'(\\HasNoChildren \\Trash) "/" "Papierkorb"',
                b'(\\Noselect) "/" "[Mail]"',
            ]
        )
        assert collect_existing_message_ids(conn) == set()
        assert conn.selected == ["INBOX", "B&APw-ro"]
        assert "Ignoring 'Papierkorb'" in capsys.readouterr().out
