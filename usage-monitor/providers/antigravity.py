"""Antigravity (Google Cloud Code) per-model quotas, unofficial endpoint.

T3 Code's Antigravity provider keeps a Google sign-in in a token file:

    %USERPROFILE%\\.t3\\userdata\\providers\\antigravity\\<id>\\antigravity-acp\\acp_token.json

with ``refresh_token``, ``client_id``, ``client_secret`` and ``token_uri``.
Each reading:

1. trades the refresh token for a short-lived access token
   (``POST https://oauth2.googleapis.com/token``), kept in memory only;
2. asks Cloud Code for the available models
   (``POST https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels``).

Every model in the reply that has ``quotaInfo`` becomes one row named after
its ``displayName``. The OAuth client details come from the token file on
disk; none are written in this source code.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.parse
from datetime import datetime
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
    expand_path,
    home_dir,
    http_json,
    make_row,
    profile_text,
    read_json_file,
)

if TYPE_CHECKING:
    from provider_config import ProviderInstance

MODELS_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
CLOUD_CODE_PROJECT = "aicode-consumers"
_TRUSTED_TOKEN_HOSTS = frozenset({"oauth2.googleapis.com", "accounts.google.com", "www.googleapis.com"})

# Short-lived access tokens, kept in memory only: {hash(refresh token): (token, expiry)}.
_token_cache: dict[str, tuple[str, float]] = {}
_token_lock = threading.Lock()


def token_search_root() -> Path:
    """Folder that holds T3 Code's Antigravity sign-ins."""
    return home_dir() / ".t3" / "userdata" / "providers" / "antigravity"


def find_token_file() -> Path | None:
    """Return the newest ``acp_token.json`` under the documented location."""
    root = token_search_root()
    if not root.is_dir():
        return None
    matches = [p for p in root.glob("*/antigravity-acp/acp_token.json") if p.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def read_token_file(path: Path) -> dict[str, str]:
    """Return refresh token and client details from the token file."""
    data = read_json_file(path, "The Antigravity sign-in file")
    refresh = data.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise ProviderError(ProviderError.AUTH, "The Antigravity sign-in file has no sign-in. Sign in to Antigravity again.")
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    if not client_id or not client_secret:
        raise ProviderError(
            ProviderError.CONFIG,
            "The Antigravity sign-in file has no app client details, so the sign-in cannot be renewed.",
        )
    token_uri = str(data.get("token_uri") or GOOGLE_TOKEN_URL)
    if (urllib.parse.urlsplit(token_uri).hostname or "") not in _TRUSTED_TOKEN_HOSTS:
        token_uri = GOOGLE_TOKEN_URL  # never send the refresh token anywhere but Google
    return {
        "refresh_token": refresh,
        "client_id": str(client_id),
        "client_secret": str(client_secret),
        "token_uri": token_uri,
    }


def _cache_key(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def refresh_access_token(details: dict[str, str]) -> tuple[str, float]:
    """Trade the refresh token for an access token. Returns (token, expiry epoch)."""
    body = urllib.parse.urlencode(
        {
            "client_id": details["client_id"],
            "client_secret": details["client_secret"],
            "refresh_token": details["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    try:
        reply = http_json(
            details["token_uri"],
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "antigravity"},
            body=body,
            auth_message="Google rejected the Antigravity sign-in. Sign in to Antigravity again.",
        )
    except ProviderError as exc:
        if exc.code == ProviderError.BAD_RESPONSE and "HTTP 400" in exc.message:
            # Google answers 400 invalid_grant for an expired or revoked sign-in.
            raise ProviderError(
                ProviderError.AUTH, "The Antigravity sign-in has expired. Sign in to Antigravity again."
            ) from None
        raise
    token = reply.get("access_token") if isinstance(reply, dict) else None
    if not isinstance(token, str) or not token:
        raise ProviderError(ProviderError.BAD_RESPONSE, "Google's sign-in reply had no access token.")
    try:
        lifetime = float(reply.get("expires_in", 3600))
    except (TypeError, ValueError):
        lifetime = 3600.0
    return token, time.time() + lifetime


def get_access_token(details: dict[str, str], force: bool = False) -> str:
    """Return a cached access token, refreshing it when needed."""
    key = _cache_key(details["refresh_token"])
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and not force and time.time() < cached[1] - 60:
            return cached[0]
    token, expiry = refresh_access_token(details)
    with _token_lock:
        _token_cache[key] = (token, expiry)
    return token


def fetch_models(access_token: str) -> Any:
    """Call ``fetchAvailableModels`` and return the decoded reply."""
    return http_json(
        MODELS_URL,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity",
        },
        body=json.dumps({"project": CLOUD_CODE_PROJECT}).encode("utf-8"),
    )


def parse_payload(
    payload: Any,
    instance: "ProviderInstance",
    now: datetime | None = None,
) -> list[UsageRow]:
    """One row per model in the reply that has ``quotaInfo``.

    ``percent_used = (1 - remainingFraction) * 100``. The reply leaves out
    ``remainingFraction`` when it is zero (standard protobuf JSON omits zero
    values), so a ``quotaInfo`` without it means the quota is used up.
    """
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, dict):
        raise ProviderError(ProviderError.BAD_RESPONSE, "Antigravity's reply had no model list.")

    rows: list[UsageRow] = []
    seen_labels: set[str] = set()
    for model_key, entry in models.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("quotaInfo"), dict):
            continue
        quota = entry["quotaInfo"]
        label = str(entry.get("displayName") or model_key)
        if label in seen_labels:
            continue
        try:
            remaining = float(quota.get("remainingFraction", 0.0))
        except (TypeError, ValueError):
            continue
        seen_labels.add(label)
        resets_at = str(quota["resetTime"]) if quota.get("resetTime") else None
        rows.append(
            make_row(
                instance,
                model_id=str(model_key),
                model_label=label,
                percent_used=clamp_percent((1.0 - remaining) * 100),
                resets_at=resets_at,
                reset_text=format_reset_countdown(resets_at, now=now) if resets_at else "",
            )
        )

    if not rows:
        raise ProviderError(ProviderError.BAD_RESPONSE, "Antigravity's reply had no quota information.")
    return rows


class AntigravityKind(ProviderKind):
    """Per-model quotas for the Antigravity sign-in used by T3 Code."""

    kind = "antigravity"
    display_name = "Antigravity"
    category = "signin"
    description = "Uses the Antigravity sign-in that T3 Code saved on this computer; press Detect to find it."
    stability = "unofficial"
    stability_note = "Unofficial Google Cloud Code endpoint, may stop working."
    docs_url = "https://antigravity.google/"
    fields = [
        FieldSpec(
            key="token_path",
            label="Sign-in file",
            kind="path",
            required=False,
            default="",
            placeholder=profile_text(".t3", "userdata", "providers", "antigravity", "...", "acp_token.json"),
            help="Filled in by Detect. Leave empty to search the standard location.",
        )
    ]
    supports_detect = True
    min_poll_seconds = 300

    def is_available(self) -> bool:
        # The token file is written by T3 Code; without its folder none can exist.
        return (home_dir() / ".t3").is_dir()

    def _path_for(self, instance: "ProviderInstance") -> Path:
        text = str(instance.settings.get("token_path") or "").strip()
        if text:
            return expand_path(text)
        found = find_token_file()
        if found is None:
            raise ProviderError(
                ProviderError.NOT_FOUND,
                f"No Antigravity sign-in found under {profile_text('.t3')}. Sign in to Antigravity in T3 Code first.",
            )
        return found

    def detect(self) -> DetectResult:
        path = find_token_file()
        if path is None:
            return DetectResult(
                found=False,
                message="No Antigravity sign-in found on this computer. "
                "This reader only works with the sign-in saved by T3 Code.",
            )
        shown = display_path(path)
        try:
            read_token_file(path)
        except ProviderError as exc:
            return DetectResult(found=False, path=shown, message=exc.message)
        return DetectResult(found=True, path=shown, message="Found your Antigravity sign-in.")

    def fetch(self, instance: "ProviderInstance", secret: str | None) -> list[UsageRow]:
        details = read_token_file(self._path_for(instance))
        access = get_access_token(details)
        try:
            payload = fetch_models(access)
        except ProviderError as exc:
            if exc.code != ProviderError.AUTH:
                raise
            # The access token may have expired early: renew once and retry.
            payload = fetch_models(get_access_token(details, force=True))
        return parse_payload(payload, instance)
