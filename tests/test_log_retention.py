"""Tests for JSONL log retention.

Sky Radar writes one line per aircraft per poll, so an instance left running
accumulates indefinitely without pruning.
"""
import configparser
from datetime import datetime, timedelta, timezone

import pytest

import app as skyradar
from app import get_log_retention_days, prune_logs, DEFAULT_LOG_RETENTION_DAYS


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Point the module's LOG_DIR at a temporary directory."""
    d = tmp_path / "logs"
    d.mkdir()
    monkeypatch.setattr(skyradar, "LOG_DIR", d)
    return d


def _make_log(log_dir, days_ago: int):
    date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    path = log_dir / f"skyradar-{date}.jsonl"
    path.write_text('{"icao24": "abc123"}\n', encoding="utf-8")
    return path


def _cfg(raw: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read_string(raw)
    return cfg


class TestRetentionSetting:
    def test_missing_section_uses_default(self):
        assert get_log_retention_days(_cfg("[location]\nname = x\n")) == DEFAULT_LOG_RETENTION_DAYS

    def test_explicit_value_used(self):
        assert get_log_retention_days(_cfg("[logging]\nretention_days = 7\n")) == 7

    def test_zero_is_honoured_not_replaced_by_default(self):
        assert get_log_retention_days(_cfg("[logging]\nretention_days = 0\n")) == 0

    def test_invalid_value_falls_back_to_default(self):
        cfg = _cfg("[logging]\nretention_days = soon\n")
        assert get_log_retention_days(cfg) == DEFAULT_LOG_RETENTION_DAYS


class TestPruning:
    def test_old_log_deleted(self, log_dir):
        old = _make_log(log_dir, days_ago=40)
        prune_logs(30)
        assert not old.exists()

    def test_recent_log_kept(self, log_dir):
        recent = _make_log(log_dir, days_ago=3)
        prune_logs(30)
        assert recent.exists()

    def test_boundary_log_kept(self, log_dir):
        """A file exactly at the retention age is inside the window, not outside."""
        boundary = _make_log(log_dir, days_ago=30)
        prune_logs(30)
        assert boundary.exists()

    def test_zero_retention_disables_pruning(self, log_dir):
        ancient = _make_log(log_dir, days_ago=9999)
        prune_logs(0)
        assert ancient.exists()

    def test_negative_retention_disables_pruning(self, log_dir):
        ancient = _make_log(log_dir, days_ago=9999)
        prune_logs(-1)
        assert ancient.exists()

    def test_unrelated_files_never_touched(self, log_dir):
        """Pruning reads the date from the filename, so a foreign file is safe
        however old it looks."""
        keep = log_dir / "notes.txt"
        keep.write_text("important", encoding="utf-8")
        odd = log_dir / "skyradar-not-a-date.jsonl"
        odd.write_text("{}", encoding="utf-8")

        prune_logs(1)

        assert keep.exists()
        assert odd.exists()

    def test_mixed_ages_only_old_removed(self, log_dir):
        old_a = _make_log(log_dir, days_ago=100)
        old_b = _make_log(log_dir, days_ago=31)
        new = _make_log(log_dir, days_ago=1)

        prune_logs(30)

        assert not old_a.exists()
        assert not old_b.exists()
        assert new.exists()


class TestAppendTriggersPrune:
    def test_append_writes_and_prunes(self, log_dir):
        old = _make_log(log_dir, days_ago=90)
        skyradar.append_to_log([{"icao24": "abc123"}], retention_days=30)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        written = log_dir / f"skyradar-{today}.jsonl"

        assert written.exists()
        assert "abc123" in written.read_text(encoding="utf-8")
        assert not old.exists()
