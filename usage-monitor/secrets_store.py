"""Credential store for Usage Monitor API keys.

Keys are kept in Windows Credential Manager through the ``keyring`` package,
one entry per provider instance:

* service  = ``SERVICE_NAME`` ("DesktopWidgets.UsageMonitor")
* username = the provider instance id (8 hex characters)
* password = the key itself

Nothing in this module logs or prints a value. Errors raised by keyring are
wrapped in ``SecretStoreError`` whose message is plain English and never
contains the secret.
"""

from __future__ import annotations

import re

import keyring
import keyring.errors

SERVICE_NAME: str = "DesktopWidgets.UsageMonitor"

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


def _check_instance_id(instance_id: str) -> None:
    """Reject empty or non-string ids before they reach keyring."""
    if not isinstance(instance_id, str) or not instance_id.strip():
        raise SecretStoreError("A provider id is needed to store its key.")


def set_secret(instance_id: str, value: str) -> None:
    """Save ``value`` as the key for ``instance_id`` (replaces any old one)."""
    _check_instance_id(instance_id)
    if not isinstance(value, str) or not value:
        raise SecretStoreError("The key is empty, so nothing was saved.")
    try:
        keyring.set_password(SERVICE_NAME, instance_id, value)
    except Exception as exc:  # noqa: BLE001 - every backend error is wrapped
        raise SecretStoreError(
            "Could not save the key in Windows Credential Manager "
            f"({type(exc).__name__})."
        ) from None


def get_secret(instance_id: str) -> str | None:
    """Return the saved key for ``instance_id``, or None when there is none."""
    _check_instance_id(instance_id)
    try:
        value = keyring.get_password(SERVICE_NAME, instance_id)
    except Exception as exc:  # noqa: BLE001
        raise SecretStoreError(
            "Could not read the key from Windows Credential Manager "
            f"({type(exc).__name__})."
        ) from None
    return value or None


def delete_secret(instance_id: str) -> None:
    """Delete the saved key for ``instance_id``. No error if there is none."""
    _check_instance_id(instance_id)
    try:
        keyring.delete_password(SERVICE_NAME, instance_id)
    except keyring.errors.PasswordDeleteError:
        return  # nothing was stored, which is fine
    except Exception as exc:  # noqa: BLE001
        raise SecretStoreError(
            "Could not delete the key from Windows Credential Manager "
            f"({type(exc).__name__})."
        ) from None


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
