// One-shot PM assignment sync. Reads Monday MCP dumps + writes Supabase.
// Args: <supabase_url> <supabase_key> <file1> [file2 ...]
import { readFileSync } from 'node:fs';

const [, , SUPABASE_URL, SUPABASE_KEY, ...FILES] = process.argv;
if (!SUPABASE_URL || !SUPABASE_KEY || !FILES.length) {
  console.error('Usage: node pm_sync_oneshot.mjs <url> <key> <file1> [file2 ...]');
  process.exit(1);
}

const pmSlug = (name) => {
  if (!name) return '';
  return name.trim().toLowerCase().split(/[\s,]+/)[0].replace(/[^a-z0-9]/g, '');
};

const rows = [];
const seen = new Set();

for (const f of FILES) {
  const data = JSON.parse(readFileSync(f, 'utf8'));
  const items = data.items || [];
  for (const it of items) {
    // Handle both shapes: array of {id, text, value} OR flat {id: value}
    const cv = it.column_values;
    const get = (k) => {
      if (Array.isArray(cv)) return (cv.find(c => c.id === k)?.text || '').trim();
      if (cv && typeof cv === 'object') return ((cv[k] ?? '') + '').trim();
      return '';
    };
    const asin = get('text_mknhd0s7');
    const pmText = get('multiple_person_mknhjhps');
    const brand = get('color_mktjf611') || null;
    if (!/^B[0-9A-Z]{9}$/.test(asin)) continue;
    if (!pmText) continue;
    const pmName = pmText.split(',')[0].trim();
    const slug = pmSlug(pmName);
    if (!slug) continue;
    const key = `${slug}|${asin}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      pm_slug: slug,
      pm_name: pmName,
      asin,
      sku: it.name,
      brand,
    });
  }
  console.log(`[parse] ${f}: ${items.length} items, running total ${rows.length} rows`);
}

console.log(`[summary] ${rows.length} unique (pm, asin) pairs`);
const bySlug = rows.reduce((a, r) => ((a[r.pm_slug] = (a[r.pm_slug] || 0) + 1), a), {});
console.log('[by PM]', bySlug);

// Wipe + insert
const del = await fetch(`${SUPABASE_URL}/rest/v1/pm_asin_assignments?asin=neq.`, {
  method: 'DELETE',
  headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` },
});
console.log(`[supabase] truncate ${del.status}`);

// Insert in chunks of 500
for (let i = 0; i < rows.length; i += 500) {
  const chunk = rows.slice(i, i + 500);
  const r = await fetch(`${SUPABASE_URL}/rest/v1/pm_asin_assignments`, {
    method: 'POST',
    headers: {
      apikey: SUPABASE_KEY,
      Authorization: `Bearer ${SUPABASE_KEY}`,
      'Content-Type': 'application/json',
      Prefer: 'return=minimal',
    },
    body: JSON.stringify(chunk),
  });
  console.log(`[supabase] inserted ${chunk.length} (${r.status}) ${r.ok ? 'OK' : await r.text()}`);
}
