"""
Keepa snapshot scraper — runs daily via GitHub Actions.
Reads ASINs from Supabase asin_list, fetches BSR/price/buybox from Keepa API,
writes snapshots and fires Teams alerts on Critical/Warning changes.
"""
import os, json, gzip, asyncio
from datetime import datetime, timezone
from typing import List, Dict, Optional
import httpx
from supabase import create_client

KEEPA_KEY = os.environ["KEEPA_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TEAMS_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")
BATCH_SIZE = 100

CHANGE_RULES = [
    {"field": "bsr",            "pct_threshold": 20,  "severity": "Warning",  "type": "bsr_change"},
    {"field": "price_amazon",   "pct_threshold": 10,  "severity": "Warning",  "type": "price_change"},
    {"field": "buybox_is_amazon","became_false": True, "severity": "Critical", "type": "buybox_lost"},
    {"field": "buybox_seller",  "changed": True,       "severity": "Critical", "type": "buybox_seller_change"},
    {"field": "in_stock",       "became_false": True,  "severity": "Critical", "type": "went_oos"},
    {"field": "parent_asin",    "changed": True,       "severity": "Critical", "type": "parent_changed"},
]


def fetch_keepa_batch(asins: List[str]) -> List[Dict]:
    url = (
        "https://api.keepa.com/product"
        "?key=" + KEEPA_KEY +
        "&domain=1&asin=" + ",".join(asins) +
        "&stats=180&buybox=1&rating=1&offers=20"
    )
    resp = httpx.get(url, timeout=60)
    resp.raise_for_status()
    raw = resp.content
    try:
        return json.loads(gzip.decompress(raw)).get("products", [])
    except Exception:
        return json.loads(raw).get("products", [])


def parse_product(p: Dict, asin_map: Dict) -> Dict:
    asin = p.get("asin", "")
    meta = asin_map.get(asin, {})

    sales = p.get("data", {}).get("SALES", [])
    bsr = None
    for v in reversed(sales or []):
        if v and v > 0:
            bsr = v
            break

    def cents(v):
        return round(v / 100, 2) if v and v > 0 else None

    stats = p.get("stats", {})
    current = stats.get("current", []) or []
    price_amazon = cents(current[0]) if len(current) > 0 else None
    price_new    = cents(current[1]) if len(current) > 1 else None

    buybox = p.get("buyBoxSeller") or ""
    buybox_is_amazon = buybox in ("Amazon.com", "ATVPDKIKX0DER", "Amazon")

    rating_list = p.get("data", {}).get("RATING", []) or []
    rating = None
    for v in reversed(rating_list):
        if v and v > 0:
            rating = round(v / 10, 1)
            break

    review_list = p.get("data", {}).get("COUNT_REVIEWS", []) or []
    review_count = review_list[-1] if review_list else None

    in_stock = p.get("availabilityAmazon", 1) == 0
    parent_asin = p.get("parentAsin") or None
    variations = p.get("variations") or []

    return {
        "asin":             asin,
        "sku":              meta.get("sku"),
        "brand":            meta.get("brand"),
        "category":         meta.get("category"),
        "snapped_at":       datetime.now(timezone.utc).isoformat(),
        "title":            (p.get("title") or "")[:300],
        "bsr":              bsr,
        "bsr_category":     (p.get("categoryTree") or [{}])[0].get("name"),
        "price_amazon":     price_amazon,
        "price_new":        price_new,
        "buybox_seller":    buybox or None,
        "buybox_is_amazon": buybox_is_amazon,
        "rating":           rating,
        "review_count":     int(review_count) if review_count and review_count > 0 else None,
        "in_stock":         in_stock,
        "parent_asin":      parent_asin,
        "variation_count":  len(variations),
        "raw_changes":      None,
    }


def detect_changes(curr: Dict, prev: Optional[Dict]) -> List[Dict]:
    if not prev:
        return []
    alerts = []
    for rule in CHANGE_RULES:
        field = rule["field"]
        cv = curr.get(field)
        pv = prev.get(field)
        if cv is None or pv is None:
            continue
        triggered = False
        if "pct_threshold" in rule and pv != 0:
            triggered = abs((cv - pv) / pv * 100) >= rule["pct_threshold"]
        elif "became_false" in rule:
            triggered = (pv is True and cv is False)
        elif "changed" in rule:
            triggered = str(cv) != str(pv)
        if triggered:
            alerts.append({
                "asin":       curr["asin"],
                "sku":        curr.get("sku"),
                "brand":      curr.get("brand"),
                "alerted_at": datetime.now(timezone.utc).isoformat(),
                "severity":   rule["severity"],
                "change_type":rule["type"],
                "old_value":  str(pv),
                "new_value":  str(cv),
                "detail":     field + ": " + str(pv) + " -> " + str(cv),
                "alert_sent": False,
            })
    return alerts


async def send_teams_alert(alert: Dict):
    if not TEAMS_URL:
        return
    asin = alert["asin"]
    sku = alert.get("sku") or "?"
    detail = alert["detail"]
    title = alert["severity"] + ": " + alert["change_type"]
    body_text = sku + " (" + asin + ")\n" + detail
    link = "https://amazon.com/dp/" + asin
    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": title,
                        "weight": "Bolder",
                        "size": "Medium",
                        "color": "Attention" if alert["severity"] == "Critical" else "Warning",
                    },
                    {
                        "type": "TextBlock",
                        "text": body_text,
                        "wrap": True,
                    },
                    {
                        "type": "ActionSet",
                        "actions": [{"type": "Action.OpenUrl", "title": "View on Amazon", "url": link}],
                    },
                ],
            },
        }],
    }
    async with httpx.AsyncClient() as client:
        await client.post(TEAMS_URL, json=payload, timeout=10)


async def run():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    resp = sb.table("asin_list").select("asin,sku,brand,category").eq("active", True).execute()
    items = resp.data or []
    asin_map = {item["asin"]: item for item in items}
    all_asins = list(asin_map.keys())
    print("[keepa] Loaded " + str(len(all_asins)) + " ASINs from Supabase")

    prev_resp = (
        sb.table("keepa_snapshots")
        .select("asin,bsr,price_amazon,buybox_is_amazon,buybox_seller,in_stock,parent_asin")
        .in_("asin", all_asins)
        .order("snapped_at", desc=True)
        .limit(len(all_asins) * 2)
        .execute()
    )
    seen: set = set()
    prev_by_asin: Dict = {}
    for row in (prev_resp.data or []):
        if row["asin"] not in seen:
            seen.add(row["asin"])
            prev_by_asin[row["asin"]] = row

    all_snapshots = []
    all_alerts = []

    for i in range(0, len(all_asins), BATCH_SIZE):
        batch = all_asins[i:i + BATCH_SIZE]
        batch_num = str(i // BATCH_SIZE + 1)
        total_batches = str((len(all_asins) - 1) // BATCH_SIZE + 1)
        print("[keepa] Batch " + batch_num + "/" + total_batches + " (" + str(len(batch)) + " ASINs)...")
        try:
            products = fetch_keepa_batch(batch)
            for p in products:
                snap = parse_product(p, asin_map)
                prev = prev_by_asin.get(snap["asin"])
                changes = detect_changes(snap, prev)
                snap["raw_changes"] = json.dumps([c["change_type"] for c in changes]) if changes else None
                all_snapshots.append(snap)
                all_alerts.extend(changes)
            print("[keepa]   Got " + str(len(products)) + " products")
        except Exception as e:
            print("[keepa] Batch error: " + str(e))

    for j in range(0, len(all_snapshots), 50):
        sb.table("keepa_snapshots").insert(all_snapshots[j:j + 50]).execute()
    print("[keepa] Wrote " + str(len(all_snapshots)) + " snapshots")

    if all_alerts:
        for j in range(0, len(all_alerts), 50):
            sb.table("keepa_alerts").insert(all_alerts[j:j + 50]).execute()
        by_sev: Dict = {}
        for a in all_alerts:
            by_sev[a["severity"]] = by_sev.get(a["severity"], 0) + 1
        print("[keepa] Alerts: " + str(by_sev))
        for alert in [a for a in all_alerts if a["severity"] == "Critical"][:10]:
            await send_teams_alert(alert)
            await asyncio.sleep(0.5)

    print("[keepa] Done.")


if __name__ == "__main__":
    asyncio.run(run())
