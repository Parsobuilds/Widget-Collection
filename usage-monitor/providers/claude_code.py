"""Claude Code plan limits (sign-in based, unofficial endpoint).

Claude Code keeps its sign-in in ``%USERPROFILE%\\.claude\\.credentials.json``
(``claudeAiOauth.accessToken``). The same endpoint Claude Code's own ``/usage``
screen uses returns the current limits:

    GET https://api.anthropic.com/api/oauth/usage

The token is read from the file for each call, used in memory only, and never
copied, cached or logged. When it has expired the user opens Claude Code once
and it refreshes the file itself; this widget never writes to it.
"""

from __future__ import annotations

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
    read_json_file,
    slug,
    to_number,
)

if TYPE_CHECKING:
    from provider_config import ProviderInstance

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
DEFAULT_CREDENTIALS = "%USERPROFILE%\\.claude\\.credentials.json"

# Fallback rows when the reply has no ``limits`` list: (key, label).
_FALLBACK_WINDOWS = (("five_hour", "5-hour session"), ("seven_day", "7-day, all models"))


def default_credentials_path() -> Path:
    """Where Claude Code stores its sign-in on this computer."""
    return home_dir() / ".claude" / ".credentials.json"


def read_access_token(path: Path) -> str:
    """Return the access token from Claude Code's sign-in file (memory only)."""
    data = read_json_file(path, "The Claude Code sign-in file")
    oauth = data.get("claudeAiOauth")
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise ProviderError(
            ProviderError.AUTH,
            "The Claude Code sign-in file has no sign-in token. Open Claude Code and sign in.",
        )
    return token.strip()


def _row_from_window(
    instance: "ProviderInstance",
    model_id: str,
    label: str,
    percent: Any,
    resets_at: Any,
    now: datetime | None,
) -> UsageRow:
    """Build one percentage row."""
    value = clamp_percent(to_number(percent, label))
    reset = str(resets_at) if resets_at else None
    return make_row(
        instance,
        model_id=model_id,
        model_label=label,
        percent_used=value,
        resets_at=reset,
        reset_text=format_reset_countdown(reset, now=now) if reset else "",
    )


# Labels for the ``kind`` of a ``limits`` entry (the names Claude's own usage
# page shows). Unknown kinds get a label made from the kind itself.
_KIND_LABELS = {"session": "Current session", "weekly_all": "All models"}


def _scope_name(scope: Any) -> str:
    """'Opus' from ``{"model": {"display_name": "Opus"}}`` or a plain string."""
    if isinstance(scope, str):
        return scope.strip()
    if isinstance(scope, dict):
        model = scope.get("model")
        if isinstance(model, dict):
            name = model.get("display_name") or model.get("id")
            if name:
                return str(name).strip()
        surface = scope.get("surface")
        if isinstance(surface, str):
            return surface.strip()
    return ""


def _limit_name(item: dict[str, Any]) -> tuple[str, str]:
    """Return ``(model_id, label)`` for one ``limits`` entry.

    Replies name each entry either with ``id`` + ``title`` or with a
    ``kind`` (``session``, ``weekly_all``, ``weekly_scoped``) plus a
    ``scope`` naming the model.
    """
    title = str(item.get("title") or "").strip()
    kind = str(item.get("kind") or item.get("category") or "").strip()
    scope = _scope_name(item.get("scope"))
    if not title:
        if kind in _KIND_LABELS:
            title = _KIND_LABELS[kind]
        elif scope:
            title = f"{scope} only"
        elif kind:
            title = kind.replace("_", " ").capitalize()
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        model_id = kind or slug(title)
        if scope and kind not in _KIND_LABELS:
            model_id = f"{model_id}_{slug(scope)}"
    return model_id, title


def parse_payload(
    payload: Any,
    instance: "ProviderInstance",
    now: datetime | None = None,
) -> list[UsageRow]:
    """Turn the ``/api/oauth/usage`` reply into rows.

    Each entry of the reply's ``limits`` list becomes one row (title, percent,
    resets_at). Older replies without ``limits`` fall back to ``five_hour``
    and ``seven_day``.
    """
    if not isinstance(payload, dict):
        raise ProviderError(ProviderError.BAD_RESPONSE, "Claude's usage reply was not in the expected format.")

    rows: list[UsageRow] = []
    used_ids: set[str] = set()
    limits = payload.get("limits")
    if isinstance(limits, list):
        for item in limits:
            if not isinstance(item, dict):
                continue
            percent = item.get("percent", item.get("utilization"))
            model_id, title = _limit_name(item)
            if percent is None or not title:
                continue
            if model_id in used_ids:
                continue
            used_ids.add(model_id)
            try:
                rows.append(_row_from_window(instance, model_id, str(title), percent, item.get("resets_at"), now))
            except ProviderError:
                continue  # one odd entry should not hide the others

    if not rows:
        for key, label in _FALLBACK_WINDOWS:
            window = payload.get(key)
            if isinstance(window, dict) and window.get("utilization") is not None:
                rows.append(
                    _row_from_window(instance, key, label, window["utilization"], window.get("resets_at"), now)
                )

    if not rows:
        raise ProviderError(ProviderError.BAD_RESPONSE, "Claude's usage reply had no limits in it.")
    return rows


class ClaudeCodeKind(ProviderKind):
    """Claude Pro/Max plan limits read with the Claude Code sign-in."""

    kind = "claude_code"
    display_name = "Claude Code"
    category = "signin"
    description = "Uses the sign-in Claude Code already saved on this computer; press Detect to find it."
    stability = "unofficial"
    stability_note = "Unofficial endpoint used by Claude Code, may stop working."
    docs_url = "https://docs.anthropic.com/en/docs/claude-code/overview"
    fields = [
        FieldSpec(
            key="credentials_path",
            label="Sign-in file",
            kind="path",
            required=False,
            default=DEFAULT_CREDENTIALS,
            placeholder=DEFAULT_CREDENTIALS,
            help="Filled in by Detect. Leave it as it is unless Claude Code keeps it elsewhere.",
        )
    ]
    supports_detect = True
    # Anthropic rate-limits this endpoint (HTTP 429) and Claude Code polls it
    # too, so check at most every 10 minutes.
    min_poll_seconds = 600

    def _path_for(self, instance: "ProviderInstance") -> Path:
        """The sign-in file chosen in settings, or the standard one."""
        text = str(instance.settings.get("credentials_path") or "").strip()
        return expand_path(text) if text else default_credentials_path()

    def detect(self) -> DetectResult:
        path = default_credentials_path()
        shown = display_path(path)
        if not path.is_file():
            return DetectResult(
                found=False,
                message="No Claude Code sign-in found. Install Claude Code and sign in, then press Detect again.",
            )
        try:
            read_access_token(path)
        except ProviderError:
            return DetectResult(
                found=False,
                path=shown,
                message="Found the Claude Code file, but it has no sign-in. Open Claude Code and sign in.",
            )
        return DetectResult(found=True, path=shown, message="Found your Claude Code sign-in.")

    def fetch(self, instance: "ProviderInstance", secret: str | None) -> list[UsageRow]:
        token = read_access_token(self._path_for(instance))
        payload = http_json(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "claude-code/2.0.32",
            },
            auth_message="Claude rejected the sign-in (it may have expired). Open Claude Code once to refresh it.",
        )
        return parse_payload(payload, instance)
