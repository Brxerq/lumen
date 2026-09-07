"""Command line entry point.

    lumen                      run the daemon (tray icon + dashboard)
    lumen run [--no-tray] [--open]
    lumen hook                 agent hook entry point (reads the payload on stdin)
    lumen scan                 list detected devices
    lumen test <device> [effect] [#rrggbb]
    lumen emit <type> [--data k=v ...]
    lumen exec -- <command>    run a command, report when it finishes
    lumen timer 25m [--name x]
    lumen connect claude|codex     install agent hooks      (disconnect to remove)
    lumen autostart on|off
    lumen open                 open the dashboard

Imports are lazy on purpose: `lumen hook` runs on every agent tool call.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["hook"]:  # fast path: stdlib only
        from lumen.integrations.agent_sessions import run_hook
        return run_hook()
    if argv[:1] == ["screen-child"]:  # the screen-glow helper process (frozen builds spawn the exe itself)
        from lumen.devices.screen import run_child
        return run_child()
    if argv[:1] == ["notch-child"]:  # the notch/status-pill helper process
        from lumen.devices.notch import run_child
        return run_child()

    for stream in (sys.stdout, sys.stderr):  # cp1252 consoles must never crash on a stray non-ASCII char
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="lumen", description="Something happens on your computer -> your devices react.")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="run the daemon (default)")
    run.add_argument("--no-tray", action="store_true")
    run.add_argument("--open", action="store_true", help="open the dashboard on start")
    sub.add_parser("hook")
    sub.add_parser("scan", help="list detected devices")
    test = sub.add_parser("test", help="play an effect on a device")
    test.add_argument("device")
    test.add_argument("effect", nargs="?", default="flash")
    test.add_argument("color", nargs="?", default="#00ff00")
    emit = sub.add_parser("emit", help="raise an event")
    emit.add_argument("type")
    emit.add_argument("--data", action="append", default=[], metavar="KEY=VALUE")
    ex = sub.add_parser("exec", help="run a command and report its exit status")
    ex.add_argument("argv", nargs=argparse.REMAINDER)
    timer = sub.add_parser("timer", help="fire timer.finished after a delay (e.g. 90s, 25m, 1h)")
    timer.add_argument("duration")
    timer.add_argument("--name", default="")
    for verb in ("connect", "disconnect"):
        p = sub.add_parser(verb, help=f"{verb} an integration's hooks")
        p.add_argument("integration", choices=["claude", "codex"])
    auto = sub.add_parser("autostart", help="start Lumen at login")
    auto.add_argument("state", choices=["on", "off"])
    sub.add_parser("open", help="open the dashboard")
    sub.add_parser("selfcheck", help="import every adapter and integration and draw the status tab off-screen (CI)")
    args = parser.parse_args(argv)

    if args.command in (None, "run"):
        from lumen.app.tray import run_app
        return run_app(no_tray=getattr(args, "no_tray", False), open_ui=True if getattr(args, "open", False) else None)
    if args.command == "scan":
        return _scan()
    if args.command == "test":
        return _test(args.device, args.effect, args.color)
    if args.command == "emit":
        from lumen.integrations.terminal import post_event
        data = dict(kv.split("=", 1) for kv in args.data if "=" in kv)
        ok = post_event(args.type, data, *_endpoint())
        print("sent" if ok else "lumen daemon not running; event dropped", file=sys.stderr)
        return 0 if ok else 1
    if args.command == "exec":
        from lumen.integrations.terminal import exec_command
        # Only the leading separator: `lumen exec -- sh -c 'a -- b'` keeps its own.
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        return exec_command(command, *_endpoint())
    if args.command == "timer":
        from lumen.integrations.terminal import run_timer
        return run_timer(args.duration, args.name, *_endpoint())
    if args.command in ("connect", "disconnect"):
        return _hooks(args.integration, args.command == "connect")
    if args.command == "autostart":
        from lumen.app import autostart
        autostart.set_enabled(args.state == "on")
        print(f"autostart {'enabled' if autostart.enabled() else 'disabled'}")
        return 0
    if args.command == "open":
        from lumen.app.tray import open_dashboard
        open_dashboard(_endpoint()[0])
        return 0
    if args.command == "selfcheck":
        return _selfcheck()
    parser.print_help()
    return 2


def _endpoint() -> tuple[int, str]:
    from lumen.core.config import Config
    settings = Config().settings
    return int(settings["port"]), str(settings.get("webhook_token", ""))


def _selfcheck() -> int:
    """What a frozen build must be able to do before it ships: every adapter and
    integration imports (a missing hidden import shows here, not on a user's
    Mac), and the status tab renders a frame without a display."""
    from lumen.core.devices import BUILTIN_ADAPTERS, adapter_modules
    from lumen.core.integrations import BUILTIN_INTEGRATIONS, integration_classes
    from lumen.devices import notch
    failures = []
    if len(adapter_modules()) < len(BUILTIN_ADAPTERS):
        failures.append("an adapter failed to import (see above)")
    if len(integration_classes()) < len(BUILTIN_INTEGRATIONS):
        failures.append("an integration failed to import (see above)")
    try:
        if not notch.self_check():
            failures.append("notch render produced the wrong size")
    except Exception as e:
        failures.append(f"notch render failed: {type(e).__name__}: {e}")
    for f in failures:
        print(f"selfcheck: {f}")
    print("selfcheck: ok" if not failures else f"selfcheck: {len(failures)} problem(s)")
    return 0 if not failures else 1


def _scan() -> int:
    from lumen.core.config import Config
    from lumen.core.devices import discover_all
    devices = discover_all({**Config().settings, "devices": Config().data["devices"]})
    if not devices:
        print("no devices found")
    for d in devices:
        state = "connected" if d.connected else "not connected"
        print(f"{d.id:28} {d.name:36} {d.kind:12} {', '.join(sorted(d.capabilities)) or '-':24} {state}")
        d.close()
    return 0


def _test(device_id: str, effect: str, color: str) -> int:
    import time

    from lumen.core.config import Config
    from lumen.core.devices import discover_all
    from lumen.core.effects import EffectPlayer
    from lumen.core.events import Event
    from lumen.core.rules import Action
    devices = discover_all({**Config().settings, "devices": Config().data["devices"]})
    player = EffectPlayer()
    player.set_devices(devices)
    player.start()
    action = Action.from_dict({"device": device_id, "effect": effect, "color": color, "message": "Test from Lumen"})
    touched = player.run(action, Event("test", "cli"))
    if not touched:
        print(f"no device '{device_id}' supports '{effect}' (try: lumen scan)")
        return 1
    time.sleep(action.duration + 0.3)
    for d in devices:
        d.close()
    return 0


def _hooks(agent: str, install: bool) -> int:
    from lumen.integrations import claude_code, codex
    cls = claude_code.ClaudeCode if agent == "claude" else codex.Codex
    integ = cls(lambda e: None, {})
    print(integ.connect() if install else integ.disconnect())
    return 0


if __name__ == "__main__":
    sys.exit(main())
