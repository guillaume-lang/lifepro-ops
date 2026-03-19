import os
from typing import List, Dict
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

_client: Client = None

def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


async def upsert_delivery_results(results: List[Dict], work_items: List[Dict]):
    """Write delivery scrape results to delivery_results table."""
    sb = get_client()
    rows = []
    for r, w in zip(results, work_items):
        if r.get("block_detected"):
            continue
        rows.append({
            "asin": r["asin"],
            "sku": r.get("sku"),
            "is_own_sku": r.get("is_own_sku", False),
            "brand": r.get("brand"),
            "category": r.get("category"),
            "deal_bucket": r.get("deal_bucket"),
            "zip": r["zip"],
            "city": r.get("city"),
            "region": r.get("region"),
            "scraped_at": r["scraped_at"],
            "prime_available": r.get("prime_available", False),
            "prime_days": r.get("prime_days"),
            "prime_date": r.get("prime_date"),
            "same_day": r.get("same_day", False),
            "tonight": r.get("tonight", False),
            "next_day": r.get("next_day", False),
            "one_hour": r.get("one_hour", False),
            "standard_date": r.get("standard_date"),
            "standard_days": r.get("standard_days"),
            "buybox_type": r.get("buybox_type"),
            "in_stock": r.get("in_stock", False),
            "block_detected": r.get("block_detected", False),
        })

    if rows:
        sb.table("delivery_results").insert(rows).execute()
        print(f"[supabase] Inserted {len(rows)} delivery results")


async def get_last_review_snapshots(asin_list: List[str]) -> List[Dict]:
    """Get most recent review snapshot for each ASIN."""
    sb = get_client()
    if not asin_list:
        return []

    # Supabase doesn't support DISTINCT ON directly — fetch recent and dedupe in Python
    resp = (
        sb.table("review_snapshots")
        .select("asin, review_count, star_rating, scraped_at")
        .in_("asin", asin_list)
        .order("scraped_at", desc=True)
        .limit(len(asin_list) * 3)  # Allow for multiple recent snapshots per ASIN
        .execute()
    )

    seen = set()
    latest = []
    for row in (resp.data or []):
        if row["asin"] not in seen:
            seen.add(row["asin"])
            latest.append(row)

    return latest


async def upsert_review_snapshots(results: List[Dict]):
    """Write review snapshots to review_snapshots table."""
    sb = get_client()
    rows = []
    for r in results:
        if r.get("block_detected"):
            continue
        rows.append({
            "asin": r["asin"],
            "sku": r.get("sku"),
            "brand": r.get("brand"),
            "category": r.get("category"),
            "scraped_at": r["scraped_at"],
            "review_count": r.get("review_count"),
            "star_rating": r.get("star_rating"),
            "prev_count": r.get("prev_count"),
            "count_delta": r.get("count_delta"),
            "delta_pct": r.get("delta_pct"),
            "unmerge_flag": r.get("unmerge_flag", False),
            "alert_sent": r.get("alert_sent", False),
        })

    if rows:
        sb.table("review_snapshots").insert(rows).execute()
        print(f"[supabase] Inserted {len(rows)} review snapshots")
