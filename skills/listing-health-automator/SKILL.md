---
name: listing-health-automator
description: >
  Automates the daily Amazon listing health check for Lifepro's 467 SKUs across all brands
  (Lifepro, Oaktiv, Petcove, Joyberri, Culvani, Sunello). Reads live data from Monday.com
  (PDP Status, stock levels, suppression types, PM assignments) and the Account Management
  board (open compliance cases), parses recent PM and ad rep update threads, then generates
  a structured morning digest showing ONLY issues that need action — grouped by severity and
  routed to the right team. Also feeds signal data to the Ads team for campaign pause/resume
  decisions. Use this skill whenever someone asks to: run the daily listing check, generate
  the morning digest, check listing health, see what's suppressed or OOS, find issues that
  need PM or compliance action today, or asks "what do I need to fix this morning."
---

# Listing Health Automator

Replaces the manual daily check your PMs perform on 467 SKUs. Reads Monday boards, classifies
issues by severity and type, routes to the right owner, and delivers a digest that shows only
what needs action.

## Board sources

| Board | ID | What it provides |
|---|---|---|
| Amazon USA Products | 8574487078 | PDP Status (25+ types), stock levels, PM/ADS rep, brand, ASIN, category |
| Amazon Account Management — New | 18397995018 | Open compliance cases: type, status, assignee, deadline |
| Amazon Account Management (old) | 5530399425 | Historical cases — some PM updates still link here |

## Step 1 — Read the Products board

Pull active SKUs (Status = Active, Top 10, NewLaunch, Relaunched) with these columns:
- `color_mknh8qz4` — PDP Status
- `status` — SKU lifecycle status  
- `color_mktjf611` — Brand
- `text_mknhd0s7` — ASIN
- `multiple_person_mknhjhps` — PM assigned
- `multiple_person_mkvmxdxe` — ADS Rep assigned
- `numeric_mkyrmea5` — FBA stock
- `numeric_mkyrk4we` — Warehouse stock
- `numeric_mkpfqdjc` — Total inventory
- `color_mknx45rf` — Stock level status
- `text_mkxp62c` — Category
- `link_mknhw0x1` — Amazon link

Filter to non-Live PDP Status only — Live SKUs need no action.

## Step 2 — Read the Account Management board

Pull open cases (Status != Done) with:
- `color_mkxmq0aj` — Issue type
- `status` — Case status (Urgent, Working on it, Stuck, Pending, Response awaiting)
- `person` — Assigned to
- `date_1` — Deadline
- `long_text` — Task description

Cross-reference case names/descriptions against ASIN or SKU names from Step 1 to link
open cases to specific SKUs.

## Step 3 — Parse recent PM update threads

Read updates from board 8574487078 from the last 24 hours. Use Claude to extract:
- BuyBox status (1P / FBM / 3P)
- Delivery time by region (EC / MC / WC — Good / Prolonged / OOS)
- Sales figures (3P: N, 1P: N)
- PPC eligibility
- Keyword rank and main KW
- Any ad rep actions (paused, budget changed, bid adjusted)

Pattern to parse:
```
PDP Check: [status or issue]
PPC Check: [Eligible / Ineligible + reason]
Coupon/Deal: [active deal or No Deal]
Delivery Time: [EC/MC/WC status]
Sales: 3P: N, 1P: N
BuyBox Offer: [1P / FBM / 3P]
Main Keyword Rank: [rank] (Main KW: [keyword])
```

## Step 4 — Classify and prioritize issues

### Priority tiers

**P0 — CRITICAL (same-day action, alert immediately)**
- Suppressed-Andon cord (listing removed by Amazon — needs POA)
- Suppressed-Product Safety Violation
- Top 10 SKU with any suppression
- Top 10 SKU OOS
- Active SKU: FBA = 0 AND Warehouse = 0 AND no inbound PO

**P1 — HIGH (action needed today)**
- Suppressed-Pricing Issue (MAP violation or price error)
- Suppressed-Reason Unknown (investigate immediately)
- ADS suppressed (PPC ineligible — ads wasted)
- DF Suppressed - High Cost / Missing Cost
- OOS with stock in warehouse (FBA shipment needed)
- Open Urgent case on Account Mgmt board

**P2 — MEDIUM (action needed this week)**
- DF Disabled (delivery fulfillment off)
- Live on Few Zip codes (regional availability issue)
- Low stock (< 2 weeks of cover estimated)
- Open Stuck case on Account Mgmt board

**P3 — WATCH (flag, no immediate action)**
- Prolonged delivery time in one region
- BuyBox lost to FBM (not OOS)
- Keyword rank drop noted in PM update
- Open Pending case

## Step 5 — Route signals

| Signal | Route to |
|---|---|
| Any suppression | PM assigned + Compliance team |
| OOS (FBA = 0) | ADS Rep (pause campaign) + Logistics |
| OOS with stock available | PM + Logistics (ship to FBA) |
| Prolonged delivery by region | Logistics |
| BuyBox lost | ADS Rep (review bid/campaign) + PM |
| PPC ineligible | Compliance (resolve eligibility) + ADS Rep |
| Pricing issue | Compliance + Pricing team |
| Andon cord | Compliance (POA required) — escalate to Guillaume |

## Step 6 — Generate the digest

### Format

```
LISTING HEALTH DIGEST — [DATE] [TIME]

SUMMARY
Active SKUs checked: [N]
Issues found: [N] ([P0: N] critical · [P1: N] high · [P2: N] medium)
Healthy (no action): [N]

────────────────────────────────
P0 CRITICAL — ACT NOW
────────────────────────────────
[Brand] [SKU] — [ASIN]
Issue: [suppression type or status]
PM: [name] | ADS Rep: [name]
Stock: FBA [N] / WH [N] / Total [N]
Open case: [link if exists]
Action: [specific recommended action]

────────────────────────────────
P1 HIGH — TODAY
────────────────────────────────
[same format]

────────────────────────────────
P2 MEDIUM — THIS WEEK  
────────────────────────────────
[same format, condensed]

────────────────────────────────
ADS TEAM SIGNALS
────────────────────────────────
PAUSE IMMEDIATELY:
• [SKU] — [reason: OOS / suppressed]

REVIEW BIDS:
• [SKU] — BuyBox lost to [FBM/3P]

RESUME WHEN CLEARED:
• [SKU] — [what needs to resolve first]

────────────────────────────────
LOGISTICS SIGNALS
────────────────────────────────
SHIP TO FBA URGENTLY:
• [SKU] — [WH stock N] available, FBA at 0

LOW STOCK WATCH:
• [SKU] — est. [N] weeks of cover
```

### Digest rules
- Never list healthy SKUs — noise kills adoption
- Always include the Amazon link and Monday item link
- Always include PM name and ADS Rep name
- For P0 issues: include specific recommended action, not just the problem
- Group by priority tier, then by brand within each tier
- ADS signals section always included even if empty — so Jose knows he has visibility

## Step 7 — Write to Supabase

Log each check run to `listing_health_runs` table:
```json
{
  "run_date": "ISO timestamp",
  "total_checked": N,
  "p0_count": N,
  "p1_count": N, 
  "p2_count": N,
  "issues": [
    {
      "sku": "LP-FLXSTPLS-BLU",
      "asin": "B08HPPBPLT",
      "brand": "Lifepro",
      "category": "Bikes",
      "pdp_status": "Suppressed-Andon cord",
      "priority": "P0",
      "pm": "Ahmad Naseem",
      "ads_rep": "Jose Johan Estevez",
      "fba_stock": 2,
      "wh_stock": 707,
      "total_stock": 1093,
      "open_case_url": "https://...",
      "action_required": "POA submission needed",
      "ads_signal": "PAUSE"
    }
  ]
}
```

This enables trend tracking (recurring suppressions), P2/SKU anomaly cross-reference,
and the command hub dashboard.

## Power Automate flow (scheduled)

Trigger: Daily at 7:00 AM EST (Monday–Friday)

Steps:
1. HTTP GET → Monday API → Products board (active SKUs, issue columns only)
2. HTTP GET → Monday API → Account Mgmt board (open cases)
3. HTTP GET → Monday API → Recent updates (last 24h)
4. HTTP POST → Claude API → Run this skill with all three payloads
5. Parse Claude response → extract digest + Supabase payload
6. HTTP POST → Supabase → Log check run
7. POST → Microsoft Teams → #listing-health channel (digest message)
8. [Conditional] If P0 count > 0 → send direct Teams message to Guillaume

## References

See `references/suppression-playbook.md` for recommended action per suppression type.
See `references/ads-routing.md` for detailed ADS team signal protocol.
