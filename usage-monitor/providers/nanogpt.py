"""NanoGPT prepaid balance (API key, documented endpoint).

    POST https://nano-gpt.com/api/check-balance
    header x-api-key: <key>

NanoGPT is pay-per-use: there is no percentage limit or reset time, only an
account balance in US dollars. If the user also uses NanoGPT through T3 Code
they can name that T3 provider and the row adds a run count from T3 Code's
local database (optional, never required).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from models import UsageRow
from providers import _t3db
from providers.base import (
    SECRET_STORE_HELP,
    FieldSpec,
    ProviderError,
    ProviderKind,
    get_path,
    http_json,
    make_row,
    money_text,
    to_number,
)

if TYPE_CHECKING:
    from provider_config import ProviderInstance

BALANCE_URL = "https://nano-gpt.com/api/check-balance"


def parse_payload(payload: object, instance: "ProviderInstance", run_count: int | None = None) -> list[UsageRow]:
    """Turn a ``check-balance`` reply into one balance row."""
    if not isinstance(payload, dict):
        raise ProviderError(ProviderError.BAD_RESPONSE, "NanoGPT's reply was not in the expected format.")
    balance = round(to_number(get_path(payload, "usd_balance"), "usd_balance"), 2)
    reason = f"{money_text(balance)} balance · pay-per-use"
    if run_count:
        reason += f" · {run_count} runs in T3 Code"
    return [
        make_row(
            instance,
            model_id="balance",
            model_label="Account balance",
            percent_used=None,
            status="ok",
            reason=reason,
            credit_balance_usd=balance,
        )
    ]


def t3_run_count(t3_instance: str) -> int | None:
    """Total T3 Code runs for that T3 provider, or None when unavailable."""
    if not t3_instance:
        return None
    try:
        counts = _t3db.count_runs(_t3db.default_db_path(), t3_instance)
    except ProviderError:
        return None
    return sum(counts.values())


class NanoGPTKind(ProviderKind):
    """NanoGPT account balance."""

    kind = "nanogpt"
    display_name = "NanoGPT"
    category = "api_key"
    description = "Needs a NanoGPT API key (nano-gpt.com, API settings) to show your prepaid balance."
    stability = "official"
    docs_url = "https://docs.nano-gpt.com/api-reference/endpoint/check-balance"
    fields = [
        FieldSpec(key="api_key", label="API key", kind="secret", help=SECRET_STORE_HELP),
        FieldSpec(
            key="t3_provider_instance",
            label="T3 Code provider name (optional)",
            required=False,
            help="Only if you use NanoGPT through T3 Code: adds a run count.",
        ),
    ]
    min_poll_seconds = 300

    def fetch(self, instance: "ProviderInstance", secret: str | None) -> list[UsageRow]:
        if not secret:
            raise ProviderError(ProviderError.CONFIG, "No API key saved. Open Settings and add your NanoGPT key.")
        payload = http_json(
            BALANCE_URL,
            method="POST",
            headers={"x-api-key": secret, "Content-Type": "application/json"},
            body=b"{}",
        )
        runs = t3_run_count(str(instance.settings.get("t3_provider_instance") or "").strip())
        return parse_payload(payload, instance, run_count=runs)
