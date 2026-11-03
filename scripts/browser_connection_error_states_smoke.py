"""Browser regression checks for connection failures, permission errors, and API-key clearing.

This is a mocked-API UI test. It does not claim direct-network, live-server, or provider coverage.
"""
import json, os, re, shutil
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
chromium = os.environ.get("ALEX_CHROMIUM_PATH") or shutil.which("chromium") or shutil.which("chromium-browser")
if not chromium:
    raise SystemExit("Chromium not found; set ALEX_CHROMIUM_PATH. Requires Playwright.")
html = (ROOT / "app/static/dashboard.html").read_text(encoding="utf-8")
css = (ROOT / "app/static/dashboard.css").read_text(encoding="utf-8")
js = (ROOT / "app/static/dashboard.js").read_text(encoding="utf-8")
html = re.sub(r'<link rel="stylesheet" href="/dashboard-assets/dashboard.css">', '', html)
html = re.sub(r'<script src="/dashboard-assets/dashboard.js" defer></script>', '', html)
html = html.replace("<head>", '<head><base href="http://alex-error.test/">')
html = html.replace("</head>", f"<style>{css}</style><script>window.__alexSessionStore={{}}; Object.defineProperty(window, 'sessionStorage', {{value: {{getItem:(k)=>window.__alexSessionStore[k] ?? null, setItem:(k,v)=>window.__alexSessionStore[k]=String(v), removeItem:(k)=>delete window.__alexSessionStore[k]}}}});</script></head>")
html = html.replace("</body>", f"<script>{js}</script></body>")
state = {"health_fail": True, "health_auth": [], "connector_auth": []}

def reply(route, body, status=200):
    return route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

def handler(route):
    req = route.request
    path = req.url.split("alex-error.test", 1)[-1]
    auth = req.headers.get("authorization")
    if path == "/health":
        state["health_auth"].append(auth)
        if state["health_fail"]:
            return reply(route, {"detail":"simulated service unavailable"}, 503)
        return reply(route, {"status":"ok", "version":"3.66", "orchestration_persistence":False, "orchestration_backend":"json", "audit_integrity":True, "audit_events":0, "pending_approvals":0})
    if path == "/api/v1/connectors":
        state["connector_auth"].append(auth)
        return reply(route, {"detail":"Insufficient permissions"}, 403)
    if path == "/api/v1/approvals?status=pending": return reply(route, {"approvals":[]})
    if path == "/api/v1/system/readiness": return reply(route, {"detail":"Administrator role required"}, 403)
    if path.startswith("/api/v1/"):
        if path == "/api/v1/workforce/agents": return reply(route, {"agents":[]})
        if path == "/api/v1/missions": return reply(route, {"missions":[]})
        if path == "/api/v1/missions/recovery-queue": return reply(route, {"missions":[]})
        if path == "/api/v1/missions/recovery-audit/pending": return reply(route, {"pending":[],"count":0})
        if path == "/api/v1/orchestrations?limit=25": return reply(route, {"orchestrations":[],"count":0,"total":0,"limit":25})
    return reply(route, {"detail":f"Unhandled mock request: {req.method} {path}"}, 404)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=chromium, args=["--no-sandbox", "--disable-dev-shm-usage"])
    context = browser.new_context()
    page = context.new_page()
    page.set_viewport_size({"width": 1280, "height": 900})
    errors=[]
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.route("http://alex-error.test/**", handler)
    page.set_content(html, wait_until="domcontentloaded")
    page.locator("#api-key").fill("temporary-test-key")
    page.locator("#connection-form button[type=submit]").click()
    page.wait_for_function("document.querySelector('#connection-message').textContent.includes('503: simulated service unavailable')")
    assert "Service unavailable" in page.locator("#overall-status").inner_text()
    assert page.locator("#connection-form button[type=submit]").is_enabled()
    assert state["health_auth"][-1] == "Bearer temporary-test-key"
    print("PASS service 503 is shown as a recoverable connection error; connect control re-enables")

    state["health_fail"] = False
    page.locator("#connection-form button[type=submit]").click()
    page.wait_for_function("document.querySelector('#connection-message').textContent.includes('protected view(s) could not be loaded')")
    assert "403: Insufficient permissions" in page.locator("#connectors-list").inner_text()
    assert "Readiness requires administrator access" in page.locator("#readiness-summary").inner_text()
    print("PASS protected API 403 errors are visible without breaking the dashboard")

    page.locator("#clear-key").click()
    page.wait_for_function("document.querySelector('#connection-message').textContent.includes('protected view(s) could not be loaded')")
    assert page.locator("#api-key").input_value() == ""
    assert context.pages[0].evaluate("window.sessionStorage.getItem('alex_api_key')") is None
    assert state["health_auth"][-1] is None
    assert state["connector_auth"][-1] is None
    print("PASS clear-key empties the input and session storage; subsequent requests omit Authorization")
    assert not errors, "Uncaught browser errors: " + "; ".join(errors)
    print("PASS no uncaught browser JavaScript errors")
    browser.close()
