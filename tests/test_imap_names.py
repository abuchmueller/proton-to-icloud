"""Tests for proton_to_icloud.imap_names — modified UTF-7 and quoting."""

import pytest

from proton_to_icloud.imap_names import (
    decode_imap_utf7,
    encode_imap_utf7,
    mailbox_arg,
    quote_wire_name,
)


@pytest.mark.parametrize(
    ("display", "wire"),
    [
        ("INBOX", "INBOX"),
        ("A&B", "A&-B"),
        ("Büro", "B&APw-ro"),
        ("Entwürfe", "Entw&APw-rfe"),
        ("日本語", "&ZeVnLIqe-"),
        ("Proton-Import/Büro", "Proton-Import/B&APw-ro"),
    ],
)
def test_utf7_round_trip(display, wire):
    assert encode_imap_utf7(display) == wire
    assert decode_imap_utf7(wire) == display


def test_quote_only_when_needed():
    assert quote_wire_name("INBOX") == "INBOX"
    assert quote_wire_name("Proton-Import/Sent") == "Proton-Import/Sent"
    assert quote_wire_name("Sent Messages") == '"Sent Messages"'


def test_quote_escapes_quotes_and_backslashes():
    assert quote_wire_name('Q3 "final"') == '"Q3 \\"final\\""'
    assert quote_wire_name("a\\b") == '"a\\\\b"'


def test_mailbox_arg_is_ascii():
    arg = mailbox_arg("Büro Ablage")
    assert arg == '"B&APw-ro Ablage"'
    arg.encode("ascii")
