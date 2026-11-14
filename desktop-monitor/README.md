# Desktop Monitor (developer notes)

A frosted-glass desktop card showing CPU, GPU, RAM and disk use. Windows only.

## Run from source

    python -m pip install -r requirements.txt
    python desktop_monitor.py

Settings (status feed, drives, low-disk alert, background) are under the gear
icon on the card or the tray "Settings..." item. Data lives in
`%APPDATA%\DesktopWidgets\DesktopMonitor`.

## Selftest

    python desktop_monitor.py --selftest
    python desktop_monitor.py --selftest --simulate-no-gpu

Both write `selftest_card.png` to the data folder and print its path.
`selftest_clean_profile.py` runs the same thing with empty temporary
`APPDATA`, `LOCALAPPDATA` and `USERPROFILE` folders.

## Tests

    python -m unittest discover -s tests -p "test_*.py" -v

(`QT_QPA_PLATFORM=offscreen` is set by the tests.)

## CPU temperature helper

`cpu_temp_helper.py` is frozen separately as `CpuTempHelper.exe` (onedir, no Qt).
The installer runs it elevated once: `--install --user DOMAIN\name
--pawnio-setup <path>`; the uninstaller runs `--uninstall`. Without it the card
shows a dash for CPU temperature.
