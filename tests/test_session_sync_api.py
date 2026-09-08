from lumen.integrations.agent_sessions import AgentIntegration


def test_force_sync_refreshes_each_agent_and_repaints(server, monkeypatch):
    call, engine, *_ = server
    calls = []
    agent = AgentIntegration(engine.bus.emit, {})
    monkeypatch.setattr(agent, "sync", lambda: calls.append("sync"), raising=False)
    engine.integrations.append(agent)
    monkeypatch.setattr(engine.player, "repaint", lambda: calls.append("repaint"))
    status, result = call("POST", "/api/sync")
    assert status == 200
    assert result["synced"] == [agent.id]
    assert calls == ["sync", "repaint"]
    assert isinstance(result["sessions"], list)


def test_force_sync_reports_failure_and_still_syncs_other_agents(server, monkeypatch):
    call, engine, *_ = server
    calls = []
    broken = AgentIntegration(engine.bus.emit, {})
    working = AgentIntegration(engine.bus.emit, {})

    def fail():
        raise OSError("private/path unreadable")

    monkeypatch.setattr(broken, "sync", fail, raising=False)
    monkeypatch.setattr(working, "sync", lambda: calls.append("sync"), raising=False)
    engine.integrations.extend([broken, working])
    status, result = call("POST", "/api/sync")
    assert status == 503
    assert "error" in result
    assert "private/path" not in result["error"]
    assert calls == ["sync"]
