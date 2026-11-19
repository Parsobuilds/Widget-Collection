"""Shared windows and independent quotas must never be combined."""
import unittest
from models import UsageRow, build_provider_summaries, is_header_window_row, model_display_row, uses_inline_metrics


def row(kind, model, percent):
    return UsageRow("instance", "PROVIDER", model, model, percent_used=percent, provider_kind=kind)


class ProviderHeaderTests(unittest.TestCase):
    def test_codex_shared_windows_keep_code_review_separate(self):
        rows = [row("codex", "primary", 9), row("codex", "secondary", 58), row("codex", "code_review", 12)]
        summary = build_provider_summaries(rows)[0]
        self.assertEqual((summary.primary_percent, summary.secondary_percent), (9, 58))
        self.assertEqual([model_display_row(r, summary).percent_used for r in rows], [None, None, 12])

    def test_claude_scoped_weekly_limit_stays_distinct_even_if_equal(self):
        rows = [row("claude_code", "session", 100), row("claude_code", "weekly_all", 78),
                row("claude_code", "weekly_scoped_fable", 78)]
        summary = build_provider_summaries(rows)[0]
        self.assertEqual(summary.secondary_percent, 78)
        self.assertEqual(model_display_row(rows[2], summary).percent_used, 78)

    def test_independent_quotas_not_merged(self):
        for kind in ("antigravity", "google_ai_studio_t3", "nanogpt_sub"):
            rows = [row(kind, name, value) for name, value in (("Argon",12),("Hydrogen",37),("Flash",0))]
            summary = build_provider_summaries(rows)[0]
            self.assertIsNone(summary.primary_percent)
            self.assertEqual([model_display_row(r, summary).percent_used for r in rows], [12,37,0])
            self.assertEqual(uses_inline_metrics(rows[0]), kind != "nanogpt_sub")


class HeaderNameTests(unittest.TestCase):
    def test_window_names_sit_under_their_bars_and_rows_are_not_repeated(self):
        rows = [row("claude_code", "session", 15), row("claude_code", "weekly_all", 80), row("claude_code", "extra", 5)]
        rows[0].model_label, rows[1].model_label = "Current session", "All models"
        summary = build_provider_summaries(rows)[0]
        self.assertEqual((summary.primary_name, summary.secondary_name), ("Current session", "All models"))
        self.assertEqual([is_header_window_row(r, summary) for r in rows], [True, True, False])

    def test_row_is_kept_when_the_header_has_no_bar_for_it(self):
        rows = [row("claude_code", "session", None), row("claude_code", "weekly_all", 80)]
        summary = build_provider_summaries(rows)[0]
        self.assertEqual([is_header_window_row(r, summary) for r in rows], [False, True])
