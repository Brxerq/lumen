"""Webhook: anything that can make an HTTP request can raise an event.

    curl -X POST http://127.0.0.1:6733/api/events \
         -H 'Content-Type: application/json' \
         -d '{"type": "build.failed", "data": {"name": "web"}}'

The endpoint lives in lumen.server; this class only describes it for the
dashboard. The server binds to loopback, so to reach it from CI or another
machine put a tunnel or reverse proxy in front and set `webhook_token` in
Settings (sent as `Authorization: Bearer <token>`).
"""

from __future__ import annotations

from lumen.core.integrations import Integration


class Webhook(Integration):
    id = "webhook"
    name = "Webhook"
    description = "Raise any event with an HTTP POST — CI pipelines, scripts, other tools."
    events = ("*",)
    docs = ("POST JSON to `/api/events` with `{\"type\": \"build.failed\", \"data\": {...}}`. "
            "Any dotted type works; the catalog types get nicer labels. GitHub Actions example:\n\n"
            "```yaml\n- run: curl -sS -X POST $LUMEN_URL/api/events -H 'Authorization: Bearer ${{ secrets.LUMEN_TOKEN }}' "
            "-d '{\"type\":\"build.${{ job.status == 'success' && 'succeeded' || 'failed' }}\"}'\n```")

    def status(self) -> dict:
        port = getattr(self, "settings", {}).get("port", 6733)
        return {"connected": True, "detail": f"POST http://127.0.0.1:{port}/api/events"}


INTEGRATION = Webhook
