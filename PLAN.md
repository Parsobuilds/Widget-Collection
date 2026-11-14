# Widget Collection: build plan

Goal: one installer, `dist\DesktopWidgets-Setup-1.0.0.exe`, with three tick-box components
(To-Do Widget, Usage Monitor, Desktop Monitor + "CPU temperature sensor" sub-option), clean first run,
providers added from a Settings window. Brief: the owner's prompt file `desktop-widgets-installer.md`.

Shared rules: [`CONVENTIONS.md`](CONVENTIONS.md). Usage Monitor interface: [`usage-monitor/INTERFACE.md`](usage-monitor/INTERFACE.md).

## Where the copies came from

| Folder | Source | Commit | Notes |
|---|---|---|---|
| `todo-widget\app` (packaging: `paths.py`, `setup_startup.py`, `make_icon.py`, icons, sample list, `test_todo_widget.py`) | To-Do Widget repo (`todo-widget`) | `e2d0b89` | clean tree |
| `todo-widget\app` (live code: `todo_widget.py`, `goals_tab.py`, `goals_manager.py`, `todo_manager.py`, `glass_theme.py`, `snap_widgets.py`, goals tests) | parent workspace repo, `_shared\` | `5fc916a` + uncommitted working-tree edits to `todo_widget.py`, `glass_theme.py`, `snap_widgets.py` | the version the owner runs daily (it has the Goals tab; the packaged repo does not) |
| `usage-monitor\` | Usage Monitor repo (no remote), branch `feat/usage-monitor` | `b0cc028` + uncommitted edits to `collectors/antigravity.py`, `collectors/nanogpt.py`, `models.py`, `usage_monitor.py`, two tests | the running version |
| `desktop-monitor\` | Desktop Monitor repo (`Desktop_Monitor`) | `0734852` + uncommitted edits to `desktop_monitor.py`, `cpu_temp_helper.py` | the running version; `lib\lhm` not copied (the build downloads it) |

The originals are read-only for this job. Their pre-existing uncommitted edits were there before
this work started (snapshot taken 2026-11-14) and are left untouched.

## Decisions (made on the owner's behalf)

1. **To-Do source:** the daily-driver `_shared` code (with Goals) merged into the packaged
   `TodoWidget` layout (`paths.py`, per-user data folder), because the brief requires a Goals tab.
2. **No name collisions with the owner's running widgets:** new data folder
   `%APPDATA%\DesktopWidgets\<App>`, Run values `DesktopWidgets.<App>`, single-instance keys
   `DesktopWidgets.<App>`, task `DesktopWidgets CPU Temperature` (see `CONVENTIONS.md`).
3. **Usage Monitor config split:** `config.json` holds providers only; window state goes in
   `widget_state.json`, so the Settings window and the card never overwrite each other.
4. **Elevated helper lives in Program Files:** the scheduled task runs elevated at every sign-in,
   so its files go to `%ProgramFiles%\DesktopWidgets\CpuTempHelper` (admin-writable only), never
   the per-user install folder, which any normal process could change.
5. **CPU helper and Inno elevation:** per-user installer (`PrivilegesRequired=lowest`); the CPU
   temperature component runs one elevated step (`Verb: runas`) only when ticked.
6. **Shared Qt runtime:** all widget exes in one PyInstaller COLLECT folder sharing `_internal`,
   if the frozen apps pass their selftests that way; otherwise three folders (size noted here).
7. **LibreHardwareMonitorLib** is downloaded by `build.py` from its official release, pinned by
   version and SHA-256, so CI does not need the gitignored `lib\lhm`.
8. Modal and other cloud spend trackers: real billing API if one is documented and usable
   without bundling a large SDK, otherwise the `manual_budget` kind.
(more added as the work proceeds)

## Chunks

### Wave 1 (parallel, no shared files)

- [ ] **usage-providers** (owns `usage-monitor\models.py`, `cache.py`, `collectors\`, `providers\`, `secrets_store.py`, `provider_config.py`, `SOURCES.md`, `tests\` except `test_settings_window.py`)
  - [ ] registry, config, credential store exactly per `INTERFACE.md`
  - [ ] catalogue: claude_code, codex, antigravity (Detect), nanogpt (key), google_ai_studio_t3 (hidden without T3 DB), custom_json, manual_budget, plus any documented key-based service
  - [ ] Modal: billing API checked; manual budget otherwise
  - [ ] no hardcoded model list, no private ledger import, no hardcoded balances or caps
  - [ ] tests: registry, custom provider (mocked HTTP), secret storage (fake keyring), config round trip, old collector tests updated
- [ ] **usage-settings-ui** (owns `usage-monitor\settings_window.py`, `tests\test_settings_window.py`)
  - [ ] provider list with status, on/off switch, Remove
  - [ ] Add provider: catalogue picker, description, unofficial label, fields, Detect, Test, Save
  - [ ] custom provider form; manual budget form says it is manual
  - [ ] tests with a stub registry (offscreen Qt)
- [ ] **desktop-monitor** (owns everything in `desktop-monitor\`)
  - [ ] depersonalised; machine name from the computer; paths per `CONVENTIONS.md`
  - [ ] no NVIDIA / AMD / Intel GPU: sensible card, no crash, no error wall
  - [ ] Settings window: optional indicators (ALEXA-style feed off unless a file is set), drives, background
  - [ ] CPU helper: `CpuTempHelper` with `--install` / `--uninstall` (elevated) and run mode, Program Files location, new task name, PawnIO only if absent
  - [ ] `disk_alert.py` without chat webhooks or personal config
  - [ ] selftest passes; unit tests added
- [ ] **todo-widget** (owns `todo-widget\app\`)
  - [ ] merge Goals code into the packaged layout; data in `%APPDATA%\DesktopWidgets\TodoWidget`
  - [ ] empty list (or 2-3 neutral examples) and empty Goals on first run
  - [ ] no venv fallbacks, no personal names/paths; tests pass
- [ ] **deps-research** (read-only): licences and redistribution for LibreHardwareMonitorLib and PawnIO, PawnIO silent install, one optional elevated step in a per-user Inno installer, SignPath Foundation terms and GitHub Actions integration. Output: `docs\research\deps.md`.

### Wave 2

- [ ] **orchestrator:** wire registry + settings window into `usage-monitor\usage_monitor.py` (empty state "Add a provider", gear on card, tray Settings, `widget_state.json`, neutral names)
- [ ] **installer** (owns `build.py`, `installer\`, PyInstaller specs, `.github\workflows\`, `THIRD_PARTY_NOTICES.md`, `SIGNING.md`)
- [ ] **docs** (owns `README.md`)

### Wave 3 (orchestrator)

- [ ] full `python build.py`
- [ ] independent review: secrets storage/logging and everything done with admin rights
- [ ] Done-when checks 1-13
- [ ] push to private `Widget-Collection`, CI run

## Status log

- 2026-11-14: scouting done, private GitHub repo `smegsmearer/Widget-Collection` created (empty),
  sources copied, `.build-venv` created (Python 3.12, `requirements-build.txt`).
