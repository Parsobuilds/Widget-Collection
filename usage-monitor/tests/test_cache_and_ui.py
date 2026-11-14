"""Tests for UsageCache and the UsageMonitorWidget tabs/stars.

The widget tests live in test_usage_monitor_ui.py.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from cache import UsageCache  # noqa: E402
from models import UsageRow  # noqa: E402

class TestCache(unittest.TestCase):
    def test_cache_preserves_last_good_reading_on_failure(self) -> None:
        """When a reading fails, UsageCache keeps the last good one and marks it stale."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = UsageCache(Path(tmp) / "cache.json")
            good_row = UsageRow(
                provider_id="abcd1234",
                provider_label="CLAUDE",
                model_id="session",
                model_label="Current session",
                percent_used=22.0,
                resets_at="2030-01-01T05:00:00Z",
                status="ok",
                provider_kind="claude_code",
            )
            snap1 = cache.fetch_with_fallback("abcd1234", lambda: [good_row])
            self.assertFalse(snap1.is_stale)
            self.assertEqual(snap1.rows[0].percent_used, 22.0)

            err_row = UsageRow(
                provider_id="abcd1234",
                provider_label="CLAUDE",
                model_id="error",
                model_label="Could not read usage",
                status="error",
                reason="api.anthropic.com is limiting how often usage can be checked (HTTP 429).",
                provider_kind="claude_code",
            )
            snap2 = cache.fetch_with_fallback("abcd1234", lambda: [err_row])
            self.assertTrue(snap2.is_stale)
            self.assertEqual(snap2.rows[0].percent_used, 22.0)
            self.assertEqual(snap2.rows[0].status, "stale")


if __name__ == "__main__":
    unittest.main()
