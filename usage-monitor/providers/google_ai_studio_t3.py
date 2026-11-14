"""Google AI Studio requests counted from T3 Code's local database.

Google's Gemini API has no read-only "remaining quota" endpoint, so this kind
counts the requests T3 Code itself sent through one of its providers today.
It is only offered when T3 Code's database exists on this computer.

* Models come from the database rows, never from a list in this code.
* The free-tier daily quota resets at midnight Pacific time; this reader uses
  the fixed 08:00 UTC boundary (midnight Pacific standard time).
* The daily cap per model is a user setting. ``0`` (the default) means "no cap
  known": the row then shows the count only, without a percentage bar.
* Requests made with the same key outside T3 Code are not counted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from models import UsageRow, format_reset_countdown
from providers import _t3db
from providers.base import (
    FieldSpec,
    ProviderError,
    ProviderKind,
    clamp_percent,
    make_row,
    setting_number,
)

if TYPE_CHECKING:
    from provider_config import ProviderInstance

RESET_HOUR_UTC = 8  # midnight Pacific standard time
DEFAULT_T3_PROVIDER = "google_ai_studio"
RECENT_DAYS = 30


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def quota_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return ``(window_start, resets_at)`` of the current daily window."""
    ref = _as_utc(now)
    today_reset = ref.replace(hour=RESET_HOUR_UTC, minute=0, second=0, microsecond=0)
    if ref.hour >= RESET_HOUR_UTC:
        return today_reset, today_reset + timedelta(days=1)
    return today_reset - timedelta(days=1), today_reset


def parse_counts(
    instance: "ProviderInstance",
    counts_today: dict[str, int],
    models_seen: list[str],
    daily_cap: float,
    resets_at: datetime,
    now: datetime | None = None,
) -> list[UsageRow]:
    """Build one row per model seen in the database.

    ``models_seen`` fixes the row order;
    ``counts_today`` gives the requests since the window started.
    """
    resets_iso = resets_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    reset_text = format_reset_countdown(resets_iso, now=_as_utc(now))
    cap = int(daily_cap) if daily_cap and daily_cap > 0 else 0

    rows: list[UsageRow] = []
    for model in models_seen:
        used = max(0, int(counts_today.get(model, 0) or 0))
        if cap:
            rows.append(
                make_row(
                    instance,
                    model_id=model,
                    model_label=model,
                    percent_used=clamp_percent(used / cap * 100),
                    secondary_label=f"{used}/{cap}",
                    resets_at=resets_iso,
                    reset_text=reset_text,
                )
            )
        else:
            rows.append(
                make_row(
                    instance,
                    model_id=model,
                    model_label=model,
                    percent_used=None,
                    reason=f"{used} requests today",
                    resets_at=resets_iso,
                    reset_text=reset_text,
                )
            )

    if not rows:
        rows.append(
            make_row(
                instance,
                model_id="none",
                model_label="No requests recorded",
                status="no_data",
                reason="T3 Code has no runs for this provider yet",
                resets_at=resets_iso,
                reset_text=reset_text,
            )
        )
    return rows


class GoogleAIStudioT3Kind(ProviderKind):
    """Requests per Gemini model today, counted from T3 Code's history."""

    kind = "google_ai_studio_t3"
    display_name = "Google AI Studio (via T3 Code)"
    category = "local"
    description = "Counts today's Gemini requests that T3 Code made with your Google AI Studio key; no key needed here."
    stability = "local"
    stability_note = "Counts only requests made through T3 Code on this computer."
    docs_url = "https://ai.google.dev/gemini-api/docs/rate-limits"
    fields = [
        FieldSpec(
            key="t3_provider_instance",
            label="T3 Code provider name",
            required=False,
            default=DEFAULT_T3_PROVIDER,
            help="The provider name T3 Code uses for your Google AI Studio key.",
        ),
        FieldSpec(
            key="daily_cap",
            label="Daily request cap per model",
            kind="number",
            required=False,
            default="0",
            help="From Google's rate-limit page for your tier. 0 shows the count only.",
        ),
    ]
    min_poll_seconds = 300

    def is_available(self) -> bool:
        return _t3db.default_db_path().is_file()

    def fetch(
        self,
        instance: "ProviderInstance",
        secret: str | None,
        now: datetime | None = None,
    ) -> list[UsageRow]:
        t3_name = str(instance.settings.get("t3_provider_instance") or DEFAULT_T3_PROVIDER).strip()
        cap = setting_number(instance.settings, "daily_cap", "Daily request cap", default=0.0) or 0.0
        if cap < 0:
            raise ProviderError(ProviderError.CONFIG, "Daily request cap cannot be negative.")

        db_path = _t3db.default_db_path()
        window_start, resets_at = quota_window(now)
        # Models used in the last 30 days get a row (busiest first), so a
        # model used yesterday still shows "0 requests today".
        recent = _t3db.count_runs(db_path, t3_name, since=window_start - timedelta(days=RECENT_DAYS))
        today = _t3db.count_runs(db_path, t3_name, since=window_start)
        models_seen = sorted(set(recent) | set(today), key=lambda m: (-today.get(m, 0), -recent.get(m, 0), m))
        return parse_counts(instance, today, models_seen, cap, resets_at, now=now)
