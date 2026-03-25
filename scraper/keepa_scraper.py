"""
Keepa snapshot scraper — runs daily via GitHub Actions.
Reads ASINs from Supabase asin_list, fetches BSR/price/buybox data from Keepa,
writes snapshots + fires alerts on Critical/Warning changes.
"""
import os, json, gzip, re, asyncio
from datetime import datetime, timezone
from typing import List, Dict, Optional
import httpx
from supabase import create_client

KEEPA_KEY = os.environ["KEEPA_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TEAMS_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")
BATCH_SIZE = 100  # Keepa max per call

CHANGE_RULES = [
    {"field": "bsr", "pct_threshold": 20, "severity": "Warning", "type": "bsr_change"},
    {"field": "price_amazon", "pct_threshold": 10, "severity": "Warning", "type": "price_change"},
    {"field": "buybox_is_amazon", "exact": True, "severity": "Critical", "type": "buybox_lost"},
    {"field": "buybox_seller", "changed": True, "severity": "Critical", "type": "buybox_seller_change"},
    {"field": "in_stock", "became_false": True, "severity": "Critical", "type": "went_oos"},
    {"field": "parent_asin", "changed": True, "severity": "Critical", "type": "parent_changed"},
]


def fetch_keepa_batch(asins: List[str]) -> List[Dict]:
    """Fetch product data from Keepa for up to 100 ASINs."""
    url = (
        f"https://api.keepa.com/product?key={KEEPA_KEY}"
        f"&domain=1&asin={','.join(asins)}"
        f"&stats=180&buybox=1&rating=1&offers=20"
    )
    resp = httpx.get(url, timeout=60)
    resp.raise_for_status()
    raw = resp.content
    try:
        data = json.loads(gzip.decompress(raw))
    except Exception:
        data = json.loads(raw)
    return data.get("products", [])


def parse_product(p: Dict, asin_map: Dict) -> Dict:
    """Extract the fields we care about from a Keepa product object."""
    asin = p.get("asin", "")
    meta = asin_map.get(asin, {})
    
    # BSR — last value in SALES array
    sales = p.get("data", {}).get("SALES", [])
    bsr = None
    for i in range(len(sales)-1, -1, -1):
        if sales[i] and sales[i] > 0:
            bsr = sales[i]
            break

    # Prices (Keepa stores in cents, -1 = unavailable)
    def cents(v): return round(v / 100, 2) if v and v > 0 else None
    stats = p.get("stats", {})
    price_amazon = cents(stats.get("current", [-1]*20)[0] if stats.get("current") else -1)
    price_new = cents(stats.get("current", [-1]*20)[1] if stats.get("current") else -1)

    # Buy box
    buybox = p.get("buyBoxSeller", "")
    buybox_is_amazon = buybox in ("Amazon.com", "ATVPDKIKX0DER", "Amazon")

    # Rating (stored as int, divide by 10)
    rating_raw = p.get("data", {}).get("RATING", [])
    rating = None
    for v in reversed(rating_raw or []):
        if v and v > 0:
            rating = round(v / 10, 1)
            break

    review_count = p.get("data", {}).get("COUNT_REVIEWS", [None])[-1]
    in_stock = p.get("availabilityAmazon", 0) == 0  # 0 = in stock in Keepa

    parent_asin = p.get("parentAsin", "")
    variations = p.get("variations", [])
    
    return {
        "asin": asin,
        "sku": meta.get("sku"),
        "brand": meta.get("brand"),
        "category": meta.get("category"),
        "snapped_at": datetime.now(timezone.utc).isoformat(),
        "title": (p.get("title") or "")[:300],
        "bsr": bsr,
        "bsr_category": p.get("categoryTree", [{}])[0].get("name") if p.get("categoryTree") else None,
        "price_amazon": price_amazon,
        "price_new": price_new,
        "buybox_seller": buybox or None,
        "buybox_is_amazon": buybox_is_amazon,
        "rating": rating,
        "review_count": int(review_count) if review_count and review_count > 0 else None,
        "in_stock": in_stock,
        "parent_asin": parent_asin or None,
        "variation_count": len(variations),
        "raw_changes": None,
    }


def detect_changes(current: Dict, previous: Optional[Dict]) -> List[Dict]:
    """Compare current snapshot to previous and return list of detected changes."""
    if not previous:
        return []
    alerts = []
    for rule in CHANGE_RULES:
        field = rule["field"]
        curr_val = current.get(field)
        prev_val = previous.get(field)
        if curr_val is None or prev_val is None:
            continue
        triggered = False
        if "pct_threshold" in rule:
            if prev_val != 0:
                pct = abs((curr_val - prev_val) / prev_val * 100)
                triggered = pct >= rule["pct_threshold"]
        elif "became_false" in rule:
            triggered = (prev_val is True and curr_val is False)
        elif "changed" in rule:
            triggered = str(curr_val) != str(prev_val)
        if triggered:
            alerts.append({
                "asin": current["asin"],
                "sku": current.get("sku"),
                "brand": current.get("brand"),
                "alerted_at": datetime.now(timezone.utc).isoformat(),
                "severity": rule["severity"],
                "change_type": rule["type"],
                "old_value": str(prev_val),
                "new_value": str(curr_val),
                "detail": f"{field}: {prev_val} → {curr_val}",
                "alert_sent": False,
            })
    return alerts


async def send_teams_alert(alert: Dict):
    if not TEAMS_URL:
        return
    severity_color = "FF0000" if alert["severity"] == "Critical" else "FFA500"
    asin = alert["asin"]
    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard", "version": "1.4",
                "body": [
                    {"type": "TextBlock", "text": f"🚨 {alert['severity']}: {alert['change_type']}",
                     "weight": "Bolder", "size": "Medium",
                     "color": "Attention" if alert["severity"] == "Critical" else "Warning"},
                    {"type": "TextBlock", "text": f"**{alert.get('sku','?')}** ({asin})
{alert['detail']}
[View on Amazon](https://amazon.com/dp/{asin})", "wrap": True},
                ]
            }
        }]
    }
    async with httpx.AsyncClient() as client:
        await client.post(TEAMS_URL, json=payload, timeout=10)


async def run():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Load all active ASINs
    resp = sb.table("asin_list").select("asin,sku,brand,category").eq("active", True).execute()
    items = resp.data or []
    asin_map = {item["asin"]: item for item in items}
    all_asins = list(asin_map.keys())
    print(f"[keepa] Loaded {len(all_asins)} ASINs from Supabase")

    # Get last snapshot for each ASIN (for change detection)
    prev_resp = (
        sb.table("keepa_snapshots")
        .select("asin,bsr,price_amazon,buybox_is_amazon,buybox_seller,in_stock,parent_asin")
        .in_("asin", all_asins)
        .order("snapped_at", desc=True)
        .limit(len(all_asins) * 2)
        .execute()
    )
    seen = set()
    prev_by_asin = {}
    for row in (prev_resp.data or []):
        if row["asin"] not in seen:
            seen.add(row["asin"])
            prev_by_asin[row["asin"]] = row

    # Fetch from Keepa in batches of 100
    all_snapshots = []
    all_alerts = []
    tokens_used = 0

    for i in range(0, len(all_asins), BATCH_SIZE):
        batch = all_asins[i:i+BATCH_SIZE]
        print(f"[keepa] Fetching batch {i//BATCH_SIZE+1}/{(len(all_asins)-1)//BATCH_SIZE+1} ({len(batch)} ASINs)...")
        try:
            products = fetch_keepa_batch(batch)
            for p in products:
                snap = parse_product(p, asin_map)
                prev = prev_by_asin.get(snap["asin"])
                changes = detect_changes(snap, prev)
                snap["raw_changes"] = json.dumps([c["change_type"] for c in changes]) if changes else None
                all_snapshots.append(snap)
                all_alerts.extend(changes)
            tokens_used += len(batch)
            print(f"[keepa]   Got {len(products)} products")
        except Exception as e:
            print(f"[keepa] Batch error: {e}")

    # Write snapshots to Supabase
    for j in range(0, len(all_snapshots), 50):
        sb.table("keepa_snapshots").insert(all_snapshots[j:j+50]).execute()
    print(f"[keepa] Wrote {len(all_snapshots)} snapshots")

    # Write alerts
    if all_alerts:
        for j in range(0, len(all_alerts), 50):
            sb.table("keepa_alerts").insert(all_alerts[j:j+50]).execute()
        print(f"[keepa] Generated {len(all_alerts)} alerts")

        # Send Critical alerts to Teams
        critical = [a for a in all_alerts if a["severity"] == "Critical"]
        for alert in critical[:10]:  # cap at 10 per run to avoid spam
            await send_teams_alert(alert)
            await asyncio.sleep(0.5)

    # Summary
    by_severity = {}
    for a in all_alerts:
        by_severity[a["severity"]] = by_severity.get(a["severity"], 0) + 1
    print(f"[keepa] Done. Alerts: {by_severity}")


if __name__ == "__main__":
    asyncio.run(run())
