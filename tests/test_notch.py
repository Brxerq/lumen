

def test_usage_meters_follow_the_open_tabs():
    from lumen.devices.notch import visible_usage
    usage = {"claude": {"five_hour": {"used": 20}}, "codex": {"seven_day": {"used": 100}}}
    show = {"claude_usage": True, "codex_usage": True, "usage_follows_tabs": True}
    claude_only = [{"agent": "claude"}]
    both = [{"agent": "claude"}, {"agent": "codex"}]

    # by default a known limit stays up whether or not that agent has a tab open
    assert sorted(visible_usage(usage, claude_only, {**show, "usage_follows_tabs": False})) == ["claude", "codex"]
    assert sorted(visible_usage(usage, [], {"claude_usage": True, "codex_usage": True})) == ["claude", "codex"]
    # opted in: Codex closed, its meter goes with it, however true the number still is.
    assert list(visible_usage(usage, claude_only, show)) == ["claude"]
    assert sorted(visible_usage(usage, both, show)) == ["claude", "codex"]
    assert visible_usage(usage, [], show) == {}
    # the two existing filters still apply on top
    assert list(visible_usage(usage, both, {**show, "codex_usage": False})) == ["claude"]
    assert list(visible_usage(usage, both, show, agents="codex")) == ["codex"]


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


def test_settings_accept_the_new_notch_knobs():
    import pytest

    from lumen.core.config import _coerce
    assert _coerce("notch_offset", 37) == 37 and _coerce("notch_offset", -1) == -1
    assert _coerce("notch_size", "thin") == "thin" and _coerce("notch_opacity", "80") == 80
    for key, bad in (("notch_offset", 101), ("notch_opacity", 10), ("notch_size", "huge")):
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
