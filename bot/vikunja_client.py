"""
Vikunja API client.
Wraps the operations the bot needs: create/list/update/delete tasks
and resolve project names to IDs.
"""

import os
import requests
from datetime import datetime, date


class VikunjaError(Exception):
    pass


# Project name aliases — same idea as accounts.py
PROJECT_ALIASES = {
    "work":     "Work",
    "office":   "Work",
    "personal": "Personal",
}


# Readable recurrence mode → Vikunja repeat_mode integer:
# 0 = repeat after each completion (e.g. every 7 days from when last done)
# 1 = repeat monthly (1st of every month, etc.)
# 2 = repeat from the original due date
REPEAT_MODE = {
    "after_done": 0,
    "monthly":    1,
    "from_date":  2,
}


class VikunjaClient:
    def __init__(self, base_url: str = None, token: str = None):
        self.base_url = (base_url or os.environ["VIKUNJA_URL"]).rstrip("/")
        self.token = token or os.environ["VIKUNJA_TOKEN"]
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self._project_cache = None  # name → id

    def _request(self, method: str, path: str, *, action: str, timeout: int = 10, **kwargs):
        try:
            resp = self.session.request(
                method,
                f"{self.base_url}{path}",
                timeout=timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise VikunjaError(f"{action} failed: {exc}") from exc

        if not resp.ok:
            raise VikunjaError(
                f"{action} failed: {resp.status_code} {resp.text[:300]}"
            )
        return resp

    # ---------- Projects ----------

    def _fetch_projects(self):
        resp = self._request("GET", "/api/v1/projects", action="Fetch projects")
        return {p["title"]: p["id"] for p in resp.json()}

    def _ensure_projects(self, refresh=False):
        if self._project_cache is None or refresh:
            self._project_cache = self._fetch_projects()

    def resolve_project(self, name: str) -> int:
        """Resolve a fuzzy project name (e.g. 'work', 'office') to a Vikunja
        project ID. Returns the ID, or raises VikunjaError if not found."""
        if not name:
            raise VikunjaError("Empty project name")
        self._ensure_projects()
        # Exact match first
        if name in self._project_cache:
            return self._project_cache[name]
        # Alias lookup
        canonical = PROJECT_ALIASES.get(name.strip().lower())
        if canonical and canonical in self._project_cache:
            return self._project_cache[canonical]
        raise VikunjaError(
            f"Unknown project '{name}'. Known: {list(self._project_cache.keys())}"
        )

    # ---------- Tasks ----------

    def create_task(self, parsed: dict) -> dict:
        """
        Create a task from a parsed todo dict.
        Expected keys:
            title (required)
            project (required, resolvable name)
            due_date (optional, ISO date or datetime string)
            priority (optional: 0=none, 1=low, 2=medium, 3=high, 4=urgent, 5=do-now)
            recurrence (optional: dict like {"mode": "monthly", "interval_days": 30})
        """
        project_id = self.resolve_project(parsed["project"])

        payload = {
            "title": parsed["title"],
        }

        if parsed.get("due_date"):
            # Vikunja wants RFC3339; ISO with timezone works
            payload["due_date"] = self._to_rfc3339(parsed["due_date"])

        if parsed.get("priority") is not None:
            payload["priority"] = int(parsed["priority"])

        # Recurrence
        rec = parsed.get("recurrence")
        if rec:
            interval = int(rec.get("interval_days", 0))
            if interval > 0:
                payload["repeat_after"] = interval * 86400  # seconds in a day
                mode_str = rec.get("mode", "from_date")
                payload["repeat_mode"] = REPEAT_MODE.get(mode_str, 2)

        resp = self._request(
            "PUT",
            f"/api/v1/projects/{project_id}/tasks",
            action="Task create",
            json=payload,
            timeout=10,
        )
        return resp.json()

    def update_task(self, task_id: int, **fields) -> dict:
        """Update specific fields on a task. Common uses:
            update_task(123, done=True)
            update_task(123, due_date='2026-05-15')
            update_task(123, title='New title')
        """
        # Fetch current task first (Vikunja's API wants the full object on POST)
        current = self.get_task(task_id)
        for k, v in fields.items():
            if k == "due_date" and v:
                current[k] = self._to_rfc3339(v)
            else:
                current[k] = v

        resp = self._request(
            "POST",
            f"/api/v1/tasks/{task_id}",
            action="Task update",
            json=current,
            timeout=10,
        )
        return resp.json()

    def get_task(self, task_id: int) -> dict:
        resp = self._request(
            "GET",
            f"/api/v1/tasks/{task_id}",
            action="Task fetch",
            timeout=10,
        )
        return resp.json()

    def delete_task(self, task_id: int) -> bool:
        self._request(
            "DELETE",
            f"/api/v1/tasks/{task_id}",
            action="Task delete",
            timeout=10,
        )
        return True

    def mark_done(self, task_id: int) -> dict:
        return self.update_task(task_id, done=True)

    def list_tasks(self, project_filter: str = None,
                   include_done: bool = False) -> list:
        """List tasks across all projects (or filtered to one).
        By default excludes done tasks."""
        self._ensure_projects()

        if project_filter:
            project_id = self.resolve_project(project_filter)
            project_ids = [project_id]
        else:
            # All projects except Inbox
            project_ids = [pid for name, pid in self._project_cache.items()
                           if name != "Inbox"]

        all_tasks = []
        for pid in project_ids:
            page = 1
            while True:
                resp = self._request(
                    "GET",
                    f"/api/v1/projects/{pid}/tasks",
                    action=f"List tasks for project {pid}",
                    params={"per_page": 100, "page": page},
                    timeout=10,
                )
                tasks = resp.json() or []
                if not tasks:
                    break
                for t in tasks:
                    if not include_done and t.get("done"):
                        continue
                    all_tasks.append(t)
                if len(tasks) < 100:
                    break
                page += 1
        return all_tasks

    # ---------- Helpers ----------

    @staticmethod
    def _to_rfc3339(dt) -> str:
        """Accept a date, datetime, or ISO string; return RFC3339 string with Z suffix."""
        if isinstance(dt, str):
            # If it's already a date-only string like "2026-05-02", convert it
            if "T" not in dt:
                return f"{dt}T00:00:00Z"
            # If it has time but no timezone, add Z
            if not (dt.endswith("Z") or "+" in dt[10:] or dt.count("-") > 2):
                return dt + "Z"
            return dt
        if isinstance(dt, datetime):
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(dt, date):
            return f"{dt.isoformat()}T00:00:00Z"
        raise ValueError(f"Unsupported date type: {type(dt)}")

# ---------- Self-test ----------

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    client = VikunjaClient()
    projects = client._fetch_projects()
    print(f"Connected. Found {len(projects)} projects:")
    for name, pid in sorted(projects.items()):
        print(f"  {pid}  {name}")
