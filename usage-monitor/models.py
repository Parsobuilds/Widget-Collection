"""Shared data structures for the Usage Monitor widget.

Every provider kind in ``providers/<kind>.py`` returns a list of ``UsageRow``
objects from its ``fetch()`` method. The UI and cache layers only depend on
these dataclasses, which keeps data fetching cleanly separated from the
PySide6 drawing code.

Nothing in this module knows about any particular provider: model names and
limits always come from the provider's own reply.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any


def format_reset_countdown(
    resets_at: str | int | float | None,
    now: datetime | None = None,
) -> str:
    """Convert an ISO-8601 timestamp or Unix epoch into 'resets in 2h 14m'.

    Returns an empty string when ``resets_at`` is missing or unparseable.
    """
    if resets_at is None or resets_at == "":
        return ""

    ref_now = now or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)

    target: datetime | None = None
    try:
        if isinstance(resets_at, (int, float)):
            target = datetime.fromtimestamp(float(resets_at), tz=timezone.utc)
        elif isinstance(resets_at, str):
            text = resets_at.strip()
            if text.isdigit():
                target = datetime.fromtimestamp(float(text), tz=timezone.utc)
            else:
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                target = datetime.fromisoformat(text)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return ""

    if target is None:
        return ""

    delta_seconds = int((target - ref_now).total_seconds())
    if delta_seconds <= 0:
        return "resets soon"

    days, rem = divmod(delta_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60

    if days > 0:
        return f"resets in {days}d {hours}h"
    if hours > 0:
        return f"resets in {hours}h {minutes:02d}m"
    return f"resets in {max(1, minutes)}m"


@dataclass
class UsageRow:
    """One usage reading displayed on the ALL and FAVOURITES tabs.

    Attributes:
        provider_id: The provider instance id from config.json (8 hex chars).
        provider_label: Upper-case section header (the instance label).
        model_id: Stable id of this reading, taken from the provider's reply
            (for example a limit id such as 'session').
        model_label: Display name shown on the left of the row, also taken
            from the provider's reply (for example 'Current session').
        percent_used: 0..100 percentage for the progress bar, or None when
            the reading has no percentage (a balance, a plain count).
        resets_at: Raw ISO-8601 UTC string (or None) when the window resets.
        reset_text: Human-readable right-hand note (e.g. 'resets in 2h 14m').
        secondary_percent: Optional second percentage for the same row.
        secondary_label: Short label for extra detail (e.g. '4/20').
        status: 'ok', 'no_data', 'stale', or 'error'.
        reason: One-line explanation when status != 'ok', or extra detail
            such as a formatted balance.
        credit_balance_usd: Remaining money/credit, if the reading has one.
        credit_spent_usd: Money spent in the period, if the reading has one.
        credit_cap_usd: Money allowance/cap for the period, if known.
        provider_kind: The provider kind id (e.g. 'claude_code').

    The three ``credit_*_usd`` fields hold amounts in US dollars for every
    kind except ``manual_budget``, where they hold the amount in the currency
    the user typed (the formatted text with the right symbol is in
    ``reason``).
    """

    provider_id: str
    provider_label: str
    model_id: str
    model_label: str
    percent_used: float | None = None
    resets_at: str | None = None
    reset_text: str = ""
    secondary_percent: float | None = None
    secondary_label: str = ""
    status: str = "ok"
    reason: str = ""
    credit_balance_usd: float | None = None
    credit_spent_usd: float | None = None
    credit_cap_usd: float | None = None
    provider_kind: str = ""

    @property
    def star_key(self) -> str:
        """Unique key used to save and restore starred rows."""
        return f"{self.provider_id}:{self.model_id}"

    @property
    def has_money(self) -> bool:
        """True when any of the money fields is set."""
        return any(
            value is not None
            for value in (
                self.credit_balance_usd,
                self.credit_spent_usd,
                self.credit_cap_usd,
            )
        )

    def display_reset_Or_reason(self, now: datetime | None = None) -> str:
        """Return the text to display at the right side of the row."""
        if self.percent_used is None:
            # A healthy reading without a percentage (a balance or a plain
            # count): show its detail text and, when known, the countdown.
            if self.status == "ok" and self.reason:
                countdown = format_reset_countdown(self.resets_at, now=now)
                return f"{self.reason} · {countdown}" if countdown else self.reason
            if self.credit_balance_usd is not None and self.reason:
                return self.reason
            if self.reason:
                return f"no data · {self.reason}"
            if self.reset_text:
                return self.reset_text
            return "no data"

        if self.status == "no_data":
            return f"no data · {self.reason}" if self.reason else "no data"

        countdown = format_reset_countdown(self.resets_at, now=now) or self.reset_text
        if self.secondary_label and countdown:
            return f"{self.secondary_label} · {countdown}"
        if self.secondary_label:
            return self.secondary_label
        return countdown

    def to_dict(self) -> dict[str, Any]:
        """Serialize this row to a plain JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UsageRow:
        """Deserialize a UsageRow from a dictionary (unknown keys ignored)."""
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class CreditRow:
    """One provider balance/spend entry displayed on the CREDITS tab."""

    provider_id: str
    provider_label: str
    title: str
    balance_usd: float | None = None
    spent_usd: float | None = None
    cap_usd: float | None = None
    percent_used: float | None = None
    detail_text: str = ""
    resets_at: str | None = None
    reset_text: str = ""
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        """Serialize this credit row to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreditRow:
        """Deserialize a CreditRow from a dictionary (unknown keys ignored)."""
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


def _money_detail(balance: float | None, spent: float | None, cap: float | None) -> str:
    """Build a '$1.00 left · $2.00 spent of $3.00' style summary."""
    parts: list[str] = []
    if balance is not None:
        parts.append(f"${balance:.2f} left")
    if spent is not None and cap is not None:
        parts.append(f"${spent:.2f} spent of ${cap:.2f}")
    elif spent is not None:
        parts.append(f"${spent:.2f} spent")
    elif cap is not None:
        parts.append(f"${cap:.2f} cap")
    return " · ".join(parts)


def build_credit_rows(usage_rows: list[UsageRow]) -> list[CreditRow]:
    """Derive CREDITS tab cards from the collected UsageRow items.

    One ``CreditRow`` per provider instance that has any money field set on
    any of its rows, in the order the instances first appear. The first row
    with money fields is used for that instance.
    """
    credits: list[CreditRow] = []
    seen: set[str] = set()

    for row in usage_rows:
        if row.provider_id in seen or not row.has_money:
            continue
        seen.add(row.provider_id)

        balance = row.credit_balance_usd
        spent = row.credit_spent_usd
        cap = row.credit_cap_usd

        percent = row.percent_used
        if percent is None and cap and spent is not None and cap > 0:
            percent = round(min(100, max(0, (spent / cap) * 100)), 1)

        detail = row.reason or _money_detail(balance, spent, cap) or "no data"

        credits.append(
            CreditRow(
                provider_id=row.provider_id,
                provider_label=row.provider_label,
                title=row.model_label,
                balance_usd=balance,
                spent_usd=spent,
                cap_usd=cap,
                percent_used=percent,
                detail_text=detail,
                resets_at=row.resets_at,
                reset_text=format_reset_countdown(row.resets_at) or row.reset_text,
                status=row.status,
            )
        )

    return credits


@dataclass
class ProviderSummary:
    """Header figures, shared by the desktop and JSON API clients."""

    provider_id: str
    provider_label: str
    primary_percent: float | None = None
    primary_label: str = ""
    secondary_percent: float | None = None
    secondary_label: str = ""
    resets_at: str | None = None
    reset_text: str = ""
    status: str = "ok"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the additive provider JSON contract."""
        return asdict(self)



def build_provider_summaries(rows: list[UsageRow]) -> list[ProviderSummary]:
    """Summarise only known shared windows; never merge independent model limits."""
    grouped: dict[str, list[UsageRow]] = {}
    for row in rows:
        grouped.setdefault(row.provider_id, []).append(row)
    summaries = []
    for provider_id, group in grouped.items():
        first = group[0]
        status = next((state for state in ("error", "stale", "no_data")
                       if any(row.status == state for row in group)), "ok")
        summary = ProviderSummary(provider_id, first.provider_label, status=status)
        primary = secondary = None
        if first.provider_kind == "claude_code":
            primary = next((r for r in group if r.model_id in {"session", "five_hour"}), None)
            secondary = next((r for r in group if r.model_id in {"weekly_all", "seven_day"}), None)
        elif first.provider_kind == "codex":
            primary = next((r for r in group if r.model_id == "primary"), None)
            secondary = next((r for r in group if r.model_id == "secondary"), None)
        if primary:
            summary.primary_percent = primary.percent_used
            summary.primary_label = f"5h {primary.percent_used:.0f}%" if primary.percent_used is not None else ""
            summary.resets_at, summary.reset_text = primary.resets_at, primary.reset_text
        if secondary:
            summary.secondary_percent = secondary.percent_used
            summary.secondary_label = f"7d {secondary.percent_used:.0f}%" if secondary.percent_used is not None else ""
        if status != "ok":
            summary.detail = " · ".join(dict.fromkeys(r.reason for r in group if r.reason and r.status != "ok"))
        summaries.append(summary)
    return summaries


def model_display_row(row: UsageRow, summary: ProviderSummary) -> UsageRow:
    """Keep scoped weekly/code-review limits; shared windows already have header bars."""
    shared_ids = {"claude_code": {"session", "five_hour", "weekly_all", "seven_day"},
                  "codex": {"primary", "secondary"}}
    if row.model_id in shared_ids.get(row.provider_kind, set()) and row.status == "ok":
        return replace(row, percent_used=None, secondary_percent=None, secondary_label="",
                       resets_at=None, reset_text="", reason="")
    return replace(row)


def uses_inline_metrics(row: UsageRow) -> bool:
    return row.provider_kind in {"antigravity", "google_ai_studio_t3"}


def is_compact_row(row: UsageRow) -> bool:
    return (row.percent_used is None and not row.secondary_label and
            not row.reset_text and not row.reason)
