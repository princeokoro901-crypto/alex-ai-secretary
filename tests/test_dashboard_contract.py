"""Static dashboard contract tests; these are not a substitute for browser E2E."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "app/static/dashboard.html"
JS_PATH = ROOT / "app/static/dashboard.js"


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.controls = []
        self.labels_for = set()
        self.buttons_without_type = []
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "label" and attrs.get("for"):
            self.labels_for.add(attrs["for"])
        if tag in {"input", "textarea", "select"}:
            self.controls.append((tag, attrs))
        if tag == "button" and "type" not in attrs:
            self.buttons_without_type.append(attrs.get("id", "(no id)"))
        if tag in {"script", "link"}:
            asset = attrs.get("src") or attrs.get("href")
            if asset:
                self.assets.append(asset)


def parsed_dashboard():
    parser = DashboardParser()
    parser.feed(HTML_PATH.read_text(encoding="utf-8"))
    return parser


def test_dashboard_ids_are_unique_and_all_static_js_lookups_resolve():
    parser = parsed_dashboard()
    duplicates = sorted(key for key, count in Counter(parser.ids).items() if count > 1)
    assert not duplicates, f"Duplicate HTML IDs: {duplicates}"
    js = JS_PATH.read_text(encoding="utf-8")
    refs = set(re.findall(r"byId\([\"']([^\"']+)[\"']\)", js))
    missing = sorted(refs - set(parser.ids))
    assert not missing, f"JavaScript references missing HTML IDs: {missing}"


def test_interactive_controls_have_accessible_names():
    parser = parsed_dashboard()
    missing = []
    for tag, attrs in parser.controls:
        control_id = attrs.get("id", "(no id)")
        named_by_label = control_id in parser.labels_for
        named_inline = bool(attrs.get("aria-label") or attrs.get("aria-labelledby"))
        # The checkbox in the orchestration confirmation is named by its wrapping label.
        wrapped_checkbox = tag == "input" and attrs.get("type") == "checkbox" and control_id == "orchestration-confirm"
        if not (named_by_label or named_inline or wrapped_checkbox):
            missing.append(control_id)
    assert not missing, f"Controls without a label/accessible name: {missing}"


def test_buttons_have_explicit_types_to_avoid_accidental_form_submission():
    parser = parsed_dashboard()
    assert not parser.buttons_without_type, parser.buttons_without_type


def test_dashboard_assets_are_same_origin_and_dom_rendering_is_safe():
    parser = parsed_dashboard()
    assert "/dashboard-assets/dashboard.js" in parser.assets
    assert "/dashboard-assets/dashboard.css" in parser.assets
    assert not any(asset.startswith(("http://", "https://", "//")) for asset in parser.assets)
    js = JS_PATH.read_text(encoding="utf-8")
    assert "innerHTML" not in js
    assert "sessionStorage" in js
    assert "localStorage" not in js


def test_dashboard_has_expected_mission_and_recovery_controls():
    parser = parsed_dashboard()
    ids = set(parser.ids)
    expected = {
        "mission-create-form", "mission-list-refresh", "mission-lookup-form",
        "agent-select", "assign-agent-button", "mission-recovery-list",
        "mission-audit-pending-list", "orchestration-form", "orchestration-confirm",
    }
    assert expected <= ids
