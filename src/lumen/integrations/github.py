"""GitHub Actions, polled through the `gh` CLI (already authenticated on most
developer machines — `gh auth login` if not).

Set the repositories to watch in the dashboard; every finished workflow run
raises `github.workflow.succeeded` or `github.workflow.failed` with the repo,
workflow name and branch. The first poll only seeds what is already finished,
so nothing fires for old runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading

from lumen.core.events import Event
from lumen.core.integrations import Integration


def list_runs(repo: str, limit: int = 10) -> list[dict]:
    flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    out = subprocess.run(["gh", "run", "list", "--repo", repo, "--limit", str(limit),
                          "--json", "databaseId,status,conclusion,name,headBranch,workflowName"],
                         capture_output=True, text=True, timeout=30, **flags)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200] or "gh failed")
    return json.loads(out.stdout or "[]")


class GitHub(Integration):
    id = "github"
    name = "GitHub Actions"
    description = "Workflow runs in the repositories you choose, via the gh CLI."
    events = ("github.workflow.succeeded", "github.workflow.failed")
    option_fields = {
        "repos": {"label": "Repositories", "type": "text", "placeholder": "owner/repo, owner/other-repo"},
        "interval_s": {"label": "Poll every (seconds)", "type": "number", "placeholder": "60"},
    }
    docs = "Needs the [GitHub CLI](https://cli.github.com) signed in (`gh auth login`). Polling, so expect up to one interval of delay."

    def __init__(self, emit, options):
        super().__init__(emit, options)
        self._stop = threading.Event()
        self._seen: dict[str, set[int]] = {}
        self._error = ""

    def repos(self) -> list[str]:
        return [r.strip() for r in str(self.options.get("repos", "")).replace(";", ",").split(",") if r.strip()]

    def start(self) -> None:
        threading.Thread(target=self._loop, name="lumen-github", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if shutil.which("gh") and self.repos():
                self._poll()
            try:
                interval = max(15, int(self.options.get("interval_s") or 60))
            except (TypeError, ValueError):
                interval = 60
            self._stop.wait(interval)

    def _poll(self) -> None:
        for repo in self.repos():
            try:
                runs = list_runs(repo)
                self._error = ""
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as e:
                self._error = f"{repo}: {e}"
                continue
            done = {r["databaseId"]: r for r in runs if r.get("status") == "completed"}
            if repo not in self._seen:
                self._seen[repo] = set(done)  # seed silently
                continue
            for run_id, run in done.items():
                if run_id in self._seen[repo]:
                    continue
                self._seen[repo].add(run_id)
                ok = run.get("conclusion") == "success"
                self.emit(Event("github.workflow.succeeded" if ok else "github.workflow.failed", "github", {
                    "repo": repo, "workflow": run.get("workflowName") or run.get("name", ""),
                    "branch": run.get("headBranch", ""), "conclusion": run.get("conclusion", ""), "run_id": run_id,
                }))

    def status(self) -> dict:
        if not shutil.which("gh"):
            return {"connected": False, "detail": "gh CLI not found"}
        if not self.repos():
            return {"connected": False, "detail": "no repositories configured"}
        if self._error:
            return {"connected": False, "detail": self._error}
        return {"connected": True, "detail": f"watching {', '.join(self.repos())}"}


INTEGRATION = GitHub
