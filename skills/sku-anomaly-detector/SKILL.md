---
name: sku-anomaly-detector
description: >
  Monitors Keepa-sourced BSR, price, and buybox signals for Lifepro's 240 active ASINs
  across all brands (Lifepro, Oaktiv, Petcove, Joyberri, Culvani, Sunello). Reads
  keepa_alerts (last 48h) and keepa_snapshots from Supabase, deduplicates by ASIN +
  change_type, interprets BSR changes by rank context, and routes signals to the Ads team.
  Use this skill when someone asks to: run the SKU anomaly check, generate the Keepa digest,
  check BSR drops, who lost the buybox overnight, what SKUs are OOS, check price changes,
  get the anomaly report, or asks "what Keepa alerts do we have today."
---

# SKU Anomaly Detector

Replaces the manual daily check of Keepa signals for 240 active ASINs. Reads `keepa_alerts`
and `keepa_snapshots` from Supabase, classifies anomalies by severity, interprets BSR changes
in context, and routes actionable signals to the right team.

## Data Sources

| Table | Purpose |
|---|---|
| `keepa_alerts` | One row per detected anomaly. 6 change types across Critical and Warning severity. Written daily by keepa_scraper.py at 7am EST. |
| `keepa_snapshots` | Daily snapshot per ASIN — BSR, price, buybox seller, in_stock, rating, review count. Most recent row per ASIN = current state. |
| `asin_list` | Master ASIN registry. Use to look up brand, category, or Monday URL by ASIN if not present in keepa data. |

## Step 1 — Query keepa_alerts (48h window)

```sql
SELECT id, asin, sku, brand, severity, change_type, old_value, new_value, detail, alerted_at
FROM keepa_alerts
WHERE alerted_at >= NOW() - INTERVAL '48 hours'
ORDER BY
  CASE severity WHEN 'Critical' THEN 0 WHEN 'Warning' THEN 1 ELSE 2 END,
  alerted_at DESC
LIMIT 200;
```

Always use a 48-hour window (not 24h) — the scraper runs once daily at 7am EST, so a 24h
window can miss alerts depending on when you're reading this.

## Step 2 — Fetch snapshot context per ASIN

```sql
SELECT DISTINCT ON (asin)
  asin, sku, brand, category, bsr, price_amazon, price_new,
  buybox_seller, buybox_is_amazon, rating, review_count,
  in_stock, parent_asin, snapped_at
FROM keepa_snapshots
WHERE asin = ANY(ARRAY['B0...', 'B0...'])
ORDER BY asin, snapped_at DESC;
```

Join snapshot context into each alert record to add current state (BSR, price, stock status).

## Step 3 — Deduplicate

If the same ASIN + change_type appears more than once in the 48h window (e.g., an OOS
ASIN that was already flagged yesterday), keep only the most recent row per ASIN + change_type
pair. Do not report the same issue twice.

## 6 Change Types

| change_type | Severity | Trigger condition |
|---|---|---|
| `went_oos` | Critical | `in_stock` flipped True → False |
| `buybox_lost` | Critical | `buybox_is_amazon` flipped True → False |
| `buybox_seller_change` | Critical | `buybox_seller` string changed to any different value |
| `parent_changed` | Critical | `parent_asin` string changed |
| `bsr_change` | Warning | BSR moved ≥ 20% in either direction (abs value) |
| `price_change` | Warning | `price_amazon` changed ≥ 10% in either direction |

**BSR direction note:** BSR is a rank — a HIGHER number = WORSE rank.
- `old_value=800, new_value=1200` → rank got worse → label "BSR DROP"
- `old_value=1200, new_value=800` → rank improved → label "BSR SPIKE"

Both directions fire the same `bsr_change` alert. Only rank declines (new > old) should
trigger an Ads MONITOR signal.

## BSR Interpretation by Rank Band

BSR changes mean very different things depending on absolute rank. Calibrate urgency accordingly.

| Rank band | Classification | Threshold for concern |
|---|---|---|
| BSR < 500 | Elite | Any ≥ 15% rank decline = investigate same day |
| BSR 500–3,000 | High performer | ≥ 20% decline = meaningful, check today |
| BSR 3,000–20,000 | Mid-tier | ≥ 30% decline = worth investigating |
| BSR 20,000–100,000 | Standard | ≥ 50% decline or coincides with OOS = flag |
| BSR > 100,000 | Long-tail | High variance is normal; only flag if doubled |

**Common root causes for BSR drops:**
- Competitor Lightning Deal or heavy coupon
- Our price increased above market
- Buybox lost or instability
- Deal or coupon ended
- Keyword rank fell (can check Search Query Performance)
- Ad budget exhausted or paused

## Severity Routing

| change_type | Owner | Same-day action |
|---|---|---|
| `went_oos` | Ads rep (pause) + Logistics (ship FBA) | Yes |
| `buybox_lost` | Ads rep (pause/reduce bids) + PM (who won BB?) | Yes |
| `buybox_seller_change` | PM + Pricing team (3P undercutting?) | Yes |
| `parent_changed` | PM + Compliance (ASIN structure integrity) | Yes |
| `bsr_change` (decline) | Ads rep (review bids/budget) | Today |
| `bsr_change` (improvement) | Note only — positive signal | No |
| `price_change` | PM + Pricing team (MAP compliance?) | Today |

## Ads Team Signal Protocol

### PAUSE (act within 1 hour)
- **`went_oos`** — Zero inventory = zero conversions. Spending ad budget with no possible return.
- **`buybox_lost`** — Our ads may drive traffic that a competitor converts at their price.
  Pause or sharply reduce bids until Amazon is back in buybox.
- **`buybox_seller_change`** — Similar to buybox_lost. Competitor may be on listing at lower price.
- **`parent_changed`** — ASIN structure issue. Do not run ads until PM confirms the listing is stable.

### MONITOR (act within same business day)
- **`bsr_change` (rank declined)** — Organic rank falling. May need to increase bids to compensate
  or investigate root cause before spending more.
- **`price_change`** — If we dropped price, check margin. If a competitor dropped price, review
  bid strategy — lower CPCs may be sufficient now.
- **`buybox_seller_change`** (already paused) — After pausing, monitor if BB returns to Amazon.

### RESUME (when condition clears)
- `went_oos` resolved → FBA restocked confirmed → resume campaigns at prior budget
- `buybox_lost` resolved → Amazon back as seller → restore full bids
- `parent_changed` resolved → PM confirms listing structure OK → resume

## Action Recommendations by change_type

| change_type | Recommended action |
|---|---|
| `went_oos` | Pause campaigns immediately. Check warehouse stock — if available, ship to FBA urgently. |
| `buybox_lost` | Pause or reduce bids. PM to investigate who won buybox and at what price. |
| `buybox_seller_change` | Pause campaigns. Check if 3P seller is undercutting. PM to review pricing and BB eligibility. |
| `parent_changed` | Do not run ads until ASIN structure is confirmed. PM to investigate in Seller/Vendor Central. |
| `bsr_change` (worse) | Monitor bids — rank declining may require bid increase to defend position. |
| `bsr_change` (better) | Rank improving — consider increasing budget to capitalize on momentum. |
| `price_change` | Check margin impact. If competitor lowered price, review bid strategy with ads rep. |

## Output Format

Return a structured digest matching the dashboard JSON schema:

```json
{
  "run_time": "ISO 8601 timestamp",
  "summary": {
    "total": 5,
    "critical": 3,
    "warning": 2,
    "brands_affected": 2
  },
  "critical_alerts": [
    {
      "asin": "B0XXXXXXXX",
      "sku": "LP-XXXXX-BLK",
      "brand": "Lifepro",
      "category": "Massage guns",
      "severity": "Critical",
      "change_type": "went_oos",
      "change_label": "OUT OF STOCK",
      "old_value": "true",
      "new_value": "false",
      "current_bsr": 1240,
      "current_price": 89.99,
      "buybox_seller": null,
      "buybox_is_amazon": false,
      "in_stock": false,
      "rating": 4.3,
      "review_count": 2841,
      "alerted_at": "ISO timestamp",
      "time_ago": "3h ago",
      "ads_signal": "PAUSE",
      "action": "Pause campaigns immediately. Check warehouse stock — if available, ship to FBA urgently."
    }
  ],
  "warning_alerts": [],
  "ads_signals": {
    "pause_skus": [
      { "sku": "LP-XXXXX-BLK", "asin": "B0XXXXXXXX", "reason": "Out of stock" }
    ],
    "monitor_skus": [
      { "sku": "LP-YYYYY-WHT", "asin": "B0YYYYYYYY", "reason": "BSR dropped 34% (rank 1100 → 1662)" }
    ]
  }
}
```

## Scraper Context

- **Schedule:** Daily at 7:00 AM EST via GitHub Actions (`keepa-snapshot.yml`)
- **Coverage:** 240 active ASINs from `asin_list` table in Supabase
- **Batching:** 100 ASINs per Keepa API request
- **Teams alerts:** Up to 10 Critical alerts per run sent to Teams webhook
- **All alerts stored:** `keepa_alerts` table is cumulative — always filter by 48h window

## Dashboard

Web interface at `tools/sku-anomaly/index.html` (live at https://lifepro-sku-anomaly.vercel.app).
Calls Claude API with Supabase MCP to render this digest visually — alert cards grouped by
severity, ads signals grid, summary bar.
