"""Tests for models.py helpers, the cache, and SOURCES.md sample consistency."""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import samples  # noqa: E402
from fakes import TempProfile  # noqa: E402

import models  # noqa: E402
from cache import UsageCache, default_cache_path  # noqa: E402
from models import UsageRow, build_credit_rows, format_reset_countdown  # noqa: E402

SOURCES = Path(__file__).resolve().parent.parent / "SOURCES.md"


def row(**values) -> UsageRow:
    base = dict(provider_id="abcd1234", provider_label="TEST", model_id="m", model_label="M")
    base.update(values)
    return UsageRow(**base)


class ModelsTests(unittest.TestCase):
    def test_no_hardcoded_model_list(self) -> None:
        self.assertFalse(hasattr(models, "T3_PROVIDER_MODELS"))

    def test_row_round_trip_with_kind(self) -> None:
        item = row(provider_kind="codex", percent_used=12.0, credit_balance_usd=1.5)
        self.assertEqual(UsageRow.from_dict(item.to_dict()), item)
        self.assertEqual(UsageRow.from_dict({**item.to_dict(), "unknown": 1}), item)
        self.assertEqual(item.star_key, "abcd1234:m")

    def test_reason_shown_for_count_rows(self) -> None:
        self.assertEqual(row(percent_used=None, reason="3 requests today").display_reset_Or_reason(), "3 requests today")

    def test_format_reset_countdown(self) -> None:
        now = datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(format_reset_countdown("2030-01-01T02:30:00Z", now=now), "resets in 2h 30m")
        self.assertEqual(format_reset_countdown(None, now=now), "")

    def test_build_credit_rows_generic(self) -> None:
        rows = [
            row(provider_id="a", model_id="x", percent_used=10.0),
            row(provider_id="b", provider_label="B", model_id="bal", model_label="Balance", credit_balance_usd=5.0),
            row(provider_id="b", model_id="other", credit_balance_usd=1.0),
            row(provider_id="c", model_label="Budget", credit_spent_usd=2.0, credit_cap_usd=8.0),
        ]
        credit = build_credit_rows(rows)
        self.assertEqual([c.provider_id for c in credit], ["b", "c"])
        self.assertEqual(credit[0].title, "Balance")
        self.assertEqual(credit[0].balance_usd, 5.0)
        self.assertEqual(credit[1].percent_used, 25.0)


class CacheTests(unittest.TestCase):
    def test_default_path(self) -> None:
        with TempProfile() as profile:
            self.assertEqual(default_cache_path(), profile.config_root / "DesktopWidgets" / "UsageMonitor" / "cache.json")

    def test_keeps_last_good_and_marks_stale(self) -> None:
        with TempProfile() as profile:
            cache = UsageCache(profile.root / "cache.json")
            good = row(percent_used=22.0)
            first = cache.fetch_with_fallback("abcd1234", lambda: [good])
            self.assertFalse(first.is_stale)
            bad = row(model_id="error", status="error", reason="Could not reach example.com.")
            second = cache.fetch_with_fallback("abcd1234", lambda: [bad])
            self.assertTrue(second.is_stale)
            self.assertEqual(second.rows[0].percent_used, 22.0)
            self.assertEqual(second.rows[0].status, "stale")
            self.assertEqual(second.error, "Could not reach example.com.")

    def test_exception_message_not_stored(self) -> None:
        with TempProfile() as profile:
            cache = UsageCache(profile.root / "cache.json")

            def boom():
                raise RuntimeError("secret-looking-detail")

            snap = cache.fetch_with_fallback("abcd1234", boom)
            self.assertNotIn("secret-looking-detail", snap.error)

    def test_min_poll_interval(self) -> None:
        with TempProfile() as profile:
            cache = UsageCache(profile.root / "cache.json")
            cache.fetch_with_fallback("abcd1234", lambda: [row(percent_used=1.0)])
            snap = cache.fetch_with_fallback("abcd1234", lambda: [row(percent_used=2.0)], min_poll_interval_seconds=600)
            self.assertEqual(snap.rows[0].percent_used, 1.0)

    def test_persists_rows_only_and_remove_prune(self) -> None:
        with TempProfile() as profile:
            path = profile.root / "cache.json"
            cache = UsageCache(path)
            cache.put("one00001", [row(provider_id="one00001")])
            cache.put("two00002", [row(provider_id="two00002")])
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(data), {"one00001", "two00002"})
            self.assertEqual(set(data["one00001"]), {"timestamp", "rows"})
            reloaded = UsageCache(path)
            self.assertIsNotNone(reloaded.get("one00001"))
            reloaded.remove("one00001")
            self.assertIsNone(UsageCache(path).get("one00001"))
            reloaded.prune(["nothing"])
            self.assertIsNone(UsageCache(path).get("two00002"))

    def test_corrupt_cache_file(self) -> None:
        with TempProfile() as profile:
            path = profile.root / "cache.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertIsNone(UsageCache(path).get("abcd1234"))


class SourcesSamplesTests(unittest.TestCase):
    """The JSON samples in SOURCES.md are the ones the parser tests use."""

    def test_samples_match(self) -> None:
        text = SOURCES.read_text(encoding="utf-8")
        found = {}
        for name, block in re.findall(r"<!-- sample:(\w+) -->\s*```json\n(.*?)```", text, flags=re.S):
            found[name] = json.loads(block)
        self.assertEqual(set(found), set(samples.ALL))
        for name, sample in samples.ALL.items():
            self.assertEqual(found[name], sample, name)


if __name__ == "__main__":
    unittest.main()
