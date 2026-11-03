import json, os, re, shutil
from pathlib import Path
from playwright.sync_api import sync_playwright

project = Path(__file__).resolve().parents[1]
chromium_path = os.environ.get('ALEX_CHROMIUM_PATH') or shutil.which('chromium') or shutil.which('chromium-browser')
if not chromium_path:
    raise SystemExit('Chromium not found; set ALEX_CHROMIUM_PATH to a Chromium executable. This optional smoke test also requires Playwright.')
html = (project / 'app/static/dashboard.html').read_text()
css = (project / 'app/static/dashboard.css').read_text()
js = (project / 'app/static/dashboard.js').read_text()
html = re.sub(r'<link rel="stylesheet" href="/dashboard-assets/dashboard.css">', '', html)
html = re.sub(r'<script src="/dashboard-assets/dashboard.js" defer></script>', '', html)
html = html.replace('<head>', '<head><base href="http://alex.test/">')
html = html.replace('</head>', f'<style>{css}</style></head>')
html = html.replace('</body>', f'<script>{js}</script></body>')

state = {'runs': [], 'review_calls': 0, 'pending_audits': True, 'audit_retry_calls': 0, 'recovery_decision_saved': False, 'recovery_decision_calls': 0}

def response(route, payload, status=200):
    return route.fulfill(status=status, content_type='application/json', body=json.dumps(payload))

def handler(route):
    req = route.request
    path = req.url.split('alex.test', 1)[-1]
    method = req.method
    if path == '/health':
        return response(route, {'status':'ok','version':'3.45','orchestration_persistence':False,'orchestration_backend':'json','audit_integrity':True,'audit_events':0,'pending_approvals':0})
    if path == '/api/v1/connectors': return response(route, {'connectors':[]})
    if path == '/api/v1/approvals?status=pending': return response(route, {'approvals':[]})
    if path == '/api/v1/system/readiness': return response(route, {'status':'sandbox_only','checks':[],'warnings':[]})
    if path == '/api/v1/missions' and method == 'GET': return response(route, {'missions':[]})
    if path == '/api/v1/missions/recovery-queue' and method == 'GET':
        items = [] if state['recovery_decision_saved'] else [{'contract':{'mission_id':'browser-recovered-mission','objective':'Browser recovery review mission'},'status':'paused','checkpoints':[{'name':'recovered_after_restart_manual_review_required'}]}]
        return response(route, {'missions':items})
    if path == '/api/v1/missions/browser-recovered-mission/recovery-decision' and method == 'POST':
        state['recovery_decision_calls'] += 1
        state['recovery_decision_saved'] = True
        return response(route, {'detail':'Decision was saved, but audit persistence is pending. Retry the recovery-audit endpoint; do not repeat the decision.'}, status=503)
    if path == '/api/v1/missions/recovery-audit/pending' and method == 'GET':
        pending = [{'mission_id':'browser-pending-audit','objective':'Browser pending audit delivery','mission_status':'paused','decision_id':'browser-decision-id','decision':'keep_paused','actor_id':'browser-operator','recorded_at':'2026-10-26T00:00:00+00:00','audit_status':'pending'}] if state['pending_audits'] else []
        return response(route, {'pending':pending,'count':len(pending)})
    if path == '/api/v1/missions/browser-pending-audit/recovery-audit/retry' and method == 'POST':
        state['audit_retry_calls'] += 1
        state['pending_audits'] = False
        return response(route, {'mission_id':'browser-pending-audit','audit_status':'recorded','already_recorded':False})
    if path == '/api/v1/workforce/agents': return response(route, {'agents':[{'agent_id':'research','name':'Research Agent','capabilities':['search_web']}]})
    if path == '/api/v1/orchestrations?limit=25': return response(route, {'orchestrations':state['runs'],'count':len(state['runs']),'total':len(state['runs']),'limit':25})
    if path == '/api/v1/orchestrate' and method == 'POST':
        payload = req.post_data_json
        run = {'orchestration_id':'browser-mock-uncertain-run','mission_id':'browser-mock-mission','status':'uncertain','selected_agent_id':'research','result':{'recovery':{'automatic_retry':False}},'recovery_state':'runtime_reported_uncertain','request_metadata':{'objective':payload['objective']},'recovery_review':None}
        state['runs'] = [run]
        return response(route, run)
    if path == '/api/v1/orchestrations/browser-mock-uncertain-run/recovery-review' and method == 'POST':
        payload = req.post_data_json
        state['review_calls'] += 1
        state['runs'][0]['recovery_review'] = {'disposition':payload['disposition'],'reviewed_at':'2026-10-25T00:00:00+00:00','reviewed_by':'browser-operator','reviewer_key_id':'browser-operator-key','reviewer_role':'operator'}
        return response(route, state['runs'][0])
    if path == '/api/v1/orchestrations/browser-mock-uncertain-run': return response(route, state['runs'][0])
    if path == '/api/v1/missions/browser-mock-mission':
        return response(route, {'contract':{'mission_id':'browser-mock-mission','objective':'Verify recovery review UI'},'status':'paused','assigned_agents':['research'],'results':[]})
    return response(route, {'detail':f'Unhandled mock request: {method} {path}'}, 404)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=chromium_path, args=['--no-sandbox','--disable-dev-shm-usage'])
    page = browser.new_page(viewport={'width':1280,'height':900})
    errors=[]
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.route('http://alex.test/**', handler)
    page.set_content(html, wait_until='domcontentloaded')
    page.locator('#api-key').fill('browser-operator-secret')
    page.locator('#connection-form button[type="submit"]').click()
    page.wait_for_function("document.querySelector('#orchestration-history-message').textContent.includes('History loaded')", timeout=10000)
    assert 'Alex Core v3.45' in page.locator('#service-detail').inner_text()

    page.locator('#mission-recovery-refresh').click()
    page.wait_for_function("document.querySelector('#mission-recovery-list').innerText.includes('Browser recovery review mission')", timeout=10000)
    page.locator('#mission-recovery-list textarea').fill('Audit delivery failure browser smoke test')
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='Keep paused').click()
    page.wait_for_function("document.querySelector('#mission-recovery-message').textContent.includes('503')", timeout=10000)
    page.wait_for_function("document.querySelector('#mission-audit-pending-list').innerText.includes('Browser pending audit delivery')", timeout=10000)
    assert state['recovery_decision_calls'] == 1
    assert 'No missions are awaiting restart recovery review.' in page.locator('#mission-recovery-list').inner_text()
    page.locator('#mission-audit-pending-refresh').click()
    page.wait_for_function("document.querySelector('#mission-audit-pending-list').innerText.includes('Browser pending audit delivery')", timeout=10000)
    page.get_by_role('button', name='Retry audit delivery').click()
    page.wait_for_function("document.querySelector('#mission-audit-pending-message').textContent.includes('Audit record delivered')", timeout=10000)
    assert state['audit_retry_calls'] == 1
    page.wait_for_function("document.querySelector('#mission-audit-pending-list').innerText.includes('No recovery audit deliveries are pending.')", timeout=10000)

    page.locator('#orchestration-objective').fill('Verify recovery review UI in mocked browser')
    page.locator('#orchestration-input').fill('{"simulated_outcome":"uncertain"}')
    page.locator('#orchestration-confirm').check()
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('#orchestration-run-button').click()
    page.wait_for_function("document.querySelector('#orchestration-message').textContent.includes('Task returned status: uncertain')", timeout=10000)

    page.locator('#orchestration-history-refresh').click()
    page.wait_for_function("document.querySelector('#orchestration-history-list').innerText.includes('Acknowledge — no retry')", timeout=10000)
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='Acknowledge — no retry').click()
    page.wait_for_function("document.querySelector('#orchestration-history-list').innerText.includes('Human review: acknowledged_no_retry')", timeout=10000)
    history_text = page.locator('#orchestration-history-list').inner_text()
    assert 'uncertain · browser-mock-uncertain-run' in history_text
    assert state['review_calls'] == 1

    page.get_by_role('button', name='Inspect run').first.click()
    page.wait_for_function("document.querySelector('#orchestration-result').textContent.includes('recovery_review')", timeout=10000)
    assert not errors, 'Uncaught browser errors: ' + '; '.join(errors)
    print('PASS: Chromium rendered the actual dashboard markup, CSS, and JavaScript.')
    print('PASS: Connect/refresh flow worked with mocked API responses.')
    print('PASS: Uncertain-run acknowledgement required confirmation and updated the review display.')
    print('PASS: Task remained uncertain; the UI did not retry it.')
    print('PASS: Read-only inspection displayed the recovery review.')
    print('PASS: Simulated 503 after decision persistence refreshed the mission and pending-audit views.')
    print('PASS: Pending audit delivery appeared in the dashboard and the retry button cleared the mocked queue.')
    print('PASS: Recovery decision was submitted once; audit retry did not repeat the decision.')
    print('PASS: No uncaught browser JavaScript errors.')
    browser.close()
