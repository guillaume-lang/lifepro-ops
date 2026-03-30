export const config = { maxDuration: 15 };

export default async function handler(req, res) {
  const SUPABASE_URL = process.env.SUPABASE_URL;
  const SUPABASE_KEY = process.env.SUPABASE_SERVICE_KEY;

  if (!SUPABASE_URL || !SUPABASE_KEY) {
    return res.status(500).json({ error: 'SUPABASE_URL or SUPABASE_SERVICE_KEY not set.' });
  }

  const headers = {
    'apikey': SUPABASE_KEY,
    'Authorization': `Bearer ${SUPABASE_KEY}`,
    'Content-Type': 'application/json'
  };

  const { action, id } = req.query;

  // List recent analyses (sidebar)
  if (action === 'list') {
    const r = await fetch(
      `${SUPABASE_URL}/rest/v1/category_analyses?select=id,created_at,category,period,analyst,market_trend,asp_trend,competition_trend,new_entrants&order=created_at.desc&limit=50`,
      { headers }
    );
    return res.status(r.status).json(await r.json());
  }

  // Get single analysis (full detail)
  if (action === 'get' && id) {
    const r = await fetch(
      `${SUPABASE_URL}/rest/v1/category_analyses?id=eq.${id}&select=*`,
      { headers }
    );
    const rows = await r.json();
    return res.status(r.status).json(rows[0] || null);
  }

  // Save new analysis
  if (req.method === 'POST' && action === 'save') {
    const r = await fetch(`${SUPABASE_URL}/rest/v1/category_analyses`, {
      method: 'POST',
      headers: { ...headers, 'Prefer': 'return=representation' },
      body: JSON.stringify(req.body)
    });
    return res.status(r.status).json(await r.json());
  }

  return res.status(400).json({ error: 'Unknown action' });
}
