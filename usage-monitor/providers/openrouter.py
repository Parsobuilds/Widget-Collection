"""OpenRouter key limit and usage (API key, documented endpoint).

    GET https://openrouter.ai/api/v1/key
    header Authorization: Bearer <key>

Documented at
https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key
The reply's ``data`` object has the key's credit ``limit`` and
``limit_remaining`` (null when the key has no limit), ``limit_reset``,
``usage`` / ``usage_daily`` / ``usage_weekly`` / ``usage_monthly`` (US dollars)
and ``free_model_daily_requests`` (``used`` / ``limit`` / ``remaining``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from models import UsageRow, format_reset_countdown
from providers.base import (
    SECRET_STORE_HELP,
    FieldSpec,
    ProviderError,
    ProviderKind,
    clamp_percent,
    http_json,
    make_row,
    money_text,
    to_number,
)

if TYPE_CHECKING:
    from provider_config import ProviderInstance

KEY_URL = "https://openrouter.ai/api/v1/key"


def _next_utc_midnight(now: datetime | None) -> str:
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    tomorrow = (base.astimezone(timezone.utc) + timedelta(days=1)).date()
    return f"{tomorrow.isoformat()}T00:00:00Z"


def parse_payload(payload: Any, instance: "ProviderInstance", now: datetime | None = None) -> list[UsageRow]:
    """Turn the ``/api/v1/key`` reply into rows."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ProviderError(ProviderError.BAD_RESPONSE, "OpenRouter's reply had no key details.")

    rows: list[UsageRow] = []
    usage = to_number(data.get("usage", 0) or 0, "usage")
    monthly = data.get("usage_monthly")
    limit = data.get("limit")

    if limit is not None:
        cap = to_number(limit, "limit")
        remaining_raw = data.get("limit_remaining")
        remaining = to_number(remaining_raw, "limit_remaining") if remaining_raw is not None else max(0.0, cap - usage)
        spent = max(0.0, cap - remaining)
        reset = str(data.get("limit_reset") or "").strip()
        reason = f"{money_text(remaining)} left of {money_text(cap)}"
        if reset:
            reason += f" ({reset})"
        rows.append(
            make_row(
                instance,
                model_id="key_limit",
                model_label="Key credit limit",
                percent_used=clamp_percent(spent / cap * 100) if cap > 0 else None,
                secondary_label=reason,
                reason=reason,
                credit_balance_usd=round(remaining, 2),
                credit_spent_usd=round(spent, 2),
                credit_cap_usd=round(cap, 2),
            )
        )
    else:
        parts = []
        if monthly is not None:
            parts.append(f"{money_text(to_number(monthly, 'usage_monthly'))} this month")
        parts.append(f"{money_text(usage)} all time")
        rows.append(
            make_row(
                instance,
                model_id="usage",
                model_label="Spend (no key limit)",
                reason=" · ".join(parts),
                credit_spent_usd=round(usage, 2),
            )
        )

    free = data.get("free_model_daily_requests")
    if isinstance(free, dict) and free.get("limit"):
        free_limit = to_number(free["limit"], "free_model_daily_requests.limit")
        used = to_number(free.get("used", 0) or 0, "free_model_daily_requests.used")
        if free_limit > 0:
            resets_at = _next_utc_midnight(now)
            rows.append(
                make_row(
                    instance,
                    model_id="free_daily",
                    model_label="Free model requests today",
                    percent_used=clamp_percent(used / free_limit * 100),
                    secondary_label=f"{used:g}/{free_limit:g}",
                    resets_at=resets_at,
                    reset_text=format_reset_countdown(resets_at, now=now),
                )
            )
    return rows


class OpenRouterKind(ProviderKind):
    """OpenRouter API key spend and limit."""

    kind = "openrouter"
    display_name = "OpenRouter"
    category = "api_key"
    description = "Needs an OpenRouter API key (openrouter.ai, Keys) to show its credit limit and spend."
    stability = "official"
    docs_url = "https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key"
    fields = [FieldSpec(key="api_key", label="API key", kind="secret", help=SECRET_STORE_HELP)]
    min_poll_seconds = 300

    def fetch(self, instance: "ProviderInstance", secret: str | None) -> list[UsageRow]:
        if not secret:
            raise ProviderError(ProviderError.CONFIG, "No API key saved. Open Settings and add your OpenRouter key.")
        payload = http_json(KEY_URL, headers={"Authorization": f"Bearer {secret}"})
        return parse_payload(payload, instance)
