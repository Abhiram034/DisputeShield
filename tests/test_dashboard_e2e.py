import re
from playwright.sync_api import Page, expect

def test_dashboard_case_search_and_review(page: Page, base_url: str):
    page.goto(base_url)
    expect(page.get_by_text(re.compile("PREVENTION IS ON", re.I))).to_be_visible()
    page.get_by_placeholder("Search cases...").fill("Olivia")
    expect(page.get_by_text("Olivia Rhye")).to_be_visible()
    expect(page.get_by_text("Phoenix Baker")).to_have_count(0)
    page.get_by_text("Olivia Rhye").click()
    expect(page.get_by_text("Investigation evidence")).to_be_visible()
    expect(page.get_by_text("Illustrative demo evidence.")).to_be_visible()
    expect(page.get_by_text("DS-2841-E1 · Illustrative scenario · Timestamp unavailable")).to_be_visible()
    expect(page.get_by_text("DEMO", exact=True).first).to_be_visible()
    page.get_by_role("button", name=re.compile("Review & approve")).click()
    expect(page.get_by_text("Action approved · now monitoring outcome")).to_be_visible()
