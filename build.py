"""Build and verify the four executables and optional per-user installer."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parent
BUILD = PROJECT / "build"
DIST = PROJECT / "dist"
VENV = PROJECT / ".build-venv"
VENV_LINUX = PROJECT / ".build-venv-linux"
LINUX_DIR = PROJECT / "installer" / "linux"
LHM_URL = ("https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/"
           "releases/download/v0.9.6/LibreHardwareMonitor.zip")
ZIP_SHA256 = "086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001"
DLL_SHA256 = "6ebc194316536ba61af5be24508ad9fcbb2ecc685e716c12e787c79530f66bf0"
LHM_DLLS = (
    "BlackSharp.Core.dll", "DiskInfoToolkit.dll", "HidSharp.dll",
    "LibreHardwareMonitorLib.dll", "Microsoft.Bcl.AsyncInterfaces.dll",
    "Microsoft.Bcl.HashCode.dll", "RAMSPDToolkit-NDD.dll", "System.Buffers.dll",
    "System.CodeDom.dll", "System.Collections.Immutable.dll",
    "System.Formats.Nrbf.dll", "System.IO.Pipelines.dll", "System.Memory.dll",
    "System.Numerics.Vectors.dll", "System.Reflection.Metadata.dll",
    "System.Resources.Extensions.dll", "System.Runtime.CompilerServices.Unsafe.dll",
    "System.Security.AccessControl.dll", "System.Security.Principal.Windows.dll",
    "System.Text.Encodings.Web.dll", "System.Text.Json.dll",
    "System.Threading.AccessControl.dll", "System.Threading.Tasks.Extensions.dll",
)
APPS = ("TodoWidget", "UsageMonitor", "DesktopMonitor", "CpuTempHelper")


def run(command: list[str], cwd: Path = PROJECT,
        env: dict[str, str] | None = None) -> None:
    """Run a build step, stopping immediately on failure."""
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def venv_python() -> Path:
    return VENV / "Scripts" / "python.exe"


def ensure_venv() -> None:
    """Reuse installed dependencies without requiring network access."""
    if sys.maxsize <= 2**32:
        raise RuntimeError("A 64-bit Python interpreter is required.")
    if not venv_python().is_file():
        run([sys.executable, "-m", "venv", str(VENV)])
    probe = subprocess.run(
        [str(venv_python()), "-c",
         "import PySide6, keyring, clr, PyInstaller, PIL, psutil, pynvml, wmi, win32api"],
        cwd=PROJECT, capture_output=True,
    )
    if probe.returncode:
        run([str(venv_python()), "-m", "pip", "install", "-r",
             str(PROJECT / "requirements-build.txt")])


def run_tests() -> None:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for folder, suite in (("todo-widget", "app"), ("usage-monitor", "tests"),
                          ("desktop-monitor", "tests")):
        run([str(venv_python()), "-m", "unittest", "discover", "-s", suite,
             "-p", "test_*.py"], cwd=PROJECT / folder, env=env)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare_lhm() -> None:
    """Verify the release archive before extracting only the runtime DLLs."""
    cache = BUILD / "cache" / "LibreHardwareMonitor-0.9.6.zip"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.is_file() or sha256(cache) != ZIP_SHA256:
        pending = cache.with_suffix(".download")
        try:
            with urllib.request.urlopen(LHM_URL, timeout=120) as source:
                with pending.open("wb") as target:
                    shutil.copyfileobj(source, target)
            if sha256(pending) != ZIP_SHA256:
                raise RuntimeError("LibreHardwareMonitor archive SHA-256 mismatch.")
            pending.replace(cache)
        finally:
            pending.unlink(missing_ok=True)
    destination = BUILD / "lhm"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(cache) as archive:
        for name in LHM_DLLS:
            # Exact root entries exclude GUI dependencies and localized resources.
            (destination / name).write_bytes(archive.read(name))
    if sha256(destination / "LibreHardwareMonitorLib.dll") != DLL_SHA256:
        raise RuntimeError("LibreHardwareMonitorLib.dll SHA-256 mismatch.")


def normalize_version(raw: str) -> str:
    version = raw.strip().lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", version):
        raise ValueError("Version must have three or four numeric parts.")
    if any(int(part) > 65535 for part in version.split(".")):
        raise ValueError("Each version part must be at most 65535.")
    return version


def write_version_info(version: str) -> None:
    BUILD.mkdir(exist_ok=True)
    parts = [int(part) for part in version.split(".")]
    parts += [0] * (4 - len(parts))
    template = (PROJECT / "installer" / "version_info.txt").read_text("utf-8")
    for app in APPS:
        text = (template.replace("@VERSION_TUPLE@", str(tuple(parts)))
                .replace("@VERSION@", ".".join(map(str, parts)))
                .replace("@APP@", app))
        (BUILD / f"version_{app}.txt").write_text(text, encoding="utf-8")


def verify_apps() -> None:
    """Smoke-test frozen programs using isolated, empty user data folders."""
    required = [DIST / "DesktopWidgets" / f"{app}.exe" for app in APPS[:3]]
    required.append(DIST / "CpuTempHelper" / "CpuTempHelper.exe")
    required.extend(DIST / "CpuTempHelper" / "lhm" / name for name in LHM_DLLS)
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"Missing build output: {path.relative_to(PROJECT)}")
    with tempfile.TemporaryDirectory(prefix="selftest-", dir=BUILD) as directory:
        root = Path(directory)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE"):
            folder = root / name
            folder.mkdir()
            env[name] = str(folder)
        for exe in required[:4]:
            run([str(exe), "--selftest"], env=env)


def build_apps() -> None:
    run([str(venv_python()), str(PROJECT / "installer" / "make_icons.py")])
    run([str(venv_python()), "-m", "PyInstaller", "--noconfirm", "--clean",
         str(PROJECT / "installer" / "DesktopWidgets.spec")])
    # PyInstaller 6 datas live under _internal; this helper needs DLLs by its exe.
    target = DIST / "CpuTempHelper" / "lhm"
    target.mkdir(parents=True, exist_ok=True)
    for name in LHM_DLLS:
        shutil.copy2(BUILD / "lhm" / name, target / name)
    verify_apps()


def find_iscc() -> str:
    candidates = [os.environ.get("ISCC"), shutil.which("ISCC.exe")]
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(variable):
            candidates.append(str(Path(os.environ[variable]) / "Inno Setup 6" / "ISCC.exe"))
    candidates.append(str(Path(os.environ.get("LOCALAPPDATA", "")) /
                          "Programs" / "Inno Setup 6" / "ISCC.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Install Inno Setup 6 or set ISCC to its compiler executable.")


def build_installer(version: str) -> None:
    BUILD.mkdir(exist_ok=True)
    verify_apps()
    run([find_iscc(), f"/DAppVersion={version}",
         str(PROJECT / "installer" / "DesktopWidgets.iss")])
    if not (DIST / f"DesktopWidgets-Setup-{version}.exe").is_file():
        raise RuntimeError("Inno Setup did not produce the expected installer.")


def clean() -> None:
    """Remove generated outputs, preserving the verified download cache."""
    if DIST.exists():
        shutil.rmtree(DIST)
    if BUILD.exists():
        for path in BUILD.iterdir():
            if path.name != "cache":
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()


# ---------------------------------------------------------------- Linux path
# Everything in this block runs only with --linux on Linux; the Windows path is untouched.
LINUX_BINARIES = ("TodoWidget", "UsageMonitor", "DesktopMonitor")
# Qt's X11 (xcb) plugin needs these. PyInstaller bundles them only when the build machine
# has them, and without them the widgets cannot start on an X11 session.
LINUX_XCB_LIBRARIES = ("libxcb-cursor.so.0", "libxcb-icccm.so.4", "libxcb-image.so.0",
                       "libxcb-keysyms.so.1", "libxcb-render-util.so.0")
LINUX_XCB_PACKAGES = "libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0"


def linux_python() -> Path:
    return VENV_LINUX / "bin" / "python"


def ensure_venv_linux() -> None:
    """Create .build-venv-linux with Python 3.12 and install the pinned packages."""
    if not linux_python().is_file():
        found = shutil.which("python3.12")
        if sys.version_info[:2] == (3, 12):
            run([sys.executable, "-m", "venv", str(VENV_LINUX)])
        elif found:
            run([found, "-m", "venv", str(VENV_LINUX)])
        elif shutil.which("uv"):
            run(["uv", "venv", "--python", "3.12", str(VENV_LINUX)])
        else:
            raise RuntimeError("Python 3.12 is required. Install python3.12 (or uv) and try again.")
    probe = subprocess.run(
        [str(linux_python()), "-c",
         "import PySide6, keyring, secretstorage, PyInstaller, PIL, psutil, pynvml"],
        cwd=PROJECT, capture_output=True,
    )
    if probe.returncode:
        requirements = str(PROJECT / "requirements-build-linux.txt")
        has_pip = subprocess.run([str(linux_python()), "-m", "pip", "--version"],
                                 capture_output=True).returncode == 0
        if has_pip:
            run([str(linux_python()), "-m", "pip", "install", "-r", requirements])
        else:  # a venv made by uv has no pip
            run(["uv", "pip", "install", "--python", str(linux_python()),
                 "-r", requirements])


def run_tests_linux() -> None:
    """Run the three unit-test suites offscreen and print how many tests ran."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    total = 0
    for folder, suite in (("todo-widget", "app"), ("usage-monitor", "tests"),
                          ("desktop-monitor", "tests")):
        command = [str(linux_python()), "-m", "unittest", "discover", "-s", suite,
                   "-p", "test_*.py"]
        print("+", " ".join(command), flush=True)
        result = subprocess.run(command, cwd=PROJECT / folder, env=env,
                                capture_output=True, text=True)
        match = re.search(r"^Ran (\d+) tests?", result.stderr, re.MULTILINE)
        count = int(match.group(1)) if match else 0
        print(f"{folder}: {count} tests, exit {result.returncode}", flush=True)
        if result.returncode:
            sys.stderr.write(result.stderr)
            raise RuntimeError(f"Unit tests failed in {folder}.")
        total += count
    print(f"Unit tests passed: {total} in total", flush=True)


def make_icons_linux() -> None:
    """Write one 256 px PNG per widget into build/linux_icons."""
    # make_icons.py draws the usage and monitor .ico files under build/ if missing.
    run([str(linux_python()), str(PROJECT / "installer" / "make_icons.py")])
    folder = BUILD / "linux_icons"
    folder.mkdir(parents=True, exist_ok=True)
    sources = {"todo": PROJECT / "todo-widget" / "app" / "todo_icon.png",
               "usage": BUILD / "usage_icon.ico", "desktop": BUILD / "monitor_icon.ico"}
    # Pillow lives in the build venv, not in the Python running build.py, so the
    # conversion runs there too (a clean CI runner has no system Pillow).
    convert = ("import sys\n"
               "from PIL import Image\n"
               "source, target = sys.argv[1], sys.argv[2]\n"
               "with Image.open(source) as image:\n"
               "    if source.endswith('.ico'):\n"
               "        image.size = max(image.info['sizes'])  # largest stored size\n"
               "    image.convert('RGBA').resize((256, 256)).save(target)\n")
    for key, source in sources.items():
        run([str(linux_python()), "-c", convert, str(source),
             str(folder / f"desktopwidgets-{key}.png")])


def verify_linux_apps() -> None:
    """Run each frozen binary with --selftest under a fresh, empty HOME."""
    bundle = DIST / "DesktopWidgets"
    for name in LINUX_BINARIES:
        if not (bundle / name).is_file():
            raise RuntimeError(f"Missing build output: {(bundle / name).relative_to(PROJECT)}")
    missing = [library for library in LINUX_XCB_LIBRARIES if not any(bundle.rglob(library))]
    if missing:
        raise RuntimeError("The bundle lacks the X11 (xcb) libraries " + ", ".join(missing)
                           + ". Install them on the build machine and build again, for example: "
                           f"sudo apt install {LINUX_XCB_PACKAGES}")
    BUILD.mkdir(exist_ok=True)
    for name in LINUX_BINARIES:
        with tempfile.TemporaryDirectory(prefix="selftest-", dir=BUILD) as directory:
            home = Path(directory) / "home"
            config = Path(directory) / "config"
            home.mkdir()
            config.mkdir()
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(config),
                       QT_QPA_PLATFORM="offscreen")
            env.pop("XDG_RUNTIME_DIR", None)
            run([str(bundle / name), "--selftest"], env=env)
            if not list(config.rglob("selftest_card.png")):
                raise RuntimeError(f"{name} --selftest wrote no selftest_card.png")


def build_apps_linux() -> None:
    make_icons_linux()
    run([str(linux_python()), "-m", "PyInstaller", "--noconfirm", "--clean",
         str(LINUX_DIR / "DesktopWidgets-linux.spec")])
    verify_linux_apps()


def pack_linux(version: str) -> Path:
    """Write dist/DesktopWidgets-<version>-linux-x86_64.tar.gz with the install scripts."""
    top = f"DesktopWidgets-{version}-linux-x86_64"
    archive = DIST / f"{top}.tar.gz"
    items = [(DIST / "DesktopWidgets", "DesktopWidgets"),
             (BUILD / "linux_icons", "icons"),
             (PROJECT / "LICENSE", "LICENSE"),
             (PROJECT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
             (LINUX_DIR / "install.sh", "install.sh"),
             (LINUX_DIR / "uninstall.sh", "uninstall.sh"),
             (LINUX_DIR / "README-linux.txt", "README-linux.txt")]

    def neutral(info: tarfile.TarInfo) -> tarfile.TarInfo:
        """Drop the builder's user and group names; keep the permission bits."""
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        if info.name.endswith(".sh"):
            info.mode = 0o755
        return info

    with tarfile.open(archive, "w:gz") as bundle:
        for source, name in items:
            bundle.add(source, arcname=f"{top}/{name}", filter=neutral)
    return archive


def main_linux(version: str, do_clean: bool) -> int:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("--linux only works on Linux. Run build.py without it on Windows.")
    if do_clean:
        clean()
    ensure_venv_linux()
    run_tests_linux()
    build_apps_linux()
    archive = pack_linux(version)
    print(f"Wrote {archive.relative_to(PROJECT)} ({archive.stat().st_size / 1e6:.1f} MB)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--app-only", action="store_true")
    mode.add_argument("--installer-only", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--linux", action="store_true",
                        help="build the Linux tarball (run this on Linux)")
    parser.add_argument("--version", default=os.environ.get("DESKTOP_WIDGETS_VERSION", "1.0.0"))
    args = parser.parse_args()
    version = normalize_version(args.version)
    if args.linux:
        if args.app_only or args.installer_only:
            parser.error("--linux cannot be combined with --app-only or --installer-only")
        return main_linux(version, args.clean)
    if not sys.platform.startswith("win"):
        raise RuntimeError("This is the Windows build. On Linux run: python build.py --linux")
    if args.clean and args.installer_only:
        parser.error("--clean cannot be combined with --installer-only")
    if args.clean:
        clean()
    if not args.installer_only:
        ensure_venv()
        run_tests()
        prepare_lhm()
        write_version_info(version)
        build_apps()
    if not args.app_only:
        build_installer(version)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
            zipfile.BadZipFile, KeyError) as error:
        sys.exit(f"Build failed: {error}")
