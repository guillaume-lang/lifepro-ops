export const config = { maxDuration: 300 };

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).end();

  const ANTHROPIC_KEY = process.env.ANTHROPIC_API_KEY;
  const MONDAY_TOKEN  = process.env.MONDAY_TOKEN;

  if (!ANTHROPIC_KEY) {
    return res.status(500).json({ error: 'ANTHROPIC_API_KEY env var not set on this Vercel project.' });
  }

  const body = req.body;

  // Inject Monday token from Vercel env into any monday-mcp server entry
  if (body.mcp_servers && MONDAY_TOKEN) {
    body.mcp_servers = body.mcp_servers.map(s =>
      s.name === 'monday-mcp' ? { ...s, authorization_token: MONDAY_TOKEN } : s
    );
  }

  const upstream = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': ANTHROPIC_KEY,
      'anthropic-version': '2023-06-01'
    },
    body: JSON.stringify(body)
  });

  const data = await upstream.json();
  return res.status(upstream.status).json(data);
}
