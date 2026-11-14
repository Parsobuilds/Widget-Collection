"""Manual budget: numbers the user types in, no network at all.

Useful for any service without a usable billing API. The user enters a
monthly credit (or a cap of their own), how much is spent so far, the
currency, and the day of the month the budget resets.

Also provides the "Modal (manual budget)" preset: Modal documents billing
reports only through its Python SDK and CLI (and only on Team/Enterprise
plans), not through a plain HTTPS endpoint, so Modal users type their numbers
from the Modal dashboard.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from models import UsageRow, format_reset_countdown
from providers.base import (
    FieldSpec,
    ProviderError,
    ProviderKind,
    clamp_percent,
    make_row,
    money_text,
    setting_number,
)

if TYPE_CHECKING:
    from provider_config import ProviderInstance


def next_reset(day: int, now: datetime | None = None) -> datetime:
    """Next midnight (UTC) on ``day`` of the month; short months use their last day."""
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    ref = ref.astimezone(timezone.utc)

    def _on(year: int, month: int) -> datetime:
        last = calendar.monthrange(year, month)[1]
        return datetime(year, month, min(day, last), tzinfo=timezone.utc)

    candidate = _on(ref.year, ref.month)
    if candidate <= ref:
        year, month = (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)
        candidate = _on(year, month)
    return candidate


def build_rows(instance: "ProviderInstance", now: datetime | None = None) -> list[UsageRow]:
    """Turn the typed numbers into one credit row."""
    settings = instance.settings
    monthly = setting_number(settings, "monthly_credit", "Monthly credit", default=0.0) or 0.0
    cap = setting_number(settings, "cap", "Cap", default=0.0) or 0.0
    spent = setting_number(settings, "spent", "Spent so far", default=0.0) or 0.0
    day_value = setting_number(settings, "reset_day", "Reset day", default=1.0)
    currency = str(settings.get("currency") or "USD").strip() or "USD"

    if monthly < 0 or cap < 0 or spent < 0:
        raise ProviderError(ProviderError.CONFIG, "Budget numbers cannot be negative.")
    day = int(day_value or 1)
    if day != day_value or not 1 <= day <= 31:
        raise ProviderError(ProviderError.CONFIG, "Reset day must be a whole number from 1 to 31.")

    # Your own cap wins over the provider's monthly credit when both are set.
    allowance = cap if cap > 0 else monthly
    resets_at = next_reset(day, now).strftime("%Y-%m-%dT%H:%M:%SZ")
    reset_text = format_reset_countdown(resets_at, now=now)

    if allowance > 0:
        left = max(0.0, allowance - spent)
        percent = clamp_percent(spent / allowance * 100)
        reason = (
            f"{money_text(left, currency)} left · "
            f"{money_text(spent, currency)} spent of {money_text(allowance, currency)}"
        )
        values = {
            "percent_used": percent,
            "secondary_label": f"{money_text(left, currency)} left",
            "credit_balance_usd": round(left, 2),
            "credit_cap_usd": round(allowance, 2),
        }
    else:
        reason = f"{money_text(spent, currency)} spent this month"
        values = {"percent_used": None}

    return [
        make_row(
            instance,
            model_id="budget",
            model_label="Monthly budget",
            resets_at=resets_at,
            reset_text=reset_text,
            reason=reason,
            credit_spent_usd=round(spent, 2),
            **values,
        )
    ]


class ManualBudgetKind(ProviderKind):
    """A monthly budget the user keeps up to date by hand."""

    kind = "manual_budget"
    display_name = "Manual budget"
    category = "budget"
    description = "For any service without a usage API: you type the monthly credit, your cap and what you have spent."
    stability = "manual"
    stability_note = "Manual: you type these numbers yourself."
    docs_url = ""
    fields = [
        FieldSpec(key="monthly_credit", label="Monthly credit", kind="number", required=False, default="0",
                  help="What the service gives or you pay per month."),
        FieldSpec(key="cap", label="Your cap", kind="number", required=False, default="",
                  help="Optional limit of your own. Used instead of the monthly credit when set."),
        FieldSpec(key="spent", label="Spent so far", kind="number", required=True, default="0",
                  help="Update this from the service's billing page."),
        FieldSpec(key="currency", label="Currency", required=False, default="USD", placeholder="USD"),
        FieldSpec(key="reset_day", label="Reset day of month", kind="number", required=False, default="1",
                  help="1 to 31; short months use their last day."),
    ]
    min_poll_seconds = 60

    def default_label(self) -> str:
        return "My budget"

    def fetch(self, instance: "ProviderInstance", secret: str | None, now: datetime | None = None) -> list[UsageRow]:
        return build_rows(instance, now=now)


class ModalBudgetKind(ManualBudgetKind):
    """Manual-budget preset for Modal (no simple public billing API)."""

    kind = "modal_budget"
    display_name = "Modal (manual budget)"
    description = (
        "Modal has no simple public billing API for this, so you type your numbers "
        "from the Modal dashboard's usage page."
    )
    docs_url = "https://modal.com/docs/guide/billing"

    def default_label(self) -> str:
        return "Modal"
