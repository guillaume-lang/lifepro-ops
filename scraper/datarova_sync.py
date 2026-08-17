"""
Datarova → Monday.com sync.

Pulls keyword rank intelligence from the Datarova User API (v0) and writes it
onto the Amazon USA Products board (8574487078) so PMs and ADS reps see, per SKU:

  - Main KW Rank        current organic rank for the item's Main KW
  - Rank Δ 7d / Δ 30d   rank movement (positive = improved)
  - Rank Taken By       ASINs that overtook us on keywords where we dropped
  - New KWs to Target   high-sales tracked keywords where we rank poorly / not at all
  - New ASINs Rising    ASINs newly entering the top-3 clicked on our Main KW
  - Category Trend      Main KW market clicks vs prior 4 weeks and vs last year
  - Datarova Synced     date of last successful sync

Env vars:
  DATAROVA_API_KEY   required — Bearer key from app.datarova.com Settings > Connections
  MONDAY_API_TOKEN   required — same token the other scrapers use
  DATAROVA_DRY_RUN   optional — "1" = fetch + compute + print, but write nothing to Monday
  DATAROVA_MAX_ITEMS optional — cap processed items (smoke tests)

Datarova API notes (from https://api.datarova.com/openapi/user-api-v0.json):
  - Base URL https://api.datarova.com/v0, Authorization: Bearer <key>
  - Pagination: data.page.next_cursor / has_more, opaque `cursor` query param
  - Rate limits: 60 req/min on most endpoints; 10 req/min on /keywords and
    /projects/{id}/ranks-by-asin. 429 responses carry Retry-After.
  - /keywords: max 100 keywords per call (25 when include_asins=true),
    max 26 weekly or 24 monthly intervals.
"""

import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, timedelta

import httpx

DATAROVA_BASE = "https://api.datarova.com/v0"
MONDAY_API_URL = "https://api.monday.com/v2"
BOARD_ID = "8574487078"
MARKETPLACE = "US"

# ---- board column ids ------------------------------------------------------
COL_ASIN = "text_mknhd0s7"
COL_MAIN_KW = "text_mkqn5qd6"
COL_DATAROVA_LINK = "link_mky2e4rt"
COL_STATUS = "status"

COL_OUT_RANK = os.environ.get("COL_OUT_RANK", "numeric_mm6aks1d")          # Main KW Rank
COL_OUT_D7 = os.environ.get("COL_OUT_D7", "numeric_mm6acsgp")              # Rank Δ 7d
COL_OUT_D30 = os.environ.get("COL_OUT_D30", "numeric_mm6ax6p")             # Rank Δ 30d
COL_OUT_TAKEN_BY = os.environ.get("COL_OUT_TAKEN_BY", "text_mm6asf9k")     # Rank Taken By
COL_OUT_NEW_KWS = os.environ.get("COL_OUT_NEW_KWS", "long_text_mm6a6ng2")  # New KWs to Target
COL_OUT_RISING = os.environ.get("COL_OUT_RISING", "long_text_mm6apc41")    # New ASINs Rising
COL_OUT_TREND = os.environ.get("COL_OUT_TREND", "text_mm6aps29")           # Category Trend
COL_OUT_SYNCED = os.environ.get("COL_OUT_SYNCED", "date_mm6asykq")         # Datarova Synced

# Same active statuses the other scrapers use: Active, NewLaunch, Relaunched, Top 10
ACTIVE_STATUS_IDS = ["1", "7", "8", "11"]

DATAROVA_LINK_RE = re.compile(r"app\.datarova\.com/projects/(\d+)(?:/(?:asins|ranks)/(B[0-9A-Z]{9}))?")
ASIN_RE = re.compile(r"^B[0-9A-Z]{9}$")

DRY_RUN = os.environ.get("DATAROVA_DRY_RUN", "").strip() in ("1", "true", "yes")
MAX_ITEMS = int(os.environ.get("DATAROVA_MAX_ITEMS", "0") or 0)


def norm_kw(kw: str) -> str:
    return re.sub(r"\s+", " ", (kw or "").strip().lower())


# ---------------------------------------------------------------------------
# Datarova client — shared budget-aware throttle per endpoint class
# ---------------------------------------------------------------------------
class Datarova:
    def __init__(self, api_key: str):
        self.headers = {"Authorization": f"Bearer {api_key.strip()}"}
        self.client = httpx.AsyncClient(timeout=60, headers=self.headers)
        # endpoint-class -> min seconds between calls (10/min endpoints get 6.5s)
        self._last_call: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def close(self):
        await self.client.aclose()

    async def get(self, path: str, params: dict | None = None, slow: bool = False) -> dict:
        """GET with per-class pacing + 429/5xx retry."""
        cls = "slow" if slow else "fast"
        min_gap = 6.5 if slow else 1.1
        async with self._locks[cls]:
            loop = asyncio.get_event_loop()
            wait = self._last_call.get(cls, 0) + min_gap - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call[cls] = loop.time()

        for attempt in range(5):
            resp = await self.client.get(f"{DATAROVA_BASE}{path}", params=params)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "15") or 15)
                print(f"[datarova] 429 on {path}, sleeping {retry_after}s")
                await asyncio.sleep(retry_after + 1)
                continue
            if resp.status_code >= 500:
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code in (401, 402, 403):
                raise RuntimeError(
                    f"Datarova API {resp.status_code} on {path}: {resp.text[:300]} "
                    "(401=bad key, 402=no plan/quota exceeded, 403=user-api not enabled)"
                )
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Datarova API: retries exhausted on {path}")

    async def get_paginated(self, path: str, params: dict | None = None,
                            list_key: str = "", slow: bool = False) -> list:
        """Follow data.page.next_cursor until has_more is false."""
        params = dict(params or {})
        out = []
        while True:
            data = (await self.get(path, params, slow=slow)).get("data", {})
            out.extend(data.get(list_key, []))
            page = data.get("page", {})
            if page.get("has_more") and page.get("next_cursor"):
                params["cursor"] = page["next_cursor"]
            else:
                return out

    async def usage(self) -> dict:
        return (await self.get("/usage")).get("data", {})

    async def project_asins(self, project_id: int) -> list:
        return await self.get_paginated(f"/projects/{project_id}/asins",
                                        {"limit": 200}, "asins")

    async def project_keywords(self, project_id: int) -> list:
        return await self.get_paginated(f"/projects/{project_id}/keywords",
                                        {"limit": 200}, "keywords")

    async def ranks_by_asin(self, project_id: int, asin: str,
                            start: date, end: date) -> list:
        return await self.get_paginated(
            f"/projects/{project_id}/ranks-by-asin",
            {"asin": asin, "start_date": start.isoformat(),
             "end_date": end.isoformat(), "limit": 20},
            "ranks", slow=True)

    async def market_keywords(self, keywords: list[str], start: date, end: date,
                              interval: str, include_asins: bool) -> dict:
        """Returns {normalized_keyword: [records]} across batched calls."""
        batch_size = 25 if include_asins else 100
        result: dict[str, list] = {}
        for i in range(0, len(keywords), batch_size):
            batch = keywords[i:i + batch_size]
            data = (await self.get("/keywords", {
                "marketplace": MARKETPLACE,
                "keywords": ",".join(batch),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "interval": interval,
                "include_asins": str(include_asins).lower(),
            }, slow=True)).get("data", {})
            for entry in data.get("keywords", []):
                result[norm_kw(entry.get("keyword", ""))] = entry.get("records", [])
        return result


# ---------------------------------------------------------------------------
# Monday.com
# ---------------------------------------------------------------------------
def monday_token() -> str:
    tok = os.environ["MONDAY_API_TOKEN"].strip()
    if tok.lower().startswith("bearer "):
        tok = tok[7:].strip()
    return tok


MONDAY_HEADERS = {
    "Authorization": "",
    "Content-Type": "application/json",
    "API-Version": "2024-01",
}


async def monday_query(client: httpx.AsyncClient, query: str, variables: dict | None = None) -> dict:
    resp = await client.post(MONDAY_API_URL,
                             json={"query": query, "variables": variables or {}},
                             headers=MONDAY_HEADERS)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"Monday API error: {data['errors']}")
    return data["data"]


async def fetch_board_items(client: httpx.AsyncClient) -> list[dict]:
    items, cursor = [], None
    col_ids = json.dumps([COL_ASIN, COL_MAIN_KW, COL_DATAROVA_LINK])
    status_vals = json.dumps(ACTIVE_STATUS_IDS)
    while True:
        cursor_clause = f', cursor: "{cursor}"' if cursor else ""
        query = f"""
        {{
          boards(ids: [{BOARD_ID}]) {{
            items_page(
              limit: 100{cursor_clause}
              query_params: {{
                operator: and
                rules: [{{ column_id: "{COL_STATUS}", compare_value: {status_vals}, operator: any_of }}]
              }}
            ) {{
              cursor
              items {{
                id
                name
                column_values(ids: {col_ids}) {{ id text value }}
              }}
            }}
          }}
        }}"""
        data = await monday_query(client, query)
        page = data["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            return items


async def write_item(client: httpx.AsyncClient, item_id: str, values: dict):
    mutation = """
    mutation ($board: ID!, $item: ID!, $vals: JSON!) {
      change_multiple_column_values(board_id: $board, item_id: $item, column_values: $vals) { id }
    }"""
    await monday_query(client, mutation, {
        "board": BOARD_ID, "item": item_id, "vals": json.dumps(values)})


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def latest_rank(daily: list[dict]) -> int | None:
    for row in sorted(daily, key=lambda r: r.get("date", ""), reverse=True):
        if row.get("organic_rank") is not None:
            return row["organic_rank"]
    return None


def rank_on(daily: list[dict], target: date, tolerance: int = 3) -> int | None:
    """Organic rank on target date, or the closest day within ±tolerance."""
    by_date = {r["date"]: r.get("organic_rank") for r in daily if r.get("date")}
    for off in range(tolerance + 1):
        for d in (target - timedelta(days=off), target + timedelta(days=off)):
            r = by_date.get(d.isoformat())
            if r is not None:
                return r
    return None


def pct(new: float, old: float) -> str:
    if not old:
        return "n/a"
    change = (new - old) / old * 100
    arrow = "↑" if change > 2 else ("↓" if change < -2 else "→")
    return f"{arrow} {change:+.0f}%"


def top3_asins(record: dict) -> list[str]:
    out = []
    for k in ("asin_1", "asin_2", "asin_3"):
        a = (record.get(k) or {}).get("asin")
        if a:
            out.append(a)
    return out


def analyze_item(item_asin: str, main_kw: str, ranks: list[dict],
                 proj_keywords: list[dict], weekly_market: list[dict],
                 monthly_market: list[dict], today: date) -> dict:
    """Compute all output column values for one item."""
    ranks_by_kw = {norm_kw(r["keyword"]): r.get("daily_ranks", []) for r in ranks}
    main_norm = norm_kw(main_kw)

    # --- Main KW rank + deltas (fall back to highest-sales tracked keyword) --
    tracked = main_norm if main_norm in ranks_by_kw else None
    if not tracked and proj_keywords:
        best = max(proj_keywords, key=lambda k: k.get("keyword_sales_l4w") or 0, default=None)
        if best and norm_kw(best["keyword"]) in ranks_by_kw:
            tracked = norm_kw(best["keyword"])
    daily = ranks_by_kw.get(tracked, [])
    cur = latest_rank(daily)
    r7 = rank_on(daily, today - timedelta(days=7))
    r30 = rank_on(daily, today - timedelta(days=30))
    d7 = (r7 - cur) if (cur is not None and r7 is not None) else None    # + = improved
    d30 = (r30 - cur) if (cur is not None and r30 is not None) else None

    # --- Rank Taken By: keywords where we dropped ≥3 spots in 7d ------------
    taken = []
    kw_top_asin = {norm_kw(k["keyword"]): k.get("top_asin") for k in proj_keywords}
    for kw, dr in ranks_by_kw.items():
        c, p = latest_rank(dr), rank_on(dr, today - timedelta(days=7))
        if c is not None and p is not None and (c - p) >= 3:
            top = kw_top_asin.get(kw)
            if top and top != item_asin:
                taken.append(f"{top} on '{kw}' (we went {p}→{c})")
    # market view: our ASIN fell out of the top-3 clicked on the Main KW
    if weekly_market:
        weeks = sorted(weekly_market, key=lambda r: r.get("date", ""))
        if len(weeks) >= 2:
            prev_top, cur_top = top3_asins(weeks[-2]), top3_asins(weeks[-1])
            if item_asin in prev_top and item_asin not in cur_top:
                newcomers = [a for a in cur_top if a not in prev_top]
                if newcomers:
                    taken.append(f"{', '.join(newcomers)} pushed us out of top-3 on '{main_kw}'")
    taken_by = "; ".join(taken[:3])

    # --- New KWs to Target: high sales, we rank >20 or unranked -------------
    opps = []
    for k in sorted(proj_keywords, key=lambda k: k.get("keyword_sales_l4w") or 0, reverse=True):
        our = k.get("organic_rank")
        sales = k.get("keyword_sales_l4w") or 0
        if sales > 0 and (our is None or our > 20):
            rank_txt = f"rank {our}" if our else "unranked"
            trend = k.get("trend_l4w")
            # trend_l4w scale is undocumented — show as % only if it looks like a fraction
            if isinstance(trend, (int, float)) and trend:
                trend_txt = f", trend {trend:+.0%}" if abs(trend) <= 5 else f", trend {trend:+,.0f}"
            else:
                trend_txt = ""
            opps.append(f"{k['keyword']} — {sales:,.0f} sales/4w, we're {rank_txt}{trend_txt}")
        if len(opps) >= 5:
            break
    new_kws = "\n".join(opps)

    # --- New ASINs Rising on the Main KW (market top-3 entrants) ------------
    rising = []
    if weekly_market:
        weeks = sorted(weekly_market, key=lambda r: r.get("date", ""))
        if len(weeks) >= 5:
            earlier = set()
            for w in weeks[:-1]:
                earlier.update(top3_asins(w))
            last = weeks[-1]
            for slot in ("asin_1", "asin_2", "asin_3"):
                a = (last.get(slot) or {})
                if a.get("asin") and a["asin"] not in earlier and a["asin"] != item_asin:
                    rising.append(f"{a['asin']} — {a.get('clicks') or 0:,.0f} clicks/wk on '{main_kw}'")
    asins_rising = "\n".join(rising[:5])

    # --- Category Trend on the Main KW ---------------------------------------
    trend_txt = ""
    if weekly_market and len(weekly_market) >= 8:
        weeks = sorted(weekly_market, key=lambda r: r.get("date", ""))
        last4 = sum(w.get("clicks") or 0 for w in weeks[-4:])
        prev4 = sum(w.get("clicks") or 0 for w in weeks[-8:-4])
        trend_txt = f"{pct(last4, prev4)} vs prior 4w"
    if monthly_market:
        months = sorted(monthly_market, key=lambda r: r.get("date", ""))
        if len(months) >= 13:
            cur_m = months[-1].get("clicks") or 0
            yoy_m = months[-13].get("clicks") or 0
            if yoy_m:
                trend_txt += f" · {pct(cur_m, yoy_m)} YoY"
    return {
        "rank": cur, "d7": d7, "d30": d30, "taken_by": taken_by,
        "new_kws": new_kws, "rising": asins_rising, "trend": trend_txt,
        "tracked_kw": tracked or "",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    api_key = os.environ.get("DATAROVA_API_KEY", "").strip()
    if not api_key:
        sys.exit("DATAROVA_API_KEY env var is required")
    MONDAY_HEADERS["Authorization"] = monday_token()

    dr = Datarova(api_key)
    today = date.today()

    usage = await dr.usage()
    print(f"[datarova] usage {usage.get('period_start')}→{usage.get('period_end')}: "
          f"{usage.get('request_count')} used, {usage.get('requests_remaining')} remaining")

    async with httpx.AsyncClient(timeout=60) as monday:
        items = await fetch_board_items(monday)
        print(f"[monday] {len(items)} active items on board")

        # Parse Datarova links → (project_id, link_asin)
        parsed = []
        for it in items:
            cols = {c["id"]: c for c in it["column_values"]}
            link_col = cols.get(COL_DATAROVA_LINK, {})
            link_text = (link_col.get("text") or "") + " " + (link_col.get("value") or "")
            m = DATAROVA_LINK_RE.search(link_text)
            if not m:
                continue
            asin = (cols.get(COL_ASIN, {}).get("text") or "").strip().upper()
            parsed.append({
                "item_id": it["id"], "name": it["name"],
                "asin": asin if ASIN_RE.match(asin) else None,
                "main_kw": (cols.get(COL_MAIN_KW, {}).get("text") or "").strip(),
                "project_id": int(m.group(1)),
                "link_asin": m.group(2),
            })
        if MAX_ITEMS:
            parsed = parsed[:MAX_ITEMS]
        print(f"[sync] {len(parsed)} items with a Datarova project link")

        # Per-project data (shared across items)
        project_ids = sorted({p["project_id"] for p in parsed})
        proj_asins, proj_kws = {}, {}
        for pid in project_ids:
            try:
                proj_asins[pid] = {a["asin"] for a in await dr.project_asins(pid)}
                proj_kws[pid] = await dr.project_keywords(pid)
            except Exception as e:
                print(f"[datarova] project {pid} failed: {e}")
                proj_asins[pid], proj_kws[pid] = set(), []
        print(f"[datarova] loaded {len(project_ids)} projects")

        # Market data for all unique Main KWs (batched)
        all_kws = sorted({norm_kw(p["main_kw"]) for p in parsed if p["main_kw"]})
        weekly, monthly = {}, {}
        if all_kws:
            try:
                weekly = await dr.market_keywords(
                    all_kws, today - timedelta(weeks=9), today, "weekly", include_asins=True)
            except Exception as e:
                print(f"[datarova] weekly market pull failed: {e}")
            try:
                monthly = await dr.market_keywords(
                    all_kws, (today - timedelta(days=395)).replace(day=1), today,
                    "monthly", include_asins=False)
            except Exception as e:
                print(f"[datarova] monthly market pull failed: {e}")
        print(f"[datarova] market data for {len(weekly)}/{len(all_kws)} keywords (weekly)")

        # Rank history per unique (project, asin) — the expensive part (10/min)
        rank_cache: dict[tuple, list] = {}
        targets = {}
        for p in parsed:
            target_asin = p["asin"] if p["asin"] in proj_asins.get(p["project_id"], set()) \
                else (p["link_asin"] or p["asin"])
            targets[p["item_id"]] = target_asin
            key = (p["project_id"], target_asin)
            rank_cache.setdefault(key, None)
        print(f"[datarova] {len(rank_cache)} unique (project, asin) rank pulls needed")
        for key in rank_cache:
            pid, asin = key
            if not asin:
                rank_cache[key] = []
                continue
            try:
                rank_cache[key] = await dr.ranks_by_asin(pid, asin, today - timedelta(days=35), today)
            except Exception as e:
                print(f"[datarova] ranks {pid}/{asin} failed: {e}")
                rank_cache[key] = []

        # Compute + write
        written = errors = 0
        for p in parsed:
            target_asin = targets[p["item_id"]]
            key = (p["project_id"], target_asin)
            res = analyze_item(
                item_asin=target_asin or "",
                main_kw=p["main_kw"],
                ranks=rank_cache.get(key) or [],
                proj_keywords=proj_kws.get(p["project_id"], []),
                weekly_market=weekly.get(norm_kw(p["main_kw"]), []),
                monthly_market=monthly.get(norm_kw(p["main_kw"]), []),
                today=today,
            )
            values = {
                COL_OUT_RANK: str(res["rank"]) if res["rank"] is not None else "",
                COL_OUT_D7: str(res["d7"]) if res["d7"] is not None else "",
                COL_OUT_D30: str(res["d30"]) if res["d30"] is not None else "",
                COL_OUT_TAKEN_BY: res["taken_by"][:255],
                COL_OUT_NEW_KWS: {"text": res["new_kws"][:2000]},
                COL_OUT_RISING: {"text": res["rising"][:2000]},
                COL_OUT_TREND: res["trend"][:255],
                COL_OUT_SYNCED: {"date": today.isoformat()},
            }
            if DRY_RUN:
                print(f"[dry-run] {p['name']} ({target_asin}) kw='{res['tracked_kw']}' "
                      f"rank={res['rank']} d7={res['d7']} d30={res['d30']} trend='{res['trend']}'")
                continue
            try:
                await write_item(monday, p["item_id"], values)
                written += 1
            except Exception as e:
                errors += 1
                print(f"[monday] write failed for {p['name']}: {e}")

        print(f"[sync] done — {written} items updated, {errors} errors, dry_run={DRY_RUN}")


if __name__ == "__main__":
    asyncio.run(main())
