"""Tests for the per-provider "Rows to show" setting and generic header bars."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakes import instance  # noqa: E402

from models import build_provider_summaries, hidden_row_ids, uses_inline_metrics  # noqa: E402
from providers.base import error_row, make_row  # noqa: E402
from providers.registry import visible_rows  # noqa: E402


def rows_for(inst, *ids: str):
    return [make_row(inst, model_id=row_id, model_label=row_id.title(), percent_used=10.0)
            for row_id in ids]


class HiddenRowsTests(unittest.TestCase):
    def test_parse_setting(self) -> None:
        self.assertEqual(hidden_row_ids({"hidden_rows": " a, b ,,c "}), ["a", "b", "c"])
        self.assertEqual(hidden_row_ids({}), [])
        self.assertEqual(hidden_row_ids(None), [])

    def test_hidden_rows_are_dropped_for_any_provider(self) -> None:
        for kind in ("nanogpt", "custom_json", "antigravity"):
            inst = instance(kind, settings={"hidden_rows": "b"})
            kept = visible_rows(inst, rows_for(inst, "a", "b", "c"))
            self.assertEqual([r.model_id for r in kept], ["a", "c"], kind)

    def test_error_rows_always_stay(self) -> None:
        inst = instance("nanogpt", settings={"hidden_rows": "error"})
        row = error_row(inst, "Sign-in expired")
        self.assertEqual(visible_rows(inst, [row]), [row])

    def test_nothing_hidden_returns_rows_unchanged(self) -> None:
        inst = instance("nanogpt")
        rows = rows_for(inst, "a")
        self.assertIs(visible_rows(inst, rows), rows)


class GenericHeaderTests(unittest.TestCase):
    def test_any_provider_with_window_ids_gets_header_bars(self) -> None:
        inst = instance("some_new_kind", "New")
        rows = rows_for(inst, "five_hour", "seven_day", "window:extra", "model-x")
        summary = build_provider_summaries(rows)[0]
        self.assertEqual((summary.primary_id, summary.secondary_id), ("five_hour", "seven_day"))
        self.assertFalse(uses_inline_metrics(rows[2]))  # window:* gets a full-width bar

    def test_claude_and_codex_keep_their_ids(self) -> None:
        claude = instance("claude_code")
        summary = build_provider_summaries(rows_for(claude, "session", "weekly_all"))[0]
        self.assertEqual((summary.primary_id, summary.secondary_id), ("session", "weekly_all"))
        codex = instance("codex")
        summary = build_provider_summaries(rows_for(codex, "primary", "secondary"))[0]
        self.assertEqual((summary.primary_id, summary.secondary_id), ("primary", "secondary"))


if __name__ == "__main__":
    unittest.main()
