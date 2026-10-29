// ClearSky · servicio de reportes de usuarios (Cloudflare Workers + D1, plan gratuito)
// POST /reporte   {lat, lon, tipo}         -> guarda un reporte (máx. 1 cada 30 min por persona y zona)
// GET  /resumen?lat=&lon=                  -> reportes de la última hora a ~10 km (público)
// GET  /export?despues=<id>                -> reportes nuevos para el script (requiere TOKEN)
const TIPOS = ['soleado', 'nublado', 'llovizna', 'lluvia', 'lluvia_fuerte', 'tormenta'];

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const cors = {
      'Access-Control-Allow-Origin': env.ORIGEN || '*',
      'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Vary': 'Origin',
    };
    const json = (o, s = 200, extra = {}) =>
      new Response(JSON.stringify(o), { status: s, headers: { ...cors, 'Content-Type': 'application/json', ...extra } });
    if (req.method === 'OPTIONS') return new Response(null, { headers: cors });
    const ahora = Math.floor(Date.now() / 1000);

    try {
      if (url.pathname === '/reporte' && req.method === 'POST') {
        const b = await req.json().catch(() => null);
        if (!b) return json({ error: 'json' }, 400);
        const lat = Math.round(Number(b.lat) * 100) / 100, lon = Math.round(Number(b.lon) * 100) / 100;
        const tipo = String(b.tipo || '');
        // Ecuador continental e insular
        if (!TIPOS.includes(tipo) || !(lat >= -6 && lat <= 2.5) || !(lon >= -92.5 && lon <= -75))
          return json({ error: 'datos' }, 400);
        const ip = req.headers.get('CF-Connecting-IP') || '';
        const h = await sha(`${ip}|${env.SAL}|${lat.toFixed(1)},${lon.toFixed(1)}`);   // irreversible, sin IP
        const prev = await env.DB.prepare('SELECT ts FROM lim WHERE h = ?').bind(h).first();
        if (prev && ahora - prev.ts < 1800) return json({ error: 'espera' }, 429);
        await env.DB.batch([
          env.DB.prepare('INSERT INTO rep (ts, lat, lon, tipo) VALUES (?, ?, ?, ?)').bind(ahora, lat, lon, tipo),
          env.DB.prepare('INSERT INTO lim (h, ts) VALUES (?, ?) ON CONFLICT(h) DO UPDATE SET ts = excluded.ts').bind(h, ahora),
          env.DB.prepare('DELETE FROM lim WHERE ts < ?').bind(ahora - 172800),            // se borra a las 48 h
        ]);
        return json({ ok: true });
      }

      if (url.pathname === '/resumen' && req.method === 'GET') {
        const lat = Number(url.searchParams.get('lat')), lon = Number(url.searchParams.get('lon'));
        if (!isFinite(lat) || !isFinite(lon)) return json({ error: 'datos' }, 400);
        const r = await env.DB.prepare(
          'SELECT tipo, COUNT(*) AS n FROM rep WHERE ts > ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? GROUP BY tipo'
        ).bind(ahora - 3600, lat - 0.1, lat + 0.1, lon - 0.1, lon + 0.1).all();
        const o = { total: 0 };
        for (const x of r.results) { o[x.tipo] = x.n; o.total += x.n; }
        return json(o, 200, { 'Cache-Control': 'public, max-age=60' });
      }

      if (url.pathname === '/export' && req.method === 'GET') {
        const auth = req.headers.get('Authorization') || '';
        if (!env.TOKEN || auth !== `Bearer ${env.TOKEN}`) return json({ error: 'no autorizado' }, 401);
        const despues = Math.max(0, parseInt(url.searchParams.get('despues') || '0', 10) || 0);
        const r = await env.DB.prepare('SELECT id, ts, lat, lon, tipo FROM rep WHERE id > ? ORDER BY id LIMIT 5000')
          .bind(despues).all();
        return json(r.results);
      }

      return json({ error: 'no encontrado' }, 404);
    } catch (e) {
      return json({ error: 'interno' }, 500);
    }
  },
};

async function sha(s) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('');
}
