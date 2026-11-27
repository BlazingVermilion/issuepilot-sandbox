"""Exercise the shipped UI in Chromium. Local fixture responses; no GitHub, model or secrets."""

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

APP = Path(__file__).resolve().parents[2]
WEB = APP / "src/issuepilot"
policy = (APP / "policies/runtime/issuepilot_policy.md").read_text(encoding="utf8")
section = policy.split("## IP-INFO-001 — ", 1)[1].split("\n## ", 1)[0].strip()
body = "Extensions disabled? N/A — checked in browser.\n\n**Observed:** draft disappears.\n\n1. Open the editor.\n2. Click `Save`.\n\n| OS | Version |\n|---|---|\n| macOS | 26 |\n\n[Source](https://example.org/source) · [Archive](ftp://example.org/file) · ![Screenshot](https://example.org/image.png)\n\n```text\nA | B -- C\n```"
stamp = "2026-09-11T13:03:00Z"
cases = {}
for n, state in [(17, "COMPLETED"), (28, "REVIEW"), (37, "FAILED")]:
    p = dict(
        category="bug",
        summary="Saving a draft",
        missing_fields=[],
        labels=["bug"],
        assignees=[],
        comment="Original draft",
        reason="Consult the intake policy.",
        citations=[dict(id="IP-INFO-001", quote=section[:1000])],
    )
    docs = [
        dict(
            id="IP-INFO-001",
            text=section,
            file="runtime/issuepilot_policy.md",
            sha256=hashlib.sha256(section.encode()).hexdigest(),
        )
    ]
    c = dict(
        id=f"case-{n}",
        number=n,
        version=1,
        status=state,
        source=dict(
            title=f"Ticket {n}",
            body=body if n != 37 else "",
            state="open",
            created_at=stamp,
            labels=[],
            author="maintainer",
        ),
        issue_url=f"https://github.com/example/repo/issues/{n}",
        proposal=dict(
            value=p,
            documents=docs if n != 37 else [],
            label_catalog={"source": "issuepilot_policy", "allowed": ["bug", "feature-request"]},
            trace=[],
            rounds=0,
        ),
        decision={},
        actions=[],
        policy_hash="fixture",
        events=[dict(kind=f"EVENT_FOR_{n}", at=stamp, data={})],
        error=None,
    )
    cases[c["id"]] = c


def capture(page, name):
    """Preserve this test fixture only when an output directory is requested."""
    output = os.getenv("ISSUEPILOT_UI_CAPTURES")
    if output:
        folder = Path(output)
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / f"{name}.png"), animations="disabled")


def run():
    seen = []
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=os.getenv("CHROMIUM_EXECUTABLE") or None
        )
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        page.set_default_timeout(5000)
        page.on("pageerror", lambda e: errors.append(str(e)))

        def serve(route):
            url = urlsplit(route.request.url)
            if url.hostname != "review.test":
                seen.append(url.hostname)
                route.abort()
                return
            path = url.path
            if path == "/" or path.startswith("/static/"):
                f = WEB / ("ui.html" if path == "/" else path.lstrip("/"))
                route.fulfill(
                    path=str(f),
                    content_type={
                        ".css": "text/css",
                        ".js": "application/javascript",
                        ".html": "text/html",
                    }[f.suffix],
                )
                return
            if path == "/health":
                data = {"mode": "demo", "version": "test", "model": "fixture"}
            elif path == "/workspace":
                data = dict(
                    repo="sandbox/issuepilot",
                    mode="demo",
                    model="fixture",
                    reviewer="maintainer",
                    engine="ollama",
                    database="postgresql",
                    writes_enabled=False,
                    dataset={"total": 2},
                )
            elif path == "/cases":
                data = [
                    dict(
                        id=c["id"],
                        number=c["number"],
                        version=1,
                        title=c["source"]["title"],
                        status=c["status"],
                        updated_at=stamp,
                    )
                    for c in cases.values()
                ]
            elif path.startswith("/cases/"):
                data = cases[path.split("/")[-1]]
            else:
                route.fulfill(status=404, body="{}")
                return
            route.fulfill(content_type="application/json", body=json.dumps(data))

        page.route("**/*", serve)
        page.goto("http://review.test/")
        page.locator("#token").fill("fixture-token")
        page.locator("#connect-form button").click()
        expect(page.locator("#disconnect")).to_be_visible()
        page.locator("#tab-inbox").click()
        row = page.get_by_role("row").filter(has_text="Ticket 28")
        row.get_by_role("button", name="Review →").click()
        expect(page.locator("#issue-title")).to_have_text("Ticket 28")
        content = page.locator("#issue-body")
        expect(content.locator("strong")).to_have_text("Observed:")
        expect(content.locator("ol li")).to_have_count(2)
        expect(content.locator("table td")).to_have_count(2)
        expect(content).to_contain_text("N/A")
        expect(content.locator("pre code")).to_have_text("A | B -- C")
        expect(content.locator("img")).to_have_count(0)
        expect(content.locator('a[href^="ftp:"]')).to_have_count(0)
        expect(page.locator("#evidence-dialog")).not_to_be_visible()
        capture(page, "review")
        page.locator("#comment").fill("Reviewer draft survives")
        page.locator("#open-evidence").click()
        expect(page.locator("#evidence-dialog .md-table tbody tr")).to_have_count(5)
        expect(page.locator("#evidence-dialog .policy-current")).to_contain_text("bug")
        expect(page.locator("#evidence")).to_contain_text(section[-80:])
        capture(page, "policy-evidence")
        page.keyboard.press("Escape")
        expect(page.locator("#open-evidence")).to_be_focused()
        page.locator("#tab-inbox").click()
        page.get_by_role("button", name="Open audit history for issue #17").click()
        expect(page.locator("#activity-title")).to_have_text("#17 · Ticket 17")
        expect(page.locator("#timeline")).to_contain_text("event for 17")
        expect(page.locator("#timeline")).not_to_contain_text("event for 28")
        assert page.url.endswith("#inbox")
        capture(page, "case-history")
        page.locator("#audit-refresh").click()
        expect(page.locator("#activity-title")).to_be_visible()
        page.locator("#audit-dialog [data-close]").click()
        page.locator("#tab-review").click()
        expect(page.locator("#comment")).to_have_value("Reviewer draft survives")
        page.locator("#trace-audit").click()
        expect(page.locator("#activity-title")).to_have_text("#28 · Ticket 28")
        expect(page.locator("#timeline")).to_contain_text("event for 28")
        page.keyboard.press("Escape")
        page.locator("#theme").click()
        page.set_viewport_size({"width": 390, "height": 760})
        page.locator("#open-evidence").click()
        assert page.locator("#evidence-dialog").evaluate(
            "e => e.getBoundingClientRect().width <= innerWidth"
        )
        page.keyboard.press("Escape")
        page.locator("#tab-inbox").click()
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("row").filter(has_text="Ticket 37").get_by_role(
            "button", name="Inspect →"
        ).click()
        expect(content).to_have_text("No description provided.")
        page.locator("#open-evidence").click()
        expect(page.locator("#evidence")).to_contain_text("Saved excerpt only")
        assert not seen and not errors, (seen, errors)
        browser.close()
    print(
        "Browser PASS · source, both dialogs, history isolation, legacy evidence, unsaved edit, mobile, no remote media"
    )


if __name__ == "__main__":
    run()
