

def test_usage_meters_follow_the_open_tabs():
    from lumen.devices.notch import visible_usage
    usage = {"claude": {"five_hour": {"used": 20}}, "codex": {"seven_day": {"used": 100}}}
    show = {"claude_usage": True, "codex_usage": True}
    claude_only = [{"agent": "claude"}]
    both = [{"agent": "claude"}, {"agent": "codex"}]

    # Codex closed: its meter goes with it, however true the number still is.
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
