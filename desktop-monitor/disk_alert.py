"""disk_alert.py - local low-disk-space notification for Desktop Monitor.

When enabled in Settings, shows one tray notification per drive when its free
space drops below the chosen threshold (default 10 GB). Nothing leaves the PC.

To avoid nagging there is a cooldown (2 hours per drive by default) and a
recovery margin: a drive only counts as "recovered" once it is a little above
the threshold again, so a drive hovering around the limit does not alert twice.
"""

from __future__ import annotations

import time

DEFAULT_THRESHOLD_GB = 10.0
DEFAULT_COOLDOWN_SECONDS = 2 * 60 * 60.0


class DiskAlertManager:
    """Watches disk rows from the metrics snapshot and notifies when one runs low."""

    def __init__(self, threshold_gb: float = DEFAULT_THRESHOLD_GB,
                 cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
                 enabled: bool = False) -> None:
        self.threshold_gb = float(threshold_gb)
        self.cooldown_seconds = float(cooldown_seconds)
        self.enabled = bool(enabled)
        # mount -> time of the last notification (only drives currently in alert)
        self._alerting: dict[str, float] = {}

    def configure(self, enabled, threshold_gb) -> None:
        """Apply the Settings values (safe to call on every snapshot)."""
        self.enabled = bool(enabled)
        try:
            self.threshold_gb = max(0.5, float(threshold_gb))
        except (TypeError, ValueError):
            self.threshold_gb = DEFAULT_THRESHOLD_GB

    def recovery_gb(self) -> float:
        """Free space a drive must reach again before it can alert a second time."""
        return self.threshold_gb + max(1.0, self.threshold_gb * 0.2)

    def check(self, disk_rows, tray_icon=None, now: float | None = None) -> list[str]:
        """Evaluate one snapshot. Returns the mount points notified this time."""
        if not self.enabled:
            return []
        now = time.time() if now is None else now
        notified: list[str] = []
        for row in disk_rows or []:
            mount = str(row.get("mount") or "")
            free_gb = row.get("free_gb")
            if not mount or free_gb is None:
                continue                      # not a real drive row ("All disks")
            try:
                free_gb = float(free_gb)
            except (TypeError, ValueError):
                continue
            if free_gb < self.threshold_gb:
                last = self._alerting.get(mount)
                if last is None or now - last > self.cooldown_seconds:
                    self._alerting[mount] = now
                    notified.append(mount)
                    self._notify(tray_icon, mount, free_gb)
            elif mount in self._alerting and free_gb >= self.recovery_gb():
                del self._alerting[mount]
        return notified

    def _notify(self, tray_icon, mount: str, free_gb: float) -> None:
        """Show the tray balloon. Never raises."""
        if tray_icon is None:
            return
        title = "Low disk space (%s)" % mount.rstrip("\\")
        text = "Only %.1f GB free on %s (alert limit: %.0f GB)." % (
            free_gb, mount, self.threshold_gb)
        try:
            from PySide6.QtWidgets import QSystemTrayIcon
            tray_icon.showMessage(title, text, QSystemTrayIcon.MessageIcon.Warning, 8000)
        except Exception:
            try:
                tray_icon.showMessage(title, text)
            except Exception:
                pass
