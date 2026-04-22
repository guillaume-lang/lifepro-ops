-- ============================================================
-- DELIVERY RESULTS TABLE
-- One row per ASIN per zip code per scrape run
-- ============================================================

CREATE TABLE IF NOT EXISTS delivery_results (
  id              BIGSERIAL PRIMARY KEY,
  asin            TEXT NOT NULL,
  sku             TEXT,
  is_own_sku      BOOLEAN NOT NULL DEFAULT false,
  brand           TEXT,
  category        TEXT,
  deal_bucket     TEXT,
  zip             TEXT NOT NULL,
  city            TEXT,
  region          TEXT,          -- EC / MC / WC
  scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Delivery options
  prime_available BOOLEAN DEFAULT false,
  prime_days      INT,
  prime_date      TEXT,
  same_day        BOOLEAN DEFAULT false,
  tonight         BOOLEAN DEFAULT false,
  next_day        BOOLEAN DEFAULT false,
  one_hour        BOOLEAN DEFAULT false,
  standard_date   TEXT,
  standard_days   INT,

  -- BuyBox & availability
  buybox_type     TEXT,          -- 1P / FBM / 3P / null
  in_stock        BOOLEAN DEFAULT false,
  block_detected  BOOLEAN DEFAULT false
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_delivery_asin_date
  ON delivery_results (asin, scraped_at DESC);

CREATE INDEX IF NOT EXISTS idx_delivery_own_date
  ON delivery_results (is_own_sku, scraped_at DESC);

CREATE INDEX IF NOT EXISTS idx_delivery_category_date
  ON delivery_results (category, scraped_at DESC);

CREATE INDEX IF NOT EXISTS idx_delivery_zip_date
  ON delivery_results (zip, scraped_at DESC);


-- ============================================================
-- REVIEW SNAPSHOTS TABLE
-- One row per ASIN per scrape run (twice daily)
-- ============================================================

CREATE TABLE IF NOT EXISTS review_snapshots (
  id            BIGSERIAL PRIMARY KEY,
  asin          TEXT NOT NULL,
  sku           TEXT,
  brand         TEXT,
  category      TEXT,
  scraped_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Current values
  review_count  INT,
  star_rating   NUMERIC(2,1),

  -- Change detection
  prev_count    INT,
  count_delta   INT,
  delta_pct     NUMERIC(6,2),
  unmerge_flag  BOOLEAN DEFAULT false,
  alert_sent    BOOLEAN DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_reviews_asin_date
  ON review_snapshots (asin, scraped_at DESC);

CREATE INDEX IF NOT EXISTS idx_reviews_unmerge
  ON review_snapshots (unmerge_flag, scraped_at DESC)
  WHERE unmerge_flag = true;


-- ============================================================
-- COMPETITOR DELIVERY COMPARISON VIEW
-- Head-to-head: our ASIN vs competitors in same category + zip
-- Used by chatbot for "why are sales down" competitor context
-- ============================================================

CREATE OR REPLACE VIEW competitor_delivery_comparison AS
WITH latest_own AS (
  SELECT DISTINCT ON (asin, zip)
    asin, sku, category, deal_bucket, zip, region,
    prime_available, prime_days, same_day, tonight, next_day, one_hour,
    buybox_type, in_stock, scraped_at
  FROM delivery_results
  WHERE is_own_sku = true
  ORDER BY asin, zip, scraped_at DESC
),
latest_comp AS (
  SELECT DISTINCT ON (asin, zip)
    asin AS comp_asin, category, zip,
    prime_available AS comp_prime, prime_days AS comp_prime_days,
    same_day AS comp_same_day, tonight AS comp_tonight,
    next_day AS comp_next_day, in_stock AS comp_in_stock,
    scraped_at
  FROM delivery_results
  WHERE is_own_sku = false
  ORDER BY asin, zip, scraped_at DESC
)
SELECT
  o.sku,
  o.asin AS own_asin,
  o.category,
  o.deal_bucket,
  o.zip,
  o.region,
  o.prime_available AS our_prime,
  o.prime_days      AS our_prime_days,
  o.same_day        AS our_same_day,
  o.tonight         AS our_tonight,
  o.next_day        AS our_next_day,
  o.buybox_type,
  o.in_stock        AS our_in_stock,
  c.comp_asin,
  c.comp_prime,
  c.comp_prime_days,
  c.comp_same_day,
  c.comp_tonight,
  c.comp_next_day,
  c.comp_in_stock,
  -- Positive = competitor is faster, negative = we are faster
  (c.comp_prime_days - o.prime_days) AS prime_day_diff,
  -- Flag when competitor has same-day but we don't
  (c.comp_same_day AND NOT o.same_day) AS competitor_same_day_advantage
FROM latest_own o
JOIN latest_comp c
  ON o.category = c.category
  AND o.zip = c.zip;


-- ============================================================
-- DELIVERY SUMMARY VIEW
-- Per-SKU aggregated delivery health across all zip codes
-- Used in the morning digest and command hub dashboard
-- ============================================================

CREATE OR REPLACE VIEW delivery_health_summary AS
WITH latest AS (
  SELECT DISTINCT ON (asin, zip)
    asin, sku, brand, category, deal_bucket, zip, region,
    prime_available, prime_days, same_day, buybox_type,
    in_stock, scraped_at
  FROM delivery_results
  WHERE is_own_sku = true
  ORDER BY asin, zip, scraped_at DESC
)
SELECT
  asin,
  sku,
  brand,
  category,
  deal_bucket,
  COUNT(*) AS zips_checked,
  SUM(CASE WHEN prime_available THEN 1 ELSE 0 END) AS zips_with_prime,
  ROUND(AVG(prime_days)::NUMERIC, 1) AS avg_prime_days,
  SUM(CASE WHEN same_day THEN 1 ELSE 0 END) AS zips_with_same_day,
  SUM(CASE WHEN in_stock THEN 1 ELSE 0 END) AS zips_in_stock,
  MAX(scraped_at) AS last_scraped,
  -- Flag if prime delivery is unavailable in any EC/MC/WC region
  BOOL_OR(NOT prime_available AND region IN ('EC','MC','WC')) AS prime_gap_detected,
  -- Flag if delivery is prolonged (>3 days prime) in majority of zips
  BOOL_OR(prime_days > 3) AS prolonged_delivery_detected
FROM latest
GROUP BY asin, sku, brand, category, deal_bucket;


-- ============================================================
-- ASIN LIST TABLE
-- Synced by Claude via Monday.com MCP — no API token needed.
-- Claude reads from Monday board 8574487078 and upserts here.
-- Scrapers read from this table, never call Monday directly.
-- ============================================================

CREATE TABLE IF NOT EXISTS asin_list (
  id              BIGSERIAL PRIMARY KEY,
  sku             TEXT NOT NULL UNIQUE,
  asin            TEXT NOT NULL,
  brand           TEXT,
  category        TEXT,
  deal_bucket     TEXT,
  review_count    INT,
  star_rating     NUMERIC(2,1),
  competitor_asins TEXT[],          -- array of competitor ASIN strings
  monday_url      TEXT,
  active          BOOLEAN NOT NULL DEFAULT true,
  synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_asin_list_sku ON asin_list (sku);
CREATE INDEX IF NOT EXISTS idx_asin_list_asin ON asin_list (asin);
CREATE INDEX IF NOT EXISTS idx_asin_list_active ON asin_list (active) WHERE active = true;
CREATE INDEX IF NOT EXISTS idx_asin_list_brand ON asin_list (brand);


-- ============================================================
-- PM ↔ ASIN ASSIGNMENTS
-- Which PM owns which ASIN. Synced nightly from Monday.com.
-- Used by the PM KPI tracker to scope every metric to one PM's portfolio.
-- ============================================================

CREATE TABLE IF NOT EXISTS pm_asin_assignments (
  pm_slug       TEXT NOT NULL,
  pm_name       TEXT,
  asin          TEXT NOT NULL,
  sku           TEXT,
  brand         TEXT,
  synced_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (pm_slug, asin)
);

CREATE INDEX IF NOT EXISTS idx_pm_assign_asin ON pm_asin_assignments (asin);
CREATE INDEX IF NOT EXISTS idx_pm_assign_slug ON pm_asin_assignments (pm_slug);


-- ============================================================
-- PM KPI WEEKLY UPLOADS
-- One row per PM per ISO-week. Populated by Power BI CSV upload
-- from the PM KPI tracker UI. Holds the metrics that aren't yet
-- auto-scraped (margin, ACOS/TACOS, YoY growth, market share, promo rev).
-- ============================================================

CREATE TABLE IF NOT EXISTS pm_kpi_weekly (
  pm_slug                  TEXT NOT NULL,
  week_start               DATE NOT NULL,
  margin_pct               NUMERIC(5,2),
  tacos_pct                NUMERIC(5,2),
  acos_pct                 NUMERIC(5,2),
  yoy_growth_pct           NUMERIC(6,2),
  market_share_delta_pct   NUMERIC(6,2),
  promo_revenue_actual     NUMERIC(12,2),
  promo_revenue_target     NUMERIC(12,2),
  source_file              TEXT,
  uploaded_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (pm_slug, week_start)
);

CREATE INDEX IF NOT EXISTS idx_pm_kpi_weekly_slug_date
  ON pm_kpi_weekly (pm_slug, week_start DESC);
