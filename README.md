# Lifepro Ops

Amazon USA Operations Platform — automated listing health, delivery intelligence, review monitoring, and deal planning.

## Projects

| Tool | URL | Status |
|---|---|---|
| Listing Health Digest | https://lifepro-listing-health.vercel.app | Live |
| Deal PM Assistant | https://lifepro-deal-pm.vercel.app | Live |
| SKU Anomaly Detector | https://lifepro-sku-anomaly.vercel.app | Live |
| Inventory Forecaster | https://lifepro-inventory.vercel.app | Live |
| PM KPI Tracker | https://lifepro-pm-kpi.vercel.app | Live |
| PPC Decision Engine | Coming P4 | Planned |
| Category Intelligence | Coming P5 | Planned |
| Command Hub | Coming P7 | Planned |

## Repo Structure

```
tools/           # Frontend tools (deployed to Vercel)
  listing-health/  # Daily listing health digest
  deal-pm/         # Deal planning PM assistant
  sku-anomaly/     # SKU anomaly monitor — Keepa BSR/price/buybox signals
  inventory-forecast/  # Inventory + PO forecaster — WH stock coverage vs lead time
  pm-kpi/          # Per-PM KPI scorecard — 13 JD metrics + Power BI CSV upload

scraper/         # Playwright scrapers (run via GitHub Actions)
  scraper/         # Python package
    main.py        # Entry point — reads SCRAPER_MODE env var
    delivery_scraper.py  # Delivery times + competitor comparison
    review_scraper.py    # Review count monitor + unmerge detection
    monday_client.py     # Reads ASIN list from Monday.com
    supabase_client.py   # Writes all data to Supabase
    alerts.py            # Teams webhook notifications
  Dockerfile       # For manual Cloud Run deployment
  supabase_schema.sql  # Run once in Supabase SQL editor
  DEPLOY.md        # Cloud Run deployment guide (alternative)

skills/          # Claude skill files
  listing-health-automator/
    SKILL.md
    references/
  sku-anomaly-detector/
    SKILL.md
  inventory-forecaster/
    SKILL.md

.github/workflows/
  delivery-scraper.yml  # Runs daily at 6am EST
  review-monitor.yml    # Runs 4x daily
  pm-sync.yml           # Nightly — syncs PM ↔ ASIN assignments from Monday
```

## Setup

### Required GitHub Secrets
Go to repo Settings → Secrets → Actions and add:

| Secret | Where to find it |
|---|---|
| `MONDAY_API_TOKEN` | monday.com → Profile → Developers → API |
| `SUPABASE_URL` | Supabase project → Settings → API → Project URL |
| `SUPABASE_SERVICE_KEY` | Supabase project → Settings → API → service_role key |
| `TEAMS_WEBHOOK_URL` | Teams channel → Connectors → Incoming Webhook |
| `MONDAY_PM_COLUMN_ID` | *(optional)* override default PM column `multiple_person_mknhjhps` — only needed if you rename the PM column on board 8574487078 |

### Supabase Schema
Run `scraper/supabase_schema.sql` once in your Supabase SQL editor.
Creates: `delivery_results`, `review_snapshots`, `competitor_delivery_comparison` view, `delivery_health_summary` view.

### Vercel Deployment
Both frontend tools are deployed. To redeploy after changes, push to this repo.
Vercel projects are connected at `vercel.com/guillaume-5260s-projects`.
