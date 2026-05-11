"""
Browser Utilities — Stealth Edition
====================================
Playwright setup using ``playwright`` + ``playwright_stealth`` for
anti-detection, plus shared helper functions for safe, human-like interactions.
"""

from __future__ import annotations

import random
import time

from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import Stealth
    _HAS_STEALTH = True
except ImportError:
    Stealth = None
    _HAS_STEALTH = False

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

class PlaywrightDriver:
    def __init__(self, headless: bool = True):
        import os
        self.headless = headless
        self.playwright_cm = sync_playwright()
        self.playwright = self.playwright_cm.__enter__()
        
        # We use a persistent directory inside the project to save login sessions.
        # This way, once you log in manually (by running with headless=False),
        # the session is saved and you won't be asked to log in again.
        user_data_dir = os.path.join(os.getcwd(), ".playwright_data")
        
        try:
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="msedge",
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={'width': 1920, 'height': 1080}
            )
            self.browser = None
            # launch_persistent_context usually starts with one page open
            if self.context.pages:
                self.page = self.context.pages[0]
            else:
                self.page = self.context.new_page()
                
        except Exception as e:
            print(f"Persistent launch failed: {e}. Falling back to temporary session.")
            self.browser = self.playwright.chromium.launch(
                channel="msedge",
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"]
            )
            self.context = self.browser.new_context(
                user_agent=random.choice(_USER_AGENTS),
                viewport={'width': 1920, 'height': 1080}
            )
            self.page = self.context.new_page()
        
        if _HAS_STEALTH and Stealth is not None:
            try:
                Stealth().apply_stealth_sync(self.page)
            except Exception:
                pass

    def quit(self):
        try:
            self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            self.playwright_cm.__exit__(None, None, None)
        except Exception:
            pass

    def get_naukri_headers(self) -> dict:
        """
        Extracts necessary headers and cookies for Naukri API calls
        from the current browser session.
        """
        cookies = self.context.cookies()
        cookie_str = "; ".join([f"{c.get('name')}={c.get('value')}" for c in cookies if c.get('name') and c.get('value')])
        
        # Try to find authorization token in cookies or local storage
        # Naukri often uses 'nauk_at' or 'studio_at'
        auth_token = ""
        for c in cookies:
            if c.get('name') == 'nauk_at':
                auth_token = c.get('value') or ""
                break
        
        if not auth_token:
            # Fallback: try to get it from local storage via evaluate
            try:
                auth_token = self.page.evaluate("localStorage.getItem('nauk_at')")
            except:
                pass

        headers = {
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "AppId": "121",
            "ClientId": "d3skt0p",
            "SystemId": "jobseeker",
            "User-Agent": self.page.evaluate("navigator.userAgent"),
            "Cookie": cookie_str
        }
        
        if auth_token:
            # Naukri uses different prefixes for different endpoints
            if "eyJ" in auth_token: # JWT
                headers["Authorization"] = f"ACCESSTOKEN = {auth_token}"
                headers["authorization"] = f"Bearer {auth_token}" # Some endpoints use Bearer
            else:
                headers["Authorization"] = auth_token
        
        return headers

def create_stealth_driver(headless: bool = True, **_kwargs):
    return PlaywrightDriver(headless=headless)

def force_quit_driver(driver):
    if driver:
        driver.quit()

def human_delay(low: float = 1.0, high: float = 3.0) -> None:
    time.sleep(random.uniform(low, high))

def scroll_into_view(_driver, element) -> None:
    try:
        element.scroll_into_view_if_needed()
        human_delay(0.3, 0.8)
    except Exception:
        pass

def scroll_page(driver, pixels: int = 600) -> None:
    try:
        driver.page.evaluate(f"window.scrollBy(0, {pixels});")
        human_delay(0.8, 1.5)
    except Exception:
        pass

def dismiss_overlays(driver_or_page) -> None:
    page = getattr(driver_or_page, "page", driver_or_page)
    if not page:
        return
    overlay_selectors = [
        'button[data-tracking-control-name="public_jobs_contextual-sign-in-modal_modal_dismiss"]',
        'button.modal__dismiss',
        'button[aria-label="Dismiss"]',
        'button[aria-label="Close"]',
        'icon.modal__dismiss',
        'button.crossIcon',
        'button[title="Close"]',
        '.chatbot_closeButton',
        'button#onetrust-accept-btn-handler',
        'button.cookie-policy__accept',
        'div[aria-labelledby="login-modal-title"] button.modal__dismiss',
        'button[data-test-id="close-button"]',
    ]
    for sel in overlay_selectors:
        try:
            # Use a shorter timeout for overlay checks
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                human_delay(0.5, 1.0)
        except Exception:
            pass

def is_login_required(driver_or_page) -> bool:
    page = getattr(driver_or_page, "page", driver_or_page)
    if not page:
        return False
    url = page.url.lower()
    
    # Naukri check: logged in if we are not on login page and login button is absent
    if "naukri.com" in url:
        if "nlogin/login" in url:
            return True
        try:
            # On Naukri, #login_Layer is the login button. If it's visible, we aren't logged in.
            login_btn = page.query_selector("#login_Layer")
            if login_btn and login_btn.is_visible():
                return True
            return False
        except Exception:
            return False
            
    if "/authwall" in url or "checkpoint" in url or "linkedin.com/login" in url:
        return True
    
    # Check for presence of large login forms that block content
    login_indicators = [
        'form[data-action*="login"]',
        '#login-submit',
        'input[name="session_key"]',
        '.authwall-join-form',
    ]
    for sel in login_indicators:
        try:
            if page.query_selector(sel):
                return True
        except:
            pass
    return False

def wait_for_login(driver_or_page, timeout_minutes: int = 5) -> bool:
    """
    If not headless, waits for the user to complete login manually.
    Returns True if login seems successful, False otherwise.
    """
    page = getattr(driver_or_page, "page", driver_or_page)
    # We can only wait if the browser is visible
    # (Note: driver.context._browser_type._headless is not easily accessible here, 
    # so we assume if we are called, we should try to wait)
    
    print(f"🛑 Login required. Waiting up to {timeout_minutes} minutes for manual login...")
    
    start_time = time.time()
    while time.time() - start_time < (timeout_minutes * 60):
        if not is_login_required(page):
            print("✅ Login completed!")
            return True
        time.sleep(2)
    
    print("❌ Login timeout.")
    return False
