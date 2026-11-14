"""Codex (ChatGPT plan) limits, sign-in based, unofficial endpoint.

The Codex CLI keeps its ChatGPT sign-in in ``%CODEX_HOME%\\auth.json`` or, when
``CODEX_HOME`` is not set, ``%USERPROFILE%\\.codex\\auth.json``
(``tokens.access_token`` and ``tokens.account_id``). The endpoint the Codex
CLI uses for its own status screen returns the rate-limit windows:

    GET https://chatgpt.com/backend-api/wham/usage

The token is used in memory only and never copied, cached or logged.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from models import UsageRow, format_reset_countdown
from providers.base import (
    DetectResult,
    FieldSpec,
    ProviderError,
    ProviderKind,
    clamp_percent,
    display_path,
    epoch_to_iso,
    expand_path,
    home_dir,
    http_json,
    make_row,
    read_json_file,
    to_number,
)

if TYPE_CHECKING:
    from provider_config import ProviderInstance

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def default_auth_path() -> tuple[Path, str]:
    """Return (real path, display text) of Codex's sign-in file."""
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        return Path(codex_home) / "auth.json", "%CODEX_HOME%\\auth.json"
    path = home_dir() / ".codex" / "auth.json"
    return path, display_path(path)


def read_tokens(path: Path) -> tuple[str, str]:
    """Return (access_token, account_id) from auth.json (memory only)."""
    data = read_json_file(path, "The Codex sign-in file")
    tokens = data.get("tokens")
    access = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not isinstance(access, str) or not access.strip():
        if data.get("OPENAI_API_KEY"):
            raise ProviderError(
                ProviderError.AUTH,
                "Codex is signed in with an API key, not a ChatGPT account, so it has no plan limits to show.",
            )
        raise ProviderError(
            ProviderError.AUTH, "The Codex sign-in file has no ChatGPT sign-in. Run Codex and sign in."
        )
    account = tokens.get("account_id") if isinstance(tokens, dict) else ""
    return access.strip(), str(account or "")


def window_name(seconds: Any, fallback: str) -> str:
    """Describe a window length: 18000 -> '5-hour limit', 604800 -> '7-day limit'."""
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return fallback
    if total <= 0:
        return fallback
    if total % 86400 == 0:
        return f"{total // 86400}-day limit"
    if total % 3600 == 0:
        return f"{total // 3600}-hour limit"
    return f"{max(1, total // 60)}-minute limit"


def _window_row(
    instance: "ProviderInstance",
    model_id: str,
    window: Any,
    fallback_label: str,
    prefix: str,
    now: datetime | None,
) -> UsageRow | None:
    """One row for a ``primary_window`` / ``secondary_window`` object."""
    if not isinstance(window, dict) or window.get("used_percent") is None:
        return None
    label = prefix + window_name(window.get("limit_window_seconds"), fallback_label)
    percent = clamp_percent(to_number(window["used_percent"], label))
    resets_at = epoch_to_iso(window.get("reset_at"))
    if resets_at is None and window.get("reset_after_seconds") is not None:
        try:
            base = now or datetime.now(timezone.utc)
            resets_at = (base + timedelta(seconds=float(window["reset_after_seconds"]))).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        except (TypeError, ValueError, OverflowError):
            resets_at = None
    return make_row(
        instance,
        model_id=model_id,
        model_label=label,
        percent_used=percent,
        resets_at=resets_at,
        reset_text=format_reset_countdown(resets_at, now=now) if resets_at else "",
    )


def parse_payload(
    payload: Any,
    instance: "ProviderInstance",
    now: datetime | None = None,
) -> list[UsageRow]:
    """Turn the ``wham/usage`` reply into rows.

    ``rate_limit.primary_window`` and ``rate_limit.secondary_window`` become
    one row each, plus the code-review window when the reply has one.
    """
    if not isinstance(payload, dict):
        raise ProviderError(ProviderError.BAD_RESPONSE, "Codex's usage reply was not in the expected format.")

    rows: list[UsageRow] = []
    rate_limit = payload.get("rate_limit")
    if isinstance(rate_limit, dict):
        for model_id, key, fallback in (
            ("primary", "primary_window", "Short-term limit"),
            ("secondary", "secondary_window", "Weekly limit"),
        ):
            row = _window_row(instance, model_id, rate_limit.get(key), fallback, "", now)
            if row is not None:
                rows.append(row)

    review = payload.get("code_review_rate_limit")
    if isinstance(review, dict):
        row = _window_row(
            instance, "code_review", review.get("primary_window"), "limit", "Code review, ", now
        )
        if row is not None:
            rows.append(row)

    if not rows:
        raise ProviderError(ProviderError.BAD_RESPONSE, "Codex's usage reply had no rate-limit windows in it.")
    return rows


class CodexKind(ProviderKind):
    """ChatGPT plan limits for Codex, read with the Codex CLI sign-in."""

    kind = "codex"
    display_name = "Codex"
    category = "signin"
    description = "Uses the ChatGPT sign-in the Codex CLI already saved on this computer; press Detect to find it."
    stability = "unofficial"
    stability_note = "Unofficial endpoint used by the Codex CLI, may stop working."
    docs_url = "https://github.com/openai/codex"
    fields = [
        FieldSpec(
            key="auth_path",
            label="Sign-in file",
            kind="path",
            required=False,
            default="",
            placeholder="%USERPROFILE%\\.codex\\auth.json",
            help="Leave empty to use the standard Codex location.",
        )
    ]
    supports_detect = True
    min_poll_seconds = 300

    def _path_for(self, instance: "ProviderInstance") -> Path:
        text = str(instance.settings.get("auth_path") or "").strip()
        return expand_path(text) if text else default_auth_path()[0]

    def detect(self) -> DetectResult:
        path, shown = default_auth_path()
        if not path.is_file():
            return DetectResult(
                found=False,
                message="No Codex sign-in found. Install the Codex CLI and sign in with ChatGPT, then press Detect again.",
            )
        try:
            read_tokens(path)
        except ProviderError as exc:
            return DetectResult(found=False, path=shown, message=exc.message)
        return DetectResult(found=True, path=shown, message="Found your Codex sign-in.")

    def fetch(self, instance: "ProviderInstance", secret: str | None) -> list[UsageRow]:
        access, account = read_tokens(self._path_for(instance))
        headers = {"Authorization": f"Bearer {access}", "User-Agent": "codex-cli"}
        if account:
            headers["ChatGPT-Account-Id"] = account
        payload = http_json(
            USAGE_URL,
            headers=headers,
            auth_message="ChatGPT rejected the Codex sign-in (it may have expired). Run Codex once to refresh it.",
        )
        return parse_payload(payload, instance)
