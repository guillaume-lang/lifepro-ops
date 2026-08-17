# Datarova User API (v0) — Reference

Source: `https://api.datarova.com/openapi/user-api-v0.json` (OpenAPI 3.2.0), extracted 2026-08-17.
Interactive docs: https://developer.datarova.com/

## Auth

- Base URL: `https://api.datarova.com/v0`
- Header: `Authorization: Bearer <api_key>`
- Key management: app.datarova.com → Settings → Connections (read-only key, rotatable)

## Pagination

List endpoints return `data.page.next_cursor` + `data.page.has_more`. Pass the cursor
back unchanged as the `cursor` query param.

## Rate limits & quota

Fixed 60-second burst window; headers `X-RateLimit-Limit/Remaining/Reset`, plus
`X-Quota-Limit/Remaining` for the monthly cycle. On 429 the response carries `Retry-After`.

| Endpoint | Limit |
|---|---|
| `GET /v0/keywords` | **10 / min** |
| `GET /v0/projects/{id}/ranks-by-asin` | **10 / min** |
| everything else | 60 / min |

Errors: 400 bad input/dates/cursor · 401 bad key · 402 no plan or quota exceeded ·
403 `user-api` feature not enabled · 404 not found · 429 throttled · 500/503 server.

## Endpoints

### GET /v0/usage
Monthly usage/quota: `period_start`, `period_end`, `request_limit`, `requests_remaining`,
`request_count`, `error_count`, `endpoint_counts{...}`, `updated_at`.

### GET /v0/projects  (`limit` ≤200, `cursor`)
Your projects: `project_name`, `should_track_variations`, `keywords_count`, `asins_count`,
`labels[]`, `marketplaces[]{project_id, marketplace, created_at, keywords_count, asins_count}`.
Note: `project_id` lives on the per-marketplace entries.

### GET /v0/projects/{projectId}
One project: `project_id`, `project_name`, `created_at`, `marketplace`,
`should_track_variations`, `keywords_count`, `asins_count`, `labels[]`.

### GET /v0/projects/{projectId}/asins  (`limit` ≤200, `cursor`)
Every ASIN tracked in the project (ours + competitors):
`asin`, `title`, `brand`, `image_url`, `price`, `reviews`, `rating`, `primary_asin`,
`keywords_in_top_10`, `choice_badges`, `visibility`, `latest_bsr`, `top_level_category`, `labels[]`.

### GET /v0/projects/{projectId}/keywords  (`limit` ≤200, `cursor`)
Every tracked keyword with current competitive state:
`keyword`, `tracked_from`, `keyword_clicks_l4w`, `keyword_sales_l4w`, `keyword_conversion_l4w`,
`top_asin`, `top_asin_sales_l4w`, `top_asin_clicks_l4w`, `top_asin_conversion_l4w`,
`trend_l4w`, `sales_distribution`, `dstr`, `organic_rank`, `sponsored_rank`,
`asins_in_top_10`, `labels[]`.

### GET /v0/projects/{projectId}/ranks-by-asin
Params: `asin` (required), `start_date`, `end_date` (required, `YYYY-MM-DD`),
`keywords` (optional filter), `limit` (default 20 keywords/page), `cursor`.
Full daily rank history for one ASIN:
`ranks[]{keyword, daily_ranks[]{date, organic_rank, sponsored_rank, choice_badge, ranked_asin}}`.
`ranked_asin` = which ASIN actually held the position (variation tracking).

### GET /v0/keywords  — market data (any keyword, not just tracked)
Params: `marketplace` (US/CA/MX/UK/DE/FR/IT/ES/AU), `keywords` (≤100, or ≤25 with
`include_asins=true`), `start_date` (required), `end_date`, `interval` (`weekly` ≤26
intervals / `monthly` ≤24), `include_asins`.
Returns real Amazon shopper behavior per keyword per interval:
`records[]{date, clicks, sales, conversion_rate, asin_1{asin, clicks, sales, conversion_rate},
asin_2{...}, asin_3{...}}` — asin_1..3 are the top-3 clicked ASINs for that keyword that period.

## How the sync uses this (scraper/datarova_sync.py)

| Board column | Source |
|---|---|
| Main KW Rank | `ranks-by-asin` → latest `organic_rank` for the item's Main KW |
| Rank Δ 7d / Δ 30d | same history, delta vs 7/30 days ago (positive = improved) |
| Rank Taken By | keywords where we dropped ≥3 spots → `top_asin`; plus Main KW market top-3 displacement |
| New KWs to Target | project keywords with high `keyword_sales_l4w` where our `organic_rank` is null or >20 |
| New ASINs Rising | `/keywords include_asins=true` → new entrants into the weekly top-3 on the Main KW |
| Category Trend | Main KW `clicks`: last 4 weeks vs prior 4 weeks, and month vs same month last year |
