"""Last-good reading cache for the Usage Monitor widget.

Keeps the latest successful ``list[UsageRow]`` and its UTC timestamp for each
provider instance (keyed by ``instance_id``) in
``%APPDATA%/DesktopWidgets/UsageMonitor/cache.json``, or a custom path in
tests. When a live reading fails or is rate limited, ``UsageCache`` returns
the last good reading marked stale.

Only rows are stored: never keys, tokens or raw replies.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from models import UsageRow


def default_cache_path() -> Path:
    """Return the default cache file: ``data_dir() / "cache.json"``."""
    import provider_config  # local import keeps module loading simple

    return provider_config.data_dir() / "cache.json"


@dataclass
class CachedProviderSnapshot:
    """Result returned when resolving a provider's rows through the cache."""

    provider_id: str
    rows: list[UsageRow]
    timestamp: float
    is_stale: bool
    error: str = ""

    @property
    def fetched_at_iso(self) -> str:
        """Return the snapshot timestamp as an ISO-8601 UTC string."""
        if self.timestamp <= 0:
            return ""
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )


class UsageCache:
    """Thread-safe JSON cache keeping the last good reading per instance id."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_cache_path()
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """Load cached provider snapshots from disk if the file exists."""
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
        except Exception:
            self._data = {}

    def _save(self) -> None:
        """Write current provider snapshots atomically to disk."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_name(self.path.name + ".tmp")
            tmp_path.write_text(
                json.dumps(self._data, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self.path)
        except Exception:
            pass

    def put(
        self,
        provider_id: str,
        rows: list[UsageRow],
        timestamp: float | None = None,
    ) -> CachedProviderSnapshot:
        """Store a fresh list of rows for ``provider_id`` and persist to disk."""
        now_ts = time.time() if timestamp is None else float(timestamp)
        with self._lock:
            self._data[provider_id] = {
                "timestamp": now_ts,
                "rows": [r.to_dict() for r in rows],
            }
            self._save()
        return CachedProviderSnapshot(
            provider_id=provider_id,
            rows=rows,
            timestamp=now_ts,
            is_stale=False,
        )

    def get(
        self,
        provider_id: str,
        max_age_seconds: float | None = None,
    ) -> CachedProviderSnapshot | None:
        """Return the cached snapshot for ``provider_id``, or None if absent."""
        with self._lock:
            entry = self._data.get(provider_id)
            if not isinstance(entry, dict):
                return None
            ts = float(entry.get("timestamp", 0.0))
            raw_rows = entry.get("rows", [])

        rows: list[UsageRow] = []
        for item in raw_rows:
            if isinstance(item, dict):
                try:
                    rows.append(UsageRow.from_dict(item))
                except Exception:
                    continue

        if not rows:
            return None

        is_stale = False
        if max_age_seconds is not None and (time.time() - ts) > max_age_seconds:
            is_stale = True

        return CachedProviderSnapshot(
            provider_id=provider_id,
            rows=rows,
            timestamp=ts,
            is_stale=is_stale,
        )

    def fetch_with_fallback(
        self,
        provider_id: str,
        fetcher: Callable[[], list[UsageRow]],
        min_poll_interval_seconds: float = 0.0,
        stale_after_seconds: float = 900.0,
    ) -> CachedProviderSnapshot:
        """Run ``fetcher()`` or reuse cache if within ``min_poll_interval_seconds``.

        If ``fetcher()`` raises an exception, returns an empty list, or returns
        only ``status == 'error'`` rows while a previous good reading exists in
        the cache, the cached reading is preserved and marked ``is_stale=True``.
        """
        existing = self.get(provider_id)
        now_ts = time.time()

        # Respect the kind's minimum poll interval.
        if (
            existing is not None
            and min_poll_interval_seconds > 0
            and (now_ts - existing.timestamp) < min_poll_interval_seconds
        ):
            return CachedProviderSnapshot(
                provider_id=provider_id,
                rows=existing.rows,
                timestamp=existing.timestamp,
                is_stale=(now_ts - existing.timestamp) > stale_after_seconds,
            )

        try:
            fresh_rows = fetcher()
        except Exception as exc:
            fresh_rows = []
            err_msg = f"Reading failed ({type(exc).__name__})."
        else:
            err_msg = next(
                (r.reason for r in fresh_rows if r.status == "error" and r.reason), ""
            )

        # Consider a fetch successful if it returned rows and not all of them
        # failed with a transient 'error' status.
        has_usable_rows = bool(fresh_rows) and any(
            r.status in ("ok", "no_data") for r in fresh_rows
        )

        if has_usable_rows:
            return self.put(provider_id, fresh_rows, timestamp=now_ts)

        # Fallback to last good reading if available
        if existing is not None:
            stale_rows: list[UsageRow] = []
            for r in existing.rows:
                copy_row = UsageRow.from_dict(r.to_dict())
                if copy_row.status == "ok":
                    copy_row.status = "stale"
                stale_rows.append(copy_row)
            return CachedProviderSnapshot(
                provider_id=provider_id,
                rows=stale_rows,
                timestamp=existing.timestamp,
                is_stale=True,
                error=err_msg or "Reading failed; showing the last good reading.",
            )

        return CachedProviderSnapshot(
            provider_id=provider_id,
            rows=fresh_rows,
            timestamp=now_ts,
            is_stale=True,
            error=err_msg or "No data available.",
        )

    def remove(self, instance_id: str) -> None:
        """Forget the cached reading of one instance (after it is removed)."""
        with self._lock:
            if self._data.pop(instance_id, None) is not None:
                self._save()

    def prune(self, keep_ids: Iterable[str]) -> None:
        """Drop cached readings of every instance not in ``keep_ids``."""
        keep = set(keep_ids)
        with self._lock:
            stale_ids = [key for key in self._data if key not in keep]
            for key in stale_ids:
                del self._data[key]
            if stale_ids:
                self._save()
