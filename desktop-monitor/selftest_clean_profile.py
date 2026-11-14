"""Run `desktop_monitor.py --selftest` as if on a brand-new Windows profile.

Creates three empty temporary folders and points APPDATA, LOCALAPPDATA and
USERPROFILE at them, so nothing from this PC's real profile can leak in.

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
    roots = {name: Path(tempfile.mkdtemp(prefix="dm_clean_%s_" % name.lower()))
             for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE")}
    env = dict(os.environ)
    env.update({name: str(path) for name, path in roots.items()})
    env["HOME"] = str(roots["USERPROFILE"])
    env.pop("DESKTOP_MONITOR_SIMULATE_NO_GPU", None)
    command = [sys.executable, str(HERE / "desktop_monitor.py"), "--selftest", *extra_args]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=300)
    print(result.stdout)
    if result.stderr.strip():
        print("--- stderr ---\n" + result.stderr)
    print("exit code: %d" % result.returncode)
    match = re.search(r"rendered card saved to (.+?) \(\d+x\d+ px\)", result.stdout)
    image = Path(match.group(1)) if match else None
    appdata = roots["APPDATA"].resolve()
    inside = bool(image and appdata in image.resolve().parents and image.is_file())
    print("image: %s" % image)
    print("image inside temp APPDATA: %s" % inside)
    print("this PC's name: %s" % platform.node())
    print("host in snapshot matches: %s" % ('"host": "%s"' % platform.node() in result.stdout))
    return 0 if result.returncode == 0 and inside else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
