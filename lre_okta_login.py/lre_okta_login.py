"""
lre_okta_login.py
Logs into LRE via Okta SSO using Playwright, captures the resulting
session cookies, and saves them (with a timestamp) for reuse by the
MCP server's background refresh loop.
"""

import json
import os
import time

from playwright.sync_api import sync_playwright

LRE_LOGIN_URL = os.environ["LRE_BASE_URL"] + "/LoadTest"  # URL that triggers the Okta redirect
OKTA_USERNAME = os.environ["OKTA_USERNAME"]
OKTA_PASSWORD = os.environ["OKTA_PASSWORD"]
COOKIE_CACHE_PATH = os.environ.get("LRE_COOKIE_CACHE", "lre_session.json")
MFA_TIMEOUT_MS = int(os.environ.get("LRE_MFA_TIMEOUT_MS", "120000"))


def login_and_capture_session(headless: bool = True) -> list[dict]:
    """
    Runs a real Okta login flow in a browser and captures the resulting
    cookies (LWSSO_COOKIE_KEY + Okta session cookies). Writes them to
    COOKIE_CACHE_PATH along with a captured_at timestamp, so callers can
    tell how fresh the session is without re-parsing cookie expiry fields.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        page.goto(LRE_LOGIN_URL)

        # --- Okta login form ---
        page.wait_for_selector("input[name='identifier'], input#okta-signin-username")
        page.fill("input[name='identifier'], input#okta-signin-username", OKTA_USERNAME)
        page.click("input[type='submit'], #okta-signin-submit")

        page.wait_for_selector("input[name='credentials.passcode'], input#okta-signin-password")
        page.fill("input[name='credentials.passcode'], input#okta-signin-password", OKTA_PASSWORD)
        page.click("input[type='submit'], #okta-signin-submit")

        # --- MFA pause point ---
        # If MFA isn't exempted for this service account, this wait is where
        # a human needs to approve a push/enter a code on first-run testing.
        page.wait_for_url("**/LoadTest/**", timeout=MFA_TIMEOUT_MS)

        cookies = context.cookies()
        payload = {
            "captured_at": time.time(),
            "cookies": cookies,
        }
        with open(COOKIE_CACHE_PATH, "w") as f:
            json.dump(payload, f)

        browser.close()
        return cookies


if __name__ == "__main__":
    # Headed on first manual run so you can watch MFA/selectors behave;
    # switch to headless=True once confirmed working end-to-end.
    cookies = login_and_capture_session(headless=False)
    print(f"Captured {len(cookies)} cookies, saved to {COOKIE_CACHE_PATH}")
