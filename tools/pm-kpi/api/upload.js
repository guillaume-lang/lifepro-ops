export const config = { maxDuration: 30 };

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_KEY;

const COLUMN_MAP = {
  pm: 'pm_slug',
  pm_slug: 'pm_slug',
  week: 'week_start',
  week_start: 'week_start',
  margin: 'margin_pct',
  margin_pct: 'margin_pct',
  tacos: 'tacos_pct',
  tacos_pct: 'tacos_pct',
  acos: 'acos_pct',
  acos_pct: 'acos_pct',
  yoy: 'yoy_growth_pct',
  yoy_growth: 'yoy_growth_pct',
  yoy_growth_pct: 'yoy_growth_pct',
  market_share: 'market_share_delta_pct',
  market_share_delta: 'market_share_delta_pct',
  market_share_delta_pct: 'market_share_delta_pct',
  promo_actual: 'promo_revenue_actual',
  promo_revenue_actual: 'promo_revenue_actual',
  promo_target: 'promo_revenue_target',
  promo_revenue_target: 'promo_revenue_target',
};

const NUMERIC_FIELDS = new Set([
  'margin_pct', 'tacos_pct', 'acos_pct', 'yoy_growth_pct',
  'market_share_delta_pct', 'promo_revenue_actual', 'promo_revenue_target',
]);

function normHeader(h) {
  return h.trim().toLowerCase().replace(/[%()]/g, '').replace(/\s+/g, '_').replace(/_+/g, '_');
}

function parseCSV(text) {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter(l => l.trim());
  if (lines.length < 2) return { headers: [], rows: [] };
  const headers = splitRow(lines[0]).map(normHeader);
  const rows = lines.slice(1).map(l => {
    const cells = splitRow(l);
    const obj = {};
    headers.forEach((h, i) => { obj[h] = (cells[i] ?? '').trim(); });
    return obj;
  });
  return { headers, rows };
}

function splitRow(line) {
  const out = [];
  let cur = '';
  let inQ = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') {
      if (inQ && line[i + 1] === '"') { cur += '"'; i++; }
      else inQ = !inQ;
    } else if (c === ',' && !inQ) {
      out.push(cur); cur = '';
    } else cur += c;
  }
  out.push(cur);
  return out;
}

function parseNumber(v) {
  if (v == null || v === '') return null;
  const n = parseFloat(String(v).replace(/[,%$]/g, ''));
  return Number.isFinite(n) ? n : null;
}

function parseWeekStart(v) {
  if (!v) return null;
  const d = new Date(v);
  if (isNaN(d)) return null;
  const day = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - day);
  return d.toISOString().slice(0, 10);
}

async function readBody(req) {
  if (typeof req.body === 'string') return req.body;
  if (req.body && typeof req.body === 'object' && 'csv' in req.body) return req.body.csv;
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
}

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).end();
  if (!SUPABASE_URL || !SUPABASE_KEY) {
    return res.status(500).json({ error: 'SUPABASE_URL / SUPABASE_SERVICE_KEY env vars not set.' });
  }

  const raw = await readBody(req);
  const filename = req.query.filename || 'upload.csv';

  let parsed;
  try {
    parsed = typeof raw === 'string' ? parseCSV(raw) : parseCSV(raw?.csv || '');
  } catch (e) {
    return res.status(400).json({ error: `CSV parse failed: ${e.message}` });
  }
  if (!parsed.rows.length) return res.status(400).json({ error: 'CSV had no data rows' });

  const rows = [];
  const errors = [];
  parsed.rows.forEach((r, idx) => {
    const out = { source_file: filename };
    for (const [k, v] of Object.entries(r)) {
      const field = COLUMN_MAP[k];
      if (!field) continue;
      out[field] = NUMERIC_FIELDS.has(field) ? parseNumber(v)
                  : field === 'week_start' ? parseWeekStart(v)
                  : field === 'pm_slug' ? String(v).toLowerCase().trim()
                  : v;
    }
    if (!out.pm_slug || !out.week_start) {
      errors.push({ row: idx + 2, reason: 'missing pm or week_start', raw: r });
      return;
    }
    rows.push(out);
  });

  if (!rows.length) {
    return res.status(400).json({ error: 'No valid rows after parsing', errors });
  }

  const upsert = await fetch(
    `${SUPABASE_URL}/rest/v1/pm_kpi_weekly?on_conflict=pm_slug,week_start`,
    {
      method: 'POST',
      headers: {
        apikey: SUPABASE_KEY,
        Authorization: `Bearer ${SUPABASE_KEY}`,
        'Content-Type': 'application/json',
        Prefer: 'resolution=merge-duplicates,return=representation',
      },
      body: JSON.stringify(rows),
    }
  );

  if (!upsert.ok) {
    return res.status(502).json({ error: `Supabase upsert failed: ${await upsert.text()}` });
  }

  const saved = await upsert.json();
  return res.status(200).json({ saved: saved.length, errors, rows: saved });
}
