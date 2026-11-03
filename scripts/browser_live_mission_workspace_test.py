"""Chromium UI test backed by the real local FastAPI server.

Chromium cannot directly navigate to loopback in some sandboxed environments.
This test renders the real dashboard in Chromium and bridges same-origin API
requests through Playwright's route handler to a real loopback Uvicorn server.
It tests browser interaction + real backend behavior, but not direct browser
networking/CORS or production deployment behavior. No external services used.
Requires Playwright and a Chromium executable (ALEX_CHROMIUM_PATH optional).
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_request(url, method="GET", headers=None, body=None, timeout=5):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def main():
    chromium = os.environ.get("ALEX_CHROMIUM_PATH") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chromium:
        raise RuntimeError("Chromium not found. Install Chromium or set ALEX_CHROMIUM_PATH.")

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="alex-v363-browser-live-") as temp:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env.update({
            "ALEX_MISSION_STORE_PATH": str(Path(temp) / "missions.sqlite"),
            "ALEX_AUDIT_LOG_PATH": str(Path(temp) / "audit.jsonl"),
            "ALEX_APPROVAL_STORE_PATH": str(Path(temp) / "approvals.sqlite"),
            "ALEX_ORCHESTRATION_STATE_PATH": str(Path(temp) / "orchestration.json"),
            "ALEX_API_KEYS_JSON": "",
        })
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            last_error = None
            for _ in range(60):
                if proc.poll() is not None:
                    raise RuntimeError("Uvicorn exited: " + proc.stderr.read().decode(errors="replace")[-2000:])
                try:
                    status, _, raw = http_request(base + "/health", timeout=2)
                    if status == 200:
                        health = json.loads(raw)
                        break
                except Exception as exc:
                    last_error = exc
                    time.sleep(.25)
            else:
                raise RuntimeError(f"Local server did not start: {last_error}")
            assert health.get("version") == "3.66", health

            html = (ROOT / "app/static/dashboard.html").read_text()
            css = (ROOT / "app/static/dashboard.css").read_text()
            js = (ROOT / "app/static/dashboard.js").read_text()
            html = html.replace('<link rel="stylesheet" href="/dashboard-assets/dashboard.css">', "")
            html = html.replace('<script src="/dashboard-assets/dashboard.js" defer></script>', "")
            html = html.replace("<head>", '<head><base href="http://alex.test/"><style>' + css + "</style>")
            html = html.replace("</body>", "<script>" + js + "</script></body>")

            browser_requests = []
            def bridge(route):
                request = route.request
                parsed = urlsplit(request.url)
                target = base + parsed.path + (("?" + parsed.query) if parsed.query else "")
                headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length", "connection", "origin", "referer"}}
                body = request.post_data_buffer
                browser_requests.append((request.method, parsed.path))
                status_code, response_headers, response_body = http_request(target, request.method, headers, body)
                content_type = response_headers.get("Content-Type", response_headers.get("content-type", "application/json"))
                route.fulfill(status=status_code, headers={"content-type": content_type, "cache-control": "no-store"}, body=response_body)

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=chromium, args=["--no-sandbox", "--disable-dev-shm-usage", "--no-proxy-server"])
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.route("http://alex.test/**", bridge)
                page.set_content(html, wait_until="domcontentloaded")

                page.locator("#connection-form button[type='submit']").click()
                page.wait_for_function("document.querySelector('#service-detail').textContent.includes('v3.66')", timeout=10000)
                page.wait_for_function("document.querySelector('#ai-provider-status').textContent.includes('not fully configured')", timeout=10000)
                assert page.locator("#report-use-ai").is_disabled(), "AI mode should be disabled when provider configuration is missing"
                assert page.locator("#ai-provider-test").is_disabled(), "Connection test should be disabled when provider configuration is missing"
                assert "not your report notes" in page.locator("#ai-provider-test-result").inner_text()
                assert any(path == "/api/v1/system/ai-provider-status" for _, path in browser_requests)
                print("PASS Chromium rendered the actual dashboard HTML, CSS, and JavaScript")
                print("PASS browser clearly reports missing AI provider configuration and disables AI mode and connection test")

                # Exercise the first practical workspace task against the real local API.
                report_notes = "Dashboard is available locally.\nRole checks pass.\nNext: validate direct browser networking."
                page.locator("#report-title").fill("Browser Workflow Report")
                page.locator("#report-audience").fill("Development team")
                page.locator("#report-notes").fill(report_notes)
                page.locator("#report-task-submit").click()
                page.wait_for_function("document.querySelector('#report-task-message').textContent.includes('Draft prepared')", timeout=10000)
                report_preview = page.locator("#report-task-result").inner_text()
                assert "Browser Workflow Report" in report_preview
                assert "Dashboard is available locally." in report_preview
                assert "Role checks pass." in report_preview
                assert "has not been independently fact-checked" in report_preview
                assert any(path == "/api/v1/workspace/draft-report" for _, path in browser_requests)
                with page.expect_download() as download_info:
                    page.locator("#report-download").click()
                download = download_info.value
                assert download.suggested_filename == "browser-workflow-report.md", download.suggested_filename
                downloaded_path = Path(temp) / download.suggested_filename
                download.save_as(str(downloaded_path))
                assert downloaded_path.exists() and "Role checks pass." in downloaded_path.read_text()
                print("PASS browser prepared a verified local report, previewed it, and downloaded Markdown")

                objective = "v3.66 browser live-server test mission"
                page.locator("#mission-objective").fill(objective)
                page.locator("#mission-budget").fill("0")
                page.locator("#mission-scope").fill("browser test")
                page.locator("#mission-create-form button[type='submit']").click()
                page.wait_for_function("document.querySelector('#mission-message').textContent.includes('Mission created')", timeout=10000)
                mission_id = page.locator("#mission-lookup-id").input_value()
                assert mission_id.startswith("mission-"), mission_id
                result = json.loads(page.locator("#mission-result").inner_text())
                assert result.get("status") == "draft", result
                assert result.get("contract", {}).get("objective") == objective, result
                print("PASS browser created a real mission through the live API; status remains draft")

                page.locator("#agent-list-refresh").click()
                page.wait_for_function("document.querySelector('#agent-select').options.length > 1", timeout=10000)
                agent_id = page.locator("#agent-select option").nth(1).get_attribute("value")
                assert agent_id, "Agent inventory returned no selectable agent"
                page.locator("#agent-select").select_option(agent_id)
                page.locator("#assign-agent-button").click()
                page.wait_for_function("document.querySelector('#mission-message').textContent.includes('Mission has not been started or executed')", timeout=10000)
                assigned = json.loads(page.locator("#mission-result").inner_text())
                assert assigned.get("status") == "draft", assigned
                assert agent_id in assigned.get("assigned_agents", []), assigned
                assert not assigned.get("results"), assigned
                print("PASS browser assigned a registered agent; mission did not start and has no results")

                page.locator("#mission-list-refresh").click()
                page.wait_for_function("document.querySelector('#mission-list').innerText.includes('v3.66 browser live-server test mission')", timeout=10000)
                assert any(path == "/api/v1/missions" for _, path in browser_requests)
                assert any(path.endswith("/agents") for _, path in browser_requests)
                for width, height in [(320, 800), (375, 812), (768, 900), (1280, 900)]:
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(80)
                    dimensions = page.evaluate("""() => ({
                        viewport: document.documentElement.clientWidth,
                        document: document.documentElement.scrollWidth,
                        missionForm: !!document.querySelector('#mission-create-form'),
                        missionButtonVisible: (() => {
                            const e = document.querySelector('#mission-create-form button[type=submit]');
                            const r = e.getBoundingClientRect();
                            return r.width > 0 && r.height > 0 && r.left >= 0 && r.right <= window.innerWidth;
                        })()
                    })""")
                    assert dimensions["document"] <= dimensions["viewport"] + 1, f"Horizontal overflow at {width}px: {dimensions}"
                    assert dimensions["missionForm"] and dimensions["missionButtonVisible"], f"Mission form unusable at {width}px: {dimensions}"
                    print(f"PASS responsive layout at {width}px: no horizontal overflow; mission submit remains visible")

                page.set_viewport_size({"width": 375, "height": 812})
                page.locator("#api-key").focus()
                page.keyboard.press("Tab")
                focus_state = page.evaluate("""() => {
                    const expected = document.querySelector('#connection-form button[type=submit]');
                    return {active: document.activeElement === expected, outlineStyle: getComputedStyle(expected).outlineStyle, outlineWidth: getComputedStyle(expected).outlineWidth};
                }""")
                assert focus_state["active"], f"Keyboard Tab did not focus connection submit: {focus_state}"
                assert focus_state["outlineStyle"] != "none" and focus_state["outlineWidth"] != "0px", f"Keyboard focus indicator missing: {focus_state}"
                print("PASS keyboard navigation reaches connection submit button with visible focus indicator")
                assert not errors, "Browser JavaScript errors: " + "; ".join(errors)
                print("PASS browser refreshed mission list from real server")
                print("PASS no uncaught browser JavaScript errors")
                print("LIMIT: API requests were bridged by the test runner because direct Chromium-to-loopback navigation may be blocked; direct browser networking/CORS and production deployment remain untested")
                browser.close()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL live workspace browser test: {exc}", file=sys.stderr)
        raise SystemExit(1)
