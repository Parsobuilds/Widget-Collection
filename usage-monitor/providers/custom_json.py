"""Custom provider: any HTTPS endpoint that answers with JSON.

The user gives the URL, GET or POST, the header that carries the key and its
prefix (for example ``Authorization`` + ``Bearer ``), and dot paths into the
reply (``data.items.0.used``):

* ``used`` + ``limit`` paths -> a percentage bar;
* ``balance`` path alone -> a money/credit row;
* ``unit`` "USD" fills the money fields; any other text is shown after the
  numbers.

Safety: only http/https; plain http only for this computer (localhost);
10-second timeout; replies over 1 MB are refused; redirects to another host
are not followed, so the key never leaves for a server the user did not name.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from models import UsageRow
from providers.base import (
    SECRET_STORE_HELP,
    FieldSpec,
    ProviderError,
    ProviderKind,
    check_url,
    clamp_percent,
    get_path,
    http_json,
    make_row,
    money_text,
    to_number,
)

if TYPE_CHECKING:
    from provider_config import ProviderInstance

_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")


def _text(settings: dict[str, Any], key: str, default: str = "") -> str:
    value = settings.get(key, default)
    return default if value is None else str(value)


def _amount(value: float, unit: str) -> str:
    """'$1.50' for USD, '1.5 tokens' for other units."""
    if unit.upper() == "USD":
        return money_text(value)
    return f"{value:,.6g} {unit}".strip()


def build_rows(payload: Any, instance: "ProviderInstance") -> list[UsageRow]:
    """Read the configured paths out of ``payload`` and build one row."""
    settings = instance.settings
    used_path = _text(settings, "used_path").strip()
    limit_path = _text(settings, "limit_path").strip()
    balance_path = _text(settings, "balance_path").strip()
    unit = _text(settings, "unit", "USD").strip() or "USD"
    is_money = unit.upper() == "USD"

    values: dict[str, Any] = {}
    reason_parts: list[str] = []
    percent: float | None = None
    secondary = ""

    if used_path and limit_path:
        used = to_number(get_path(payload, used_path), used_path)
        limit = to_number(get_path(payload, limit_path), limit_path)
        if limit <= 0:
            raise ProviderError(ProviderError.BAD_RESPONSE, f"The limit at '{limit_path}' is zero, so no percentage can be shown.")
        percent = clamp_percent(used / limit * 100)
        secondary = f"{_amount(used, unit)} of {_amount(limit, unit)}"
        if is_money:
            values["credit_spent_usd"] = round(used, 2)
            values["credit_cap_usd"] = round(limit, 2)

    if balance_path:
        balance = to_number(get_path(payload, balance_path), balance_path)
        reason_parts.append(f"{_amount(balance, unit)} left")
        if is_money:
            values["credit_balance_usd"] = round(balance, 2)

    if secondary and reason_parts:
        secondary = f"{secondary} · {reason_parts[0]}"

    return [
        make_row(
            instance,
            model_id="usage",
            model_label=instance.label or "Usage",
            percent_used=percent,
            secondary_label=secondary,
            reason=" · ".join(reason_parts) if percent is None else "",
            **values,
        )
    ]


def validate_settings(settings: dict[str, Any], secret: str | None) -> None:
    """Raise CONFIG errors for settings that cannot work."""
    url = _text(settings, "url").strip()
    if not url:
        raise ProviderError(ProviderError.CONFIG, "Enter the URL of the usage endpoint.")
    check_url(url)
    method = _text(settings, "method", "GET").strip().upper() or "GET"
    if method not in ("GET", "POST"):
        raise ProviderError(ProviderError.CONFIG, "Method must be GET or POST.")
    used = _text(settings, "used_path").strip()
    limit = _text(settings, "limit_path").strip()
    balance = _text(settings, "balance_path").strip()
    if bool(used) != bool(limit):
        raise ProviderError(ProviderError.CONFIG, "Set both the used path and the limit path, or neither.")
    if not (used or balance):
        raise ProviderError(ProviderError.CONFIG, "Set a used path and a limit path, or a balance path.")
    if secret:
        header = _text(settings, "key_header", "Authorization").strip() or "Authorization"
        if not _HEADER_NAME.match(header):
            raise ProviderError(ProviderError.CONFIG, "The key header name has characters a header cannot use.")
        prefix = _text(settings, "key_prefix")
        if any(ch in secret + prefix for ch in "\r\n"):
            raise ProviderError(ProviderError.CONFIG, "The key or its prefix contains a line break.")


class CustomJsonKind(ProviderKind):
    """Any JSON usage endpoint the user describes."""

    kind = "custom_json"
    display_name = "Custom (JSON endpoint)"
    category = "custom"
    description = "For any service with a JSON usage or balance URL: you give the URL, the key header and where the numbers are."
    stability = "user-defined"
    stability_note = "You set this up yourself; it works as long as the service keeps the same reply shape."
    docs_url = ""
    fields = [
        FieldSpec(key="url", label="URL", kind="url", placeholder="https://example.com/api/usage",
                  help="https:// only (http:// is allowed for localhost)."),
        FieldSpec(key="method", label="Method", kind="choice", required=False, default="GET", choices=["GET", "POST"]),
        FieldSpec(key="api_key", label="API key", kind="secret", required=False,
                  help="Optional. " + SECRET_STORE_HELP),
        FieldSpec(key="key_header", label="Key header", required=False, default="Authorization",
                  help="Header that carries the key, e.g. Authorization or x-api-key."),
        FieldSpec(key="key_prefix", label="Key prefix", required=False, default="Bearer ",
                  help="Text before the key, e.g. 'Bearer '. Leave empty for none."),
        FieldSpec(key="used_path", label="Used path", required=False, placeholder="data.used",
                  help="Dot path to the amount used. Needs a limit path too."),
        FieldSpec(key="limit_path", label="Limit path", required=False, placeholder="data.limit"),
        FieldSpec(key="balance_path", label="Balance path", required=False, placeholder="data.balance",
                  help="Dot path to the remaining balance."),
        FieldSpec(key="unit", label="Unit", required=False, default="USD",
                  help="USD for money, or any word such as credits or tokens."),
    ]
    min_poll_seconds = 300

    def default_label(self) -> str:
        return "My service"

    def fetch(self, instance: "ProviderInstance", secret: str | None) -> list[UsageRow]:
        settings = instance.settings
        validate_settings(settings, secret)
        method = (_text(settings, "method", "GET").strip().upper() or "GET")
        headers: dict[str, str] = {}
        if secret:
            header = _text(settings, "key_header", "Authorization").strip() or "Authorization"
            headers[header] = _text(settings, "key_prefix") + secret
        body = None
        if method == "POST":
            headers["Content-Type"] = "application/json"
            body = b"{}"
        payload = http_json(_text(settings, "url").strip(), method=method, headers=headers, body=body)
        return build_rows(payload, instance)
