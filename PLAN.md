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
9. **Licence holder:** `LICENSE` (MIT, same as the To-Do Widget) names "Desktop Widgets
   contributors", because nothing personal may ship in the installer or the repository.
10. **Neutral commit identity:** this repository's local git identity is "Desktop Widgets
    contributors" with the account's GitHub no-reply address, so a later public history carries
    no personal name or email.
11. **Search list outside the repo:** the exact personal-data search terms live in a script
    outside the repository (a list inside it would itself be personal data).
12. **Modal:** no key-based HTTP billing API (billing is only exposed through its Python SDK /
    CLI, Team plans), so it ships as the `modal_budget` manual preset, labelled manual.
13. **Extra key providers:** OpenRouter and DeepSeek added after matching their official API
    reference pages; tested with the documented sample replies (mocked), not live.
14. **To-Do Goals input:** the packaged app gets an add-goal / add-step input line, because
    goals were previously only added from a command-line tool a frozen exe does not have.
15. **PawnIO is not bundled:** no redistribution permission was found, so the elevated helper
    installs it from the official release at install time, pinned by version and SHA-256, and
    only when it is not already present. A PawnIO that was there before is never removed.
16. **LibreHardwareMonitorLib (MPL-2.0) is bundled** in the CPU helper's `lhm\` folder,
    downloaded by `build.py` from the official release pinned by SHA-256, with the MPL notice
    and a link to the matching source in `THIRD_PARTY_NOTICES.md`.
17. **Usage Monitor polling** runs only on the worker thread (the old card called the poll from
    the UI thread on Refresh); Settings changes trigger a queued forced poll.
18. **Elevated helper hardening:** `CpuTempHelper.exe` resolves `ProgramFiles`, `ProgramData`,
    and `System32` via Win32 Known Folders / `GetSystemDirectoryW` when frozen, always verifies
    PawnIO SHA-256 + stages setups inside `%ProgramFiles%` before execution, writes Task
    Scheduler XML inside `%ProgramFiles%`, checks reparse points on every write/log path,
    and avoids user-writable `--uninstall` fallbacks.
19. **Interactive uninstall secret purge & custom_json prefix:** `--purge-secrets` exits non-zero
    if Credential Manager deletion fails so the uninstaller preserves settings on error;
    `SettingsWindow` preserves trailing spaces on `key_prefix` (e.g. `"Bearer "`).

## Chunks

### Wave 1 (parallel, no shared files)

- [x] **usage-providers** (owns `usage-monitor\models.py`, `cache.py`, `collectors\`, `providers\`, `secrets_store.py`, `provider_config.py`, `SOURCES.md`, `tests\` except `test_settings_window.py`)
  - [x] registry, config, credential store exactly per `INTERFACE.md`
  - [x] catalogue: claude_code, codex, antigravity (Detect), nanogpt (key), google_ai_studio_t3 (hidden without T3 DB), custom_json, manual_budget, plus any documented key-based service (openrouter, deepseek, modal_budget)
  - [x] Modal: billing API checked; manual budget otherwise
  - [x] no hardcoded model list, no private ledger import, no hardcoded balances or caps
  - [x] tests: registry, custom provider (mocked HTTP), secret storage (fake keyring), config round trip, old collector tests updated
- [x] **usage-settings-ui** (owns `usage-monitor\settings_window.py`, `tests\test_settings_window.py`)
  - [x] provider list with status, on/off switch, Remove
  - [x] Add provider: catalogue picker, description, unofficial label, fields, Detect, Test, Save
  - [x] custom provider form; manual budget form says it is manual
  - [x] tests with a stub registry (offscreen Qt)
- [x] **desktop-monitor** (owns everything in `desktop-monitor\`)
  - [x] depersonalised; machine name from the computer; paths per `CONVENTIONS.md`
  - [x] no NVIDIA / AMD / Intel GPU: sensible card, no crash, no error wall
  - [x] Settings window: optional indicators (ALEXA-style feed off unless a file is set), drives, background
  - [x] CPU helper: `CpuTempHelper` with `--install` / `--uninstall` (elevated) and run mode, Program Files location, new task name, PawnIO only if absent
  - [x] `disk_alert.py` without chat webhooks or personal config
  - [x] selftest passes; unit tests added
- [x] **todo-widget** (owns `todo-widget\app\`)
  - [x] merge Goals code into the packaged layout; data in `%APPDATA%\DesktopWidgets\TodoWidget`
  - [x] empty list (or 2-3 neutral examples) and empty Goals on first run
  - [x] no venv fallbacks, no personal names/paths; tests pass
- [x] **deps-research** (read-only): licences and redistribution for LibreHardwareMonitorLib and PawnIO, PawnIO silent install, one optional elevated step in a per-user Inno installer, SignPath Foundation terms and GitHub Actions integration. Output: `docs\research\deps.md`.

### Wave 2

- [x] **orchestrator:** wire registry + settings window into `usage-monitor\usage_monitor.py` (empty state "Add a provider", gear on card, tray Settings, `widget_state.json`, neutral names)
- [x] **installer** (owns `build.py`, `installer\`, PyInstaller specs, `.github\workflows\`, `THIRD_PARTY_NOTICES.md`, `SIGNING.md`)
- [x] **docs** (owns `README.md`, `docs\*.png`)

### Wave 3 (orchestrator)

- [x] full `python build.py`
- [x] independent review: secrets storage/logging and everything done with admin rights
- [x] Done-when checks 1-13 (Check 9 interactive UAC prompt left for owner approval)
- [x] push to private `Widget-Collection`, CI run

## Status log

- 2026-11-14: scouting done, private GitHub repo `smegsmearer/Widget-Collection` created (empty),
  sources copied, `.build-venv` created (Python 3.12, `requirements-build.txt`).
- 2026-11-14: wave 1 done and verified by the orchestrator. Tests: usage-monitor 189 OK,
  desktop-monitor 55 OK, todo-widget 89 OK. Clean-profile selftests: all three exit 0 (cards
  checked by eye). Workers: usage-providers = Opus, usage-settings-ui / desktop-monitor /
  todo-widget = Sonnet, deps-research = Haiku. No re-dispatches. Orchestrator wired
  `usage_monitor.py`. Personal-data sweep clean; commit `be50f03` pushed to the private repo.
- 2026-11-14: wave 2 completed via Codex after Claude 5h limit reset window (`docs` =
  `gpt-6.1-terra`, `installer` = `gpt-6.1-sol`). Orchestrator handoff to Antigravity
  `gemini-4-argon`.
- 2026-11-14: wave 3 security review completed by `gemini-4-hydrogen` (`xhigh` thinking;
  `gpt-6-astra` hit ChatGPT allowance). Fixed 1 High + 3 Medium + Low hardening findings
  (SHA-256 verification and ProgramFiles staging for `--pawnio-setup`, Task XML inside
  ProgramFiles, reparse-point checks on `write()`/`log()`/`setup_log()`, Win32 Known
  Folders + safe env-var `rmdir` in `cpu_temp_helper.py`, `--purge-secrets` non-zero exit
  on error, cross-port redirect check, `key_prefix` trailing space fix in `settings_window.py`).
- 2026-11-14: rebuilt `dist\DesktopWidgets-Setup-1.0.0.exe` (50,018,352 bytes / 47.70 MB).
  Tests: 89 To-Do + 190 Usage Monitor + 55 Desktop Monitor = 334 OK. All 4 frozen `--selftest`
  checks passed. Automated Checks 1-8 and 10-11 passed; Check 9 automated parts passed (live
  UAC install/uninstall of `monitor\cputemp` left for interactive approval).

# Linux port (branch `linux-port`)

Spec: [`docs/LINUX_PORT.md`](docs/LINUX_PORT.md). Briefs: `docs/briefs/<chunk>.md` (shared 13-line header).

## Linux decisions (made on the owner's behalf)

L1. **Linux names** (component keys `todo`/`usage`/`desktop`, launchers `desktopwidgets-<key>`, menu/autostart
    `desktopwidgets-<key>.desktop`, lock `desktopwidgets-<key>.lock`) fixed in `docs/LINUX_PORT.md` before wave 1.
L2. **Python 3.12 on the test machine via `uv`** (standalone uv 0.9.9 under the test folder; CPython 3.12.12), because
    the system Python is 3.14 and the build pins PySide6 6.12.0.
L3. **Screenshots on GNOME Wayland over ssh** use the xdg-desktop-portal Screenshot call with `interactive=false`, after
    granting the portal's `screenshot` permission to unsandboxed callers in the session permission store (revoked at
    the end). `gnome-screenshot` and the Shell D-Bus API are refused on GNOME 50.
L4. **Keyring over ssh**: the logged-in session's gnome-keyring is unlocked; ssh commands reach it by setting
    `DBUS_SESSION_BUS_ADDRESS` to the session bus. A set/get/delete probe with a fake value worked.
L5. **Live model list differs from the shared model list kept outside this repository** (new `claudeAgent_minimax` provider; Antigravity now lists only
    Gemini models). Not edited, because that folder is read-only for this job; recorded here instead.
L6. **windows-ci never installs on the owner's PC**; the real install/uninstall proof runs only on the CI runner.
L7. **build-linux needed a temporary push trigger** (`linux-port`, workflow file only): `workflow_dispatch` returns 404
    until the workflow is on the default branch, and merging is out of scope. Two runs, then the trigger was removed.
L8. **The xcb (X11) libraries are bundled.** Qt's xcb plugin needs libxcb-cursor0/icccm4/image0/keysyms1/render-util0.
    CI installs them so PyInstaller bundles them; the test-machine build lacked them, so its tarball could not start on
    an X11 session. Installed them on the build machine, rebuilt, and `build.py --linux` now fails if they are missing.

## Linux chunks

### Wave 1 (parallel, disjoint files)
- [ ] test-vms (Codex gpt-6.1-sol failed: ChatGPT usage limit -> Claude Sonnet 5.5, high -> that orchestrator hit the Claude API rate limit at 15:32) - `~/dw_test/vm/` on the test machine only
  - [x] Both guests installed and snapshotted (`clean-windows`, `clean-ubuntu`); start scripts and `autounattend.xml` written.
  - [ ] Resumed as `test-vms-resume` (Claude Sonnet 5.5 high, brief `docs/briefs/test-vms-resume.md`): install the CURRENT Windows installer inside the guest, screenshot each widget, install the Linux tarball in the Ubuntu guest, write `README-vm.txt`, revert both snapshots, leave both VMs powered off.
- [x] todo-linux (Claude Sonnet 5.5, medium) - `todo-widget/app/`
- [x] usage-linux (Claude Sonnet 5.5, high) - `usage-monitor/`
- [x] desktop-linux (Claude Sonnet 5.5, medium) - `desktop-monitor/`
- [x] windows-ci (Codex gpt-6.1-sol failed: ChatGPT usage limit -> Antigravity gemini-4-argon failed: quota -> re-dispatched to Claude Sonnet 5.5, medium) - `installer/ci/`, `.github/workflows/build-installer.yml`
### Wave 2
- [x] packaging-linux (Codex out of allowance; Antigravity out of quota; planned: Claude Sonnet 5.5 medium) - `build.py`, `installer/linux/`, `requirements-build-linux.txt`, `.github/workflows/build-linux.yml`
### Wave 3
- [x] docs-linux (Claude Haiku 5.5, medium) - README Linux section, CONVENTIONS, notices; orchestrator merged
### Wave 4
- [ ] final-review (Claude Opus 5.5, high, read-only; first dispatch died with the Claude API rate limit) - Linux secret storage/logging; install.sh/uninstall.sh
  - [ ] Resumed as `final-review` (Claude Opus 5.5 high via the claudeAgent_minimax provider, brief `docs/briefs/final-review.md`); output `temp/evidence/final-review/findings.md`.

### Wave 5 (independent review findings, parallel, disjoint files)
- [ ] fix-secrets (Claude Opus 5.5 high, `claudeAgent_minimax`) - owns `usage-monitor\` - A-01 to A-08
- [ ] fix-installer (Codex gpt-6.1-sol high) - owns `installer\linux\` - B-01 to B-11

## Linux status log
- 2026-11-14 14:30: scouting done; test-machine venv, shot.py, libsecret-tools/gnome-screenshot/grim installed; briefs written.
- 2026-11-14 14:26: windows-ci landed (9 unit tests OK), committed c8a8a80, pushed linux-port; CI run 43604824642 started.
- 2026-11-14 14:40: todo-linux landed (Windows 100 OK/1 skip, Linux 100 OK; cards checked). Orchestrator fix: LEGACY_BG_CONFIG_PATH back on one line for the sweep allowance. Sweep 0 hits.
- 2026-11-14 14:45: CI run 43604824642 (linux-port @ c8a8a80) green: build + verify-install (7 PASS lines, RESULT: PASS),
  signing skipped, artifacts DesktopWidgets-Setup (52,701,212 B) + selftest-cards. Finding F1: on the clean runner the
  Usage Monitor card renders tofu boxes (offscreen font DB has no "Segoe UI"; it asks for the family without checking,
  Desktop Monitor checks and falls back). Fix in usage_monitor.py after usage-linux lands (family check + fallback),
  then re-run CI.
- 2026-11-14 14:50: desktop-linux landed (Windows 93 OK/3 skip, Linux 70 OK/2 skip; card vs top/free/nvidia-smi: CPU 3.8 vs 5.1 % (sampling window), RAM 3.35 GB vs 3392 MiB, GPU 0 %/44 C/278 vs 279 MiB). NVML v2 memory struct used on Linux only. Sweep 0 hits.
- 2026-11-14 15:00: usage-linux landed (Windows 217 OK/2 skip, Linux 217 OK/2 skip; key `test-key-123` in Secret Service,
  absent from config/log, removed with the provider; fail backend -> Settings refuses). Orchestrator fix F1 applied
  (`font_family_available`). Wave 1 app work committed 6de2096; `.gitattributes` (*.sh LF) 333cd13; pushed.
  CI re-run 43605842283 started to confirm F1. Wave 2 packaging-linux dispatched (Claude Sonnet 5.5 medium).
- 2026-11-14 15:25: CI 43605842283 green but the Usage card was still boxes: the Qt offscreen platform on Windows lists
  no font families at all. Real fix F1b: Usage Monitor's selftest sets `QT_QPA_FONTDIR` to the Windows fonts folder in
  offscreen mode (same as Desktop Monitor); `qt_font` back to the original. Local selftest card shows real text;
  tests 217 OK/2 skip. CI re-run to confirm.
- 2026-11-14 15:40: packaging-linux landed (Claude Sonnet 5.5 medium). `build.py --linux` on the test machine exit 0,
  387 tests (todo 100, usage 217, desktop 70); tarball 96,200,706 B; install/uninstall flag paths verified in temp HOMEs
  (evidence temp/evidence/packaging-linux/). Windows `build.py --help` unchanged plus `--linux`. Committed. Wave 3 docs
  chunk dispatched (Claude Haiku 5.5).
- 2026-11-14 16:05: docs-linux landed (Claude Haiku 5.5). CI build-linux run 1 failed (no Pillow outside the venv);
  fixed in build.py (icon conversion runs in the venv Python); run 43607523770 green, artifact DesktopWidgets-linux.
  Windows CI 43606551888 green with readable cards (F1 closed). Committed 3dcc563, pushed.
- 2026-11-14 15:20 (test machine clock): checks 2, 3, 4, 5, 6, 7 PASS (evidence temp/evidence/checks/). Check 6 found
  decision L8 (xcb libraries); temporary build-linux trigger removed (L7). Rebuilding the tarball with the libraries.
- 2026-11-14 15:40: check 1 PASS (Windows build exit 0; 100/217/93 tests; installer 51,518,258 B). Checks 8, 9, 11, 12
  PASS; dwtest throwaway user install + selftests PASS, user deleted. Tarball rebuilt with xcb libs (96,231,393 B).
  Wave 4 final-review dispatched (Claude Opus 5.5 high, read-only). test-vms still running.
- 2026-11-14 15:32: the orchestrator (Claude Opus 5.5) and two of its children died with "Claude API rate limit
  reached". Everything already committed and pushed was safe (`linux-port` = 7739b16, all pushed). The rate limit hit
  during the two chunks that were still open: test-vms and final-review.
- 2026-11-14 15:45: parent took over the merge. Verified state: both CI workflows green (build-installer 43606551888,
  build-linux 43607523770), sweeps clean (files 0 hits / 99 files, gitlog 0 hits / 10 commits), README Linux section and
  THIRD_PARTY_NOTICES entries present, both guests installed with snapshots, Windows guest sitting at a clean desktop.
  Re-dispatched the two open chunks to the claudeAgent_minimax provider (not throttled): `test-vms-resume` (Sonnet 5.5
  high) and `final-review` (Opus 5.5 high). Test machine cleaned: fake provider server on :8765 killed, the stray
  ~/.local/bin/python3.12 symlink removed, the portal screenshot permission deleted (Lookup returns an empty table).
- 2026-11-14 15:47: the last green build-linux run predated the xcb bundling change in build.py, so the temporary push
  trigger was re-added and pushed (cddd24d) to run the workflow on the final commit; build-installer dispatched directly
  with `gh workflow run build-installer.yml --ref linux-port` (run 43611784236). Both runs in flight; the trigger is
  removed again once build-linux is green.
- 2026-11-14 15:50: build-linux run 43611774040 green on cddd24d with the xcb bundling in place (job build, success);
  the five libxcb-* libraries are in the tarball; the trigger was removed again (132d1f9) and pushed. build-installer run
  43611784236 green: 7 PASS lines, RESULT: PASS, artifacts DesktopWidgets-Setup (52,724,822 B) + selftest-cards.
- 2026-11-14 15:52: **finding F2 (parent, from the CI artifacts): the To-Do Widget card rendered as empty boxes on the
  clean runner.** The offscreen platform loads no system fonts on Windows; Usage Monitor (F1b) and Desktop Monitor had
  been given `QT_QPA_FONTDIR`, but the To-Do Widget never was. Fixed in `todo-widget/app/todo_widget.py` (commit
  2468937): the same three lines, Windows-gated, so Linux is untouched. Verified: the local offscreen card now shows real
  text, Windows tests 100 OK (1 skip), Linux tests 100 OK, and the Linux card still shows the empty "No tasks right now!"
  state. build-installer re-dispatched (run 43612388707) to confirm on a clean runner.
- 2026-11-14 15:56: build-installer run 43612388707 green (build + verify-install, 7 PASS lines, RESULT: PASS), artifacts
  DesktopWidgets-Setup (52,721,725 B) and selftest-cards. The To-Do card from the clean runner now shows real text, so
  F2 is closed. The tarball's install.sh and uninstall.sh hashes match the repository copies exactly.
- 2026-11-14 15:53: parent re-verified the earlier checks from the evidence on disk: check 9's short-name hits are
  substrings inside third-party binaries (Tomsk, Sao_Tome, Tomé, Tombstone) - a whole-word search of the extracted
  tarball returns 0 files, and the data folder is clean; check 7's card (real GNOME Wayland desktop) reads RAM
  5.8/15.0 GB against `free` 5980/15402 MB, GPU 43 C against `nvidia-smi` 43 C, disks / 259 GB free of 441 GB, CPU 23 %
  against `top` 21.1 us + 18.9 sy (different sampling window), CPU 50 C against k10temp 44-46 C.
- 2026-11-14 16:05: the independent review finished (Claude Opus 5.5 high, read-only, `claudeAgent_minimax`;
  findings in `temp/evidence/final-review/findings.md`). 19 findings, every one proved by running code on the test
  machine or on Windows: **B-01 (high)** - `uninstall.sh --purge-data` deletes the whole shared
  `$XDG_CONFIG_HOME/DesktopWidgets` folder, so the shared `widget_background.json` and the other widgets' data are
  destroyed; A-01/A-02 (medium) - `purge_secrets()` only deletes the keys named in `config.json` (a missing or corrupt
  config means the key survives while the app says it removed it) and the key is written before the config is saved with
  no rollback; B-02/B-03/B-04/B-05 (medium) - stale launchers after a partial re-install, relative XDG paths used as
  given, the uninstaller deleting another install's menu entry, and the uninstaller not stopping the running widgets;
  A-03 to A-08 and B-06 to B-11 (low/info). The review also confirmed as correct: the key never reaches a file through
  the app, the fail/null backends refuse, Remove touches only our own service and ids, the prefix guards, the zenity
  tick-box, the launcher content, `desktop-file-validate`, the upgrade path and the Windows behaviour.
- 2026-11-14 16:10: the findings are split into two fix chunks with disjoint file scopes and run in parallel:
  `fix-secrets` (Claude Opus 5.5 high) owns `usage-monitor\` and fixes A-01 to A-08; `fix-installer` (Codex gpt-6.1-sol
  high) owns `installer\linux\` and fixes B-01 to B-11. Each fix needs a regression test. The reviewer added `xvfb` to
  the test machine with apt while trying to drive the zenity dialog; no other package was added.
- 2026-11-14 16:15: the local Windows build was re-run from HEAD (2468937 + the plan commits): exit 0, tests 100 + 217
  + 93 = 410 OK, installer `dist\DesktopWidgets-Setup-1.0.0.exe` 51,509,505 B (sha256 40081fca...), so `dist/` now
  includes the To-Do font fix. The local `installer\ci\verify_install.py` was then run against that exact file with a
  clean profile: 7 PASS lines and RESULT: PASS (silent install, the three selftests, the uninstall that keeps the
  user data, and the todo-only reinstall), and the To-Do card it produced shows real text.
- 2026-11-14 16:20: **check 13, Windows half, verified by the parent from the evidence:** the current Windows installer was
  copied to the test machine, put on a CD image and run inside the Win11 Enterprise Evaluation guest. The guest's own
  terminal shows the installed exe's SHA256 8c2482e8891dac5707fb250285f8b3d785600ca7057e9c7b41ad5598d24cc135, which is
  the build under test. All three widgets run with a clean first run: the Usage Monitor shows "No providers yet" with the
  "Add a provider" button, the To-Do Widget shows the three neutral examples, and the Desktop Monitor shows the guest's
  own name DW-TEST-WIN, GPU 0 % on "Microsoft Basic Display Adapter" (no NVIDIA and no error wall), a dash for the
  temperature, RAM 2.1/5.9 GB and C:\ 40 GB free of 59 GB. Note for the report: this installer (51,518,258 B) was built
  at 7739b16, before the To-Do offscreen-font fix, which only touches the `--selftest` path; the installed app is the
  same, and the post-fix build is the one CI run 43612388707 proved on a clean runner.
