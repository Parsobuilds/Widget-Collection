"""Run `desktop_monitor.py --selftest` as if on a brand-new profile.

Windows: creates three empty temporary folders and points APPDATA, LOCALAPPDATA and
USERPROFILE at them, so nothing from this PC's real profile can leak in.
Linux: one empty temporary folder becomes HOME, and XDG_CONFIG_HOME /
XDG_RUNTIME_DIR are removed, so the data folder is <empty home>/.config/...

    python selftest_clean_profile.py                    # normal run
    python selftest_clean_profile.py --simulate-no-gpu  # pretend there is no NVIDIA card

Exit code 0 means: the selftest exited 0 and printed an image path that lies inside
the temporary APPDATA. The path is printed at the end.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(extra_args: list[str]) -> int:
    env = dict(os.environ)
    if sys.platform.startswith("win"):
        roots = {name: Path(tempfile.mkdtemp(prefix="dm_clean_%s_" % name.lower()))
                 for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE")}
        env.update({name: str(path) for name, path in roots.items()})
        env["HOME"] = str(roots["USERPROFILE"])
        data_root = roots["APPDATA"]
    else:
        home = Path(tempfile.mkdtemp(prefix="dm_clean_home_"))
        env["HOME"] = str(home)
        env.pop("XDG_CONFIG_HOME", None)
        env.pop("XDG_RUNTIME_DIR", None)
        data_root = home
    env.pop("DESKTOP_MONITOR_SIMULATE_NO_GPU", None)
    command = [sys.executable, str(HERE / "desktop_monitor.py"), "--selftest", *extra_args]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=300)
    print(result.stdout)
    if result.stderr.strip():
        print("--- stderr ---\n" + result.stderr)
    print("exit code: %d" % result.returncode)
    match = re.search(r"rendered card saved to (.+?) \(\d+x\d+ px\)", result.stdout)
    image = Path(match.group(1)) if match else None
    appdata = data_root.resolve()
    inside = bool(image and appdata in image.resolve().parents and image.is_file())
    print("image: %s" % image)
    print("image inside the empty profile: %s" % inside)
    print("this PC's name: %s" % platform.node())
    print("host in snapshot matches: %s" % ('"host": "%s"' % platform.node() in result.stdout))
    return 0 if result.returncode == 0 and inside else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
