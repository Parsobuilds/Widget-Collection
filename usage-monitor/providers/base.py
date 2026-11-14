"""Building blocks shared by every provider kind.

* ``FieldSpec`` / ``DetectResult`` / ``TestResult``: what the Settings window
  needs to build a form and report results.
* ``ProviderError``: the one exception a provider's ``fetch()`` raises. Its
  message is shown to the user as-is, so it is plain English and has no secret.
* ``ProviderKind``: base class for one catalogue entry.
* Helpers: a safe HTTP call that turns every failure into a ``ProviderError``,
  JSON path lookup, number parsing, home-folder path display, row
  construction.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from models import UsageRow

if TYPE_CHECKING:  # only for type hints; avoids a circular import
    from provider_config import ProviderInstance

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 1_000_000  # 1 MB is far more than any usage reply needs
USER_AGENT = "DesktopWidgets-UsageMonitor/1.0"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


# ---------------------------------------------------------------------------
# Data classes used by the Settings window
# ---------------------------------------------------------------------------
@dataclass
class FieldSpec:
    """One input on the Add provider form."""

    key: str
    label: str
    kind: str = "text"  # "text" | "secret" | "url" | "number" | "path" | "choice"
    required: bool = True
    default: str = ""
    placeholder: str = ""
    help: str = ""
    choices: list[str] = field(default_factory=list)


@dataclass
class DetectResult:
    """Outcome of the Detect button."""

    found: bool
    path: str = ""  # display form using %USERPROFILE%, never an expanded path
    message: str = ""


@dataclass
class TestResult:
    """Outcome of the Test button."""

    __test__ = False  # tell test runners this is not a test case

    ok: bool
    message: str
    rows: list[UsageRow] = field(default_factory=list)


class ProviderError(Exception):
    """Raised by a provider's ``fetch()``.

    ``code`` is one of the constants below; ``message`` is shown to the user
    as-is, so it is plain English and contains no secret.
    """

    AUTH = "auth"  # wrong/expired key or sign-in
    NETWORK = "network"  # no internet, DNS, timeout, connection refused
    NOT_FOUND = "not_found"  # sign-in file or local database missing
    RATE_LIMITED = "rate_limited"
    BAD_RESPONSE = "bad_response"  # endpoint changed / JSON not as expected
    CONFIG = "config"  # a required setting is missing or invalid

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __repr__(self) -> str:
        return f"ProviderError({self.code!r}, {self.message!r})"


# ---------------------------------------------------------------------------
# Provider kind base class
# ---------------------------------------------------------------------------
class ProviderKind:
    """Base class; one subclass instance per catalogue entry."""

    kind: str = ""
    display_name: str = ""
    category: str = ""  # "signin" | "api_key" | "custom" | "budget" | "local"
    description: str = ""
    stability: str = "official"  # "official" | "unofficial" | "local" | "manual" | "user-defined"
    stability_note: str = ""
    docs_url: str = ""
    fields: list[FieldSpec] = []
    supports_detect: bool = False
    min_poll_seconds: int = 300

    def is_available(self) -> bool:
        """False hides this kind from the catalogue."""
        return True

    def default_label(self) -> str:
        """Label suggested for a new instance."""
        return self.display_name

    def default_settings(self) -> dict[str, Any]:
        """FieldSpec defaults for every non-secret field."""
        return {spec.key: spec.default for spec in self.fields if spec.kind != "secret"}

    def secret_field(self) -> FieldSpec | None:
        """Return this kind's secret field, if it has one."""
        return next((spec for spec in self.fields if spec.kind == "secret"), None)

    def detect(self) -> DetectResult:
        """Look for a local sign-in. Only meaningful when ``supports_detect``."""
        return DetectResult(found=False, message="This provider has no automatic detection.")

    def fetch(self, instance: "ProviderInstance", secret: str | None) -> list[UsageRow]:
        """One live reading. Raises ProviderError on any failure."""
        raise ProviderError(ProviderError.CONFIG, "This provider cannot be read yet.")

    def __repr__(self) -> str:
        return f"<ProviderKind {self.kind}>"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_ENV_PATTERN = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


IS_WINDOWS = sys.platform == "win32"

# Help text for every API-key field: says where the key is kept on this computer.
SECRET_STORE_HELP = (
    "Saved in Windows Credential Manager." if IS_WINDOWS else "Saved in your system key store."
)


def home_dir() -> Path:
    """The user's home folder (``%USERPROFILE%`` on Windows, ``Path.home()`` on Linux)."""
    if IS_WINDOWS:
        profile = os.environ.get("USERPROFILE")
        if profile:
            return Path(profile)
    return Path.home()


def profile_text(*parts: str) -> str:
    """Show a path under the home folder the way this platform writes it.

    On Windows ``profile_text(".claude", "x.json")`` gives a ``%USERPROFILE%`` path
    with backslashes; on Linux it gives ``~/.claude/x.json``. Only used for display and as
    a default the user can edit; the real path is always built with ``home_dir()``.
    """
    if IS_WINDOWS:
        return "%USERPROFILE%\\" + "\\".join(parts)
    return "~/" + "/".join(parts)


def expand_path(text: str) -> Path:
    """Expand ``%USERPROFILE%``-style variables, ``$VAR`` (Linux) and ``~`` in a settings path."""

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.upper() == "USERPROFILE":
            return str(home_dir())
        return os.environ.get(name, match.group(0))

    expanded = _ENV_PATTERN.sub(_replace, text.strip())
    if not IS_WINDOWS:
        expanded = os.path.expandvars(expanded)
    if expanded.startswith("~"):
        expanded = str(home_dir()) + expanded[1:]
    return Path(expanded)


def display_path(path: Path) -> str:
    """Show ``path`` with the home folder written as ``%USERPROFILE%`` (Windows) or ``~`` (Linux)."""
    try:
        relative = Path(path).resolve().relative_to(home_dir().resolve())
    except (ValueError, OSError):
        return str(path)
    return profile_text(*relative.parts)


def read_json_file(path: Path, what: str) -> dict[str, Any]:
    """Read a local JSON file, raising NOT_FOUND/CONFIG ProviderErrors."""
    if not path.is_file():
        raise ProviderError(ProviderError.NOT_FOUND, f"{what} not found at {display_path(path)}.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        raise ProviderError(ProviderError.CONFIG, f"{what} at {display_path(path)} could not be read.") from None
    if not isinstance(data, dict):
        raise ProviderError(ProviderError.CONFIG, f"{what} at {display_path(path)} is not in the expected format.")
    return data


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to the same host and port over the same (or safer) scheme.

    urllib copies request headers (including the API key) onto the redirected
    request, so a redirect to another host or port could hand the key to a stranger.
    """

    @staticmethod
    def _effective_port(parts: urllib.parse.SplitResult) -> int | None:
        if parts.port is not None:
            return parts.port
        return 443 if parts.scheme == "https" else (80 if parts.scheme == "http" else None)

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl))
        same_host = (old.hostname or "").lower() == (new.hostname or "").lower()
        scheme_ok = new.scheme == old.scheme or (old.scheme == "http" and new.scheme == "https")
        old_port = self._effective_port(old)
        new_port = self._effective_port(new)
        port_ok = (old_port == new_port) or (
            old.scheme == "http" and new.scheme == "https" and old.port is None and new.port is None
        )
        if not (same_host and scheme_ok and port_ok):
            raise ProviderError(
                ProviderError.BAD_RESPONSE,
                f"{old.hostname} redirected to a different address, which is not followed for safety.",
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirectHandler)


def open_url(request: urllib.request.Request, timeout: float):
    """Open ``request``. Tests replace this function to stay offline."""
    return _OPENER.open(request, timeout=timeout)


def check_url(url: str) -> urllib.parse.SplitResult:
    """Validate a URL: http/https only, plain http only for this computer."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        raise ProviderError(ProviderError.CONFIG, "The URL is not valid.") from None
    if parts.scheme not in ("http", "https") or not host:
        raise ProviderError(ProviderError.CONFIG, "The URL must start with https:// and include a host name.")
    if parts.scheme == "http" and host.lower() not in LOCAL_HOSTS:
        raise ProviderError(
            ProviderError.CONFIG,
            f"Plain http:// is only allowed for localhost. Use https:// for {host}.",
        )
    return parts


def http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_RESPONSE_BYTES,
    auth_message: str = "",
) -> bytes:
    """Make one HTTP call and return the reply body.

    Every failure becomes a ``ProviderError`` with a plain message. The reply
    body of a failed call is never read, and no header value or exception
    text is ever copied into the message.
    """
    parts = check_url(url)
    host = parts.hostname or "the server"
    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    all_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=all_headers, method=method)

    network_message = f"Could not reach {host}. Check your internet connection or the URL."
    try:
        with open_url(request, timeout) as response:
            raw = response.read(max_bytes + 1)
    except ProviderError:
        raise
    except urllib.error.HTTPError as exc:
        code = exc.code
        try:
            exc.close()
        except Exception:  # noqa: BLE001
            pass
        if code in (401, 403):
            raise ProviderError(
                ProviderError.AUTH, auth_message or f"The service rejected the key (HTTP {code})."
            ) from None
        if code == 429:
            raise ProviderError(
                ProviderError.RATE_LIMITED,
                f"{host} is limiting how often usage can be checked (HTTP 429). Try again in a few minutes.",
            ) from None
        if code == 404:
            raise ProviderError(ProviderError.BAD_RESPONSE, "The service returned HTTP 404. Check the URL.") from None
        raise ProviderError(ProviderError.BAD_RESPONSE, f"The service returned HTTP {code}.") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
        raise ProviderError(ProviderError.NETWORK, network_message) from None
    except ValueError:
        raise ProviderError(ProviderError.CONFIG, "The URL is not valid.") from None

    if len(raw) > max_bytes:
        raise ProviderError(ProviderError.BAD_RESPONSE, "The reply was larger than 1 MB, so it was ignored.")
    return raw


def parse_json(raw: bytes) -> Any:
    """Decode a JSON reply, raising BAD_RESPONSE when it is not JSON."""
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ProviderError(ProviderError.BAD_RESPONSE, "The reply was not JSON.") from None


def http_json(url: str, **kwargs: Any) -> Any:
    """``http_request`` followed by ``parse_json``."""
    return parse_json(http_request(url, **kwargs))


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def get_path(data: Any, path: str) -> Any:
    """Follow a dot path such as ``data.items.0.used`` through dicts/lists."""
    current = data
    for part in [p for p in path.strip().split(".") if p != ""]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            index = int(part)
            if -len(current) <= index < len(current):
                current = current[index]
            else:
                raise ProviderError(ProviderError.BAD_RESPONSE, f"The reply has no value at '{path}'.")
        else:
            raise ProviderError(ProviderError.BAD_RESPONSE, f"The reply has no value at '{path}'.")
    return current


def to_number(value: Any, what: str) -> float:
    """Turn an int/float or numeric string into a float, else BAD_RESPONSE."""
    if isinstance(value, bool):
        raise ProviderError(ProviderError.BAD_RESPONSE, f"The value at '{what}' is not a number.")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(",", ""))
        except ValueError:
            raise ProviderError(ProviderError.BAD_RESPONSE, f"The value at '{what}' is not a number.") from None
    else:
        raise ProviderError(ProviderError.BAD_RESPONSE, f"The value at '{what}' is not a number.")
    if number != number or number in (float("inf"), float("-inf")):
        raise ProviderError(ProviderError.BAD_RESPONSE, f"The value at '{what}' is not a number.")
    return number


def setting_number(settings: dict[str, Any], key: str, label: str, default: float | None = None) -> float | None:
    """Read an optional number setting typed by the user (CONFIG on junk)."""
    raw = settings.get(key, "")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        number = float(str(raw).strip().replace(",", ""))
    except ValueError:
        raise ProviderError(ProviderError.CONFIG, f"{label} must be a number.") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise ProviderError(ProviderError.CONFIG, f"{label} must be a number.")
    return number


def clamp_percent(value: float) -> float:
    """Round to one decimal and clamp to the 0-100 range."""
    return round(min(100, max(0, value)), 1)


def epoch_to_iso(value: Any) -> str | None:
    """Convert a Unix epoch (int/float/digit string) to an ISO-8601 UTC string."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            text = value.strip()
            if not text.isdigit():
                return text or None
            value = float(text)
        moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text: str) -> str:
    """Make a short stable id from a title ('Current session' -> 'current-session')."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned or "reading"


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
def make_row(instance: "ProviderInstance", model_id: str, model_label: str, **values: Any) -> UsageRow:
    """Create a row stamped with the instance's id, label and kind."""
    return UsageRow(
        provider_id=instance.instance_id,
        provider_label=(instance.label or instance.kind).upper(),
        model_id=model_id,
        model_label=model_label,
        provider_kind=instance.kind,
        **values,
    )


def error_row(instance: "ProviderInstance", message: str) -> UsageRow:
    """The single row shown when a provider could not be read."""
    return make_row(
        instance,
        model_id="error",
        model_label="Could not read usage",
        status="error",
        reason=message,
    )


def money_text(amount: float, currency: str = "USD") -> str:
    """Format an amount with a currency symbol ('$1.50', '€2.00', '3.00 CHF')."""
    symbols = {"USD": "$", "$": "$", "EUR": "€", "€": "€", "GBP": "£", "£": "£", "JPY": "¥", "CNY": "¥", "¥": "¥"}
    code = (currency or "USD").strip()
    symbol = symbols.get(code.upper()) or symbols.get(code)
    if symbol:
        return f"{symbol}{amount:,.2f}"
    return f"{amount:,.2f} {code}"
