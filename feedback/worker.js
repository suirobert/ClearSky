// ClearSky - servicio de reportes "¿Está lloviendo donde estás?"
// Cloudflare Worker + base de datos D1 (plan gratuito).
//   POST /reporte   {lat, lon, tipo: "lluvia"|"llovizna"|"seca"}
//   GET  /resumen?lat=..&lon=..   reportes de la última hora a ~3 km
//   GET  /exportar?desde=UNIX     (requiere Authorization: Bearer TOKEN_EXPORTAR) para uso futuro en la verificación
// Privacidad: la ubicación se redondea a 0.01° (~1 km). La IP nunca se guarda: solo un código
// irreversible (hash con sal secreta que cambia cada día) para limitar reportes repetidos, y ese
// código se borra a las 48 h.

const TIPOS = ["lluvia", "llovizna", "seca"];
const ECUADOR = {latMin: -5.1, latMax: 1.7, lonMin: -92.1, lonMax: -75.1};
const ESPERA_CELDA_S = 30 * 60;     // un reporte por persona y zona cada 30 min
const MAX_IP_HORA = 6;              // máximo de reportes por persona por hora
const RADIO = 0.03;                 // ~3 km para el resumen

function cors(req, env) {
  const permitidos = (env.ORIGENES || "").split(",").map(s => s.trim()).filter(Boolean);
  const origen = req.headers.get("Origin") || "";
  return {
    "Access-Control-Allow-Origin": permitidos.includes(origen) ? origen : (permitidos[0] || "*"),
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}
const json = (obj, status, h) => new Response(JSON.stringify(obj), {status, headers: {...h, "Content-Type": "application/json"}});

async function hash(texto) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(texto));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}
const redondear = x => Math.round(x * 100) / 100;

export default {
  async fetch(req, env) {
    const h = cors(req, env);
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, {status: 204, headers: h});
    try {
      if (req.method === "POST" && url.pathname === "/reporte") return await reporte(req, env, h);
      if (req.method === "GET" && url.pathname === "/resumen") return await resumen(url, env, h);
      if (req.method === "GET" && url.pathname === "/exportar") return await exportar(req, url, env, h);
      if (req.method === "GET" && url.pathname === "/") return json({servicio: "ClearSky reportes", ok: true}, 200, h);
      return json({error: "no encontrado"}, 404, h);
    } catch (e) {
      return json({error: "error interno"}, 500, h);
    }
  },

  // Cada hora: borra los códigos anti-repetición de más de 48 h (privacidad)
  async scheduled(evento, env) {
    const limite = Math.floor(Date.now() / 1000) - 48 * 3600;
    await env.DB.prepare("UPDATE reportes SET h_celda = NULL, h_ip = NULL WHERE ts < ? AND h_ip IS NOT NULL").bind(limite).run();
  },
};

async function reporte(req, env, h) {
  const permitidos = (env.ORIGENES || "").split(",").map(s => s.trim());
  if (!permitidos.includes(req.headers.get("Origin") || "")) return json({error: "origen no permitido"}, 403, h);
  let b;
  try { b = await req.json(); } catch { return json({error: "formato inválido"}, 400, h); }
  const lat = Number(b.lat), lon = Number(b.lon), tipo = String(b.tipo || "");
  if (!TIPOS.includes(tipo) || !Number.isFinite(lat) || !Number.isFinite(lon) ||
      lat < ECUADOR.latMin || lat > ECUADOR.latMax || lon < ECUADOR.lonMin || lon > ECUADOR.lonMax)
    return json({error: "datos inválidos"}, 400, h);
  const la = redondear(lat), lo = redondear(lon);
  const ahora = Math.floor(Date.now() / 1000);
  const dia = new Date().toISOString().slice(0, 10);
  const ip = req.headers.get("CF-Connecting-IP") || "0.0.0.0";
  const sal = env.SAL || "cambia-esta-sal";
  const hIp = await hash(`${sal}|${dia}|${ip}`);
  const hCelda = await hash(`${sal}|${dia}|${ip}|${la}|${lo}`);

  const r = await env.DB.prepare(
    "SELECT (SELECT COUNT(*) FROM reportes WHERE h_celda = ?1 AND ts > ?2) AS celda, " +
    "(SELECT COUNT(*) FROM reportes WHERE h_ip = ?3 AND ts > ?4) AS ip"
  ).bind(hCelda, ahora - ESPERA_CELDA_S, hIp, ahora - 3600).first();
  if (r.celda > 0 || r.ip >= MAX_IP_HORA) return json({error: "espera un momento"}, 429, h);

  await env.DB.prepare("INSERT INTO reportes (ts, lat, lon, tipo, h_celda, h_ip) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(ahora, la, lo, tipo, hCelda, hIp).run();
  return json({ok: true}, 200, h);
}

async function resumen(url, env, h) {
  const lat = Number(url.searchParams.get("lat")), lon = Number(url.searchParams.get("lon"));
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return json({error: "datos inválidos"}, 400, h);
  const desde = Math.floor(Date.now() / 1000) - 3600;
  const {results} = await env.DB.prepare(
    "SELECT tipo, COUNT(*) AS n FROM reportes WHERE ts > ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? GROUP BY tipo"
  ).bind(desde, lat - RADIO, lat + RADIO, lon - RADIO, lon + RADIO).all();
  const out = {lluvia: 0, llovizna: 0, seca: 0, total: 0};
  for (const f of results) { out[f.tipo] = f.n; out.total += f.n; }
  return json(out, 200, {...h, "Cache-Control": "public, max-age=120"});
}

async function exportar(req, url, env, h) {
  if (!env.TOKEN_EXPORTAR || req.headers.get("Authorization") !== `Bearer ${env.TOKEN_EXPORTAR}`)
    return json({error: "no autorizado"}, 401, h);
  const desde = Number(url.searchParams.get("desde") || 0);
  const {results} = await env.DB.prepare(
    "SELECT ts, lat, lon, tipo FROM reportes WHERE ts > ? ORDER BY ts LIMIT 50000").bind(desde).all();
  return json(results, 200, h);
}
