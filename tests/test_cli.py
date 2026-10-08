"""Tests for proton_to_icloud.cli — argument parsing."""

import argparse
from datetime import datetime, timedelta, timezone

import pytest

from proton_to_icloud.cli import _build_parser, parse_since


class TestParseSince:
    def test_date_only_is_local_midnight(self):
        result = parse_since("2026-09-01")
        assert result.tzinfo is not None
        assert result == datetime(2026, 9, 1).astimezone()

    def test_explicit_offset_preserved(self):
        result = parse_since("2026-09-01T14:30+01:00")
        assert result.utcoffset() == timedelta(hours=1)
        assert result.astimezone(timezone.utc).hour == 13

    def test_z_suffix_is_utc(self):
        assert parse_since("2026-09-01T00:00:00Z").utcoffset() == timedelta(0)

    def test_space_separator(self):
        assert parse_since("2026-09-01 14:30") == datetime(2026, 9, 1, 14, 30).astimezone()

    def test_invalid_raises_argument_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_since("last tuesday")


class TestUploadSinceArgument:
    def test_default_is_none(self):
        args = _build_parser().parse_args(["upload", "-s", "x", "-e", "a@b.c"])
        assert args.since is None

    def test_parsed_into_aware_datetime(self):
        args = _build_parser().parse_args(
            ["upload", "-s", "x", "-e", "a@b.c", "--since", "2026-09-01T00:00Z"]
        )
        assert args.since == datetime(2026, 9, 1, tzinfo=timezone.utc)

    def test_invalid_value_exits(self):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["upload", "-s", "x", "-e", "a@b.c", "--since", "nope"])
