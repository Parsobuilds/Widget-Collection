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
contains the secret; the original backend error is dropped instead of being
kept in the exception chain, because a backend message can hold the key.

The module can also answer "which ids does the key store hold for us?" on
Linux (``list_service_instance_ids``). The ``keyring`` package has no portable
list API, so the Secret Service is asked directly through ``secretstorage``,
the library the keyring Secret Service backend itself uses.
"""

from __future__ import annotations

import re
import sys
from typing import Any, NoReturn

import keyring
import keyring.errors

SERVICE_NAME: str = "DesktopWidgets.UsageMonitor"

IS_WINDOWS: bool = sys.platform == "win32"

# What the user sees (Settings window) when keys cannot be saved on this computer.
if IS_WINDOWS:
    NO_KEY_STORE_MESSAGE: str = "Windows Credential Manager is not available, so keys cannot be saved."
    LOCKED_KEY_STORE_MESSAGE: str = (
        "Windows Credential Manager is locked, so keys cannot be saved. Unlock it and try again."
    )
else:
    NO_KEY_STORE_MESSAGE: str = (
        "No key store is available on this computer, so keys cannot be saved. "
        "Install gnome-keyring or another Secret Service provider."
    )
    LOCKED_KEY_STORE_MESSAGE: str = (
        "Your key store is locked, so keys cannot be saved. Unlock your key store and try again."
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
# A key with a well-known prefix (an OpenAI, GitHub, Slack or Google key, a
# JWT). The shape is enough to recognise it, the value is never needed.
_KEY_PREFIX_PATTERN = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_\-]{8,}|sk-ant-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{16,}"
    r"|gho_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|xox[baprs]-[A-Za-z0-9\-]{8,}"
    r"|AKIA[0-9A-Z]{12,}|AIza[A-Za-z0-9_\-]{20,}"
    r"|eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,})"
)
# A token with digits and a run of at least four capital letters: that is the
# shape of many API keys ("FAKE-KEY-0000"), and it is not the shape of an
# ordinary name.
_CAPS_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*[0-9])(?=[A-Za-z0-9_\-]*[A-Z]{4})"
    r"[A-Za-z0-9_\-]{8,}(?![A-Za-z0-9_\-])"
)
# A long random-looking token (20 characters or more): only a key is that
# long, and ordinary names ("my-service-2024", "claude-sonnet-4-5") are not.
_LONG_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*[0-9])(?=[A-Za-z0-9_\-]*[A-Za-z])"
    r"[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9_\-])"
)

# The answer of the last key store check, remembered so the app does not ask
# the key store over and over. On Linux every check is a Secret Service round
# trip and a locked key ring can answer with an unlock prompt, so the answer
# is asked once and kept for the life of the process.
_probe_backend: object | None = None
_probe_result: str = ""


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


def no_key_store() -> bool:
    """True when there is no key store at all, so nothing could have been saved."""
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001
        return True
    return _backend_is_unusable(backend)


def _is_locked_error(exc: BaseException) -> bool:
    """True when the key store says it is there but locked.

    The Secret Service backend raises ``KeyringLocked`` when the collection
    could not be unlocked, which is a different problem from "there is no key
    store on this computer".
    """
    locked_type = getattr(keyring.errors, "KeyringLocked", None)
    if locked_type is not None and isinstance(exc, locked_type):
        return True
    name = type(exc).__name__.lower()
    if "locked" in name or "dismissed" in name:
        return True
    text = str(exc).lower()
    return "locked" in text or "unlock" in text


def _problem_for_error(exc: BaseException) -> str:
    """The plain-English reason for a key store error (locked or missing)."""
    if not IS_WINDOWS and _is_locked_error(exc):
        return LOCKED_KEY_STORE_MESSAGE
    return NO_KEY_STORE_MESSAGE


def reset_key_store_probe() -> None:
    """Forget the remembered key store answer, so the next check asks again."""
    global _probe_backend, _probe_result
    _probe_backend = None
    _probe_result = ""


def key_store_problem() -> str:
    """Return "" when keys can be saved here, else a plain-English reason.

    Checks the active keyring backend, then asks it for an entry that does not
    exist: a backend that errors on use (locked or unreachable) fails that
    probe. On Windows the real Credential Manager always passes.

    The answer is remembered for the life of the process (one Secret Service
    round trip instead of one per form). Use ``reset_key_store_probe`` to ask
    again.
    """
    global _probe_backend, _probe_result
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001
        return NO_KEY_STORE_MESSAGE
    if backend is _probe_backend:
        return _probe_result
    if _backend_is_unusable(backend):
        problem = NO_KEY_STORE_MESSAGE
    else:
        try:
            keyring.get_password(SERVICE_NAME, _PROBE_ID)
            problem = ""
        except Exception as exc:  # noqa: BLE001 - any error on use means "not usable"
            problem = _problem_for_error(exc)
    _probe_backend = backend
    _probe_result = problem
    return problem


def key_store_available() -> bool:
    """True when ``set_secret`` can be expected to work."""
    return not key_store_problem()


def _failure(action: str, exc: BaseException) -> SecretStoreError:
    """Build the error for a failed keyring call without echoing any value."""
    if not IS_WINDOWS:
        return KeyStoreUnavailableError(_problem_for_error(exc))
    return SecretStoreError(f"Could not {action} the key in {STORE_NAME} ({type(exc).__name__}).")


def _raise_clean(error: SecretStoreError) -> NoReturn:
    """Raise ``error`` with no exception chain behind it.

    This must be called *after* the ``except`` block that caught the backend
    error: a backend message can hold the key, so it must not stay in the
    chain (``__context__``) of the error the user sees.
    """
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    raise error


def _check_instance_id(instance_id: str) -> None:
    """Reject empty or non-string ids before they reach keyring."""
    if not isinstance(instance_id, str) or not instance_id.strip():
        raise SecretStoreError("A provider id is needed to store its key.")


def _refuse_if_no_store() -> None:
    """Raise ``KeyStoreUnavailableError`` when the active backend keeps nothing."""
    unusable = True
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001
        unusable = True
    else:
        unusable = _backend_is_unusable(backend)
    if unusable:
        _raise_clean(KeyStoreUnavailableError(NO_KEY_STORE_MESSAGE))


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
    error: SecretStoreError | None = None
    try:
        keyring.set_password(SERVICE_NAME, instance_id, value)
    except Exception as exc:  # noqa: BLE001 - every backend error is wrapped
        error = _failure("save", exc)
        # The backend frames hold the key: drop them with the traceback.
        exc.__traceback__ = None
    if error is not None:
        _raise_clean(error)


def get_secret(instance_id: str) -> str | None:
    """Return the saved key for ``instance_id``, or None when there is none."""
    _check_instance_id(instance_id)
    _refuse_if_no_store()
    error: SecretStoreError | None = None
    value: str | None = None
    try:
        value = keyring.get_password(SERVICE_NAME, instance_id)
    except Exception as exc:  # noqa: BLE001
        error = _failure("read", exc)
        exc.__traceback__ = None
    if error is not None:
        _raise_clean(error)
    return value or None


def delete_secret(instance_id: str) -> bool:
    """Delete the saved key for ``instance_id``. No error if there is none.

    Returns True when a key was really removed and False when there was
    nothing to remove. With no key store at all nothing could have been
    saved, so that is quiet too.
    """
    _check_instance_id(instance_id)
    try:
        _refuse_if_no_store()
    except KeyStoreUnavailableError:
        return False
    error: SecretStoreError | None = None
    removed = False
    try:
        keyring.delete_password(SERVICE_NAME, instance_id)
        removed = True
    except keyring.errors.PasswordDeleteError:
        removed = False  # nothing was stored, which is fine
    except Exception as exc:  # noqa: BLE001
        error = _failure("delete", exc)
        exc.__traceback__ = None
    if error is not None:
        _raise_clean(error)
    return removed


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
    """Delete saved keys for every id in ``instance_ids``.

    Returns the number of keys that were really removed: an id that held no
    key does not count.
    """
    count = 0
    first_error: SecretStoreError | None = None
    for instance_id in instance_ids:
        if isinstance(instance_id, str) and instance_id.strip():
            try:
                if delete_secret(instance_id):
                    count += 1
            except SecretStoreError as exc:
                if first_error is None:
                    first_error = exc
    if raise_on_error and first_error is not None:
        raise first_error
    return count


# ---------------------------------------------------------------- listing the service
def _secretstorage_module() -> Any | None:
    """The ``secretstorage`` library, or None when it is not installed.

    It is the library the keyring Secret Service backend itself uses, so it is
    the way to ask the key store "what do you hold for us?".
    """
    try:
        import secretstorage
    except Exception:  # noqa: BLE001 - the library is optional
        return None
    return secretstorage


def can_list_service_items() -> bool:
    """True when this computer can list the items of the key store service."""
    return _secretstorage_module() is not None


def _default_collection(module: Any, bus: Any) -> Any:
    """The default collection of the Secret Service."""
    getter = getattr(module, "get_default_collection", None)
    if getter is None:  # older layouts keep it in the collection module
        getter = module.collection.get_default_collection
    return getter(bus)


def list_service_instance_ids() -> list[str] | None:
    """Return the ids the key store holds for ``SERVICE_NAME``.

    The ``keyring`` package has no portable "list" API, so on Linux the Secret
    Service is asked directly. Returns None when the store cannot be listed
    (no Secret Service, a locked collection, no permission): the caller then
    knows it cannot promise that nothing is left behind.
    """
    module = _secretstorage_module()
    if module is None:
        return None
    try:
        bus = module.dbus_init()
        collection = _default_collection(module, bus)
        items = collection.search_items({"service": SERVICE_NAME})
    except Exception:  # noqa: BLE001 - any problem means "cannot be listed"
        return None
    ids: list[str] = []
    for item in items:
        try:
            attributes = item.get_attributes()
        except Exception:  # noqa: BLE001 - skip an item we cannot read
            continue
        username = attributes.get("username") if hasattr(attributes, "get") else None
        if isinstance(username, str) and username.strip():
            ids.append(username)
    return ids


def redact(text: str, *secrets: str | None) -> str:
    """Return ``text`` with every secret and credential-looking part masked.

    Every non-empty value in ``secrets`` is replaced by ``***`` (longest
    first, so a key that contains another key is fully masked), and so is
    anything shaped like ``Bearer <token>``, ``x-api-key: <key>``, a key with
    a well-known prefix or a long word that mixes letters and digits.
    """
    if not isinstance(text, str):
        text = str(text)
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "***")
    text = _BEARER_PATTERN.sub(lambda m: f"{m.group(1)} ***", text)
    text = _HEADER_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _KEY_PREFIX_PATTERN.sub("***", text)
    text = _CAPS_TOKEN_PATTERN.sub("***", text)
    text = _LONG_TOKEN_PATTERN.sub("***", text)
    return text
