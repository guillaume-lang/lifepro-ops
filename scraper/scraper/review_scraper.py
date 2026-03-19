import asyncio
import os
import random
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from playwright.async_api import async_playwright, Page

from scraper.monday_client import fetch_all_asins
from scraper.supabase_client import get_last_review_snapshots, upsert_review_snapshots
from scraper.alerts import send_teams_alert

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

CONCURRENCY = 8
UNMERGE_PCT_THRESHOLD = 20.0   # >20% drop triggers flag
UNMERGE_ABS_THRESHOLD = 50     # AND absolute drop > 50 reviews
REQUEST_DELAY_MIN = 1.5
REQUEST_DELAY_MAX = 3.5


async def scrape_review_data(page: Page, asin: str) -> Dict:
    """Scrape review count and star rating for one ASIN."""
    url = f"https://www.amazon.com/dp/{asin}?th=1"
    result = {
        "asin": asin,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "review_count": None,
        "star_rating": None,
        "block_detected": False,
    }

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        title = await page.title()
        if any(x in title.lower() for x in ["robot", "captcha", "sorry"]):
            result["block_detected"] = True
            return result

        # Review count — multiple selectors for resilience
        count_text = await _get_text(page, [
            "#acrCustomerReviewText",
            "span[data-hook='total-review-count']",
            "#reviews-medley-footer a span",
        ])
        if count_text:
            nums = re.findall(r"[\d,]+", count_text.replace(",", ""))
            if nums:
                result["review_count"] = int(nums[0].replace(",", ""))

        # Star rating
        rating_text = await _get_text(page, [
            "span[data-hook='rating-out-of-text']",
            "#averageCustomerReviews .a-icon-alt",
            "span.reviewCountTextLinkedHistogram",
        ])
        if rating_text:
            match = re.search(r"(\d+\.?\d*)", rating_text)
            if match:
                result["star_rating"] = float(match.group(1))

    except Exception as e:
        print(f"[reviews] Error scraping {asin}: {e}")
        result["block_detected"] = True

    return result


async def _get_text(page: Page, selectors: List[str]) -> Optional[str]:
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                if text:
                    return text
        except Exception:
            continue
    return None


def _detect_unmerge(current: Dict, previous: Dict) -> Dict:
    """Compare current snapshot to previous and flag unmerges."""
    prev_count = previous.get("review_count") if previous else None
    curr_count = current.get("review_count")

    result = {
        **current,
        "prev_count": prev_count,
        "count_delta": None,
        "delta_pct": None,
        "unmerge_flag": False,
        "alert_sent": False,
    }

    if prev_count is None or curr_count is None:
        return result

    delta = curr_count - prev_count
    pct = (delta / prev_count * 100) if prev_count > 0 else 0.0

    result["count_delta"] = delta
    result["delta_pct"] = round(pct, 2)

    if (
        pct <= -UNMERGE_PCT_THRESHOLD
        and abs(delta) >= UNMERGE_ABS_THRESHOLD
    ):
        result["unmerge_flag"] = True

    return result


async def run_review_scrape():
    """Main entry point for review monitoring job."""
    print("[reviews] Fetching ASIN list from Monday.com...")
    items = await fetch_all_asins()
    print(f"[reviews] Monitoring {len(items)} ASINs")

    # Get last known snapshot for each ASIN from Supabase
    asin_list = [item["asin"] for item in items]
    prev_snapshots = await get_last_review_snapshots(asin_list)
    prev_by_asin = {s["asin"]: s for s in prev_snapshots}

    # Build work items
    work_items = [
        {"asin": item["asin"], "sku": item["sku"], "brand": item["brand"],
         "category": item["category"], "monday_url": item["monday_url"]}
        for item in items
    ]
    random.shuffle(work_items)

    results = []
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )

        async def scrape_one(work):
            async with semaphore:
                ua = random.choice(USER_AGENTS)
                context = await browser.new_context(
                    user_agent=ua,
                    locale="en-US",
                    viewport={"width": 1280, "height": 800},
                )
                page = await context.new_page()
                try:
                    raw = await scrape_review_data(page, work["asin"])
                    enriched = _detect_unmerge(raw, prev_by_asin.get(work["asin"]))
                    enriched.update({
                        "sku": work["sku"],
                        "brand": work["brand"],
                        "category": work["category"],
                        "monday_url": work["monday_url"],
                    })
                    return enriched
                finally:
                    await context.close()

        tasks = [scrape_one(w) for w in work_items]
        results = await asyncio.gather(*tasks)
        await browser.close()

    # Write all snapshots to Supabase
    await upsert_review_snapshots(results)

    # Fire alerts for unmerges
    unmerges = [r for r in results if r.get("unmerge_flag") and not r.get("block_detected")]
    blocked = [r for r in results if r.get("block_detected")]

    print(f"[reviews] Complete. {len(results)} scraped, {len(unmerges)} unmerges detected, {len(blocked)} blocked.")

    for u in unmerges:
        msg = (
            f"**{u['sku']}** ({u['asin']})\n"
            f"Brand: {u['brand']} · Category: {u['category']}\n"
            f"Reviews: **{u['prev_count']} → {u['review_count']}** "
            f"({u['delta_pct']}% · -{abs(u['count_delta'])} reviews)\n"
            f"[Monday item]({u['monday_url']}) · "
            f"[Amazon listing](https://www.amazon.com/dp/{u['asin']})"
        )
        await send_teams_alert(
            title="Review Unmerge Detected",
            message=msg,
            severity="critical"
        )
        # Mark alert sent
        u["alert_sent"] = True

    # Re-write updated alert_sent flags
    if unmerges:
        await upsert_review_snapshots(unmerges)

    if len(blocked) / max(len(results), 1) > 0.15:
        await send_teams_alert(
            title="Review Scraper — High Block Rate",
            message=f"{len(blocked)}/{len(results)} requests blocked. Check scraper health.",
            severity="warning"
        )
