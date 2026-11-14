"""CpuTempHelper - CPU temperature helper for Desktop Monitor (Windows).

Windows only lets administrator processes read the CPU's temperature sensor.
This small helper runs elevated, reads the sensor through the LibreHardwareMonitor
library (the "lhm" folder next to the exe, no other app needed) and writes the value
to cpu_temp.json in the signed-in user's Desktop Monitor data folder every 2 seconds.
The widget itself stays a normal, non-admin process and just reads that file.

Three ways to run it (frozen as CpuTempHelper.exe, no Qt):

    CpuTempHelper.exe                     run mode: the elevated scheduled task starts this
    CpuTempHelper.exe --install  ...      (installer, elevated, once) copy to Program Files,
                                          install the PawnIO driver if needed, register and
                                          start the scheduled task
    CpuTempHelper.exe --uninstall         (uninstaller, elevated) undo all of that

    --install arguments:
        --user DOMAIN\\name        the signed-in user the task runs for (elevation may change
                                  the account, so the installer passes it explicitly)
        --pawnio-setup <path>     PawnIO setup exe to run silently when PawnIO is missing

Problems during --install / --uninstall are written in plain English to
%ProgramData%\\DesktopWidgets\\cpu_temp_setup.log and the exit code is not 0.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

try:
    import winreg
except ImportError:  # not Windows (unit tests of the pure functions still import)
    winreg = None

# ---------------------------------------------------------------- names and constants
PRODUCT_DIR_NAME = "DesktopWidgets"
TASK_NAME = "DesktopWidgets CPU Temperature"
MUTEX_NAME = "Local\\DesktopWidgets.CpuTempHelper"
HELPER_EXE_NAME = "CpuTempHelper.exe"
INSTALL_SUBDIR = Path(PRODUCT_DIR_NAME) / "CpuTempHelper"      # below %ProgramFiles%

# LibreHardwareMonitorLib 0.9.6. The build rewrites this line when it bumps the pinned version.
EXPECTED_LHM_DLL_SHA256 = "6ebc194316536ba61af5be24508ad9fcbb2ecc685e716c12e787c79530f66bf0"

# PawnIO (kernel driver LibreHardwareMonitor uses to read CPU sensors).
# Pinned release URL and SHA-256 for PawnIO 2.2.0.
PAWNIO_SETUP_URL = "https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe"
EXPECTED_PAWNIO_SETUP_SHA256 = "1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032"
PAWNIO_SETUP_ARGS = ("-install", "-silent")
PAWNIO_UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"
PAWNIO_SERVICE_KEY = r"SYSTEM\CurrentControlSet\Services\PawnIO"
OWN_REG_KEY = r"SOFTWARE\DesktopWidgets\CpuTempHelper"
OWN_REG_VALUE = "PawnIOInstalledByUs"
OWN_INSTALLED_VALUE = "Installed"
SETUP_EXIT_OK = (0, 3010)          # 3010 = success, restart needed
SETUP_TIMEOUT_S = 300

INTERVAL = 2.0
READ_TIMEOUT = 20.0
PREFERRED = ("CPU Package", "Core (Tctl/Tdie)", "Core (Tctl)", "Core Max")
CREATE_NO_WINDOW = 0x08000000


class SetupError(Exception):
    """A problem during --install / --uninstall, worded for a person (not a traceback)."""


# ---------------------------------------------------------------- folders
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> str:
    """Folder of the exe when frozen, else of this script."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def lhm_dir() -> str:
    """LibreHardwareMonitor DLL folder: lhm\\ next to the exe, or lib\\lhm\\ from source."""
    if is_frozen():
        return os.path.join(exe_dir(), "lhm")
    return os.path.join(exe_dir(), "lib", "lhm")


def lhm_dll_path() -> str:
    return os.path.join(lhm_dir(), "LibreHardwareMonitorLib.dll")


def data_dir() -> str:
    """%APPDATA%\\DesktopWidgets\\DesktopMonitor of the user this helper runs as."""
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, PRODUCT_DIR_NAME, "DesktopMonitor")


def out_path() -> str:
    return os.path.join(data_dir(), "cpu_temp.json")


def log_path() -> str:
    return os.path.join(data_dir(), "cpu_temp_helper.log")


def _known_folder(guid_bytes: bytes, env_name: str, fallback: str) -> Path:
    """Resolve a Windows Known Folder via SHGetKnownFolderPath when elevated,
    or use os.environ when overridden in unit tests."""
    env_val = os.environ.get(env_name)
    if env_val and not is_frozen():
        return Path(env_val)
    try:
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        out_ptr = ctypes.c_wchar_p()
        hr = shell32.SHGetKnownFolderPath(guid_bytes, 0, None, ctypes.byref(out_ptr))
        if hr == 0 and out_ptr.value:
            resolved = Path(out_ptr.value)
            ole32.CoTaskMemFree(out_ptr)
            return resolved
    except Exception:
        pass
    return Path(env_val or fallback)


# FOLDERID_ProgramFilesX64 = {6D809377-6AF0-444B-8957-A3773F02200E}
_GUID_PROGRAM_FILES = b"\x77\x93\x80\x6d\xf0\x6a\x4b\x44\x89\x57\xa3\x77\x3f\x02\x20\x0e"
# FOLDERID_ProgramData = {62AB5D82-FDC1-4DC3-A9DD-070D1D495D97}
_GUID_PROGRAM_DATA = b"\x82\x5d\xab\x62\xc1\xfd\xc3\x4d\xa9\xdd\x07\x0d\x1d\x49\x5d\x97"


def program_files_dir() -> Path:
    return _known_folder(_GUID_PROGRAM_FILES, "ProgramFiles", r"C:\Program Files")


def target_dir() -> Path:
    """%ProgramFiles%\\DesktopWidgets\\CpuTempHelper (only administrators can change it)."""
    return program_files_dir() / INSTALL_SUBDIR


def setup_log_path() -> Path:
    base = _known_folder(_GUID_PROGRAM_DATA, "ProgramData", r"C:\ProgramData")
    return base / PRODUCT_DIR_NAME / "cpu_temp_setup.log"


# ---------------------------------------------------------------- logging
def log(message: str) -> None:
    """Run-mode log (data folder of the user), rotated at about 512 KB."""
    try:
        folder = data_dir()
        for candidate in (Path(folder).parent, Path(folder)):
            if is_reparse_point(candidate):
                return
        os.makedirs(folder, exist_ok=True)
        if is_reparse_point(folder):
            return
        path = log_path()
        if is_reparse_point(path) or is_reparse_point(path + ".1"):
            return
        if os.path.exists(path) and os.path.getsize(path) > 512 * 1024:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("%s  %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"),
                                       message))
    except OSError:
        pass


def setup_log(message: str) -> None:
    """Install/uninstall log: one plain-English line each, in %ProgramData%."""
    path = setup_log_path()
    try:
        for candidate in (path.parent.parent, path.parent):
            if is_reparse_point(candidate):
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        if is_reparse_point(path.parent) or is_reparse_point(path) or is_reparse_point(str(path) + ".1"):
            return
        if path.exists() and path.stat().st_size > 512 * 1024:
            os.replace(path, str(path) + ".1")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("%s  %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"),
                                       message))
    except OSError:
        pass


# ================================================================ run mode
def single_instance():
    """Named mutex; returns None when another helper already runs."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def verify_dll_integrity(dll_path: str | None = None,
                         expected_sha256: str | None = None) -> str:
    """Check the SHA-256 of LibreHardwareMonitorLib.dll before loading it elevated.

    Raises when the file is missing or the hash differs (fail closed).
    """
    dll_path = dll_path or lhm_dll_path()
    expected_sha256 = expected_sha256 or EXPECTED_LHM_DLL_SHA256
    if not os.path.isfile(dll_path):
        raise FileNotFoundError("LibreHardwareMonitorLib.dll not found at: %s" % dll_path)
    hasher = hashlib.sha256()
    with open(dll_path, "rb") as dll_file:
        for chunk in iter(lambda: dll_file.read(65536), b""):
            hasher.update(chunk)
    actual = hasher.hexdigest().lower()
    if actual != expected_sha256.lower():
        raise RuntimeError("LibreHardwareMonitorLib.dll does not match the pinned SHA-256; "
                           "refusing to load it")
    return actual


def open_computer():
    dll_path = lhm_dll_path()
    verify_dll_integrity(dll_path, EXPECTED_LHM_DLL_SHA256)
    from pythonnet import load
    load("netfx")
    import clr  # noqa: F401  (pythonnet)
    sys.path.append(lhm_dir())
    clr.AddReference(dll_path)
    from LibreHardwareMonitor.Hardware import Computer, SensorType
    computer = Computer()
    computer.IsCpuEnabled = True
    computer.Open()
    return computer, SensorType


def read_temp(computer, sensor_type):
    found = {}
    for hardware in computer.Hardware:
        hardware.Update()
        for sensor in hardware.Sensors:
            if sensor.SensorType == sensor_type.Temperature and sensor.Value is not None:
                value = float(sensor.Value)
                if 1.0 < value < 125.0:
                    found[str(sensor.Name).strip()] = value
    for name in PREFERRED:
        if name in found:
            return found[name], name
    cores = {k: v for k, v in found.items() if k.lower().startswith("core")}
    if cores:
        name = max(cores, key=cores.get)
        return cores[name], name
    return None, None


def write(value, sensor):
    folder = data_dir()
    for candidate in (Path(folder).parent, Path(folder)):
        if is_reparse_point(candidate):
            raise OSError("Refusing to write CPU temperature through a reparse point: %s" % candidate)
    os.makedirs(folder, exist_ok=True)
    if is_reparse_point(folder):
        raise OSError("Refusing to write CPU temperature into a reparse point folder: %s" % folder)
    target = out_path()
    tmp = target + ".tmp"
    if is_reparse_point(target) or is_reparse_point(tmp):
        raise OSError("Refusing to write CPU temperature to a reparse point file: %s" % target)
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"cpu_temp": value, "sensor": sensor, "time": time.time()}, handle)
    # The widget opens cpu_temp.json for reading; Windows refuses the swap while
    # that read is open, so retry briefly instead of dropping the reading.
    for attempt in range(10):
        try:
            if is_reparse_point(target) or is_reparse_point(tmp):
                raise OSError("Refusing to swap CPU temperature file across a reparse point.")
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05)


def timed_read(computer, sensor_type):
    """read_temp() in a worker thread. If the sensor driver hangs, log it and exit
    so Task Scheduler starts a fresh helper (the task restarts it on failure)."""
    box = {}

    def work():
        try:
            box["result"] = read_temp(computer, sensor_type)
        except Exception as exc:  # reported back to the loop below
            box["error"] = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(READ_TIMEOUT)
    if worker.is_alive():
        log("sensor read hung for over %d s; exiting so Task Scheduler restarts the helper"
            % READ_TIMEOUT)
        os._exit(3)
    if "error" in box:
        raise box["error"]
    return box["result"]


def run_mode() -> int:
    admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    if not admin:
        log("helper launched without admin rights; asking the scheduled task to start it")
        try:
            subprocess.Popen(
                [schtasks_exe(), "/Run", "/TN", TASK_NAME],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            pass
        return 1
    if single_instance() is None:
        return 0
    log("helper started (admin=%s)" % admin)
    try:
        computer, sensor_type = open_computer()
    except Exception as exc:
        log("could not load LibreHardwareMonitorLib from %s: %s" % (lhm_dir(), exc))
        return 1
    misses = 0
    while True:
        started = time.time()
        try:
            value, sensor = timed_read(computer, sensor_type)
            took = time.time() - started
            if took > 4.0:
                log("slow sensor read: %.1f s" % took)
            if value is None:
                misses += 1
                if misses in (3, 30) or misses % 900 == 0:
                    log("no CPU temperature reading (admin=%s)" % admin)
            else:
                if misses >= 3:
                    log("CPU temperature readable again (%s)" % sensor)
                misses = 0
                write(round(value, 1), sensor)
        except Exception as exc:
            log("read failed: %s" % exc)
        time.sleep(INTERVAL)


# ================================================================ install / uninstall
def system32_dir() -> str:
    try:
        buf = ctypes.create_unicode_buffer(260)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buf, 260)
        if length > 0 and buf.value:
            return buf.value
    except Exception:
        pass
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "System32")


def schtasks_exe() -> str:
    return os.path.join(system32_dir(), "schtasks.exe")


def current_user() -> str:
    """DOMAIN\\name of the process owner (fallback when --user is not given)."""
    domain = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or ""
    name = os.environ.get("USERNAME") or ""
    return "%s\\%s" % (domain, name) if domain else name


def is_reparse_point(path) -> bool:
    """True for a symlink, junction or any other reparse point (does not follow it)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & flag)


def ensure_safe_target(target) -> None:
    """Refuse to install through a symlink/junction (it could redirect an elevated copy)."""
    target = Path(target)
    for path in (target.parent, target):
        if is_reparse_point(path):
            raise SetupError("The install folder %s is a link to somewhere else, so it was "
                             "not touched. Remove the link and run the setup again." % path)


def stop_task(run=subprocess.run) -> None:
    """Ask Task Scheduler to end the running helper. A missing task is fine."""
    try:
        run([schtasks_exe(), "/End", "/TN", TASK_NAME], capture_output=True,
            creationflags=CREATE_NO_WINDOW, timeout=60)
    except Exception:
        pass


def copy_helper(source_dir, target, run=subprocess.run) -> Path:
    """Copy the helper folder (exe + _internal + lhm) to the Program Files folder.

    Replaces an older copy. Does nothing when the source already is the target.
    """
    source = Path(source_dir)
    target = Path(target)
    if not (source / HELPER_EXE_NAME).is_file():
        raise SetupError("%s was not found next to the installer step (%s), so there is "
                         "nothing to install." % (HELPER_EXE_NAME, source))
    ensure_safe_target(source)
    ensure_safe_target(target)
    try:
        if os.path.normcase(os.path.realpath(source)) == os.path.normcase(os.path.realpath(target)):
            return target
    except OSError:
        pass
    stop_task(run)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=False)
    except OSError as exc:
        raise SetupError("Could not copy the CPU temperature helper to %s: %s" % (target, exc))
    return target


# -- PawnIO -------------------------------------------------------------------
def _open_key(registry, path, access=None):
    """Open an HKLM key in the 64-bit registry view."""
    flags = registry.KEY_READ | getattr(registry, "KEY_WOW64_64KEY", 0)
    return registry.OpenKey(registry.HKEY_LOCAL_MACHINE, path, 0, access if access else flags)


def pawnio_installed(registry=None) -> bool:
    """True when PawnIO is installed: its Uninstall entry has a DisplayName, or its service exists."""
    registry = registry or winreg
    if registry is None:
        return False
    try:
        key = _open_key(registry, PAWNIO_UNINSTALL_KEY)
        try:
            name, _kind = registry.QueryValueEx(key, "DisplayName")
        finally:
            registry.CloseKey(key)
        if str(name).strip():
            return True
    except OSError:
        pass
    try:
        registry.CloseKey(_open_key(registry, PAWNIO_SERVICE_KEY))
        return True
    except OSError:
        return False


def decide_pawnio(installed: bool, setup_path) -> tuple[str, int]:
    """What --install does about PawnIO: (action, value for PawnIOInstalledByUs).

    present  -> ("keep", 0)     never touch a driver somebody else installed
    absent   -> ("install", 1)  run the setup we were given (or download the pinned release)
    """
    if installed:
        return "keep", 0
    if setup_path:
        return "install", 1
    return "missing", 0


def verify_pawnio_sha256(setup_path: str | Path,
                         expected_sha256: str = EXPECTED_PAWNIO_SETUP_SHA256) -> str:
    """Verify the SHA-256 of the PawnIO setup binary before executing it elevated."""
    path = Path(setup_path)
    if not path.is_file():
        raise SetupError("The PawnIO setup file was not found: %s" % path)
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    actual = hasher.hexdigest().lower()
    if actual != expected_sha256.lower():
        raise SetupError("The PawnIO setup file did not match the pinned SHA-256 checksum; "
                         "refusing to run it.")
    return actual


def download_pawnio(dest_dir: Path, url: str = PAWNIO_SETUP_URL,
                    expected_sha256: str = EXPECTED_PAWNIO_SETUP_SHA256) -> Path:
    """Download the official pinned PawnIO setup into an admin-owned folder and check its SHA-256."""
    import urllib.request
    dest_dir = Path(dest_dir)
    ensure_safe_target(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "PawnIO_setup.exe"
    if not url.startswith("https://"):
        raise SetupError("Refusing to download PawnIO over a non-HTTPS URL.")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DesktopWidgets-CpuTempHelper/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read(20 * 1024 * 1024)
        dest.write_bytes(data)
    except Exception as exc:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise SetupError("Could not download the PawnIO driver setup from GitHub: %s" % exc)
    try:
        verify_pawnio_sha256(dest, expected_sha256)
    except Exception:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return dest


def install_pawnio(setup_path, run=subprocess.run, verify_hash: bool = False,
                   staging_dir: Path | None = None) -> None:
    """Run the PawnIO setup silently; raises SetupError if it fails.

    When staging_dir is provided, the setup file is copied into that admin-writable
    directory first and verified against EXPECTED_PAWNIO_SETUP_SHA256 before execution
    to prevent a time-of-check/time-of-use swap in a user-writable folder.
    """
    setup = str(setup_path)
    if not os.path.isfile(setup):
        raise SetupError("The PawnIO setup file was not found: %s" % setup)
    staged = None
    try:
        if staging_dir is not None:
            staging_dir = Path(staging_dir)
            ensure_safe_target(staging_dir)
            staging_dir.mkdir(parents=True, exist_ok=True)
            staged = staging_dir / "_pawnio_staged_setup.exe"
            shutil.copy2(setup, staged)
            setup = str(staged)
        if verify_hash:
            verify_pawnio_sha256(setup)
        result = run([setup, *PAWNIO_SETUP_ARGS], capture_output=True,
                     creationflags=CREATE_NO_WINDOW, timeout=SETUP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise SetupError("The PawnIO driver setup did not finish within %d minutes."
                         % (SETUP_TIMEOUT_S // 60))
    except SetupError:
        raise
    except OSError as exc:
        raise SetupError("The PawnIO driver setup could not be started: %s" % exc)
    finally:
        if staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass
    if result.returncode not in SETUP_EXIT_OK:
        raise SetupError("The PawnIO driver setup failed (exit code %s)." % result.returncode)


def set_installed_by_us(value: int, registry=None, installed: int = 1) -> None:
    registry = registry or winreg
    try:
        key = registry.CreateKeyEx(registry.HKEY_LOCAL_MACHINE, OWN_REG_KEY, 0,
                                   registry.KEY_SET_VALUE | getattr(registry, "KEY_WOW64_64KEY", 0))
        try:
            registry.SetValueEx(key, OWN_REG_VALUE, 0, registry.REG_DWORD, int(value))
            registry.SetValueEx(key, OWN_INSTALLED_VALUE, 0, registry.REG_DWORD, int(installed))
        finally:
            registry.CloseKey(key)
    except OSError as exc:
        raise SetupError("Could not record the setup state in the registry: %s" % exc)


def installed_by_us(registry=None) -> bool:
    """True only if our key says --install put PawnIO on this PC."""
    registry = registry or winreg
    if registry is None:
        return False
    try:
        key = _open_key(registry, OWN_REG_KEY)
        try:
            value, _kind = registry.QueryValueEx(key, OWN_REG_VALUE)
        finally:
            registry.CloseKey(key)
        return int(value) == 1
    except (OSError, ValueError, TypeError):
        return False


def pawnio_quiet_uninstall_string(registry=None):
    registry = registry or winreg
    try:
        key = _open_key(registry, PAWNIO_UNINSTALL_KEY)
        try:
            value, _kind = registry.QueryValueEx(key, "QuietUninstallString")
        finally:
            registry.CloseKey(key)
        return str(value).strip() or None
    except OSError:
        return None


def uninstall_pawnio(registry=None, run=subprocess.run) -> bool:
    """Run PawnIO's own quiet uninstaller, but ONLY if --install put it there."""
    registry = registry or winreg
    if not installed_by_us(registry):
        return False
    command = pawnio_quiet_uninstall_string(registry)
    if not command:
        setup_log("PawnIO was installed by this setup but has no quiet uninstall command; "
                  "it was left in place.")
        return False
    try:
        run(command, capture_output=True, creationflags=CREATE_NO_WINDOW,
            timeout=SETUP_TIMEOUT_S)
    except Exception as exc:
        setup_log("Removing the PawnIO driver failed: %s" % exc)
        return False
    return True


def remove_own_registry(registry=None) -> None:
    registry = registry or winreg
    flags = getattr(registry, "KEY_WOW64_64KEY", 0)
    for path in (OWN_REG_KEY, r"SOFTWARE\DesktopWidgets"):
        try:
            registry.DeleteKeyEx(registry.HKEY_LOCAL_MACHINE, path, flags, 0)
        except OSError:
            pass              # not there, or the parent still has other products below it


# -- scheduled task -----------------------------------------------------------
def build_task_xml(user: str, exe_path) -> str:
    """Task Scheduler XML: at logon of `user`, highest run level, interactive, no time limit,
    restart on failure, no duplicates, plus a 1-minute repetition as watchdog."""
    exe = str(exe_path)
    folder = os.path.dirname(exe)
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Reads the CPU temperature for Desktop Monitor (needs administrator rights).</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Repetition>
        <Interval>PT1M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <WorkingDirectory>{folder}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
""".format(user=xml_escape(user), command=xml_escape(exe), folder=xml_escape(folder))


def register_task(user: str, exe_path, run=subprocess.run, temp_dir=None) -> None:
    """Create (or replace) the scheduled task from generated XML."""
    import tempfile
    xml_text = build_task_xml(user, exe_path)
    if temp_dir is not None:
        ensure_safe_target(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)
    handle, xml_file = tempfile.mkstemp(suffix=".xml", dir=temp_dir)
    os.close(handle)
    try:
        # schtasks wants UTF-16 for an XML that says encoding="UTF-16".
        with open(xml_file, "w", encoding="utf-16") as out:
            out.write(xml_text)
        result = run([schtasks_exe(), "/Create", "/TN", TASK_NAME, "/XML", xml_file, "/F"],
                     capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, timeout=60)
    except OSError as exc:
        raise SetupError("Could not run Task Scheduler: %s" % exc)
    finally:
        try:
            os.remove(xml_file)
        except OSError:
            pass
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise SetupError("Task Scheduler refused to create the task: %s"
                         % (detail[-1] if detail else "exit code %s" % result.returncode))


def start_task(run=subprocess.run) -> None:
    result = run([schtasks_exe(), "/Run", "/TN", TASK_NAME], capture_output=True, text=True,
                 creationflags=CREATE_NO_WINDOW, timeout=60)
    if result.returncode != 0:
        raise SetupError("The task was created but could not be started right now. "
                         "It will start the next time you sign in.")


def delete_task(run=subprocess.run) -> None:
    stop_task(run)
    try:
        run([schtasks_exe(), "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True,
            creationflags=CREATE_NO_WINDOW, timeout=60)
    except Exception as exc:
        setup_log("Could not delete the scheduled task: %s" % exc)


def remove_install_dir(target, run=subprocess.run, popen=subprocess.Popen) -> None:
    """Delete the Program Files copy. If this very process runs from there, a short
    delayed cmd does it after the process has exited."""
    target = Path(target)
    if not target.exists():
        return
    if is_reparse_point(target):
        setup_log("The install folder %s is a link, so it was not deleted." % target)
        return
    own = os.path.normcase(os.path.abspath(exe_dir()))
    running_inside = own == os.path.normcase(os.path.abspath(target))
    if not running_inside:
        try:
            shutil.rmtree(target)
            return
        except OSError as exc:
            setup_log("Deleting %s failed (%s); scheduling it for after this step." % (target, exc))
    sys32 = system32_dir()
    cmd_exe = os.path.join(sys32, "cmd.exe")
    ping_exe = os.path.join(sys32, "ping.exe")
    env = dict(os.environ, _DW_RMDIR_TARGET=str(target.resolve()), _DW_PING_EXE=ping_exe)
    command = '"%_DW_PING_EXE%" -n 4 127.0.0.1 >nul & rmdir /s /q "%_DW_RMDIR_TARGET%"'
    popen([cmd_exe, "/c", command], creationflags=CREATE_NO_WINDOW,
          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, env=env)


def do_install(user: str, pawnio_setup=None, source_dir=None, run=subprocess.run,
               registry=None, allow_download: bool = False) -> None:
    """The whole --install, in order. Raises SetupError with a plain-English message."""
    registry = registry or winreg
    source = source_dir or (exe_dir() if is_frozen() else None)
    if source is None:
        raise SetupError("--install only works from the packaged CpuTempHelper.exe.")
    target = copy_helper(source, target_dir(), run)
    setup_log("Copied the CPU temperature helper to %s" % target)

    installed = pawnio_installed(registry)
    downloaded_setup: Path | None = None
    if not installed and not pawnio_setup and allow_download:
        downloaded_setup = download_pawnio(target)
        pawnio_setup = str(downloaded_setup)

    action, flag = decide_pawnio(installed, pawnio_setup)
    try:
        if action == "install":
            install_pawnio(pawnio_setup, run, verify_hash=True, staging_dir=target)
            setup_log("Installed the PawnIO driver.")
            if not pawnio_installed(registry):
                setup_log("Note: PawnIO setup finished but Windows does not list it yet.")
        elif action == "keep":
            setup_log("PawnIO was already installed; leaving it as it is.")
        else:
            raise SetupError("The PawnIO driver is not installed and no setup file was given "
                             "(--pawnio-setup), so CPU temperature cannot work.")
    finally:
        if downloaded_setup is not None:
            try:
                downloaded_setup.unlink(missing_ok=True)
            except OSError:
                pass

    register_task(user or current_user(), target / HELPER_EXE_NAME, run, temp_dir=target)
    setup_log("Registered the scheduled task '%s' for %s" % (TASK_NAME, user or current_user()))
    set_installed_by_us(flag, registry)
    try:
        start_task(run)
    except SetupError as exc:
        setup_log(str(exc))        # not fatal: the task is in place
    setup_log("CPU temperature helper installed.")


def do_uninstall(run=subprocess.run, registry=None, popen=subprocess.Popen) -> None:
    """The whole --uninstall. Never raises for a missing piece."""
    registry = registry or winreg
    delete_task(run)
    removed_driver = uninstall_pawnio(registry, run)
    remove_install_dir(target_dir(), run, popen)
    remove_own_registry(registry)
    setup_log("CPU temperature helper removed%s." % (" (PawnIO driver removed too)"
                                                      if removed_driver else ""))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog=HELPER_EXE_NAME, description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true",
                      help="elevated, once: copy to Program Files, set up the driver and the task")
    mode.add_argument("--uninstall", action="store_true",
                      help="elevated: remove the task, the files and (if we added it) PawnIO")
    mode.add_argument("--selftest", action="store_true",
                      help="verify LibreHardwareMonitorLib.dll integrity and exit 0")
    parser.add_argument("--user", default="", help="DOMAIN\\name the task runs for")
    parser.add_argument("--pawnio-setup", default="", help="PawnIO setup exe to run if needed")
    parser.add_argument("--download-pawnio", action="store_true",
                        help="download the official pinned PawnIO setup if PawnIO is absent")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.selftest:
        verify_dll_integrity()
        print("CpuTempHelper selftest OK:", lhm_dll_path())
        return 0
    if not (args.install or args.uninstall):
        return run_mode()
    try:
        if args.install:
            do_install(args.user, args.pawnio_setup or None,
                       allow_download=bool(args.download_pawnio))
        else:
            do_uninstall()
        return 0
    except SetupError as exc:
        setup_log("ERROR: %s" % exc)
        return 1
    except Exception as exc:  # last resort: still a readable line, not a traceback
        setup_log("ERROR: Unexpected problem (%s: %s)" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
