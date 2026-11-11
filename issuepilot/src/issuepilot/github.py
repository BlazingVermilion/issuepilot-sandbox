"""Restricted GitHub adapter. The agent never receives write credentials."""

import httpx
from sqlalchemy import select
from .db import DemoIssue


class GitHub:
    def __init__(self, settings, sessions):
        self.settings, self.sessions = settings, sessions
        self.client = httpx.Client(
            base_url="https://api.github.com",
            timeout=20,
            headers={
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def close(self):
        self.client.close()

    def list_labels(self):
        if self.settings.mode == "demo":
            from .labels import DEMO_LABELS

            return [dict(x) for x in DEMO_LABELS]
        labels = []
        for page in range(1, 101):
            response = self.client.get(
                f"/repos/{self.settings.repo}/labels", params={"per_page": 100, "page": page}
            )
            response.raise_for_status()
            batch = response.json()
            labels.extend(batch)
            if len(batch) < 100:
                return labels
        raise ValueError("Label pagination limit reached; refusing an incomplete catalog")

    def read(self, number):
        if self.settings.mode == "demo":
            with self.sessions() as session:
                row = session.get(DemoIssue, number)
                if not row:
                    raise ValueError("Demo issue missing")
                return row.data
        response = self.client.get(f"/repos/{self.settings.repo}/issues/{number}")
        response.raise_for_status()
        issue = response.json()
        if "pull_request" in issue:
            raise ValueError("Pull requests are out of scope")
        return {
            "number": number,
            "title": issue["title"],
            "body": issue["body"] or "",
            "state": issue["state"],
            "updated_at": issue["updated_at"],
            "labels": sorted(x["name"] for x in issue["labels"]),
            "assignees": sorted(x["login"] for x in issue["assignees"]),
        }

    def marker_exists(self, number, marker):
        if self.settings.mode == "demo":
            with self.sessions() as session:
                return any(marker in x for x in session.get(DemoIssue, number).comments)
        # Paginate rather than checking only the first 30 comments.
        for page in range(1, 101):
            result = self.client.get(
                f"/repos/{self.settings.repo}/issues/{number}/comments",
                params={"per_page": 100, "page": page},
            )
            result.raise_for_status()
            comments = result.json()
            if any(marker in c.get("body", "") for c in comments):
                return True
            if len(comments) < 100:
                return False
        raise ValueError("Comment pagination limit reached; manual reconciliation required")

    def write(self, number, kind, payload):
        if self.settings.mode == "live" and not self.settings.writes_enabled:
            raise ValueError("Live GitHub writes are disabled")
        if kind not in {"comment", "labels", "assignees"}:
            raise ValueError("Unsupported write tool")
        if self.settings.mode == "demo":
            with self.sessions.begin() as session:
                row = session.execute(
                    select(DemoIssue).where(DemoIssue.number == number).with_for_update()
                ).scalar_one()
                if kind == "comment":
                    row.comments = [*row.comments, payload["body"]]
                else:
                    data = dict(row.data)
                    data[kind] = sorted(set(data.get(kind, []) + payload[kind]))
                    row.data = data
            return {"demo": True, "kind": kind}
        if kind == "labels":
            from .labels import catalog

            current = catalog(self.settings, self.list_labels())
            if not set(payload["labels"]) <= set(current["allowed"]):
                raise ValueError("A label was removed or disallowed before the GitHub write")
        endpoint = "comments" if kind == "comment" else kind
        response = self.client.post(
            f"/repos/{self.settings.repo}/issues/{number}/{endpoint}", json=payload
        )
        response.raise_for_status()
        return {"http_status": response.status_code}
