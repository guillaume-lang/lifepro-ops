# Inventory Forecaster — SKILL.md

## 1. Purpose

Daily inventory cover analysis for Lifepro Amazon USA (1P Vendor Central). Identifies SKUs at risk of stocking out before a factory PO can arrive, and SKUs approaching 180-day aging overstock thresholds.

---

## 2. Business Context

- **1P only** — Amazon issues POs to Lifepro. Lifepro ships from a shared warehouse. No FBA.
- **Shared warehouse** — WH stock is consumed by Amazon VC, DTC, retail, and all other channels.
- **Factory lead time** — 100-120 days from PO to warehouse receipt.
- **Top 10 SKUs** — highest-revenue SKUs must maintain a minimum 6-month (180-day) cover at all times.
- **Aging risk** — Amazon penalises inventory over 180 days old. Overstocking beyond 210 days is a financial risk.

---

## 3. Data Sources

### Monday.com — Board 8574487078 (Amazon USA Products)

| Field | Column ID |
|---|---|
| SKU (item name) | item name |
| WH Stock | numeric_mkyrk4we |
| Brand | color_mktjf611 |
| ASIN | text_mknhd0s7 |
| Category | text_mkxp62c |

### Supabase — keepa_snapshots

| Column | Description |
|---|---|
| asin | Amazon ASIN |
| bsr | Best Seller Rank at snapshot time |
| snapped_at | UTC timestamp |

Query pattern:
- Latest BSR: SELECT bsr FROM keepa_snapshots WHERE asin = $1 ORDER BY snapped_at DESC LIMIT 1
- 7-day-ago BSR: SELECT bsr FROM keepa_snapshots WHERE asin = $1 AND snapped_at <= NOW() - INTERVAL '7 days' ORDER BY snapped_at DESC LIMIT 1

---

## 4. Velocity Formula

  monthly_units  = bsr_to_monthly_units(bsr, category)
  bsr_trend_adj  = 1.0-1.2 if BSR improving (rank number falling = more sales)
                 = 0.8-1.0 if BSR worsening (rank rising = fewer sales)
  channel_split  = 0.7  (Amazon takes approx 70% of shared WH demand)
  daily_velocity = monthly_units x bsr_trend_adj x channel_split / 30
  days_of_cover  = wh_stock / daily_velocity

### BSR to Monthly Units Benchmarks

| Category | BSR 1-500 | BSR 500-3k | BSR 3k-20k | BSR 20k-100k | BSR >100k |
|---|---|---|---|---|---|
| Massage guns | 800 | 400 | 80 | 20 | 5 |
| Vibration plates | 600 | 300 | 60 | 15 | 4 |
| Light therapy | 700 | 350 | 70 | 18 | 4 |
| Default | 500 | 250 | 50 | 12 | 3 |

---

## 5. Classification Logic

### Regular SKUs

| Status | Days of Cover | Meaning |
|---|---|---|
| REORDER_NOW | < 120 days | Must order today — inside lead time window |
| LOW | 120-149 days | Approaching lead time window |
| WATCH | 150-179 days | Monitor weekly |
| OK | 180-209 days | Healthy |
| OVERSTOCK | >= 210 days | Aging risk — stop replenishment |

### Top 10 SKUs (higher minimum)

| Status | Days of Cover | Meaning |
|---|---|---|
| REORDER_NOW | < 180 days | Below 6-month target |
| LOW | 180-209 days | At minimum target |
| OK | >= 210 days | Healthy |

---

## 6. PO Recommendation

  target_days        = 210 for Top 10 SKUs, 195 for regular (mid of OK range)
  recommended_po_qty = ceil((target_days - days_of_cover) x daily_velocity)

Only include recommended_po_qty for REORDER_NOW and LOW SKUs.

---

## 7. Edge Cases

| Scenario | Handling |
|---|---|
| No BSR in Keepa | Use category median band (3k-20k) |
| WH stock = 0 | status = REORDER_NOW, days_of_cover = 0, still calculate velocity |
| No category match | Use Default benchmarks |
| daily_velocity rounds to 0 | Set to 0.1 to avoid divide-by-zero |
| No 7-day-ago BSR snapshot | Set bsr_trend_adj = 1.0 (neutral) |

---

## 8. Output JSON Schema

{
  "run_time": "<ISO 8601>",
  "summary": {
    "total": 0,
    "reorder_now": 0,
    "low": 0,
    "watch": 0,
    "overstock": 0,
    "ok": 0
  },
  "reorder_now": [
    {
      "sku": "<item name from Monday>",
      "asin": "<ASIN>",
      "brand": "<brand>",
      "category": "<category>",
      "wh_stock": 0,
      "bsr": 0,
      "daily_velocity": 0.0,
      "days_of_cover": 0,
      "recommended_po_qty": 0,
      "is_top_10": false,
      "status": "REORDER_NOW"
    }
  ],
  "low": [],
  "watch": [],
  "overstock": [],
  "ok_count": 47
}

ok_count is a number only. Do not return the full list of OK SKUs to save tokens.

---

## 9. Known Limitations

- Velocity is estimated from Keepa BSR, not actual units sold. Treat as directional, not exact.
- Channel split factor (0.7) is a fixed assumption.
- BSR benchmarks are category estimates, not Lifepro-specific actuals.
- Pending POs already in transit are not accounted for.
- Power BI actual sales data integration is a future P3+ enhancement.

---

## 10. Dashboard

Live at: https://lifepro-inventory.vercel.app
