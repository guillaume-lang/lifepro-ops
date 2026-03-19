import os
import re
import httpx
from typing import List, Dict

MONDAY_API_URL = "https://api.monday.com/v2"
_RAW_TOKEN = os.environ["MONDAY_API_TOKEN"].strip()
# Monday personal tokens are raw JWTs - strip "Bearer " if accidentally included
MONDAY_TOKEN = _RAW_TOKEN.lstrip("Bearer ").lstrip("bearer ").strip()

BOARD_ID = "8574487078"

HEADERS = {
    "Authorization": MONDAY_TOKEN,
    "Content-Type": "application/json",
    "API-Version": "2024-01",
}

ASIN_SPLIT_RE = re.compile(r"[\t\n\r,;|\s]+")

def parse_competitor_asins(raw: str | None) -> List[str]:
    if not raw or raw.strip() in ("", "-", "N/A"):
        return []
    parts = ASIN_SPLIT_RE.split(raw.strip())
    return [p.strip() for p in parts if re.match(r"^B[0-9A-Z]{9}$", p.strip())]

async def test_connection():
    """Quick auth test before running full fetch."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            MONDAY_API_URL,
            json={"query": "{me{name email}}"},
            headers=HEADERS
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise ValueError(f"Monday API auth failed: {data['errors']}")
        me = data.get("data", {}).get("me", {})
        print(f"[monday] Connected as: {me.get('name')} ({me.get('email')})")
        return True

async def fetch_all_asins() -> List[Dict]:
    await test_connection()

    items = []
    cursor = None

    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            cursor_clause = f', cursor: "{cursor}"' if cursor else ""
            query = f"""
            {{
              boards(ids: [{BOARD_ID}]) {{
                items_page(
                  limit: 100{cursor_clause}
                  query_params: {{
                    operator: and
                    rules: [{{
                      column_id: "status"
                      compare_value: ["1","7","8","11"]
                      operator: any_of
                    }}]
                  }}
                ) {{
                  cursor
                  items {{
                    id
                    name
                    url
                    column_values(ids: [
                      "text_mknhd0s7",
                      "text_mkxj3ec8",
                      "text_mkxp62c",
                      "color_mktjf611",
                      "color_mky9e9at",
                      "numeric_mknjr9cg",
                      "numeric_mknj71zj"
                    ]) {{
                      id
                      text
                    }}
                  }}
                }}
              }}
            }}
            """
            resp = await client.post(
                MONDAY_API_URL,
                json={"query": query},
                headers=HEADERS
            )
            resp.raise_for_status()
            data = resp.json()

            if "errors" in data:
                print(f"[monday] API error: {data['errors']}")
                break

            page = data["data"]["boards"][0]["items_page"]
            for item in page["items"]:
                cols = {c["id"]: c["text"] for c in item["column_values"]}
                asin = (cols.get("text_mknhd0s7") or "").strip()
                if not asin or not re.match(r"^B[0-9A-Z]{9}$", asin):
                    continue
                items.append({
                    "sku": item["name"],
                    "asin": asin,
                    "brand": cols.get("color_mktjf611") or "",
                    "category": cols.get("text_mkxp62c") or "",
                    "deal_bucket": cols.get("color_mky9e9at") or "",
                    "review_count": _safe_int(cols.get("numeric_mknjr9cg")),
                    "star_rating": _safe_float(cols.get("numeric_mknj71zj")),
                    "competitor_asins": parse_competitor_asins(cols.get("text_mkxj3ec8")),
                    "monday_url": item["url"],
                })

            cursor = page.get("cursor")
            print(f"[monday] Fetched {len(items)} items so far...")
            if not cursor:
                break

    print(f"[monday] Total active ASINs: {len(items)}")
    return items

def _safe_int(val):
    try:
        return int(float(val)) if val else None
    except (ValueError, TypeError):
        return None

def _safe_float(val):
    try:
        return round(float(val), 1) if val else None
    except (ValueError, TypeError):
        return None
