

def test_notch_shows_only_requested_account_windows():
    from lumen.devices.notch import clean_usage
    block = {"used": 42, "resets_at": 1234}
    summary = dict.fromkeys(("five_hour", "seven_day", "seven_day_sonnet", "daily", "gemini_pro", "gemini_flash"), block)
    assert clean_usage(dict.fromkeys(("claude", "codex", "gemini"), summary)) == {
        "claude": {"five_hour": block, "seven_day": block},
        "codex": {"seven_day": block},
        "gemini": {"five_hour": block, "seven_day": block},
    }
    assert clean_usage({"gemini": {"daily": block, "gemini_pro": block}}) == {"gemini": {}}
    assert clean_usage({"five_hour": block}) == {"claude": {"five_hour": block}}


def test_notch_reuses_fonts_between_animation_frames():
    from lumen.devices.notch import _font
    _font.cache_clear()
    assert _font(33, bold=True) is _font(33, bold=True)
    assert _font.cache_info().hits == 1


def test_minimal_usage_rows_expand_independently_and_keep_hit_positions():
    from lumen.devices.notch import HEIGHT, ROW, clean_usage, render, usage_layout
    block = {"used": 42, "resets_at": 7200}
    usage = clean_usage({"claude": {"five_hour": block, "seven_day": block},
                         "codex": {"seven_day": block}, "gemini": None})
    folded = usage_layout(usage, 2)
    expanded = usage_layout(usage, 2, {"claude", "gemini"})
    assert expanded[0][2] == folded[0][2]
    assert expanded[0][3] == ROW + 16
    assert expanded[1][2] == folded[1][2] + ROW + 16
    assert expanded[2][3] == 0  # unavailable is never an empty expandable panel
    for size in ("thin", "regular", "thick"):
        kwargs = dict(zones=[(255, 180, 0)] * 6, rows=[], palette={}, usage=usage, unfolded=True, size=size)
        closed = render(**kwargs)
        opened = render(**kwargs, expanded_usage={"claude"})
        assert opened.height - closed.height == ROW + 16
        assert closed.height > HEIGHT


def test_single_bar_uses_remaining_allowance_and_provider_window():
    from lumen.devices.notch import bar_window, remaining, remaining_label, usage_layout
    summary = {"five_hour": {"used": 21.72}, "seven_day": {"used": 68}}
    assert remaining(summary["five_hour"]) == 78.28
    assert remaining_label(summary["five_hour"]) == "78.28% left"
    assert remaining_label(summary["seven_day"]) == "32% left"
    assert bar_window("claude", summary) == "five_hour"
    assert bar_window("gemini", summary) == "five_hour"
    assert bar_window("codex", summary) == "seven_day"
    assert bar_window("claude", {"seven_day": {"used": 68}}) is None
    assert usage_layout({"claude": {"seven_day": {"used": 68}}}, 0, {"claude"})[0][3] == 0
    assert remaining({"used": 120}) == 0 and remaining({"used": -1}) == 100


def test_usage_meters_follow_the_open_tabs():
    from lumen.devices.notch import visible_usage
    usage = {"claude": {"five_hour": {"used": 20}}, "codex": {"seven_day": {"used": 100}}}
    show = {"claude_usage": True, "codex_usage": True, "usage_follows_tabs": True}
    claude_only = [{"agent": "claude", "status": "running"}]
    both = [{"agent": "claude", "status": "running"}, {"agent": "codex", "status": "input"}]

    # by default a known limit stays up whether or not that agent has a tab open
    assert sorted(visible_usage(usage, claude_only, {**show, "usage_follows_tabs": False})) == ["claude", "codex"]
    assert sorted(visible_usage(usage, [], {"claude_usage": True, "codex_usage": True})) == ["claude", "codex"]
    # opted in: Codex closed, its meter goes with it, however true the number still is.
    assert list(visible_usage(usage, claude_only, show)) == ["claude"]
    assert sorted(visible_usage(usage, both, show)) == ["claude", "codex"]
    assert visible_usage(usage, [], show) == {}
    assert list(visible_usage(usage, [{"agent": "claude", "status": "done"}], show)) == ["claude"]
    assert list(visible_usage(usage, [*claude_only, {"agent": "codex", "status": "done", "ts": 1}], show)) == ["claude"]
    # the two existing filters still apply on top
    assert list(visible_usage(usage, both, {**show, "codex_usage": False})) == ["claude"]
    assert list(visible_usage(usage, both, show, agents="codex")) == ["codex"]


def test_quota_rows_follow_recent_completed_tabs_and_their_expiry(monkeypatch):
    from lumen.devices import notch
    monkeypatch.setattr(notch.time, "time", lambda: 10000)
    usage = {a: {"seven_day": {"used": 20}} for a in ("claude", "codex", "gemini")}
    sessions = [{"agent": "claude", "status": "done", "ts": 9900},
                {"agent": "codex", "status": "running", "ts": 1},
                {"agent": "gemini", "status": "done", "ts": 1}]
    show = {"usage_follows_tabs": True}
    assert list(notch.visible_usage(usage, sessions, show)) == ["claude", "codex"]
    assert list(notch.visible_usage(usage, sessions, show, options={"completed_hide_min": 1})) == ["codex"]
    assert list(notch.visible_usage(usage, sessions, show, options={"show": {"sessions": False}})) == ["claude", "codex"]


def test_the_tab_can_sit_anywhere_along_the_edge():
    from lumen.devices.notch import offset_for, tab_x
    # no offset: the preset corners
    assert tab_x(1000, 200, "top") == 400 and tab_x(1000, 200, "bottom") == 400
    assert tab_x(1000, 200, "top-left") == 24 and tab_x(1000, 200, "top-right") == 776
    # an offset is the tab's centre, and never hangs off the screen
    assert tab_x(1000, 200, "top", 50) == 400
    assert tab_x(1000, 200, "top-left", 0) == 0 and tab_x(1000, 200, "top", 100) == 800
    assert tab_x(1000, 200, "top", 150) == 800
    # a drag round-trips through the percentage it saves
    for x in (0, 123, 400, 800):
        assert abs(tab_x(1000, 200, "top", offset_for(1000, 200, x)) - x) <= 5
    assert offset_for(0, 200, 10) == 50


def test_thin_and_thick_tabs_render():
    from lumen.core.rules import DEFAULT_PALETTE
    from lumen.devices.notch import SIZES, panel_row_at, render
    zones = [(240, 170, 40)] * 6
    heights = {s: render(zones, [], DEFAULT_PALETTE, size=s).height for s in SIZES}
    widths = {s: render(zones, [], DEFAULT_PALETTE, size=s).width for s in SIZES}
    assert heights["thin"] < heights["regular"] < heights["thick"]
    assert widths["thin"] < widths["regular"] < widths["thick"]
    assert render(zones, [], DEFAULT_PALETTE, size="bogus").height == heights["regular"]
    # the click map follows the folded height
    assert panel_row_at(SIZES["thin"][0] + 12, 1, SIZES["thin"][0]) == 0
    assert panel_row_at(SIZES["thin"][0] + 12, 1, SIZES["thick"][0]) is None


def test_clickable_notch_sessions_match_the_filtered_rows():
    from lumen.devices.notch import session_rows, visible_sessions
    sessions = [{"id": "codex", "agent": "codex", "slot": 0}, {"id": "claude", "agent": "claude", "slot": 1}]
    assert [s["id"] for s in visible_sessions(sessions, {"agents": "claude", "show": {"sessions": True}})] == ["claude"]
    assert visible_sessions(sessions, {"agents": "all", "show": {"sessions": False}}) == []
    assert visible_sessions(sessions, {"agents": "all", "show": True}) == sessions
    assert session_rows([{"agent": "codex", "title": "Quiet task", "status": "running"}])[0][1] == "Quiet task"


def test_notch_only_advertises_focus_for_claude(monkeypatch):
    from lumen.devices.notch import focus_session
    monkeypatch.setattr("lumen.devices.notch.session_pids", lambda: {"claude": 42})
    assert focus_session({"id": "codex", "agent": "codex"}) is False


def test_notch_prioritizes_running_rows_and_keeps_click_targets_aligned():
    from lumen.devices.notch import session_rows, visible_sessions

    sessions = [
        {"id": "idle", "agent": "codex", "title": "Idle", "slot": 0, "status": "done"},
        {"id": "waiting", "agent": "claude", "title": "Waiting", "slot": 1, "status": "input"},
        {"id": "busy", "agent": "codex", "title": "Busy", "slot": 7, "status": "running"},
    ]
    visible = visible_sessions(sessions, {})
    assert [s["id"] for s in visible] == ["busy", "waiting", "idle"]
    assert [r[1] for r in session_rows(visible)] == ["Busy", "Waiting", "Idle"]
    assert [s["id"] for s in visible_sessions(sessions, {"agents": "codex"})] == ["busy", "idle"]
    assert [r[1] for r in session_rows(sessions, prioritize=False)] == ["Idle", "Waiting", "Busy"]
    assert sessions[0]["id"] == "idle"


def test_settings_accept_the_new_notch_knobs():
    import pytest

    from lumen.core.config import _coerce
    assert _coerce("notch_offset", 37) == 37 and _coerce("notch_offset", -1) == -1
    assert _coerce("notch_size", "thin") == "thin" and _coerce("notch_opacity", "80") == 80
    assert _coerce("notch_completed_hide_min", "30") == 30
    for key, bad in (("notch_offset", 101), ("notch_opacity", 10), ("notch_completed_hide_min", 1441), ("notch_size", "huge")):
        with pytest.raises(ValueError):
            _coerce(key, bad)


def test_the_tab_hides_when_nothing_has_happened_for_a_while():
    from lumen.devices.notch import idle_hidden
    quiet = [{"status": "done", "ts": 1_000}]
    assert idle_hidden(quiet, 0, now=1_000 + 99_999) is False          # 0 = never
    assert idle_hidden(quiet, 10, now=1_000 + 599) is False
    assert idle_hidden(quiet, 10, now=1_000 + 601) is True
    assert idle_hidden([{"status": "running", "ts": 1_000}], 10, now=1_000 + 601) is False   # still working
    assert idle_hidden([{"status": "input", "ts": 1_000}], 10, now=1_000 + 601) is False     # still waiting on you
    assert idle_hidden([], 10, now=100_000) is True                     # no tabs at all counts as idle


def test_completed_tabs_age_out_without_hiding_active_tabs():
    from lumen.devices.notch import visible_sessions

    sessions = [
        {"id": "old", "status": "done", "ts": 1_000},
        {"id": "recent", "status": "done", "ts": 2_500},
        {"id": "working", "status": "running", "ts": 1_000},
        {"id": "waiting", "status": "input", "ts": 1_000},
    ]
    assert [s["id"] for s in visible_sessions(sessions, {"completed_hide_min": 30}, now=3_000)] == ["working", "waiting", "recent"]
    assert [s["id"] for s in visible_sessions(sessions, {"completed_hide_min": 0}, now=99_000)] == ["working", "waiting", "old", "recent"]


def test_bars_carry_their_agents_colour():
    from lumen.core.rules import DEFAULT_PALETTE
    from lumen.devices.notch import AGENT_ACCENT, bar_accents, render
    rows = [("claude", "a", "running", 10, "", None), ("codex", "b", "done", None, "", None), ("other", "c", "done", None, "", None)]
    assert bar_accents(rows) == [AGENT_ACCENT["claude"], AGENT_ACCENT["codex"], None]
    assert bar_accents(rows, show=False) is None
    zones = [(240, 170, 40)] * 3 + [(95, 227, 106)] * 3
    plain = render(zones, rows[:2], DEFAULT_PALETTE, (1, 0, 1), [10, None])
    capped = render(zones, rows[:2], DEFAULT_PALETTE, (1, 0, 1), [10, None], accents=bar_accents(rows[:2]))
    assert plain.size == capped.size and plain.tobytes() != capped.tobytes()
    assert AGENT_ACCENT["claude"] in {capped.getpixel((x, capped.height // 2))[:3] for x in range(capped.width)}


def test_left_and_right_edges_round_the_panel_and_place_along_the_height():
    from lumen.core.rules import DEFAULT_PALETTE
    from lumen.devices.notch import POSITIONS, VERTICAL, render, tab_x
    assert set(VERTICAL) <= set(POSITIONS)
    zones = [(240, 170, 40)] * 6
    flat = render(zones, [], DEFAULT_PALETTE, unfolded=True)
    round_ = render(zones, [], DEFAULT_PALETTE, unfolded=True, rounded=True)
    assert flat.getpixel((0, 0))[3] > 0 and round_.getpixel((0, 0))[3] == 0   # the top corners are cut only when rounded
    assert tab_x(1080, 200, "top", 50) == 440                                  # the same rule, run along the height
