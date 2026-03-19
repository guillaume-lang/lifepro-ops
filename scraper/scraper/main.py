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
    else:
        print(f"[error] Unknown SCRAPER_MODE: {MODE}. Use 'delivery' or 'reviews'.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
