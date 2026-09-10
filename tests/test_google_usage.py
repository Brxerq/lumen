"""Google / Gemini and Antigravity usage limits and session tracking tests."""

from lumen.integrations import gemini
from lumen.integrations import google_usage as gu


def test_google_usage_summarize_various_payloads():
    data = {
        "daily": {"utilization": 45.2, "resets_at": 1788700000},
        "five_hour": {"used": 15},
        "gemini_pro": {"utilization": 82.6},
        "gemini_flash": {"used_percent": 30},
        "extra_unknown": 123,
    }
    out = gu.summarize(data)
    assert out["daily"]["used"] == 45 and out["daily"]["resets_at"] == 1788700000.0
    assert out["five_hour"]["used"] == 15 and out["five_hour"]["resets_at"] is None
    assert out["gemini_pro"]["used"] == 83
    assert out["gemini_flash"]["used"] == 30
    assert "extra_unknown" not in out

    # Ordering and labels
    ord_keys = gu.ordered(out)
    assert ord_keys == ["five_hour", "daily", "gemini_flash", "gemini_pro"]
    assert gu.label("daily") == "Daily · 24 hours"
    assert gu.label("five_hour") == "Session · 5 hours"
    assert gu.label("gemini_pro") == "Gemini Pro"
    assert gu.label("gemini_flash") == "Gemini Flash"


def test_google_usage_credentials_and_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(gu, "_latest", None)
    fake_token = {"access_token": "ya29.test", "type": "gemini"}
    monkeypatch.setattr(gu, "credentials", lambda: fake_token)
    monkeypatch.setattr(gu, "fetch", lambda tok, **kw: {"daily": {"utilization": 25.0}})

    res = gu.refresh()
    assert res is not None
    assert res["daily"]["used"] == 25
    assert gu.latest() == res


def test_gemini_scanner_finds_antigravity_sessions(tmp_path):
    # Set up mock gemini / antigravity directory structure
    root = tmp_path / ".gemini"
    base = root / "antigravity"
    conv_dir = base / "conversations"
    annot_dir = base / "annotations"
    conv_dir.mkdir(parents=True)
    annot_dir.mkdir(parents=True)

    # SQLite DB with steps table
    import sqlite3
    db_file = conv_dir / "test-uuid.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("CREATE TABLE steps (idx INT, step_type INT, status INT)")
    cur.execute("INSERT INTO steps VALUES (1, 0, 2)")
    conn.commit()
    conn.close()

    # Annotation file with title
    annot_file = annot_dir / "test-uuid.pbtxt"
    annot_file.write_text('title: "Fix test failure"\n')

    meta = {}
    states = gemini.antigravity_states(home=root, metadata=meta)
    assert states == {"test-uuid": True}
    assert "test-uuid" in meta
    s = meta["test-uuid"]
    assert s["status"] == gemini.RUNNING
    assert s["title"] == "Fix test failure"
    assert s["cwd"] == "Antigravity"


def test_notch_renders_gemini_agent_and_quota():
    from lumen.core.rules import DEFAULT_PALETTE
    from lumen.devices.notch import AGENT_ACCENT, render

    assert "gemini" in AGENT_ACCENT
    zones = [(66, 133, 244)] * 6
    rows = [("gemini", "Antigravity Task", "running", 15, "C:/Github/lumen", None)]
    img = render(zones, rows, DEFAULT_PALETTE, unfolded=True, usage={"gemini": {"daily": {"used": 50}}})
    assert img.width > 0 and img.height > 0
