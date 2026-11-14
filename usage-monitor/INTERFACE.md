# Usage Monitor provider interface (settled before wave 1)

Two workers build against this file at the same time:

- **usage-providers** owns `models.py`, `cache.py`, `collectors\`, `providers\` (new package),
  `secrets_store.py` (new), `provider_config.py` (new), `SOURCES.md`, and the tests for them.
- **usage-settings-ui** owns `settings_window.py` (new) and `tests\test_settings_window.py` (new).
- The orchestrator owns `usage_monitor.py` and wires both in afterwards.

Do not change a signature below without telling the orchestrator. Everything is importable with the
`usage-monitor` folder on `sys.path` (flat imports: `import provider_config`,
`from providers import registry`).

---

## 1. Config file

Path: `%APPDATA%\DesktopWidgets\UsageMonitor\config.json` (see `CONVENTIONS.md`).
It holds **only provider choices and non-secret settings**. Widget position/size/tab/favourites live
in a separate `widget_state.json` owned by `usage_monitor.py`, so the settings window and the card
never overwrite each other.

```json
{
  "version": 2,
  "poll_minutes": 5,
  "providers": [
    {
      "instance_id": "3f9a1c2e",
      "kind": "claude_code",
      "label": "Claude",
      "enabled": true,
      "settings": {"credentials_path": "%USERPROFILE%\\.claude\\.credentials.json"}
    },
    {
      "instance_id": "8d0b44aa",
      "kind": "custom_json",
      "label": "My service",
      "enabled": true,
      "settings": {
        "url": "https://example.com/api/usage",
        "method": "GET",
        "key_header": "Authorization",
        "key_prefix": "Bearer ",
        "used_path": "data.used",
        "limit_path": "data.limit",
        "balance_path": "data.balance",
        "unit": "USD"
      }
    }
  ]
}
```

A missing, empty or unreadable file means `{"version": 2, "poll_minutes": 5, "providers": []}`.
A first run therefore has **no providers**. Secrets never appear in this file.

### `provider_config.py`

```python
CONFIG_VERSION: int = 2

@dataclass
class ProviderInstance:
    instance_id: str          # 8 hex chars from uuid4, unique within the file
    kind: str                 # a ProviderKind.kind from the catalogue
    label: str                # what the card shows as the section header (upper-cased by the UI)
    enabled: bool = True
    settings: dict[str, Any] = field(default_factory=dict)   # non-secret values only

    def to_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderInstance": ...

def data_dir() -> Path                      # %APPDATA%\DesktopWidgets\UsageMonitor, created on demand
def config_path() -> Path                   # data_dir() / "config.json"
def load_config(path: Path | None = None) -> dict[str, Any]       # never raises; defaults on any error
def save_config(config: dict[str, Any], path: Path | None = None) -> None   # atomic (tmp + os.replace)
def list_instances(config: dict[str, Any]) -> list[ProviderInstance]
def new_instance_id(config: dict[str, Any]) -> str
def add_instance(config: dict[str, Any], instance: ProviderInstance) -> None       # appends, in place
def update_instance(config: dict[str, Any], instance: ProviderInstance) -> None    # replace by id
def remove_instance(config: dict[str, Any], instance_id: str) -> None
    # removes from config AND calls secrets_store.delete_secret(instance_id)
def set_enabled(config: dict[str, Any], instance_id: str, enabled: bool) -> None
```

`add_instance`/`update_instance` raise `ValueError` if `settings` contains a key whose
`FieldSpec.kind == "secret"` (defence in depth: secrets go to `secrets_store` only).

---

## 2. Credential store: `secrets_store.py`

Windows Credential Manager through the `keyring` package.

```python
SERVICE_NAME: str = "DesktopWidgets.UsageMonitor"

class SecretStoreError(Exception): ...      # message is plain English, never contains the secret

def set_secret(instance_id: str, value: str) -> None
def get_secret(instance_id: str) -> str | None
def delete_secret(instance_id: str) -> None   # no error if absent
def has_secret(instance_id: str) -> bool
def redact(text: str, *secrets: str | None) -> str
    # replaces every non-empty secret (and any "Bearer <...>" / "x-api-key: ..." pattern) with "***"
```

- `keyring` username = `instance_id`, service = `SERVICE_NAME`. One secret per provider instance.
- Nothing in this module logs or prints a value. Errors from keyring are wrapped in
  `SecretStoreError` with the secret redacted.
- Tests replace the backend with an in-memory fake (`keyring.set_keyring(...)` with a small
  `keyring.backend.KeyringBackend` subclass), never the real vault.

---

## 3. Providers: `providers\` package

```
providers\
    __init__.py
    base.py          # dataclasses + ProviderError + helpers below
    registry.py      # catalogue, fetch/test/collect entry points
    claude_code.py   # one module per kind (wrapping or replacing collectors\*.py logic)
    codex.py
    ...
```

### `providers\base.py`

```python
@dataclass
class FieldSpec:
    key: str                  # settings key; for kind == "secret" the value goes to secrets_store
    label: str                # shown next to the input
    kind: str = "text"        # "text" | "secret" | "url" | "number" | "path" | "choice"
    required: bool = True
    default: str = ""
    placeholder: str = ""
    help: str = ""            # one short line under the input
    choices: list[str] = field(default_factory=list)

@dataclass
class DetectResult:
    found: bool
    path: str = ""            # display form using %USERPROFILE%, never an expanded user path
    message: str = ""         # plain English, e.g. "Found your Claude Code sign-in."

@dataclass
class TestResult:
    ok: bool
    message: str              # plain English: reading summary on success, reason on failure
    rows: list[UsageRow] = field(default_factory=list)

class ProviderError(Exception):
    """Raised by a provider's fetch(). `code` is one of the constants below; `message` is shown to
    the user as-is, so it is plain English and contains no secret."""
    AUTH = "auth"                 # wrong/expired key or sign-in
    NETWORK = "network"           # no internet, DNS, timeout, connection refused
    NOT_FOUND = "not_found"       # sign-in file or local database missing
    RATE_LIMITED = "rate_limited"
    BAD_RESPONSE = "bad_response" # endpoint changed / JSON not as expected
    CONFIG = "config"             # a required setting is missing or invalid
    def __init__(self, code: str, message: str) -> None: ...

class ProviderKind:
    """Base class; one subclass instance per catalogue entry."""
    kind: str = ""                     # stable id stored in config, e.g. "claude_code"
    display_name: str = ""             # "Claude Code"
    category: str = ""                 # "signin" | "api_key" | "custom" | "budget"
    description: str = ""              # ONE sentence: what it needs from the user
    stability: str = "official"        # "official" | "unofficial" | "local" | "manual"
    stability_note: str = ""           # e.g. "Unofficial endpoint, may stop working."
    docs_url: str = ""
    fields: list[FieldSpec] = []       # what the Add window asks for (secret fields included)
    supports_detect: bool = False
    min_poll_seconds: int = 300

    def is_available(self) -> bool: return True     # False hides it from the catalogue
    def default_label(self) -> str: return self.display_name
    def default_settings(self) -> dict[str, Any]: ...   # FieldSpec defaults, non-secret only
    def detect(self) -> DetectResult: ...            # only when supports_detect
    def fetch(self, instance: ProviderInstance, secret: str | None) -> list[UsageRow]:
        """One live reading. Raises ProviderError on any failure. Never logs the secret."""
```

### `providers\registry.py`

```python
def catalogue() -> list[ProviderKind]               # available kinds only, display order
def all_kinds() -> list[ProviderKind]               # including unavailable (for tests)
def get_kind(kind: str) -> ProviderKind | None

def fetch_instance(instance: ProviderInstance) -> list[UsageRow]
    """Reads the secret from secrets_store and calls kind.fetch(). Never raises: on
    ProviderError (or any exception) returns ONE row with status="error" and
    reason=<plain-English message>, provider_id=instance.instance_id."""

def test_instance(instance: ProviderInstance, secret_override: str | None = None) -> TestResult
    """One live call for the Test button. Uses secret_override when given (the Add window tests
    a key before saving it), else the stored secret. Bypasses the cache. Never raises."""

def collect_enabled(config: dict[str, Any], cache: UsageCache | None = None,
                    force: bool = False) -> tuple[list[UsageRow], bool, str]
    """For every enabled instance whose kind is available: fetch through the cache
    (keyed by instance_id, min interval = kind.min_poll_seconds unless force). Returns
    (rows, any_stale, "HH:MM"). No configured providers -> ([], False, stamp)."""
```

### Rows

`models.UsageRow` keeps its fields and adds `provider_kind: str = ""`. For a provider instance:

- `provider_id` = `instance.instance_id`; `provider_label` = `instance.label.upper()`;
  `provider_kind` = `instance.kind`.
- `model_id` / `model_label` come **from the provider's reply** (for example Claude's `limits`
  list: "Current session", "All models"; Codex's primary/secondary window; Antigravity's
  per-model `displayName`; a custom provider's single row named after the instance). No
  hardcoded model list anywhere. `T3_PROVIDER_MODELS` is deleted.
- Money readings set `credit_balance_usd` / `credit_spent_usd` / `credit_cap_usd`.
  `models.build_credit_rows(rows)` becomes generic: one `CreditRow` per provider instance that
  has any money field set, with no provider ids hardcoded.

### Catalogue (first draft, the providers worker confirms each)

| kind | category | source | stability |
|---|---|---|---|
| `claude_code` | signin + Detect | `%USERPROFILE%\.claude\.credentials.json`, `/api/oauth/usage` | unofficial |
| `codex` | signin + Detect | `%USERPROFILE%\.codex\auth.json` (or `%CODEX_HOME%`), `wham/usage` | unofficial |
| `antigravity` | signin + Detect | token file per `SOURCES.md`; hidden when no token file can exist | unofficial |
| `nanogpt` | api_key | `POST https://nano-gpt.com/api/check-balance`, header `x-api-key` | official if documented |
| `google_ai_studio_t3` | local | T3 Code database; `is_available()` False when the DB is absent | local |
| `custom_json` | custom | user URL, header, prefix, JSON paths | user-defined |
| `manual_budget` | budget | user types monthly credit, cap and spent-so-far | manual |
| new key-based kinds | api_key | only if the endpoint and reply are confirmed in official docs | official |

`custom_json` paths use dots and integer indexes (`data.items.0.used`). `used`+`limit` gives a
percent bar; `balance` alone gives a money/credit row; `unit` is `"USD"` (money fields) or any
other text (shown after the numbers). Only `http`/`https` URLs. `http://` is allowed only for
`localhost` / `127.0.0.1` (and shown with a warning otherwise refused).

---

## 4. What the settings window can rely on

```python
from providers import registry
from providers.base import ProviderKind, FieldSpec, DetectResult, TestResult
import provider_config, secrets_store
```

- Build the Add form from `kind.fields`; `kind == "secret"` fields use a password-echo
  `QLineEdit` and are saved with `secrets_store.set_secret(instance_id, value)`; everything else
  goes in `instance.settings`.
- Show `kind.description`, and `kind.stability_note` in amber when `stability == "unofficial"`,
  plus a plain "Manual: you type these numbers yourself" line when `stability == "manual"`.
- Detect button only when `kind.supports_detect`.
- Test button: `registry.test_instance(instance, secret_override=<typed key or None>)`, run off
  the UI thread (`QThreadPool`/`QRunnable` or a `QThread`), result message shown in the window.
- Network calls never run on the UI thread.

### Settings window API (used by `usage_monitor.py`)

```python
class SettingsWindow(QtWidgets.QDialog):
    providers_changed = QtCore.Signal()   # emitted after any add / remove / enable toggle / edit
    def __init__(self, parent: QtWidgets.QWidget | None = None,
                 config_path: Path | None = None) -> None: ...

def open_settings(parent: QtWidgets.QWidget | None = None) -> SettingsWindow
    """Shows the single shared window (creates it once, then raises/activates it)."""
```

The window loads `provider_config.load_config(config_path)` itself and saves after each change.
