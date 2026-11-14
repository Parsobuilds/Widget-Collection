# Shared conventions for the three widgets

Every worker follows these names exactly, so the installer, the uninstaller and the apps agree.
Nothing here may collide with the author's own source-run widgets, which use
`%APPDATA%\UsageMonitor`, `%APPDATA%\DesktopMonitor`, the legacy background folder (below), Run values
`UsageMonitor` / `DesktopMonitor`, and the scheduled task `DesktopMonitor CPU temp`.

## Product

- Product name: **Desktop Widgets**. Publisher string: `Desktop Widgets contributors`.
- Version: `1.0.0`.

## Per-user data folders (created on first run, never shipped pre-filled)

| App | Data folder |
|---|---|
| To-Do Widget | `%APPDATA%\DesktopWidgets\TodoWidget` |
| Usage Monitor | `%APPDATA%\DesktopWidgets\UsageMonitor` |
| Desktop Monitor | `%APPDATA%\DesktopWidgets\DesktopMonitor` |
| Shared background setting | `%APPDATA%\DesktopWidgets\widget_background.json` |

Always build these from `os.environ["APPDATA"]` (fall back to `Path.home() / "AppData" / "Roaming"`),
never from a literal path. Tests must point them at a temp folder (set `APPDATA` in the test or
monkeypatch the module's path function).

### Shared background setting

- Read and write `%APPDATA%\DesktopWidgets\widget_background.json`.
- If that file does not exist, **read** (never write) the legacy file
  `%APPDATA%\TomTodoList\widget_background.json` as a fallback. This is the one allowed mention of
  `TomTodoList` in each app. Name the constant `LEGACY_BG_CONFIG_PATH` so the search in the
  checks can find and justify it.
- JSON keys (unchanged from today): `image_path`, `blur_radius`, `tint_opacity`, `brightness`,
  `saturation`, `fit_mode`, `enabled`. Keep whatever keys the current code already reads.
- The file picker for choosing a background image opens in the user's Pictures folder
  (`QStandardPaths.PicturesLocation`), never a hardcoded folder.

## Executables, single-instance keys, startup entries

| App | Exe name | Single-instance key / mutex | HKCU Run value name |
|---|---|---|---|
| To-Do Widget | `TodoWidget.exe` | `DesktopWidgets.TodoWidget` | `DesktopWidgets.TodoWidget` |
| Usage Monitor | `UsageMonitor.exe` | `DesktopWidgets.UsageMonitor` | `DesktopWidgets.UsageMonitor` |
| Desktop Monitor | `DesktopMonitor.exe` | `DesktopWidgets.DesktopMonitor` | `DesktopWidgets.DesktopMonitor` |
| CPU temperature helper | `CpuTempHelper.exe` | `Local\DesktopWidgets.CpuTempHelper` | (scheduled task, see below) |

- The installer's "Start when I sign in" task writes the HKCU Run value above, pointing at the
  installed exe. Each app's own "Start with Windows" tray toggle writes and deletes the **same**
  value, using `sys.executable` when frozen (`getattr(sys, "frozen", False)`) and
  `pythonw.exe "<script>"` when run from source.
- Window titles: `To-Do Widget`, `Usage Monitor`, `Desktop Monitor`.

## CPU temperature helper

- Scheduled task name: `DesktopWidgets CPU Temperature`.
- Installed (by an elevated step, only when the installer's "CPU temperature sensor" box is ticked)
  to `%ProgramFiles%\DesktopWidgets\CpuTempHelper\` so that only administrators can change the
  files the elevated task runs. The task never points into the per-user install folder.
- Writes `%APPDATA%\DesktopWidgets\DesktopMonitor\cpu_temp.json` of the signed-in user
  (same JSON as today: `{"cpu_temp": float|null, "sensor": str|null, "time": epoch}`).
- LibreHardwareMonitorLib DLLs sit in a `lhm\` folder next to `CpuTempHelper.exe`.

## Frozen-app rules (all apps)

- Bundled read-only files: `resource_path(...)` based on `sys._MEIPASS` when frozen, else the
  script folder. Never write next to the exe.
- Logs go to the app's data folder, rotate at about 512 KB, and never contain secrets, tokens,
  API keys or full response bodies from authenticated endpoints.
- `--selftest` writes `selftest_card.png` into the app's data folder and prints its path,
  then exits 0. It must work with `APPDATA`, `LOCALAPPDATA` and `USERPROFILE` pointed at empty
  folders.
- No reference anywhere (code, comments, tests, docs) to the author's name, user name or PC
  name, the author's private folders or other projects, absolute `C:\Users\...` or `D:\` paths,
  private network addresses, chat webhooks, or the author's own balances or spending caps. The exact
  search list used by the release checks is kept outside this repository. Use
  `%USERPROFILE%`/`%APPDATA%` forms in docs and `os.environ`/`Path.home()` in code.

## Code style

Python 3.12, PEP 8, type hints, clear docstrings and comments (the owner is learning Python).
Tests use `unittest` (no pytest dependency), fake values and mocked HTTP. Never a real key or token.

## Linux

The Linux names (component keys, launchers, menu and autostart file names, lock files) are in the
table in `docs/LINUX_PORT.md`. Use that table; it is not copied here.

- **Data folder:** use `XDG_CONFIG_HOME` if it is set, otherwise `~/.config`, then add
  `DesktopWidgets/<App>`. Build it from `os.environ` and `Path.home()`, never a literal path. The
  shared background file is `DesktopWidgets/widget_background.json` there, with no legacy fallback.
- **Lock files:** single-instance locks use `fcntl.flock` on `desktopwidgets-<key>.lock` in
  `XDG_RUNTIME_DIR`, or in the app's data folder if that is not set.
- **Autostart entries:** each app owns only its own `desktopwidgets-<key>.desktop` file in
  `$XDG_CONFIG_HOME/autostart`. It writes and deletes that file and nothing else. The installer's
  `--autostart` option writes the same file.
- **Keys:** store them only through `keyring`, which uses the Secret Service (gnome-keyring, KWallet).
  If no key store is available, say so in plain English in Settings and refuse to save. Never write
  a key to a file.
- **Platform checks:** Linux-only code sits behind `sys.platform.startswith("linux")`. Windows-only
  imports (`winreg`, `wmi`, `win32*`, `pythoncom`, `ctypes.windll`) sit behind a `sys.platform == "win32"`
  check or inside a function, so every module still imports on Linux.
