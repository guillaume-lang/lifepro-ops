export const config = { maxDuration: 30 };

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_KEY;

const TARGETS = {
  margin_pct:               { op: '>=', value: 25, unit: '%' },
  tacos_pct:                { op: '<=', value: 7,  unit: '%' },
  acos_pct:                 { op: '<=', value: 10, unit: '%' },
  yoy_growth_pct:           { op: '>=', value: 15, unit: '%' },
  market_share_delta_pct:   { op: '>=', value: 5,  unit: '%' },
  promo_revenue_pct:        { op: '>=', value: 90, unit: '%' },
  tasks_on_time_pct:        { op: '>=', value: 100, unit: '%' },
  listing_compliance_pct:   { op: '>=', value: 100, unit: '%' },
  response_under_24h_pct:   { op: '>=', value: 100, unit: '%' },
  resolution_under_48h_pct: { op: '>=', value: 100, unit: '%' },
  avg_rating:               { op: '>=', value: 4.4, unit: '★' },
  in_stock_pct:             { op: '>=', value: 95, unit: '%' },
  overstock_pct:            { op: '<=', value: 5,  unit: '%' },
  competitor_review_done:   { op: '==', value: true, unit: '' },
};

async function sb(path) {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` },
  });
  if (!r.ok) throw new Error(`Supabase ${r.status}: ${await r.text()}`);
  return r.json();
}

async function sbSafe(path) {
  try { return await sb(path); } catch { return []; }
}

function isoWeekStart(d = new Date()) {
  const x = new Date(d);
  const day = (x.getUTCDay() + 6) % 7;
  x.setUTCDate(x.getUTCDate() - day);
  return x.toISOString().slice(0, 10);
}

function bucket(metric, value) {
  if (value == null) return 'unknown';
  const t = TARGETS[metric];
  if (!t) return 'unknown';
  const delta = t.op === '>=' ? value - t.value : t.value - value;
  if (t.op === '==') return value === t.value ? 'ok' : 'red';
  if (delta >= 0) return 'ok';
  const pct = Math.abs(delta) / (t.value || 1);
  return pct <= 0.1 ? 'amber' : 'red';
}

export default async function handler(req, res) {
  if (req.method !== 'GET') return res.status(405).end();
  if (!SUPABASE_URL || !SUPABASE_KEY) {
    return res.status(500).json({ error: 'SUPABASE_URL / SUPABASE_SERVICE_KEY env vars not set.' });
  }

  const pm = (req.query.pm || '').toLowerCase().trim();
  if (!pm) return res.status(400).json({ error: 'pm query param required' });

  try {
    const [pms, assignments, weekly, reviews, deliveries] = await Promise.all([
      sbSafe(`pm_asin_assignments?select=pm_slug,pm_name&limit=5000`),
      sbSafe(`pm_asin_assignments?pm_slug=eq.${pm}&select=asin,sku,brand`),
      sbSafe(`pm_kpi_weekly?pm_slug=eq.${pm}&order=week_start.desc&limit=8`),
      sbSafe(`review_snapshots?select=asin,star_rating,scraped_at&order=scraped_at.desc&limit=5000`),
      sbSafe(`delivery_health_summary?select=asin,zips_checked,zips_in_stock`),
    ]);

    const pmSet = new Set(pms.map(r => r.pm_slug));
    const pmList = [...pmSet].sort().map(slug => ({
      slug,
      name: (pms.find(r => r.pm_slug === slug)?.pm_name) || slug,
    }));

    const asins = new Set(assignments.map(a => a.asin));

    // KPI 11 — rating (weighted mean over latest per-ASIN snapshot)
    const latestByAsin = new Map();
    for (const r of reviews) {
      if (!asins.has(r.asin)) continue;
      if (!latestByAsin.has(r.asin)) latestByAsin.set(r.asin, r);
    }
    const ratings = [...latestByAsin.values()].map(r => r.star_rating).filter(v => v != null);
    const avg_rating = ratings.length ? +(ratings.reduce((a, b) => a + b, 0) / ratings.length).toFixed(2) : null;

    // KPI 12 — in-stock % across delivery checks
    const del = deliveries.filter(d => asins.has(d.asin));
    const totalChecks = del.reduce((a, d) => a + (d.zips_checked || 0), 0);
    const inStockChecks = del.reduce((a, d) => a + (d.zips_in_stock || 0), 0);
    const in_stock_pct = totalChecks ? +((inStockChecks / totalChecks) * 100).toFixed(1) : null;

    // KPIs 1–6 from latest weekly upload
    const latest = weekly[0] || {};
    const promo_revenue_pct =
      latest.promo_revenue_target && latest.promo_revenue_actual != null
        ? +((latest.promo_revenue_actual / latest.promo_revenue_target) * 100).toFixed(1)
        : null;

    const sparkFor = (field) => weekly.slice().reverse().map(w => w[field] ?? null);

    const metrics = {
      margin_pct:             { value: latest.margin_pct ?? null,             spark: sparkFor('margin_pct') },
      tacos_pct:              { value: latest.tacos_pct ?? null,              spark: sparkFor('tacos_pct') },
      acos_pct:               { value: latest.acos_pct ?? null,               spark: sparkFor('acos_pct') },
      yoy_growth_pct:         { value: latest.yoy_growth_pct ?? null,         spark: sparkFor('yoy_growth_pct') },
      market_share_delta_pct: { value: latest.market_share_delta_pct ?? null, spark: sparkFor('market_share_delta_pct') },
      promo_revenue_pct:      { value: promo_revenue_pct,                     spark: [] },
      tasks_on_time_pct:        { value: null, spark: [], note: 'Awaiting Monday.com sync' },
      listing_compliance_pct:   { value: null, spark: [], note: 'Awaiting listing-health Supabase write' },
      response_under_24h_pct:   { value: null, spark: [], note: 'Awaiting Monday.com sync' },
      resolution_under_48h_pct: { value: null, spark: [], note: 'Awaiting Monday.com sync' },
      avg_rating:             { value: avg_rating, spark: [], asins: latestByAsin.size },
      in_stock_pct:           { value: in_stock_pct, spark: [], zips: totalChecks },
      overstock_pct:          { value: null, spark: [], note: 'Awaiting inventory-forecast Supabase write' },
      competitor_review_done: { value: null, spark: [], note: 'Awaiting Monday.com sync' },
    };

    const payload = {};
    for (const [k, v] of Object.entries(metrics)) {
      payload[k] = { ...v, target: TARGETS[k], status: bucket(k, v.value) };
    }

    const summary = { ok: 0, amber: 0, red: 0, unknown: 0 };
    for (const m of Object.values(payload)) summary[m.status]++;

    return res.status(200).json({
      pm,
      pm_list: pmList,
      week_start: isoWeekStart(),
      asin_count: asins.size,
      summary,
      metrics: payload,
      generated_at: new Date().toISOString(),
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
