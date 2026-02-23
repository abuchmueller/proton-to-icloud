"""Tests for proton_to_icloud.progress."""

from proton_to_icloud.progress import format_duration


class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(0) == "0s"
        assert format_duration(1) == "1s"
        assert format_duration(59) == "59s"

    def test_minutes(self):
        assert format_duration(60) == "1m 00s"
        assert format_duration(90) == "1m 30s"
        assert format_duration(3599) == "59m 59s"

    def test_hours(self):
        assert format_duration(3600) == "1h 00m 00s"
        assert format_duration(3661) == "1h 01m 01s"
        assert format_duration(86400) == "24h 00m 00s"

    def test_float_input(self):
        assert format_duration(90.7) == "1m 30s"
