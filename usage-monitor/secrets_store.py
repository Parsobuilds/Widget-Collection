"""Credential store for Usage Monitor API keys.

Keys are kept by the operating system through the ``keyring`` package: Windows
Credential Manager on Windows, the Secret Service (gnome-keyring, KWallet and
friends) on Linux. One entry per provider instance:

* service  = ``SERVICE_NAME`` ("DesktopWidgets.UsageMonitor")
* username = the provider instance id (8 hex characters)
* password = the key itself

Nothing in this module logs or prints a value, and a key is never written to a
file: when no key store is usable, saving is refused with a plain-English
message (``NO_KEY_STORE_MESSAGE``) instead of falling back to a file. Errors
raised by keyring are wrapped in ``SecretStoreError`` whose message never
contains the secret.
"""

from __future__ import annotations

import re
import sys

import keyring
import keyring.errors

SERVICE_NAME: str = "DesktopWidgets.UsageMonitor"

IS_WINDOWS: bool = sys.platform == "win32"

# What the user sees (Settings window) when keys cannot be saved on this computer.
if IS_WINDOWS:
    NO_KEY_STORE_MESSAGE: str = "Windows Credential Manager is not available, so keys cannot be saved."
else:
    NO_KEY_STORE_MESSAGE: str = (
        "No key store is available on this computer, so keys cannot be saved. "
        "Install gnome-keyring or another Secret Service provider."
    )

# Where keys live, for sentences such as "Saved in <store>".
STORE_NAME: str = "Windows Credential Manager" if IS_WINDOWS else "your system key store"

# Entry name used only to ask the store "are you working?" (it never exists).
_PROBE_ID: str = "key-store-probe"

# Patterns that look like a credential inside free text (for example an
# exception message that echoes a request header). ``redact`` masks them.
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer)\s+[^\s,;\"']+")
_HEADER_PATTERN = re.compile(
    r"(?i)\b(x-api-key|api[-_]?key|authorization)(\s*[:=]\s*)(?!bearer\b)[^\s,;\"']+"
)


class SecretStoreError(Exception):
    """Raised when the credential store cannot be used.

    The message is plain English and never contains the secret value.
    """


class KeyStoreUnavailableError(SecretStoreError):
    """Raised on Linux when there is no usable key store (see ``NO_KEY_STORE_MESSAGE``)."""


# ---------------------------------------------------------------- key store check
def _backend_is_unusable(backend: object, depth: int = 0) -> bool:
    """True when ``backend`` is one of keyring's "nothing here" backends.

    That is the ``fail`` backend (keyring's answer when no real backend can be
    loaded), the ``null`` backend (accepts a key and silently throws it away)
    and a chainer with no usable backend inside it.
    """
    from keyring.backends import chainer, fail, null

    if isinstance(backend, (fail.Keyring, null.Keyring)):
        return True
    if isinstance(backend, chainer.ChainerBackend) and depth < 3:
        try:
            inner = list(backend.backends)
        except Exception:  # noqa: BLE001 - a chainer that cannot list is unusable
            return True
        return not any(not _backend_is_unusable(item, depth + 1) for item in inner)
    return False


def key_store_problem() -> str:
    """Return "" when keys can be saved here, else a plain-English reason.

    Checks the active keyring backend, then asks it for an entry that does not
    exist: a backend that errors on use (locked or unreachable) fails that
    probe. On Windows the real Credential Manager always passes.
    """
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001
        return NO_KEY_STORE_MESSAGE
    if _backend_is_unusable(backend):
        return NO_KEY_STORE_MESSAGE
    try:
        keyring.get_password(SERVICE_NAME, _PROBE_ID)
    except Exception:  # noqa: BLE001 - any error on use means "not usable"
        return NO_KEY_STORE_MESSAGE
    return ""


def key_store_available() -> bool:
    """True when ``set_secret`` can be expected to work."""
    return not key_store_problem()


def _failure(action: str, exc: BaseException) -> SecretStoreError:
    """Build the error for a failed keyring call without echoing any value."""
    if not IS_WINDOWS:
        return KeyStoreUnavailableError(NO_KEY_STORE_MESSAGE)
    return SecretStoreError(f"Could not {action} the key in {STORE_NAME} ({type(exc).__name__}).")


def _check_instance_id(instance_id: str) -> None:
    """Reject empty or non-string ids before they reach keyring."""
    if not isinstance(instance_id, str) or not instance_id.strip():
        raise SecretStoreError("A provider id is needed to store its key.")


def _refuse_if_no_store() -> None:
    """Raise ``KeyStoreUnavailableError`` when the active backend keeps nothing."""
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001
        raise KeyStoreUnavailableError(NO_KEY_STORE_MESSAGE) from None
    if _backend_is_unusable(backend):
        raise KeyStoreUnavailableError(NO_KEY_STORE_MESSAGE)


# ---------------------------------------------------------------- the store
def set_secret(instance_id: str, value: str) -> None:
    """Save ``value`` as the key for ``instance_id`` (replaces any old one).

    Raises ``SecretStoreError`` (``KeyStoreUnavailableError`` when there is no
    usable key store) and never writes the key anywhere else.
    """
    _check_instance_id(instance_id)
    if not isinstance(value, str) or not value:
        raise SecretStoreError("The key is empty, so nothing was saved.")
    _refuse_if_no_store()
    try:
        keyring.set_password(SERVICE_NAME, instance_id, value)
    except Exception as exc:  # noqa: BLE001 - every backend error is wrapped
        raise _failure("save", exc) from None


def get_secret(instance_id: str) -> str | None:
    """Return the saved key for ``instance_id``, or None when there is none."""
    _check_instance_id(instance_id)
    _refuse_if_no_store()
    try:
        value = keyring.get_password(SERVICE_NAME, instance_id)
    except Exception as exc:  # noqa: BLE001
        raise _failure("read", exc) from None
    return value or None


def delete_secret(instance_id: str) -> None:
    """Delete the saved key for ``instance_id``. No error if there is none.

    With no key store at all nothing could have been saved, so that is quiet too.
    """
    _check_instance_id(instance_id)
    try:
        _refuse_if_no_store()
    except KeyStoreUnavailableError:
        return
    try:
        keyring.delete_password(SERVICE_NAME, instance_id)
    except keyring.errors.PasswordDeleteError:
        return  # nothing was stored, which is fine
    except Exception as exc:  # noqa: BLE001
        raise _failure("delete", exc) from None


def has_secret(instance_id: str) -> bool:
    """True when a key is saved for ``instance_id``."""
    try:
        return get_secret(instance_id) is not None
    except SecretStoreError:
        return False


def delete_all_secrets(
    instance_ids: list[str] | tuple[str, ...],
    *,
    raise_on_error: bool = False,
) -> int:
    """Delete saved keys for every id in ``instance_ids``. Returns count deleted."""
    count = 0
    first_error: SecretStoreError | None = None
    for instance_id in instance_ids:
        if isinstance(instance_id, str) and instance_id.strip():
            try:
                delete_secret(instance_id)
                count += 1
            except SecretStoreError as exc:
                if first_error is None:
                    first_error = exc
    if raise_on_error and first_error is not None:
        raise first_error
    return count


def redact(text: str, *secrets: str | None) -> str:
    """Return ``text`` with every secret and credential-looking part masked.

    Every non-empty value in ``secrets`` is replaced by ``***`` (longest
    first, so a key that contains another key is fully masked), and so is
    anything shaped like ``Bearer <token>`` or ``x-api-key: <key>``.
    """
    if not isinstance(text, str):
        text = str(text)
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "***")
    text = _BEARER_PATTERN.sub(lambda m: f"{m.group(1)} ***", text)
    text = _HEADER_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    return text
