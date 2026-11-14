# Linux port: spec for workers

Goal: the three widgets (To-Do Widget, Usage Monitor, Desktop Monitor) run on Linux with the same
features as on Windows, installed by one script that lets the user tick which widgets to install.
Windows behaviour must not change: the Windows unit tests must still pass and the Windows build
must still work. Work happens on branch `linux-port` of this repository.

## Test machine (real hardware, real desktop)

The Linux test machine is an Ubuntu desktop with GNOME on Wayland, an NVIDIA GPU, gnome-keyring and
libsecret. How to reach it (host, user, key, folders) is given in the orchestrator's brief and is
deliberately not written in this repository. Rules that apply to every worker:

- Copy code over with tar through ssh. Never put a GitHub token or any secret on that machine.
- Use a scratch folder named `dw_test/<name>` in the test user's home. Never touch other folders.
- To show a window on its real screen from an ssh command, pass the desktop session's variables
  (`XDG_RUNTIME_DIR`, `WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS`, `QT_QPA_PLATFORM=wayland`; also try
  `xcb`). For tests with no screen use `QT_QPA_PLATFORM=offscreen`.
- Install system packages with `sudo apt-get install -y ...` if needed and list each one in your report.

## Linux conventions (all apps)

| Thing | Linux rule |
|---|---|
| Data folder | `$XDG_CONFIG_HOME/DesktopWidgets/<App>` (default `~/.config/DesktopWidgets/<App>`); `<App>` is `TodoWidget`, `UsageMonitor`, `DesktopMonitor` |
| Shared background setting | `$XDG_CONFIG_HOME/DesktopWidgets/widget_background.json` (no legacy fallback on Linux) |
| Secrets | `keyring` (Secret Service / gnome-keyring). If no keyring backend is available, say so in plain English in Settings and refuse to save the key, never write it to a file |
| Autostart | `$XDG_CONFIG_HOME/autostart/desktopwidgets-<app>.desktop` (Freedesktop autostart), same toggle as the Windows tray "Start with Windows" (label "Start when I sign in") |
| Single instance | a lock file in `$XDG_RUNTIME_DIR` (fall back to the data folder) named `desktopwidgets-<app>.lock`, using `fcntl.flock` |
| Frozen app | PyInstaller onedir; `sys._MEIPASS` rules the same as Windows |
| CPU temperature | `psutil.sensors_temperatures()` first (coretemp, k10temp, zenpower, cpu_thermal); no helper, no admin, no driver. Show a dash when nothing is available |
| GPU | NVIDIA through `nvidia-ml-py` (NVML) if it loads; otherwise sysfs/`/sys/class/drm` basics where available; otherwise show "No GPU readings" with no error wall |
| Fonts | no Segoe fonts. Use the Qt default sans family, and draw the gear icon with a painted path or a font glyph that exists everywhere (do not depend on a symbol font) |
| Desktop effects | no `ctypes.windll`, no DWM blur, no `win32gui`. Frameless translucent window via Qt flags. If always-on-bottom or snapping is not possible on Wayland, degrade quietly and document it |
| Notifications / tray | Qt `QSystemTrayIcon`. On GNOME the tray needs the AppIndicator extension; if the tray is not available the app must still work and offer Settings another way (right-click on the card header, or `--settings` flag) |

Platform checks: `sys.platform == "win32"` / `sys.platform.startswith("linux")`. Windows-only
imports (`winreg`, `wmi`, `win32*`, `pythoncom`, `ctypes.windll`) must sit behind a platform check or
inside a function so importing the module on Linux never fails.

Same rules as the Windows build: Python 3.12 style, type hints, PEP 8, comments for a learner,
`unittest` only, fake values and mocked HTTP, never a real key or token, no personal names or paths in
any file (the project sweep checks for them), `os.environ`/`Path.home()` instead of literal paths.

## Chunks and ownership (no two workers edit the same file)

| Worker | Owns | Done when |
|---|---|---|
| todo-linux | `todo-widget\app\` | tests pass on Windows (`.build-venv`) and on the test machine offscreen; app runs on the test machine's real desktop with an empty first run and Goals tab |
| usage-linux | `usage-monitor\` | same; Settings window, add a custom provider against a local test server, key lands in gnome-keyring and not in config or logs |
| desktop-linux | `desktop-monitor\` | same; real CPU/RAM/disk/GPU readings on the test machine (RTX 2060 SUPER), temperature from hwmon, Settings dialog works; no `cpu_temp_helper` on Linux |
| packaging-linux | `build.py`, `installer\linux\`, `.github\workflows\build-linux.yml`, `docs\LINUX_PORT.md` notes, `README` Linux section is left to the orchestrator | `python build.py --linux` (run on the test machine) freezes the apps and makes `dist/DesktopWidgets-1.0.0-linux-x86_64.tar.gz` plus `install.sh` that lets the user tick widgets and writes Start Menu `.desktop` entries and optional autostart; `uninstall.sh` removes program files and entries and asks before deleting user data |

Shared files (`CONVENTIONS.md`, `PLAN.md`, `THIRD_PARTY_NOTICES.md`, `README.md`) are edited only by
the orchestrator after the workers finish.

## Rules for every worker

- Do not commit and do not push. The orchestrator commits.
- Never edit anything outside your owned folders. Never touch the original source repositories
  (`TodoWidget`, `Usage_Monitor`, `Desktop_Monitor`, `_shared` in the parent workspace).
- Windows tests must keep passing: run each app's tests with `.build-venv\Scripts\python.exe -m unittest discover` from the app folder (check how the Windows build invokes them in `build.py`).
- Report: files changed, test counts on Windows and on the test machine, what you saw on the test machine's real screen
  (take a screenshot on the test machine with `gnome-screenshot` or `grim` if available, copy it back to your
  own scratch folder, and describe it), what does not work on Linux and why, and system packages added.

## Linux names (fixed by the orchestrator; every worker uses these exactly)

| App | Component key | Frozen binary (in the bundle folder) | Launcher in `~/.local/bin` | Menu entry and autostart file | Lock file |
|---|---|---|---|---|---|
| To-Do Widget | `todo` | `TodoWidget` | `desktopwidgets-todo` | `desktopwidgets-todo.desktop` | `desktopwidgets-todo.lock` |
| Usage Monitor | `usage` | `UsageMonitor` | `desktopwidgets-usage` | `desktopwidgets-usage.desktop` | `desktopwidgets-usage.lock` |
| Desktop Monitor | `desktop` | `DesktopMonitor` | `desktopwidgets-desktop` | `desktopwidgets-desktop.desktop` | `desktopwidgets-desktop.lock` |

- Install folder: `~/.local/share/DesktopWidgets/` (frozen bundle with the shared `_internal`), or `--prefix`.
- Menu entries: `~/.local/share/applications/desktopwidgets-<key>.desktop`.
- Autostart: `$XDG_CONFIG_HOME/autostart/desktopwidgets-<key>.desktop`. The app's "Start when I sign in"
  toggle and `install.sh --autostart` write the same file; `Exec=` is the absolute path of the running
  binary (`sys.executable` when frozen, otherwise the Python interpreter plus the script), quoted.
- Lock: `$XDG_RUNTIME_DIR/desktopwidgets-<key>.lock` (fallback: the app's data folder).
- Every app accepts `--selftest` (writes `selftest_card.png` in its data folder, prints the path, exits 0,
  works with empty `HOME` and `XDG_CONFIG_HOME`). Usage Monitor and Desktop Monitor also accept
  `--settings` (open the Settings window at start), because GNOME may have no tray.
- Logs: in the data folder, rotated at about 512 KB, never a key or token.
