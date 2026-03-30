export const config = { maxDuration: 30 };

export default async function handler(req, res) {
  const DATADIVE_KEY = process.env.DATADIVE_API_KEY;

  if (!DATADIVE_KEY) {
    return res.status(500).json({ error: 'DATADIVE_API_KEY env var not set on this Vercel project.' });
  }

  // Path passed as ?p=/niches?pageSize=100&page=1 etc.
  const path = req.query.p || '';

  const upstream = await fetch(`https://api.datadive.tools/v1${path}`, {
    headers: { 'x-api-key': DATADIVE_KEY }
  });

  const data = await upstream.json();
  return res.status(upstream.status).json(data);
}
