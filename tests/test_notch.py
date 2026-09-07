

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
