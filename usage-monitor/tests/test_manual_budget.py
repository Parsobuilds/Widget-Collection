"""Tests for the manual budget kind and the Modal preset (no network)."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakes import KeyringMixin, instance  # noqa: E402

from models import build_credit_rows  # noqa: E402
from providers import registry  # noqa: E402
from providers.base import ProviderError  # noqa: E402
from providers.manual_budget import ManualBudgetKind, ModalBudgetKind, build_rows, next_reset  # noqa: E402

NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)


class NextResetTests(unittest.TestCase):
    def test_later_this_month(self) -> None:
        self.assertEqual(next_reset(15, NOW), datetime(2030, 1, 15, tzinfo=timezone.utc))

    def test_next_month_and_year(self) -> None:
        self.assertEqual(next_reset(1, NOW), datetime(2030, 2, 1, tzinfo=timezone.utc))
        self.assertEqual(next_reset(5, datetime(2030, 12, 20, tzinfo=timezone.utc)),
                         datetime(2031, 1, 5, tzinfo=timezone.utc))

    def test_short_month_uses_last_day(self) -> None:
        self.assertEqual(next_reset(31, datetime(2030, 2, 3, tzinfo=timezone.utc)),
                         datetime(2030, 2, 28, tzinfo=timezone.utc))


class BuildRowsTests(unittest.TestCase):
    def test_monthly_credit(self) -> None:
        rows = build_rows(instance("manual_budget", "Cloud", {"monthly_credit": "30", "spent": "7.5",
                                                              "currency": "USD", "reset_day": "1"}), now=NOW)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.model_label, "Monthly budget")
        self.assertEqual(row.percent_used, 25.0)
        self.assertEqual(row.credit_balance_usd, 22.5)
        self.assertEqual(row.credit_spent_usd, 7.5)
        self.assertEqual(row.credit_cap_usd, 30.0)
        self.assertEqual(row.reason, "$22.50 left · $7.50 spent of $30.00")
        self.assertEqual(row.resets_at, "2030-02-01T00:00:00Z")
        self.assertEqual(row.provider_kind, "manual_budget")

    def test_own_cap_wins(self) -> None:
        row = build_rows(instance("manual_budget", settings={"monthly_credit": "30", "cap": "10", "spent": "5"}), now=NOW)[0]
        self.assertEqual(row.percent_used, 50.0)
        self.assertEqual(row.credit_cap_usd, 10.0)

    def test_overspent_clamps(self) -> None:
        row = build_rows(instance("manual_budget", settings={"monthly_credit": "10", "spent": "12"}), now=NOW)[0]
        self.assertEqual(row.percent_used, 100)
        self.assertEqual(row.credit_balance_usd, 0.0)

    def test_other_currency_text(self) -> None:
        row = build_rows(instance("manual_budget", settings={"monthly_credit": "20", "spent": "5",
                                                            "currency": "EUR"}), now=NOW)[0]
        self.assertEqual(row.reason, "€15.00 left · €5.00 spent of €20.00")
        row = build_rows(instance("manual_budget", settings={"monthly_credit": "20", "spent": "5",
                                                            "currency": "CHF"}), now=NOW)[0]
        self.assertIn("15.00 CHF left", row.reason)

    def test_spend_only(self) -> None:
        row = build_rows(instance("manual_budget", settings={"spent": "4"}), now=NOW)[0]
        self.assertIsNone(row.percent_used)
        self.assertEqual(row.reason, "$4.00 spent this month")

    def test_bad_values(self) -> None:
        for settings in ({"spent": "-1"}, {"spent": "x"}, {"spent": "1", "reset_day": "0"},
                         {"spent": "1", "reset_day": "32"}, {"spent": "1", "reset_day": "2.5"}):
            with self.assertRaises(ProviderError) as ctx:
                build_rows(instance("manual_budget", settings=settings), now=NOW)
            self.assertEqual(ctx.exception.code, ProviderError.CONFIG)

    def test_credit_row_uses_reason(self) -> None:
        rows = build_rows(instance("manual_budget", "Cloud", {"monthly_credit": "30", "spent": "7.5"}), now=NOW)
        credit = build_credit_rows(rows)
        self.assertEqual(len(credit), 1)
        self.assertEqual(credit[0].title, "Monthly budget")
        self.assertEqual(credit[0].detail_text, "$22.50 left · $7.50 spent of $30.00")
        self.assertEqual(credit[0].percent_used, 25.0)


class KindTests(KeyringMixin, unittest.TestCase):
    def test_no_network_used(self) -> None:
        with mock.patch("providers.base.open_url", side_effect=AssertionError("network used")):
            result = registry.test_instance(instance("manual_budget", settings={"monthly_credit": "5", "spent": "1"}))
        self.assertTrue(result.ok, result.message)

    def test_metadata(self) -> None:
        kind = ManualBudgetKind()
        self.assertEqual(kind.stability, "manual")
        self.assertEqual(kind.category, "budget")
        self.assertIsNone(kind.secret_field())

    def test_modal_preset(self) -> None:
        kind = ModalBudgetKind()
        self.assertEqual(kind.kind, "modal_budget")
        self.assertEqual(kind.display_name, "Modal (manual budget)")
        self.assertEqual(kind.stability, "manual")
        self.assertEqual(kind.docs_url, "https://modal.com/docs/guide/billing")
        self.assertIn("Modal dashboard", kind.description)
        rows = registry.fetch_instance(instance("modal_budget", "Modal", {"monthly_credit": "30", "spent": "6"}))
        self.assertEqual(rows[0].percent_used, 20.0)
        self.assertEqual(rows[0].provider_kind, "modal_budget")


if __name__ == "__main__":
    unittest.main()
