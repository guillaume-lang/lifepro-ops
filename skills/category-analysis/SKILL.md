# Category Analysis Skill

## When to invoke
User says something like: "Run category analysis for [product]", "Analyse the [category] market", "Monthly analysis for [term]", or invokes `/category-analysis`.

---

## Step 0 — Get product + analyst info (if not provided)

Ask the user:
1. What product/category? (e.g. "massage gun", "infrared sauna blanket")
2. Who is running this analysis? (analyst name)
3. What period? (default: current month + year)

---

## Step 1 — Disambiguate the category

Many Amazon terms refer to multiple distinct markets. **Never proceed without confirming which one.**

Common ambiguous terms and what to ask:
| Input | Ask about |
|---|---|
| "sauna" | Infrared Sauna Blanket / Infrared Sauna Cabin / Barrel Sauna / Portable Steam Sauna |
| "massager" | Massage Gun / Foot Massager / Neck Massager / Handheld Massager |
| "bike" | Exercise Bike / Stationary Bike / Under-Desk Bike |
| "dumbbell" | Adjustable Dumbbell Set / Fixed Hex Dumbbells / Rubber Dumbbells |
| "light therapy" | Red Light Panel / LED Face Mask / Red Light Wand |
| "treadmill" | Standard Treadmill / Walking Pad / Under-Desk Treadmill |

If the input is unambiguous (e.g. "massage gun", "vibration plate"), confirm and proceed.

---

## Step 2 — DataDive (Automated via API)

Use `WebFetch` to search DataDive niches. The proxy is at `https://lifepro-datadive.vercel.app/api/datadive`.

**Search for matching niche:**
```
GET https://lifepro-datadive.vercel.app/api/datadive?p=%2Fniches%3FpageSize%3D100%26page%3D1
```
Search across pages 1–5 until a niche with a matching `heroKeyword` is found.

**Fetch niche details once found:**
```
GET https://lifepro-datadive.vercel.app/api/datadive?p=%2Fniches%2F{nicheId}%2Fkeywords
GET https://lifepro-datadive.vercel.app/api/datadive?p=%2Fniches%2F{nicheId}%2Fcompetitors
```

**Extract and record:**
- Total Search Volume (`statistics.totalSvOfKeywords`)
- Number of keywords (`statistics.numKeywords`)
- Visible keywords (`statistics.numVisibleKeywords`)
- Top 20 keywords by search volume
- Top 10 competitors by visibility
- Last research date

---

## Step 3 — Google Trends (Playwright — automated)

Navigate to the pre-built URL:
```
https://trends.google.com/trends/explore?q={KEYWORD}&geo=US&date=today%2012-m
```

1. Use `mcp__playwright__browser_navigate` to open the URL
2. Wait 3–4 seconds for charts to render (`mcp__playwright__browser_wait_for`)
3. Take a screenshot (`mcp__playwright__browser_take_screenshot`)
4. Use `mcp__playwright__browser_snapshot` or `mcp__playwright__browser_evaluate` to extract:
   - The trend line data points if visible in the DOM
   - Any percentage changes shown
5. Note: trend direction (up / down / flat), any visible YoY change %, seasonal spikes

---

## Step 4 — Product Opportunity Explorer (Claude in Chrome)

The PM must be logged into Vendor Central in their Chrome browser.

1. Use `mcp__Claude_in_Chrome__navigate` to open:
   ```
   https://vendorcentral.amazon.com
   ```
2. Navigate to Analytics → Product Opportunity Explorer (or Selling Coach → Opportunity Explorer)
3. Search for the confirmed category keyword
4. The **Opportunity Explorer Insights** extension (ID: `bahjpahpadcebglglpfbgomiigjlcgam`) should automatically add an insights panel
5. Use `mcp__Claude_in_Chrome__get_page_text` to extract:
   - ASP (today, 90 days ago, 180 days ago if shown)
   - Search volume figures
   - Click share for top products and brands
   - Product count / brand count / new product count
6. Take a screenshot with `mcp__Claude_in_Chrome__computer` action `screenshot`

**If not logged in:** Ask PM to log into Vendor Central first, then retry.

---

## Step 5 — Smart Scout (Claude in Chrome)

1. Check if Smart Scout is already open in Chrome: `mcp__Claude_in_Chrome__tabs_context_mcp`
2. If logged in: `mcp__Claude_in_Chrome__navigate` to `https://www.smartscout.com`
3. Search for the keyword and navigate to Brand Market Share view
4. Extract: top brands, market share %, market share change %, ad spend share %
5. Screenshot

**If not logged in:** Ask PM — "Please open Smart Scout and log in, I'll wait." Then retry.

---

## Step 6 — Datarova (Claude in Chrome)

1. Navigate to `https://www.datarova.com`
2. Go to the **Trends** tab
3. Search for the keyword
4. Set comparison to current year vs same period last year
5. Extract: Total Clicks (this year / last year), Total Sales (this year / last year), Conversion Rate (this year / last year)
6. Screenshot

**If not logged in:** Ask PM to log in first.

---

## Step 7 — Growth Opportunities (Claude in Chrome — Vendor Central)

1. Navigate to `https://vendorcentral.amazon.com` (should already be open from Step 4)
2. Go to Analytics → Growth Opportunities
3. Search for the keyword
4. Extract: Search Volume, Search Growth %, Units Sold (180d), Customer Clicks (180d), Average Price, Average Rating, Return Rate %, Search Conversion Rate
5. Screenshot

---

## Step 8 — Marketplace Product Guidance (Claude in Chrome — Vendor Central)

1. Still in Vendor Central
2. Navigate to Selling Coach → Marketplace Product Guidance
3. Find the keyword / category
4. Screenshot the demand and sales opportunity charts
5. Note: demand trend direction, any specific guidance or alerts

---

## Step 9 — Synthesise

With all collected data, produce the standard analysis JSON:

```json
{
  "category": "...",
  "period": "March 2026",
  "analyst": "...",
  "market_trend": "up|down|flat",
  "asp_trend": "up|down|flat",
  "competition_trend": "harder|easier|stable",
  "new_entrants": "high|medium|low",
  "confidence": "high|medium|low",
  "sections": {
    "market_performance": "2–3 sentences covering overall market size, growth trajectory, SV trends",
    "seasonal_patterns": "2–3 sentences on seasonality, peak periods, YoY timing shifts",
    "market_structure": "2–3 sentences on brand concentration, top players, new entrant activity",
    "product_search_dynamics": "2–3 sentences on keyword trends, ASP movements, search behaviour",
    "strategic_considerations": {
      "opportunities": ["..."],
      "challenges": ["..."],
      "risks": ["..."]
    }
  },
  "verdict": "One paragraph executive summary for the PM",
  "signal_alignment": "One sentence: do the data sources agree or conflict?"
}
```

Guidance:
- Use exact numbers from DataDive (API) — these are reliable
- Extract numbers from screenshots where visible — describe what you see
- Note confidence level: high if 5+ sources, medium if 3–4, low if <3
- Flag any sources that were unavailable

---

## Step 10 — Save to Supabase

POST the result to the category analysis tool:

```
POST https://category-analysis.vercel.app/api/analyses?action=save
Content-Type: application/json

{
  "category": "...",
  "period": "...",
  "analyst": "...",
  "raw_inputs": { ... all extracted data ... },
  "analysis": { ... the JSON from step 9 ... },
  "market_trend": "...",
  "asp_trend": "...",
  "competition_trend": "...",
  "new_entrants": "..."
}
```

The analysis will then be visible at https://category-analysis.vercel.app

---

## Disambiguation reference — Lifepro product portfolio

Common Lifepro categories and their correct search terms:

| Category | Search term for DataDive/Trends |
|---|---|
| Massage Guns | `massage gun` |
| Vibration Plates | `vibration plate` |
| Infrared Sauna Blankets | `infrared sauna blanket` |
| Infrared Sauna Cabins | `infrared sauna` |
| Foot Massagers | `foot massager` |
| Red Light Therapy Panels | `red light therapy` |
| Walking Pads / Treadmills | `walking pad` or `treadmill` |
| Adjustable Dumbbells | `adjustable dumbbell` |
| Exercise Bikes | `exercise bike` |
| Self-Cleaning Litter Boxes | `self cleaning litter box` |
| Trampolines / Rebounders | `mini trampoline` |
