"""Release smoke test: the frozen binary's live status tab (Tk window, ImageTk).

    python tests/smoke_notch_child.py dist/lumen.exe

`selfcheck` only renders off-screen; this starts `notch-child`, sends a real
update, checks it is still alive, then closes stdin and expects a clean exit.
Linux needs a display: run it under `xvfb-run -a`.
"""

import json
import subprocess
import sys
import time

exe = sys.argv[1]
payload = {"sessions": [{"id": "smoke", "agent": "claude", "status": "input", "cwd": "/tmp/smoke"}],
           "usage": {"claude": {"five_hour": {"used": 30}}, "codex": {"seven_day": {"used": 60}}},
           "options": {"theme": "liquid"}}
child = subprocess.Popen([exe, "notch-child"], stdin=subprocess.PIPE)
assert child.stdin is not None
child.stdin.write(("240 170 40 230 60 60 0 240 48|" + json.dumps(payload) + "\n").encode())
child.stdin.flush()
time.sleep(5)
if child.poll() is not None:
    sys.exit(f"notch-child died after an update (exit {child.returncode})")
child.stdin.close()
try:
    code = child.wait(timeout=20)
except subprocess.TimeoutExpired:
    child.kill()
    sys.exit("notch-child did not exit when stdin closed")
if code != 0:
    sys.exit(f"notch-child exited with {code}")
print("notch-child: ok")
