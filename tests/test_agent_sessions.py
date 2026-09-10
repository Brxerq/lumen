import json
import os
import sqlite3
import time
import tracemalloc

from lumen.core.events import Event
from lumen.integrations import agent_sessions as ag
from lumen.integrations import claude_code, codex
from lumen.integrations.agent_sessions import DONE, INPUT, RUNNING


def test_large_transcript_scans_keep_memory_bounded_and_retry_partial_lines(tmp_path):
    transcript = tmp_path / "large.jsonl"
    filler = json.dumps({"type": "response_item", "text": "x" * 1024}).encode() + b"\n"
    complete = b'{"type":"event_msg","payload":{"type":"task_complete"}}'
    usage = b'{"message":{"usage":{"input_tokens":7}}}\n'
    with transcript.open("wb") as f:
        f.write(b'{"type":"event_msg","payload":{"type":"task_started"}}\n')
        for _ in range(4096):
            f.write(filler)
        f.write(usage)
        f.write(complete)  # still being written by the agent
    record = {}
    for scan in (lambda: codex.turn_open(transcript), lambda: ag._accumulate_tokens(transcript, record)):
        tracemalloc.start()
        try:
            scan()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 1024 * 1024, f"scan allocated {peak} bytes for a 4 MB transcript"
    assert codex.turn_open(transcript) is True
    assert record["tokens"]["in"] == 7
    assert record["transcript_offset"] == transcript.stat().st_size - len(complete)
    with transcript.open("ab") as f:
        f.write(b"\n" + usage)
    assert codex.turn_open(transcript) is False
    ag._accumulate_tokens(transcript, record)
    assert record["tokens"]["in"] == 14


# What the daemon does with a directory of hook files, spelled out here rather
# than wrapped in helpers that only the tests would ever call.
def _statuses(state_dir, now=None):
    return [(d.get("agent", "claude"), d["status"], sid)
            for sid, d in ag.read_sessions(state_dir, now).items()]


def _fold(hooked, truth):
    return ag.aggregate(ag.session_statuses(hooked, truth).values())


def test_aggregate_priority():
    assert ag.aggregate([]) == DONE
    assert ag.aggregate([DONE, DONE]) == DONE
    assert ag.aggregate([DONE, RUNNING]) == RUNNING
    assert ag.aggregate([RUNNING, INPUT, DONE]) == INPUT
    assert ag.aggregate(s for s in [DONE, RUNNING]) == RUNNING  # a generator, as _poll passes


def test_hook_lifecycle_writes_and_forgets(tmp_path):
    ev = lambda name, sid="s1": {"hook_event_name": name, "session_id": sid}
    assert ag.apply_hook(ev("UserPromptSubmit"), tmp_path, now=100.0) == RUNNING
    assert _statuses(tmp_path, now=101.0) == [("claude", RUNNING, "s1")]
    assert ag.apply_hook(ev("PermissionRequest"), tmp_path, now=102.0) == INPUT
    assert ag.apply_hook({**ev("Stop", "s2"), "turn_id": "t"}, tmp_path, now=102.0) == DONE
    sessions = _statuses(tmp_path, now=103.0)
    assert sorted(sessions) == [("claude", INPUT, "s1"), ("codex", DONE, "s2")]
    assert _fold({"s1": INPUT}, {}) == INPUT
    assert _fold({"s2": DONE}, {}) == DONE
    assert ag.apply_hook(ev("SessionEnd"), tmp_path) is None
    assert _statuses(tmp_path, now=103.0) == [("codex", DONE, "s2")]
    assert ag.apply_hook({"hook_event_name": "Bogus", "session_id": "x"}, tmp_path) is None
    assert ag.apply_hook({"hook_event_name": "Stop"}, tmp_path) is None


def test_hook_payload_cannot_escape_the_state_dir(tmp_path):
    state_dir = tmp_path / "sessions"
    victim = tmp_path / "victim.json"
    victim.write_text("{}")
    for bad in ("../victim", "..\\..\\evil", "a/b", "a.b", "", None, " "):
        payload = {"hook_event_name": "SessionEnd", "session_id": bad}
        assert ag.apply_hook(payload, state_dir) is None
        assert ag.apply_hook({**payload, "hook_event_name": "Stop"}, state_dir) is None
    assert victim.exists() and not state_dir.exists()  # nothing touched, nothing created
    assert ag.apply_hook({"hook_event_name": "Stop", "session_id": "ok-id_2"}, state_dir) == DONE


def test_stale_and_corrupt_sessions_ignored(tmp_path):
    (tmp_path / "old.json").write_text(json.dumps({"status": INPUT, "ts": 0}))
    (tmp_path / "bad.json").write_text("{not json")
    assert _statuses(tmp_path, now=ag.STALE_AFTER_S + 1) == []
    assert not (tmp_path / "old.json").exists() and (tmp_path / "bad.json").exists()  # stale is deleted


def test_fold_record_overrides_hooks():
    # record says closed -> done even if the hook file is stuck on running
    assert _fold({"a": RUNNING}, {"a": False}) == DONE
    # record says open -> running, and a hook `input` survives while open
    assert _fold({"a": INPUT}, {"a": True}) == INPUT
    assert _fold({"a": INPUT}, {"a": False}) == DONE
    # sessions only one side knows about pass through
    assert _fold({}, {"b": True}) == RUNNING
    assert _fold({"cli": RUNNING}, {}) == RUNNING


def test_install_and_uninstall_hooks(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "node other.js"}]}],
                                              "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "py -m lumen hook"}]}]}}))
    cmd = ag.install_hooks(settings, ag.CLAUDE_HOOKS, command="lumen hook")
    assert cmd == "lumen hook"
    data = json.loads(settings.read_text())
    assert (tmp_path / "settings.json.bak-lumen").exists()
    assert ag.hooks_installed(settings)
    # other people's hooks kept, our own older entry replaced, matchers set
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "node other.js"
    ours = [h["command"] for g in data["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert ours == ["lumen hook"]
    pre = [g for g in data["hooks"]["PreToolUse"] if g.get("matcher") == "AskUserQuestion"]
    assert pre and pre[0]["hooks"][0]["async"] is True
    # idempotent
    ag.install_hooks(settings, ag.CLAUDE_HOOKS, command="lumen hook")
    assert sum(1 for g in json.loads(settings.read_text())["hooks"]["Stop"] for h in g["hooks"] if h["command"] == "lumen hook") == 1
    assert ag.uninstall_hooks(settings)
    data = json.loads(settings.read_text())
    assert data["hooks"] == {"Stop": [{"hooks": [{"type": "command", "command": "node other.js"}]}]}
    assert not ag.hooks_installed(settings)


def test_codex_rollout_fallback(tmp_path):
    db = tmp_path / "state.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("create table threads (id text, rollout_path text, archived int, updated_at_ms int)")
    now = time.time()
    started = {"type": "event_msg", "payload": {"type": "task_started"}}
    complete = {"type": "event_msg", "payload": {"type": "task_complete"}}
    assistant_msg = {"type": "response_item", "payload": {"type": "message", "role": "assistant"}}
    filler = [{"type": "event_msg", "payload": {"type": "token_count", "x": "y" * 20000}}] * 3
    for name, entries, age in [
        ("busy", [started, assistant_msg] + filler, 5),
        ("idle", [started, complete] + filler, 5),
        ("noevents", [{"type": "response_item", "payload": {"type": "function_call"}}], 5),
        ("old", [started], 3600),
        ("archived", [started], 5),
    ]:
        p = tmp_path / f"{name}.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        os.utime(p, (now - age, now - age))
        conn.execute("insert into threads values (?, ?, ?, ?)", (name, str(p), name == "archived", int((now - age) * 1000)))
    conn.commit()
    conn.close()
    assert codex.rollout_states(db, now=now) == {"busy": True, "idle": False, "noevents": False}
    hooked = codex.rollout_states(db, now=now, hooked=["old", "archived", "gone"])
    assert hooked["old"] is True and hooked["archived"] is False and "gone" not in hooked
    assert codex.rollout_states(tmp_path / "missing.sqlite", now=now) == {}
    # incremental: appended events flip state, a partial trailing line is not consumed
    busy = tmp_path / "busy.jsonl"
    with busy.open("a") as f:
        f.write(json.dumps(complete) + "\n" + json.dumps(started)[:-3])
    assert codex.turn_open(busy) is False
    with busy.open("a") as f:
        f.write(json.dumps(started)[-3:] + "\n")
    assert codex.turn_open(busy) is True


def test_codex_keeps_quiet_roots_and_excludes_internal_workers(tmp_path):
    db = tmp_path / "state.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("create table threads (id text, rollout_path text, archived int, updated_at_ms int, cwd text, title text, source text)")
    now = time.time()

    def add(tid, age, source="vscode", updated_age=0):
        rollout = tmp_path / f"{tid}.jsonl"
        # Payload order is intentionally different from Codex's usual output.
        rollout.write_text("\n".join(json.dumps(entry) for entry in [
            {"payload": {"id": tid}, "type": "session_meta"},
            {"payload": {"type": "task_started"}, "type": "event_msg"},
        ]) + "\n")
        os.utime(rollout, (now - age, now - age))
        conn.execute("insert into threads values (?, ?, 0, ?, ?, ?, ?)",
                     (tid, str(rollout), int((now - updated_age) * 1000), "C:/project", f"{tid} title", source))

    add("fresh", 5)
    add("quiet-a", codex.ACTIVE_WINDOW_S + 1)
    add("quiet-b", codex.ACTIVE_WINDOW_S + 600)
    add("worker", 5, '{"subagent":{"thread_spawn":{}}}')
    add("guardian", 5, '{"subagent":{"other":"guardian"}}')
    conn.commit()
    conn.close()

    metadata = {}
    assert codex.rollout_states(db, now=now, metadata=metadata) == {"fresh": True, "quiet-a": True, "quiet-b": True}
    assert metadata["quiet-a"]["cwd"] == "C:/project"
    assert metadata["quiet-a"]["title"] == "quiet-a title"

    # Once a root is observed, its quiet database row remains eligible on later
    # polls; `hooked` models the tracker's bounded known-root set here.
    conn = sqlite3.connect(db)
    conn.execute("update threads set updated_at_ms = ? where id = 'quiet-a'", (int((now - codex.ACTIVE_WINDOW_S - 1) * 1000),))
    conn.commit()
    conn.close()
    assert codex.rollout_states(db, now=now, hooked=["quiet-a"])["quiet-a"] is True


def test_codex_turn_parser_ignores_foreign_history_segment(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    entries = [
        {"type": "session_meta", "payload": {"id": "current"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "session_meta", "payload": {"id": "historical"}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ]
    rollout.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    assert codex.turn_open(rollout, "current") is True


def test_codex_rollout_read_failure_is_not_an_empty_observation(tmp_path):
    db = tmp_path / "state.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("create table threads (id text, rollout_path text, archived int, updated_at_ms int)")
    conn.execute("insert into threads values ('missing', ?, 0, ?)", (str(tmp_path / "missing.jsonl"), int(time.time() * 1000)))
    conn.commit()
    conn.close()
    import pytest
    with pytest.raises(codex.TrackingUnavailable):
        codex.rollout_states(db)


def test_claude_transcript_fallback(tmp_path):
    home = tmp_path
    (home / "sessions").mkdir()
    now = time.time()

    def mk(name, entries, age, pid=os.getpid()):
        sess = {"pid": pid, "sessionId": name, "cwd": r"C:\Users\x\proj"}
        (home / "sessions" / f"{name}.json").write_text(json.dumps(sess))
        t = claude_code.transcript_path(sess, home)
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        os.utime(t, (now - age, now - age))

    end = {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text"}]}}
    tool = {"type": "assistant", "message": {"stop_reason": "tool_use", "content": [{"type": "tool_use"}]}}
    attach = {"type": "attachment"}
    prompt = {"type": "user", "message": {"content": "go"}, "origin": {"kind": "human"}}
    # injected when a tab is reopened with background work unaccounted for; nobody answers it
    notice = {"type": "user", "message": {"content": "<task-notification/>"}, "origin": {"kind": "task-notification"}}
    mk("idle", [tool, end, attach, attach], 5)
    mk("busy", [end, prompt, tool, attach], 5)
    mk("queued", [end, prompt], 5)              # prompt sent, first tokens not written yet
    mk("toolresult", [tool, {"type": "user", "message": {"content": [{"type": "tool_result"}]}}], 5)
    mk("reopened", [tool, end, notice], 5)      # was reported "working" for hours before this
    mk("old", [tool], 3600)
    mk("dead", [tool], 5, pid=999999)
    assert claude_code.transcript_states(home) == {"idle": False, "busy": True, "queued": True,
                                                  "toolresult": True, "reopened": False, "old": True}


def test_agent_integration_emits_transitions(monkeypatch):
    events: list[Event] = []
    ag._latest.clear()

    ag._sessions.clear()

    class Fake(ag.AgentIntegration):
        agent = "claude"
        statuses = iter([DONE, DONE, RUNNING, INPUT, DONE])

        def current_sessions(self):
            return {"s1": next(self.statuses)}

    integ = Fake(events.append, {})
    for _ in range(5):
        integ._poll()
    per_tab = [e for e in events if e.type == "agents.sessions"]
    # the first poll paints the zones even with nothing seated (s1 is idle and has no hook file);
    # the folded status rides along with each snapshot
    assert [[s["status"] for s in e.data["sessions"]] for e in per_tab] == [[], [RUNNING], [INPUT], [DONE]]
    assert [e.data["status"] for e in per_tab] == [DONE, RUNNING, INPUT, DONE]
    assert per_tab[1].data["sessions"] == [{"id": "s1", "agent": "claude", "slot": 0, "label": "", "status": RUNNING,
                                                "started": None, "ts": None, "cwd": "", "context": None,
                                                "activity": "", "tokens": None, "model": "", "title": "", "source": "",
                                                "tracking_health": None, "cost_usd": None}]
    types = [(e.type, e.data.get("status")) for e in events if e.type != "agents.sessions"]
    # first poll settles the aggregate without a per-agent flash; then each change emits both
    assert types == [("agents.status", "done"),
                     ("agent.running", "running"), ("agents.status", "running"),
                     ("agent.needs_input", "input"), ("agents.status", "input"),
                     ("agent.session.needs_input", "input"),
                     ("agent.finished", "done"), ("agents.status", "done"),
                     ("agent.session.finished", "done")]


def test_session_slots_are_sticky_across_agents(tmp_path):
    ag._sessions.clear()
    rec = lambda started, cwd="": {"started": started, "cwd": cwd}
    snap = ag._update_sessions("claude", {"b": RUNNING, "a": DONE}, {"a": rec(1, "C:/x"), "b": rec(2)})
    assert [(s["id"], s["slot"], s["cwd"]) for s in snap] == [("b", 0, ""), ("a", 1, "C:/x")]  # b started last
    snap = ag._update_sessions("codex", {"c": INPUT}, {})                   # a newcomer uses the next free zone
    assert [(s["id"], s["slot"]) for s in snap] == [("b", 0), ("a", 1), ("c", 2)]
    assert ag._update_sessions("codex", {"c": INPUT}, {}) is None          # nothing changed
    snap = ag._update_sessions("claude", {"b": DONE, "d": RUNNING}, {})     # a ended; d fills its free zone
    assert [(s["id"], s["slot"], s["status"]) for s in snap] == [("b", 0, DONE), ("d", 1, RUNNING), ("c", 2, INPUT)]
    assert [s["id"] for s in ag.all_sessions()] == ["b", "d", "c"]
    # idle sessions known only from the agent's record (no hook file) take no zone until they work,
    # then keep it: Claude Desktop keeps every old tab's process alive
    snap = ag._update_sessions("claude", {"b": DONE, "d": RUNNING, "e": DONE, "f": DONE}, {})
    assert snap is None and "e" not in ag._sessions
    snap = ag._update_sessions("claude", {"b": DONE, "d": RUNNING, "e": RUNNING, "f": DONE}, {})
    assert [(s["id"], s["slot"]) for s in snap] == [("b", 0), ("d", 1), ("c", 2), ("e", 3)]
    snap = ag._update_sessions("claude", {"b": DONE, "d": RUNNING, "e": DONE, "f": DONE}, {})
    assert [(s["id"], s["status"]) for s in snap][-1] == ("e", DONE)
    # hook files keep `started` and cwd, and can be forgotten from the dashboard
    ev = {"hook_event_name": "UserPromptSubmit", "session_id": "s9", "cwd": "C:/proj"}
    ag.apply_hook(ev, tmp_path, now=10.0)
    ag.apply_hook({**ev, "hook_event_name": "Stop"}, tmp_path, now=20.0)
    r = ag.read_sessions(tmp_path, now=21.0)["s9"]
    assert (r["started"], r["ts"], r["cwd"], r["status"]) == (10.0, 20.0, "C:/proj", DONE)
    assert ag.forget_session("s9", tmp_path) and not ag.forget_session("s9", tmp_path)
    assert ag.read_sessions(tmp_path, now=21.0) == {}


def test_dismiss_hides_fallback_only_session_until_next_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMEN_HOME", str(tmp_path))
    ag._sessions.clear()
    ag._snapshot.clear()
    ag._dismissed.clear()
    integ = ag.AgentIntegration(lambda e: None, {})
    integ.agent = "codex"
    states = iter([{"task": True}, {"task": True}, {"task": False}, {"task": True}])
    integ.truth = lambda hooked: next(states)

    assert integ.current_sessions() == {"task": RUNNING}
    ag._update_sessions("codex", {"task": RUNNING}, {})
    assert ag.forget_session("task")
    assert integ.current_sessions() == {}
    assert integ.current_sessions() == {}
    assert integ.current_sessions() == {"task": RUNNING}


def test_other_agent_poll_preserves_dismissal_across_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMEN_HOME", str(tmp_path))
    monkeypatch.setattr(ag, "_sessions", {})
    monkeypatch.setattr(ag, "_snapshot", [])
    monkeypatch.setattr(ag, "_dismissed", {})
    codex = ag.AgentIntegration(lambda e: None, {})
    codex.agent = "codex"
    codex.truth = lambda hooked: {"task": True}
    claude = ag.AgentIntegration(lambda e: None, {})
    claude.agent = "claude"
    claude.truth = lambda hooked: {"other": True}
    ag._update_sessions("codex", codex.current_sessions(), {})
    assert ag.forget_session("task")

    assert claude.current_sessions() == {"other": RUNNING}
    assert ag.slots.pinned("task")["dismissed_status"] == RUNNING
    assert codex.current_sessions() == {}
    ag._dismissed.clear()  # simulate a daemon restart loading the persisted marker
    assert codex.current_sessions() == {}
    codex.truth = lambda hooked: {"task": False}
    assert codex.current_sessions() == {}
    assert claude.current_sessions() == {"other": RUNNING}
    codex.truth = lambda hooked: {"task": True}
    assert codex.current_sessions() == {"task": RUNNING}
    assert ag.slots.pinned("task")["dismissed_status"] is None


def test_context_window_comes_from_the_transcripts_last_usage(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join([
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
        json.dumps({"type": "assistant", "message": {"model": "claude-fable-5-1", "usage": {
            "input_tokens": 32, "cache_creation_input_tokens": 5288, "cache_read_input_tokens": 160898, "output_tokens": 9}}}),
        "not json at all",
        json.dumps({"type": "system", "usage": "the word, not the object"}),
    ]) + "\n")
    assert ag.context_usage(t) == {"tokens": 166218, "window": 200_000}
    assert ag.context_percent({"context": ag.context_usage(t)}) == 83
    assert ag.context_usage(tmp_path / "missing.jsonl") is None
    assert ag.context_percent({}) is None and ag.context_percent({"context": {"tokens": "x"}}) is None
    t.write_text(json.dumps({"message": {"model": "claude-opus-5[1m]", "usage": {"input_tokens": 400_000}}}) + "\n")
    assert ag.context_usage(t) == {"tokens": 400_000, "window": 1_000_000}
    # the hook records it, and a snapshot key moves only on a five-percent step
    ev = {"hook_event_name": "UserPromptSubmit", "session_id": "ctx1", "transcript_path": str(t)}
    ag.apply_hook(ev, tmp_path, now=1.0)
    rec = json.loads((tmp_path / "ctx1.json").read_text())
    # cumulative session totals ride along with the context window
    assert rec["context"] == {"tokens": 400_000, "window": 1_000_000, "input_tokens": 400_000, "output_tokens": 0}
    a = ag._key([{"id": "x", "agent": "claude", "slot": 0, "status": RUNNING, "context": {"tokens": 41, "window": 100}}])
    b = ag._key([{"id": "x", "agent": "claude", "slot": 0, "status": RUNNING, "context": {"tokens": 44, "window": 100}}])
    c = ag._key([{"id": "x", "agent": "claude", "slot": 0, "status": RUNNING, "context": {"tokens": 46, "window": 100}}])
    assert a == b != c


def test_activity_is_a_short_phrase_and_survives_hooks_that_carry_no_tool(tmp_path):
    act = lambda **p: ag.activity_of(p)
    assert act(tool_name="Edit", tool_input={"file_path": r"C:\proj\src\api.py"}) == "Editing api.py"
    assert act(tool_name="Write", tool_input={"file_path": "/x/app.js"}) == "Editing app.js"
    assert act(tool_name="Read", tool_input={"file_path": "/x/api.py"}) == "Reading api.py"
    assert act(tool_name="Bash", tool_input={"command": "pytest -q " + "x" * 60}) == "Running: pytest -q " + "x" * 30
    assert act(tool_name="Grep", tool_input={"pattern": "x"}) == "Searching"
    assert act(tool_name="Task", tool_input={}) == "Delegating"
    assert act(tool_name="AskUserQuestion") == "Asking you"
    assert act(tool_name="WebFetch") == "Using WebFetch"
    assert act(tool_name="Read", tool_input="not a dict") == "Reading"
    assert act(hook_event_name="UserPromptSubmit") == "Thinking"
    assert act(hook_event_name="Stop") == ""
    assert ag.activity_of({"hook_event_name": "Notification"}, "Editing api.py") == "Editing api.py"

    ev = {"session_id": "act1", "hook_event_name": "UserPromptSubmit"}
    ag.apply_hook(ev, tmp_path, now=1.0)
    read = lambda: json.loads((tmp_path / "act1.json").read_text())["activity"]
    assert read() == "Thinking"
    ag.apply_hook({**ev, "hook_event_name": "PostToolUse", "tool_name": "Bash",
                   "tool_input": {"command": "ruff check"}}, tmp_path, now=2.0)
    assert read() == "Running: ruff check"
    ag.apply_hook({**ev, "hook_event_name": "PermissionRequest"}, tmp_path, now=3.0)
    assert read() == "Running: ruff check"          # a prompt interrupts the activity, it doesn't replace it
    ag.apply_hook({**ev, "hook_event_name": "Stop"}, tmp_path, now=4.0)
    assert read() == ""


def test_session_tokens_accumulate_over_the_whole_transcript(tmp_path):
    t = tmp_path / "t.jsonl"
    turn = lambda out, model="claude-opus-5": json.dumps(
        {"type": "assistant", "message": {"model": model, "usage": {
            "input_tokens": 10, "output_tokens": out, "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 1000}}})
    t.write_text(turn(1) + "\n" + turn(2) + "\n")
    ev = {"hook_event_name": "PostToolUse", "session_id": "cost1", "transcript_path": str(t)}
    ag.apply_hook(ev, tmp_path, now=1.0)
    rec = json.loads((tmp_path / "cost1.json").read_text())
    assert rec["tokens"] == {"in": 20, "out": 3, "cache_write": 200, "cache_read": 2000}
    assert rec["model"] == "claude-opus-5" and rec["transcript_offset"] == t.stat().st_size
    assert rec["context"]["input_tokens"] == 20 and rec["context"]["output_tokens"] == 3

    # only the appended bytes are re-read, and a partial trailing line waits for next time
    with t.open("a") as f:
        f.write(turn(5) + "\n" + turn(9)[:-4])
    ag.apply_hook(ev, tmp_path, now=2.0)
    rec = json.loads((tmp_path / "cost1.json").read_text())
    assert rec["tokens"] == {"in": 30, "out": 8, "cache_write": 300, "cache_read": 3000}
    with t.open("a") as f:
        f.write(turn(9)[-4:] + "\n")
    ag.apply_hook(ev, tmp_path, now=3.0)
    assert json.loads((tmp_path / "cost1.json").read_text())["tokens"]["out"] == 17
    # a truncated/replaced transcript starts the count over rather than freezing
    t.write_text(turn(4) + "\n")
    ag.apply_hook(ev, tmp_path, now=4.0)
    assert json.loads((tmp_path / "cost1.json").read_text())["tokens"] == {
        "in": 10, "out": 4, "cache_write": 100, "cache_read": 1000}


def test_session_cost_uses_the_models_price():
    tokens = {"in": 1_000_000, "out": 1_000_000, "cache_write": 1_000_000, "cache_read": 1_000_000}
    assert ag.session_cost_usd({"tokens": tokens, "model": "claude-opus-5[1m]"}) == 15 + 75 + 18.75 + 1.5
    assert ag.session_cost_usd({"tokens": tokens, "model": "claude-fable-5-1"}) == 15 + 75 + 18.75 + 1.5
    assert ag.session_cost_usd({"tokens": tokens, "model": "claude-sonnet-4-5"}) == 3 + 15 + 3.75 + 0.3
    assert ag.session_cost_usd({"tokens": tokens, "model": "claude-haiku-4-5"}) == 1 + 5 + 1.25 + 0.1
    assert ag.session_cost_usd({"tokens": tokens, "model": "who-knows"}) == 15 + 75 + 18.75 + 1.5  # priced high
    assert ag.session_cost_usd({}) == 0.0 and ag.session_cost_usd({"tokens": {"in": "x"}}) == 0.0


def test_snapshot_carries_activity_and_cost_and_the_key_steps_by_the_dollar():
    ag._sessions.clear()
    rec = {"started": 1, "ts": 2, "activity": "Reading api.py", "model": "claude-opus-5",
           "tokens": {"out": 100_000}}                                  # 100k output tokens = $7.50
    snap = ag._update_sessions("claude", {"a": RUNNING}, {"a": rec})
    assert snap[0]["activity"] == "Reading api.py" and snap[0]["cost_usd"] == 7.5
    assert snap[0]["model"] == "claude-opus-5" and snap[0]["tokens"] == {"out": 100_000}
    # the activity moves -> an event; the cost creeping inside the same dollar -> none
    assert ag._update_sessions("claude", {"a": RUNNING}, {"a": {**rec, "activity": "Editing api.py"}})
    assert ag._update_sessions("claude", {"a": RUNNING}, {"a": {**rec, "activity": "Editing api.py",
                                                               "tokens": {"out": 101_000}}}) is None
    assert ag._update_sessions("claude", {"a": RUNNING}, {"a": {**rec, "activity": "Editing api.py",
                                                               "tokens": {"out": 120_000}}})  # $9 -> a step


def test_codex_recovery_finds_quiet_running_tasks_after_restart(tmp_path):
    db = tmp_path / 'state.sqlite'
    now = time.time()
    with sqlite3.connect(db) as conn:
        conn.execute('create table threads (id text, rollout_path text, archived int, updated_at_ms int)')
        for tid, boundary, age in [('running', 'task_started', 7200), ('finished', 'task_complete', 7200),
                                   ('ancient', 'task_started', 30 * 24 * 3600)]:
            rollout = tmp_path / f'{tid}.jsonl'
            rollout.write_text(json.dumps({'type': 'event_msg', 'payload': {'type': boundary}}) + '\n')
            conn.execute('insert into threads values (?, ?, 0, ?)', (tid, str(rollout), int((now - age) * 1000)))
    assert codex.rollout_states(db, now=now, recover=True) == {'running': True}
    assert codex.rollout_states(db, now=now, recover=True, hooked=['ancient']) == {'running': True, 'ancient': True}
    assert codex.rollout_states(db, now=now, hooked=['ancient']) == {'ancient': True}


def test_terminal_rollout_timestamp_overrides_stale_hook(tmp_path):
    db = tmp_path / 'state.sqlite'
    rollout = tmp_path / 'task.jsonl'
    now = time.time()
    rollout.write_text(json.dumps({'type': 'event_msg', 'payload': {'type': 'task_complete'}}) + '\n')
    os.utime(rollout, (now, now))
    with sqlite3.connect(db) as conn:
        conn.execute('create table threads (id text, rollout_path text, archived int, updated_at_ms int)')
        conn.execute('insert into threads values (?, ?, 0, ?)', ('task', str(rollout), int((now - 60) * 1000)))
    metadata = {}
    truth = codex.rollout_states(db, now=now, metadata=metadata)
    assert ag.session_statuses({'task': RUNNING}, truth, {'task': {'ts': now - 30}}, metadata) == {'task': DONE}


def test_unknown_truth_timestamp_does_not_override_completion():
    assert ag.session_statuses({'task': RUNNING}, {'task': False}, {'task': {'ts': time.time()}}) == {'task': DONE}


def test_force_sync_repaints_and_reports_failed_tracking(monkeypatch):
    import pytest
    monkeypatch.setattr(ag, '_sessions', {})
    monkeypatch.setattr(ag, '_snapshot', [])
    monkeypatch.setattr(ag, '_dismissed', {})
    events = []
    integ = ag.AgentIntegration(events.append, {})
    integ.agent = 'codex'
    integ.truth = lambda hooked: {'task': True}
    integ._poll()
    events.clear()
    integ.sync()
    assert [e.type for e in events] == ['agents.sessions']
    def unavailable(hooked):
        raise OSError('locked tracker')
    integ.truth = unavailable
    with pytest.raises(RuntimeError, match='unavailable'):
        integ.sync()
    assert integ.sessions[0]['status'] == RUNNING
    assert integ.sessions[0]['tracking_health'] == 'unavailable'


def test_claude_metadata_does_not_seat_idle_desktop_tabs(monkeypatch):
    monkeypatch.setattr(ag, '_sessions', {})
    monkeypatch.setattr(ag, '_snapshot', [])
    monkeypatch.setattr(ag, '_dismissed', {})
    def states(metadata):
        metadata.update({'idle': {'cwd': '/project', 'ts': 1}, 'busy': {'cwd': '/project', 'ts': 2}})
        return {'idle': False, 'busy': True}
    monkeypatch.setattr(claude_code, 'transcript_states', states)
    integ = claude_code.ClaudeCode(lambda e: None, {})
    integ._poll()
    assert [s['id'] for s in integ.sessions] == ['busy']


def test_dismissal_does_not_hide_other_tasks_in_the_same_project():
    ag.slots.remember('dismissed', '/project', slot=2, dismissed_status=RUNNING)
    assert ag.slots.pinned('dismissed', '/project')['dismissed_status'] == RUNNING
    assert ag.slots.pinned('new-task', '/project').get('dismissed_status') is None
    assert ag.slots.pinned('new-task', '/project')['slot'] == 2


def test_codex_retries_startup_recovery_after_tracking_failure(monkeypatch):
    monkeypatch.setattr(ag, '_sessions', {})
    monkeypatch.setattr(ag, '_snapshot', [])
    calls = []
    def states(**kwargs):
        calls.append(kwargs['recover'])
        if len(calls) == 1:
            raise codex.TrackingUnavailable('locked')
        return {'quiet': True}
    monkeypatch.setattr(codex, 'rollout_states', states)
    integ = codex.Codex(lambda e: None, {})
    integ._poll()
    integ._poll()
    assert calls == [True, True]
    assert integ.sessions[0]['status'] == RUNNING
