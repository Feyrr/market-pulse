#!/usr/bin/env python3
import json
import os
import re
import logging
from datetime import datetime
from pathlib import Path

import requests
import yfinance as yf
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_FILE = BASE_DIR / "logs" / "alerts.log"
LOG_FILE.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Reasonable MYR price range for 1g gold bar (adjust if gold moves a lot)
PRICE_RANGE = (250, 2500)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    # Environment variables override config.json (used in GitHub Actions)
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg["telegram"]["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHANNEL_ID"):
        cfg["telegram"]["channel_id"] = os.environ["TELEGRAM_CHANNEL_ID"]
    return cfg


# ---------------------------------------------------------------------------
# Market data (stocks + gold spot)
# ---------------------------------------------------------------------------

def get_stock_price(ticker, label):
    try:
        fi = yf.Ticker(ticker).fast_info
        price = fi.last_price
        prev = fi.previous_close
        change = price - prev
        pct = change / prev * 100
        return {"ticker": ticker, "label": label, "price": price, "change": change, "pct": pct}
    except Exception as e:
        log.error(f"{ticker} fetch failed: {e}")
        return {"ticker": ticker, "label": label, "price": None, "change": None, "pct": None}


def get_gold_spot_myr():
    try:
        gold_usd_oz = yf.Ticker("GC=F").fast_info.last_price
        usdmyr = yf.Ticker("USDMYR=X").fast_info.last_price
        return {
            "usd_per_oz": gold_usd_oz,
            "myr_per_oz": gold_usd_oz * usdmyr,
            "myr_per_gram": gold_usd_oz * usdmyr / 31.1035,
            "usdmyr": usdmyr,
        }
    except Exception as e:
        log.error(f"Gold spot fetch failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Price extraction helpers
# ---------------------------------------------------------------------------

def find_rm_prices(text):
    """Extract all RM prices from text that fall within the plausible 1g gold range."""
    raw = re.findall(r"RM\s*([\d,]+(?:\.\d{1,2})?)", text.replace("\xa0", " "), re.IGNORECASE)
    lo, hi = PRICE_RANGE
    results = []
    for r in raw:
        try:
            val = float(r.replace(",", ""))
            if lo <= val <= hi:
                results.append(val)
        except ValueError:
            pass
    return results


def extract_from_soup(soup, selectors):
    """Try each CSS selector, fall back to full-page regex scan."""
    lo, hi = PRICE_RANGE
    for sel in selectors:
        try:
            elem = soup.select_one(sel)
            if elem:
                if elem.name == "meta":
                    # Price is in the content attribute, not visible text
                    try:
                        val = float(elem.get("content", "").replace(",", ""))
                        if lo <= val <= hi:
                            return val
                    except ValueError:
                        pass
                else:
                    prices = find_rm_prices(elem.get_text(separator=" "))
                    if prices:
                        return prices[0]
                    # Some elements contain a bare number without RM prefix
                    try:
                        val = float(elem.get_text(separator=" ").strip().replace(",", ""))
                        if lo <= val <= hi:
                            return val
                    except ValueError:
                        pass
        except Exception:
            pass
    # Full-page fallback
    prices = find_rm_prices(soup.get_text(separator=" "))
    return prices[0] if prices else None


# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

def scrape_requests(url, selectors):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        price = extract_from_soup(soup, selectors)
        if price:
            return price, "ok"
        return None, "no_price"
    except requests.Timeout:
        log.warning(f"Timeout: {url}")
        return None, "timeout"
    except requests.HTTPError as e:
        code = e.response.status_code
        log.warning(f"HTTP {code}: {url}")
        status = "blocked" if code in (403, 429) else f"http_{code}"
        return None, status
    except requests.ConnectionError:
        log.warning(f"Connection error: {url}")
        return None, "unreachable"
    except Exception as e:
        log.error(f"Request error {url}: {e}")
        return None, "error"


def scrape_playwright(url, selectors, wait_selector=None, wait_js=None):
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.warning("playwright not installed - skipping JS site. Run: playwright install chromium")
        return None, "no_playwright"

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=HEADERS["User-Agent"])
            page = ctx.new_page()
            try:
                page.goto(url, timeout=30_000, wait_until="networkidle")
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=10_000)
                    except PWTimeout:
                        pass
                if wait_js:
                    try:
                        page.wait_for_function(wait_js, timeout=10_000)
                    except PWTimeout:
                        pass
                soup = BeautifulSoup(page.content(), "lxml")
                price = extract_from_soup(soup, selectors)
                if price:
                    return price, "ok"
                return None, "no_price"
            except PWTimeout:
                log.warning(f"Playwright timeout: {url}")
                return None, "timeout"
            finally:
                browser.close()
    except Exception as e:
        log.error(f"Playwright error {url}: {e}")
        return None, "error"


def scrape_site(site):
    name = site["name"]
    url = site["url"]
    method = site.get("method", "requests")
    selectors = site.get("price_selectors", [])

    try:
        if method == "playwright":
            price, status = scrape_playwright(url, selectors, site.get("wait_selector"), site.get("wait_js"))
        else:
            price, status = scrape_requests(url, selectors)
    except Exception as e:
        log.error(f"[{name}] unexpected error: {e}")
        price, status = None, "error"

    if price:
        log.info(f"[OK] {name}: RM {price:.2f}")
    else:
        log.warning(f"[{status.upper()}] {name}")

    return {"name": name, "price": price, "status": status}


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(token, channel_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": channel_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    resp.raise_for_status()
    log.info("Telegram message sent.")


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def sign(val):
    return "+" if val >= 0 else ""


def build_message(stocks, gold, sites):
    now = datetime.now().strftime("%d %b %Y, %H:%M MYT")
    lines = [f"*Gold & Market Update*", f"_{now}_", ""]

    # Stocks
    lines.append("*Stocks*")
    for s in stocks:
        if s["price"] is not None:
            currency = "RM" if s["ticker"].endswith(".KL") else "$"
            price_str = f"RM {s['price']:.2f}" if currency == "RM" else f"${s['price']:.2f}"
            lines.append(
                f"  {s['label']} ({s['ticker']}): `{price_str}`  "
                f"{sign(s['change'])}{s['change']:.2f} ({sign(s['pct'])}{s['pct']:.2f}%)"
            )
        else:
            lines.append(f"  {s['label']} ({s['ticker']}): N/A")

    lines.append("")

    # Gold spot
    if gold:
        lines += [
            "*Gold Spot*",
            f"  USD/oz  : `${gold['usd_per_oz']:,.2f}`",
            f"  MYR/oz  : `RM {gold['myr_per_oz']:,.2f}`",
            f"  MYR/1g  : `RM {gold['myr_per_gram']:.2f}`",
            f"  USD/MYR : `{gold['usdmyr']:.4f}`",
        ]
    else:
        lines.append("*Gold Spot*: N/A")

    lines.append("")
    lines.append("*Dealer Prices - 1g Gold Bar*")

    status_labels = {
        "ok":           "",
        "no_price":     " - price not found",
        "timeout":      " - timeout",
        "blocked":      " - blocked (403/429)",
        "unreachable":  " - site unreachable",
        "no_playwright": " - playwright missing",
    }

    for s in sites:
        label = s["name"]
        if s["price"] is not None:
            lines.append(f"  {label}: `RM {s['price']:.2f}`")
        else:
            status = s.get("status", "error")
            # show HTTP code for unexpected HTTP errors like http_404, http_500
            if status.startswith("http_"):
                detail = f" - {status.replace('_', ' ')}"
            else:
                detail = status_labels.get(status, f" - {status}")
            lines.append(f"  {label}: N/A{detail}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    cfg = load_config()

    # SITE_FREQUENCY_HOURS env var controls which sites run this cycle.
    # "all" (or unset) runs every site regardless of frequency.
    freq_filter = os.environ.get("SITE_FREQUENCY_HOURS", "all")
    if freq_filter == "all":
        sites_to_run = cfg["sites"]
    else:
        target = int(freq_filter)
        sites_to_run = [s for s in cfg["sites"] if s.get("frequency_hours", 1) == target]

    log.info(f"=== Gold Alert Run (freq={freq_filter}, sites={len(sites_to_run)}) ===")

    stocks = [get_stock_price(s["ticker"], s["label"]) for s in cfg["stocks"]]
    gold = get_gold_spot_myr()
    sites = [scrape_site(s) for s in sites_to_run]

    msg = build_message(stocks, gold, sites)
    log.info(f"\n{msg}\n")

    tg = cfg["telegram"]
    send_telegram(tg["bot_token"], tg["channel_id"], msg)
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
