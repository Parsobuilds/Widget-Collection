"""DeepSeek account balance (API key, documented endpoint).

    GET https://api.deepseek.com/user/balance
    header Authorization: Bearer <key>

Documented at https://api-docs.deepseek.com/api/get-user-balance
Reply: ``is_available`` (bool) and ``balance_infos``, a list with one entry
per currency (``currency`` "CNY" or "USD", ``total_balance``,
``granted_balance``, ``topped_up_balance``, all strings).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from models import UsageRow
from providers.base import FieldSpec, ProviderError, ProviderKind, http_json, make_row, money_text, to_number

if TYPE_CHECKING:
    from provider_config import ProviderInstance

BALANCE_URL = "https://api.deepseek.com/user/balance"


def parse_payload(payload: Any, instance: "ProviderInstance") -> list[UsageRow]:
    """One balance row per currency in the reply."""
    infos = payload.get("balance_infos") if isinstance(payload, dict) else None
    if not isinstance(infos, list):
        raise ProviderError(ProviderError.BAD_RESPONSE, "DeepSeek's reply had no balance list.")
    available = bool(payload.get("is_available", True))

    rows: list[UsageRow] = []
    for info in infos:
        if not isinstance(info, dict) or info.get("total_balance") is None:
            continue
        currency = str(info.get("currency") or "USD").upper()
        total = round(to_number(info["total_balance"], "total_balance"), 2)
        reason = f"{money_text(total, currency)} balance"
        granted = info.get("granted_balance")
        if granted is not None:
            reason += f" ({money_text(to_number(granted, 'granted_balance'), currency)} granted)"
        if not available:
            reason += " · not enough for API calls"
        rows.append(
            make_row(
                instance,
                model_id=f"balance_{currency.lower()}",
                model_label=f"Balance ({currency})",
                reason=reason,
                # The money fields are US dollars, so only a USD balance fills them.
                credit_balance_usd=total if currency == "USD" else None,
            )
        )
    if not rows:
        raise ProviderError(ProviderError.BAD_RESPONSE, "DeepSeek's reply had no balance in it.")
    return rows


class DeepSeekKind(ProviderKind):
    """DeepSeek API account balance."""

    kind = "deepseek"
    display_name = "DeepSeek"
    category = "api_key"
    description = "Needs a DeepSeek API key (platform.deepseek.com, API keys) to show your account balance."
    stability = "official"
    docs_url = "https://api-docs.deepseek.com/api/get-user-balance"
    fields = [FieldSpec(key="api_key", label="API key", kind="secret", help="Saved in Windows Credential Manager.")]
    min_poll_seconds = 300

    def fetch(self, instance: "ProviderInstance", secret: str | None) -> list[UsageRow]:
        if not secret:
            raise ProviderError(ProviderError.CONFIG, "No API key saved. Open Settings and add your DeepSeek key.")
        payload = http_json(BALANCE_URL, headers={"Authorization": f"Bearer {secret}"})
        return parse_payload(payload, instance)
