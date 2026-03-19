import asyncio
import os
import random
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from playwright.async_api import async_playwright, Page, BrowserContext
from tenacity import retry, stop_after_attempt, wait_exponential

from scraper.supabase_client import fetch_asin_list
from scraper.supabase_client import upsert_delivery_results
from scraper.alerts import send_teams_alert

ZIP_CODES = [
    {"zip": "10001", "city": "New York, NY",     "region": "EC"},
    {"zip": "33101", "city": "Miami, FL",          "region": "EC"},
    {"zip": "19101", "city": "Philadelphia, PA",   "region": "EC"},
    {"zip": "30301", "city": "Atlanta, GA",         "region": "EC"},
    {"zip": "60601", "city": "Chicago, IL",         "region": "MC"},
    {"zip": "75201", "city": "Dallas, TX",          "region": "MC"},
    {"zip": "55401", "city": "Minneapolis, MN",     "region": "MC"},
    {"zip": "80202", "city": "Denver, CO",          "region": "MC"},
    {"zip": "90210", "city": "Los Angeles, CA",     "region": "WC"},
    {"zip": "98101", "city": "Seattle, WA",         "region": "WC"},
    {"zip": "85001", "city": "Phoenix, AZ",         "region": "WC"},
    {"zip": "97201", "city": "Portland, OR",        "region": "WC"},
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

CONCURRENCY = 5  # Max parallel Playwright instances
REQUEST_DELAY_MIN = 2.5
REQUEST_DELAY_MAX = 5.5


async def scrape_asin_zip(page: Page, asin: str, zip_info: Dict) -> Dict:
    """Scrape all delivery options for one ASIN at one zip code."""
    url = f"https://www.amazon.com/dp/{asin}?th=1&psc=1"
    result = {
        "asin": asin,
        "zip": zip_info["zip"],
        "city": zip_info["city"],
        "region": zip_info["region"],
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "prime_available": False,
        "prime_days": None,
        "prime_date": None,
        "same_day": False,
        "tonight": False,
        "next_day": False,
        "one_hour": False,
        "standard_date": None,
        "standard_days": None,
        "buybox_type": None,
        "in_stock": False,
        "block_detected": False,
        "raw_delivery_text": None,
    }

    try:
        # Set zip code via cookie before navigating
        await page.context.add_cookies([{
            "name": "i18n-prefs",
            "value": "USD",
            "domain": ".amazon.com",
            "path": "/",
        }])

        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        # Check if blocked
        title = await page.title()
        if any(x in title.lower() for x in ["robot", "captcha", "sorry", "page not found"]):
            result["block_detected"] = True
            return result

        # Inject zip code
        await _set_zip_code(page, zip_info["zip"])
        await asyncio.sleep(random.uniform(1.5, 3.0))

        # Extract delivery block
        delivery_text = await _extract_delivery_block(page)
        result["raw_delivery_text"] = delivery_text

        if delivery_text:
            result.update(_parse_delivery_options(delivery_text))

        # BuyBox
        result["buybox_type"] = await _get_buybox_type(page)

        # In stock
        add_to_cart = await page.query_selector("#add-to-cart-button")
        buy_now = await page.query_selector("#buy-now-button")
        result["in_stock"] = bool(add_to_cart or buy_now)

    except Exception as e:
        print(f"[delivery] Error scraping {asin} @ {zip_info['zip']}: {e}")
        result["block_detected"] = True

    return result


async def _set_zip_code(page: Page, zip_code: str):
    """Click the delivery location and set zip code."""
    try:
        loc_btn = await page.query_selector("#glow-ingress-block, #nav-global-location-popover-link")
        if not loc_btn:
            return
        await loc_btn.click()
        await page.wait_for_selector("#GLUXZipUpdateInput, #GLUX_Full_AddressLine1", timeout=5000)

        zip_input = await page.query_selector("#GLUXZipUpdateInput")
        if zip_input:
            await zip_input.fill("")
            await zip_input.type(zip_code, delay=50)
            await asyncio.sleep(0.5)

        apply_btn = await page.query_selector("[data-action='GLUXZipUpdate'] input[type='submit'], #GLUXZipUpdate")
        if apply_btn:
            await apply_btn.click()
            await asyncio.sleep(1.5)

        # Close dialog if still open
        done_btn = await page.query_selector(".a-popover-footer .a-button-primary button")
        if done_btn:
            await done_btn.click()
            await asyncio.sleep(1.0)

        await page.reload(wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1.5)

    except Exception:
        pass  # Zip injection failing is non-fatal


async def _extract_delivery_block(page: Page) -> Optional[str]:
    """Extract the full delivery block text."""
    selectors = [
        "#mir-layout-DELIVERY_BLOCK",
        "#deliveryBlockMessage",
        "#delivery-message",
        "#ddmDeliveryMessage",
        "#dynamicDeliveryMessage",
    ]
    for sel in selectors:
        el = await page.query_selector(sel)
        if el:
            return (await el.inner_text()).strip()
    return None


def _parse_delivery_options(text: str) -> Dict:
    """Parse all delivery option types from raw delivery block text."""
    t = text.lower()
    result = {
        "prime_available": False,
        "prime_days": None,
        "prime_date": None,
        "same_day": False,
        "tonight": False,
        "next_day": False,
        "one_hour": False,
        "standard_date": None,
        "standard_days": None,
    }

    # Same-day / tonight / 1-hour
    result["same_day"] = bool(re.search(r"same.?day|today|today by \d", t))
    result["tonight"] = bool(re.search(r"by tonight|get it tonight|tonight", t))
    result["next_day"] = bool(re.search(r"one.?day|tomorrow|next.?day|overnight", t))
    result["one_hour"] = bool(re.search(r"1.?hour|2.?hour|within \d hour", t))

    # Prime delivery
    prime_match = re.search(
        r"free delivery\s+(\w+,?\s+\w+\s+\d+)|prime\s+(?:free\s+)?(?:delivery|shipping)\s+(\w+,?\s+\w+\s+\d+)",
        t
    )
    if prime_match:
        result["prime_available"] = True
        date_str = (prime_match.group(1) or prime_match.group(2) or "").strip()
        result["prime_date"] = date_str
        result["prime_days"] = _estimate_days(date_str)

    # Standard delivery
    std_match = re.search(r"arrives?\s+(\w+,?\s+\w+\s+\d+(?:\s*-\s*\w+\s+\d+)?)", t)
    if std_match:
        result["standard_date"] = std_match.group(1).strip()
        result["standard_days"] = _estimate_days(std_match.group(1))

    return result


def _estimate_days(date_str: str) -> Optional[int]:
    """Rough estimate of days from a date string like 'Mon, Mar 21'."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc)
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
    }
    if not date_str:
        return None
    try:
        parts = date_str.lower().replace(",", "").split()
        # Find month and day number
        month_num = None
        day_num = None
        for i, p in enumerate(parts):
            for m, n in months.items():
                if p.startswith(m):
                    month_num = n
                    break
            if month_num and i + 1 < len(parts):
                nums = re.findall(r"\d+", parts[i + 1])
                if nums:
                    day_num = int(nums[0])
                    break
        if month_num and day_num:
            year = today.year if month_num >= today.month else today.year + 1
            target = datetime(year, month_num, day_num, tzinfo=timezone.utc)
            delta = (target - today).days
            return max(0, delta)
    except Exception:
        pass
    return None


async def _get_buybox_type(page: Page) -> Optional[str]:
    """Determine who has the BuyBox."""
    try:
        # 1P / Amazon sold-by check
        sold_by = await page.query_selector("#sellerProfileTriggerId, #merchant-info")
        if sold_by:
            text = (await sold_by.inner_text()).lower()
            if "amazon.com" in text or "amazon" in text:
                return "1P"
            if text.strip():
                return "3P"

        # FBM indicator
        fulfillment = await page.query_selector("#fulfilledByThirdParty, #ddmMerchantMessage")
        if fulfillment:
            text = (await fulfillment.inner_text()).lower()
            if "merchant" in text or "seller" in text:
                return "FBM"

        # Check if Add to Cart exists at all
        atc = await page.query_selector("#add-to-cart-button")
        if atc:
            return "1P"  # Default assumption if we can't determine

    except Exception:
        pass
    return None


async def run_delivery_scrape():
    """Main entry point for delivery scrape job."""
    print("[delivery] Loading ASIN list from Supabase...")
    items = await fetch_asin_list()

    # Build work queue: own ASINs + deduplicated competitor ASINs
    # Own ASINs: scrape all zip codes
    # Competitor ASINs: only scrape if not already a known ASIN
    own_asin_set = {item["asin"] for item in items}
    own_work = []
    for item in items:
        for zip_info in ZIP_CODES:
            own_work.append({
                "asin": item["asin"],
                "sku": item["sku"],
                "brand": item["brand"],
                "category": item["category"],
                "deal_bucket": item["deal_bucket"],
                "is_own_sku": True,
                "zip_info": zip_info,
            })

    # Competitor ASINs — deduplicate across all SKUs, pair with their category
    comp_asins_seen = set()
    comp_work = []
    for item in items:
        for comp_asin in item["competitor_asins"]:
            if comp_asin in own_asin_set:
                continue  # It's also our own ASIN
            if comp_asin in comp_asins_seen:
                continue
            comp_asins_seen.add(comp_asin)
            for zip_info in ZIP_CODES:
                comp_work.append({
                    "asin": comp_asin,
                    "sku": None,
                    "brand": None,
                    "category": item["category"],
                    "deal_bucket": item["deal_bucket"],
                    "is_own_sku": False,
                    "zip_info": zip_info,
                })

    total_work = own_work + comp_work
    print(f"[delivery] Queue: {len(own_work)} own scrapes + {len(comp_work)} competitor scrapes = {len(total_work)} total")

    # Shuffle to avoid sequential same-IP hits on same ASIN
    random.shuffle(total_work)

    results = []
    semaphore = asyncio.Semaphore(CONCURRENCY)
    batch_size = 50

    async with async_playwright() as p:
        for batch_start in range(0, len(total_work), batch_size):
            batch = total_work[batch_start:batch_start + batch_size]
            print(f"[delivery] Processing batch {batch_start//batch_size + 1} / {len(total_work)//batch_size + 1}")

            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )

            tasks = []
            for work in batch:
                tasks.append(_scrape_with_semaphore(
                    semaphore, browser, work
                ))

            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)

            await browser.close()

            # Write batch to Supabase
            await upsert_delivery_results(batch_results, total_work[batch_start:batch_start + batch_size])
            print(f"[delivery] Written {len(batch_results)} rows to Supabase")

    # Summary
    blocked = sum(1 for r in results if r.get("block_detected"))
    successful = len(results) - blocked
    print(f"[delivery] Complete. {successful} successful, {blocked} blocked.")

    if blocked / max(len(results), 1) > 0.1:
        await send_teams_alert(
            title="Delivery Scraper — High Block Rate",
            message=f"{blocked}/{len(results)} requests blocked by Amazon ({100*blocked//len(results)}%). Check proxy config.",
            severity="warning"
        )


async def _scrape_with_semaphore(semaphore, browser, work: Dict) -> Dict:
    async with semaphore:
        ua = random.choice(USER_AGENTS)
        context = await browser.new_context(
            user_agent=ua,
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1280, "height": 800},
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            }
        )
        page = await context.new_page()
        try:
            result = await scrape_asin_zip(page, work["asin"], work["zip_info"])
            result.update({
                "sku": work["sku"],
                "brand": work["brand"],
                "category": work["category"],
                "deal_bucket": work["deal_bucket"],
                "is_own_sku": work["is_own_sku"],
            })
        finally:
            await context.close()
        return result
