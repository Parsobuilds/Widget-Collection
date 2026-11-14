# Usage Monitor (developer notes)

A PySide6 desktop card that shows usage limits and credit balances for the AI services and
budgets the user adds in its Settings window. End-user documentation is in the top-level
`README.md`; endpoint details are in [`SOURCES.md`](SOURCES.md) and the module interface in
[`INTERFACE.md`](INTERFACE.md).

## Layout

| File | Purpose |
|---|---|
| `usage_monitor.py` | The card: painting, tabs, favourites, tray menu, desktop pinning, polling thread |
| `settings_window.py` | Settings window: provider list, Add/Edit form, Detect, Test |
| `provider_config.py` | `%APPDATA%\DesktopWidgets\UsageMonitor\config.json` (providers, no secrets) |
| `secrets_store.py` | API keys in Windows Credential Manager (via `keyring`) |
| `providers\` | One module per provider kind plus `registry.py` |
| `cache.py`, `models.py` | Last good readings and the row dataclasses |

The card's own position, size, tab and favourites are kept in `widget_state.json` next to
`config.json`.

## Run from source

From the repository root, with the build environment created by `python build.py`:

```
.build-venv\Scripts\python.exe usage-monitor\usage_monitor.py
.build-venv\Scripts\python.exe usage-monitor\usage_monitor.py --selftest
```

`--selftest` reads the configured providers once, prints a table and saves
`selftest_card.png` into the data folder.

## Tests

```
cd usage-monitor
..\.build-venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

The tests are offline: HTTP replies are mocked, keyring is an in-memory fake and `APPDATA`
points at a temporary folder.
