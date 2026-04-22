import os
import asyncio
import sys
from dotenv import load_dotenv

load_dotenv()

MODE = os.environ.get("SCRAPER_MODE", "delivery")

async def main():
    print(f"[lifepro-scraper] Starting in mode: {MODE}")

    if MODE == "delivery":
        from scraper.delivery_scraper import run_delivery_scrape
        await run_delivery_scrape()
    elif MODE == "reviews":
        from scraper.review_scraper import run_review_scrape
        await run_review_scrape()
    elif MODE == "pm_sync":
        from scraper.monday_client import fetch_pm_assignments
        from scraper.supabase_client import upsert_pm_assignments
        rows = await fetch_pm_assignments()
        await upsert_pm_assignments(rows)
    else:
        print(f"[error] Unknown SCRAPER_MODE: {MODE}. Use 'delivery', 'reviews', or 'pm_sync'.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
