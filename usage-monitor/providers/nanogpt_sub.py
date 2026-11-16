"""NanoGPT subscription quotas, independent of prepaid credit balances."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from models import UsageRow, format_reset_countdown
from providers.base import (FieldSpec, ProviderError, ProviderKind,
                            SECRET_STORE_HELP, http_json, make_row)

USAGE_URL = "https://nano-gpt.com/api/subscription/v1/usage"


def _count(value: float) -> str:
    return f"{value / 1_000_000:.1f}M".replace(".0M", "M") if value >= 1_000_000 else f"{value:,.0f}"


def parse_payload(payload: Any, instance: Any, now: datetime | None = None) -> list[UsageRow]:
    """The API returns a fraction and milliseconds; convert each exactly once."""
    if not isinstance(payload, dict):
        raise ProviderError(ProviderError.BAD_RESPONSE, "Invalid subscription response.")
    if payload.get("active") is not True or payload.get("state") != "active":
        return [make_row(instance, "subscription", "Subscription inactive", status="no_data",
                         reason="Check your NanoGPT subscription.")]
    rows = []
    for key, label in (("weeklyInputTokens", "Weekly input tokens"), ("dailyImages", "Daily images")):
        try:
            quota = payload[key]
            used, limit, fraction, epoch = (float(quota["used"]), float(payload["limits"][key]),
                                            float(quota["percentUsed"]), float(quota["resetAt"]))
            if not all(math.isfinite(v) for v in (used, limit, fraction, epoch)) or limit <= 0:
                raise ValueError("Invalid numbers")
            reset = datetime.fromtimestamp(epoch / 1000, timezone.utc).isoformat()
            rows.append(make_row(instance, key, label,
                percent_used=round(max(0, min(100, fraction * 100)), 1), resets_at=reset,
                reset_text=format_reset_countdown(reset, now=now),
                secondary_label=f"{_count(used)} / {_count(limit)}"))
        except (KeyError, TypeError, ValueError, OSError, OverflowError):
            rows.append(make_row(instance, key, label, status="error", reason=f"Invalid {label.lower()} quota."))
    return rows


class NanoGPTSubscriptionKind(ProviderKind):
    kind = "nanogpt_sub"
    display_name = "NanoGPT Subscription"
    category = "api_key"
    description = "Weekly input tokens and daily images for a NanoGPT subscription. Separate from prepaid credits."
    fields = [FieldSpec(key="api_key", label="Subscription API key", kind="secret", help=SECRET_STORE_HELP)]
    min_poll_seconds = 300

    def fetch(self, instance: Any, secret: str | None) -> list[UsageRow]:
        if not secret:
            raise ProviderError(ProviderError.CONFIG, "Add your NanoGPT subscription API key in Settings.")
        payload = http_json(USAGE_URL, headers={"x-api-key": secret, "Accept": "application/json"})
        return parse_payload(payload, instance)
