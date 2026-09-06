"""Your shell: wrap a command, emit an event by hand, or run a timer.

    lumen exec -- npm run build        # command.succeeded / command.failed when it exits
    lumen emit deploy.succeeded --data name=api
    lumen timer 25m --name pomodoro    # timer.finished

Events go to the running daemon over its local API; if the daemon is not
running the command still runs, and the event is simply dropped. For every
long command without wrapping it, add the shell snippet from the dashboard
(zsh/bash `precmd` that emits `command.finished` when a command took more
than 30 seconds).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
import urllib.request

from lumen.core.integrations import Integration

SHELL_SNIPPET = r"""
# Lumen: notify when a command that took > 30 s finishes (bash/zsh)
__lumen_pre() { __lumen_t0=$SECONDS; __lumen_cmd=$1; }
__lumen_post() {
  local code=$? ; [ -z "$__lumen_t0" ] && return
  local dt=$((SECONDS - __lumen_t0)); unset __lumen_t0
  [ "$dt" -ge 30 ] && lumen emit "command.$([ $code -eq 0 ] && echo succeeded || echo failed)" \
      --data "name=${__lumen_cmd%% *}" --data "duration=$dt" --data "exit_code=$code" >/dev/null 2>&1 &
}
if [ -n "$ZSH_VERSION" ]; then
  autoload -Uz add-zsh-hook; add-zsh-hook preexec __lumen_pre; add-zsh-hook precmd __lumen_post
else
  trap '__lumen_pre "$BASH_COMMAND"' DEBUG; PROMPT_COMMAND="__lumen_post;${PROMPT_COMMAND}"
fi
""".strip()


def post_event(type_: str, data: dict | None = None, port: int = 6733, token: str = "") -> bool:
    body = json.dumps({"type": type_, "data": data or {}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/events", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=2):
            return True
    except OSError:
        return False


def exec_command(argv: list[str], port: int, token: str = "") -> int:
    if not argv:
        print("usage: lumen exec -- <command...>", file=sys.stderr)
        return 2
    t0 = time.monotonic()
    try:
        # Windows: quote with the rules cmd.exe actually uses, not POSIX ones,
        # so an argument with spaces survives. shell=True is needed for .cmd
        # and .bat wrappers (npm, yarn), which CreateProcess cannot run.
        code = subprocess.call(subprocess.list2cmdline(argv) if sys.platform == "win32" else argv,
                               shell=sys.platform == "win32")
    except OSError as e:
        print(f"lumen exec: {e}", file=sys.stderr)
        return 127
    printable = subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)
    post_event("command.succeeded" if code == 0 else "command.failed",
               {"name": argv[0], "command": printable, "exit_code": code, "duration": round(time.monotonic() - t0, 1)},
               port, token)
    return code


def parse_duration(text: str) -> float:
    units = {"s": 1, "m": 60, "h": 3600}
    text = text.strip().lower()
    if text and text[-1] in units:
        return float(text[:-1]) * units[text[-1]]
    return float(text)


def run_timer(spec: str, name: str, port: int, token: str = "") -> int:
    seconds = parse_duration(spec)
    print(f"lumen timer: {name or spec} — {int(seconds)}s", flush=True)
    time.sleep(seconds)
    post_event("timer.finished", {"name": name or spec, "seconds": seconds}, port, token)
    return 0


class Terminal(Integration):
    id = "terminal"
    name = "Terminal & scripts"
    description = "Wrap commands, emit events from scripts, run timers — from any shell."
    events = ("command.succeeded", "command.failed", "timer.finished", "*")
    docs = ("`lumen exec -- <command>` reports the exit status when it ends. `lumen emit <type> --data k=v` raises any "
            "event. `lumen timer 25m` fires `timer.finished`. Paste the shell snippet below into `~/.zshrc` or "
            "`~/.bashrc` to be told about every slow command automatically.\n\n```sh\n" + SHELL_SNIPPET + "\n```")

    def status(self) -> dict:
        return {"connected": True, "detail": "lumen exec / emit / timer"}


INTEGRATION = Terminal
