import json
import os
import sqlite3
import time

from lumen.core.events import Event
from lumen.integrations import agent_sessions as ag
from lumen.integrations import claude_code, codex
from lumen.integrations.agent_sessions import DONE, INPUT, RUNNING


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
                                            "started": None, "ts": None, "cwd": "", "context": None}]
    types = [(e.type, e.data.get("status")) for e in events if e.type != "agents.sessions"]
    # first poll settles the aggregate without a per-agent flash; then each change emits both
    assert types == [("agents.status", "done"),
                     ("agent.running", "running"), ("agents.status", "running"),
                     ("agent.needs_input", "input"), ("agents.status", "input"),
                     ("agent.finished", "done"), ("agents.status", "done")]


def test_session_slots_are_sticky_across_agents(tmp_path):
    ag._sessions.clear()
    rec = lambda started, cwd="": {"started": started, "cwd": cwd}
    snap = ag._update_sessions("claude", {"b": RUNNING, "a": DONE}, {"a": rec(1, "C:/x"), "b": rec(2)})
    assert [(s["id"], s["slot"], s["cwd"]) for s in snap] == [("b", 0, ""), ("a", 1, "C:/x")]  # b started last
    snap = ag._update_sessions("codex", {"c": INPUT}, {})                   # a newcomer takes zone 0
    assert [(s["id"], s["slot"]) for s in snap] == [("c", 0), ("b", 1), ("a", 2)]
    assert ag._update_sessions("codex", {"c": INPUT}, {}) is None          # nothing changed
    snap = ag._update_sessions("claude", {"b": DONE, "d": RUNNING}, {})     # a ended; d opens on zone 0
    assert [(s["id"], s["slot"], s["status"]) for s in snap] == [("d", 0, RUNNING), ("c", 1, INPUT), ("b", 2, DONE)]
    assert [s["id"] for s in ag.all_sessions()] == ["d", "c", "b"]
    # idle sessions known only from the agent's record (no hook file) take no zone until they work,
    # then keep it: Claude Desktop keeps every old tab's process alive
    snap = ag._update_sessions("claude", {"b": DONE, "d": RUNNING, "e": DONE, "f": DONE}, {})
    assert snap is None and "e" not in ag._sessions
    snap = ag._update_sessions("claude", {"b": DONE, "d": RUNNING, "e": RUNNING, "f": DONE}, {})
    assert [(s["id"], s["slot"]) for s in snap] == [("e", 0), ("d", 1), ("c", 2), ("b", 3)]
    snap = ag._update_sessions("claude", {"b": DONE, "d": RUNNING, "e": DONE, "f": DONE}, {})
    assert [(s["id"], s["status"]) for s in snap][0] == ("e", DONE)
    # hook files keep `started` and cwd, and can be forgotten from the dashboard
    ev = {"hook_event_name": "UserPromptSubmit", "session_id": "s9", "cwd": "C:/proj"}
    ag.apply_hook(ev, tmp_path, now=10.0)
    ag.apply_hook({**ev, "hook_event_name": "Stop"}, tmp_path, now=20.0)
    r = ag.read_sessions(tmp_path, now=21.0)["s9"]
    assert (r["started"], r["ts"], r["cwd"], r["status"]) == (10.0, 20.0, "C:/proj", DONE)
    assert ag.forget_session("s9", tmp_path) and not ag.forget_session("s9", tmp_path)
    assert ag.read_sessions(tmp_path, now=21.0) == {}


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
    assert rec["context"] == {"tokens": 400_000, "window": 1_000_000}
    a = ag._key([{"id": "x", "agent": "claude", "slot": 0, "status": RUNNING, "context": {"tokens": 41, "window": 100}}])
    b = ag._key([{"id": "x", "agent": "claude", "slot": 0, "status": RUNNING, "context": {"tokens": 44, "window": 100}}])
    c = ag._key([{"id": "x", "agent": "claude", "slot": 0, "status": RUNNING, "context": {"tokens": 46, "window": 100}}])
    assert a == b != c
