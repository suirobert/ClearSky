// Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
// Licencia: PolyForm Noncommercial 1.0.0 (ver el archivo LICENSE). Uso no comercial.
// ClearSky · servicio de reportes, avisos y vigilancia (Cloudflare Workers + D1, plan gratuito) · v20
// POST /reporte     {lat, lon, tipo}            -> guarda un reporte. Tipos de clima (1 cada 30 min por persona y zona), "truenos" y "inundado"
//                                                  (1 cada 10 min por persona y zona; cada categoría tiene su propio límite)
// GET  /resumen?lat=&lon=                       -> reportes de CLIMA de la última hora a ~10 km (público). No cuenta truenos ni inundación.
// GET  /alertas?lat=&lon=                       -> truenos (30 min, 10 km) e inundación (3 h, 2 km) reportados por la gente. Solo cuentan
//                                                  cuando coinciden al menos 2 PERSONAS distintas (3 reportes si todavía no existe la columna "h").
// GET  /export?despues=<id>                     -> reportes nuevos para el script (requiere TOKEN)
// GET  /vapid                                   -> clave pública para los avisos
// POST /suscribir   {sub, lat, lon, nombre, prefs?, admin?} -> activa avisos. prefs: 1 = lluvia, 2 = tormentas, 4 = oficiales
// POST /desuscribir {endpoint}                  -> los desactiva
// GET  /vapid-nuevo                             -> genera claves VAPID (requiere TOKEN; se usa una sola vez)
// GET  /estado                                  -> antigüedad de la página publicada (público)
// Tarea programada (cron, cada 30 min) -> avisos al teléfono, según lo que cada persona eligió:
//   TORMENTA (2): 5 o más rayos en 20 min a menos de 25 km (GOES-19 GLM, desde ahora.json). Máximo uno cada 2 h.
//   LLUVIA   (1): el satélite o los reportes de la gente dicen que llueve ahora; o la lluvia llega en menos de 40 min según el
//                 movimiento medido por el satélite (SOLO si esa función está "verificada" en ahora.json); o el pronóstico da 60% o
//                 más en las próximas 2 horas. Máximo uno cada 6 h. El aviso abre la página con la pregunta "¿Te llovió?".
//   OFICIAL  (4): un boletín del INAMHI que afecta su región (desde 3 h antes de su inicio y mientras esté vigente). Una vez por boletín.
//   VIGILANCIA: si la página o la capa "ahora" llevan horas sin actualizarse, relanza el workflow de GitHub (si hay GH_TOKEN)
//                 y avisa a los suscriptores administradores.
const CLIMA = ['soleado', 'despejado', 'nublado', 'llovizna', 'lluvia', 'lluvia_fuerte', 'tormenta'];
const EXTRA = ['truenos', 'inundado'];                    // no cuentan como lluvia ni como seco
const TIPOS = [...CLIMA, ...EXTRA];
const LIMITE_S = { clima: 1800, truenos: 600, inundado: 600 };            // segundos entre reportes de la misma categoría
const VENTANA_S = { truenos: 1800, inundado: 3 * 3600 };                  // cuánto dura cada tipo de aviso
const RADIO_KM = { truenos: 10, inundado: 2 };
const MIN_PERSONAS = 2, MIN_REPORTES_SIN_H = 3;           // sin la columna "h" no se pueden distinguir personas: se exige más
const SITIO = 'https://suirobert.github.io/ClearSky';
const AHORA_URL = 'https://raw.githubusercontent.com/suirobert/ClearSky/ahora/ahora.json';
const REPO = 'suirobert/ClearSky';
const PROB_AVISO = 60, HORAS_ENTRE_AVISOS = 6, HORAS_ENTRE_TORMENTAS = 2, MAX_ZONAS = 15, MAX_ENVIOS = 25, HORAS_VIEJA = 3;
const RAYOS_AVISO = 5, NOWCAST_MAX_MIN = 40, NOWCAST_MIN_MM = 1.5;
const P_LLUVIA = 1, P_TORMENTA = 2, P_OFICIAL = 4;
const REPO_AHORA = 'ahora.yml';
const esCronAhora = (c) => /^\*\/10\s/.test(String(c || '').trim());      // cron de cada 10 minutos
const enEcuador = (lat, lon) => lat >= -6 && lat <= 2.5 && lon >= -92.5 && lon <= -75;
const REGION_DE_ZONA = { costa: 'litoral', sierra: 'interandina', oriente: 'amazonia', galapagos: 'insular' };
const pref = (s) => (s.pref == null ? 7 : s.pref);
const SIN_COLUMNA = /no such column|no column named|has no column/i;

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const permitidos = (env.ORIGEN || '*').split(',').map(s => s.trim().replace(/\/+$/, ''));
    const origen = req.headers.get('Origin') || '';
    const cors = {
      'Access-Control-Allow-Origin': permitidos.includes('*') ? '*' : (permitidos.includes(origen) ? origen : permitidos[0]),
      'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Vary': 'Origin',
    };
    const json = (o, s = 200, extra = {}) =>
      new Response(JSON.stringify(o), { status: s, headers: { ...cors, 'Content-Type': 'application/json', ...extra } });
    if (req.method === 'OPTIONS') return new Response(null, { headers: cors });
    const ahora = Math.floor(Date.now() / 1000);
    const autorizado = () => env.TOKEN && (req.headers.get('Authorization') || '') === `Bearer ${env.TOKEN}`;

    try {
      if (url.pathname === '/reporte' && req.method === 'POST') {
        const b = await req.json().catch(() => null);
        if (!b) return json({ error: 'json' }, 400);
        const lat = Math.round(Number(b.lat) * 100) / 100, lon = Math.round(Number(b.lon) * 100) / 100;
        const tipo = String(b.tipo || '');
        if (!TIPOS.includes(tipo)) return json({ error: 'datos' }, 400);
        if (!enEcuador(lat, lon)) return json({ error: 'fuera' }, 400);
        const ip = req.headers.get('CF-Connecting-IP') || '';
        const cat = EXTRA.includes(tipo) ? tipo : 'clima';                                 // cada categoría tiene su propio límite
        const h = await sha(`${ip}|${env.SAL}|${lat.toFixed(1)},${lon.toFixed(1)}` + (cat === 'clima' ? '' : '|' + cat));   // irreversible, sin IP
        const prev = await env.DB.prepare('SELECT ts FROM lim WHERE h = ?').bind(h).first();
        if (prev && ahora - prev.ts < LIMITE_S[cat]) return json({ error: 'espera' }, 429);
        // código irreversible de la persona (no de la zona): sirve para contar PERSONAS distintas en truenos e inundación.
        // Solo se guarda en esos dos tipos y se borra a las 24 h. Nunca sale del servicio (/export no lo incluye).
        const persona = cat === 'clima' ? null : (await sha(`${ip}|${env.SAL}|persona`)).slice(0, 16);
        const comunes = [
          env.DB.prepare('INSERT INTO lim (h, ts) VALUES (?, ?) ON CONFLICT(h) DO UPDATE SET ts = excluded.ts').bind(h, ahora),
          env.DB.prepare('DELETE FROM lim WHERE ts < ?').bind(ahora - 172800),            // se borra a las 48 h
        ];
        try {
          await env.DB.batch([
            env.DB.prepare('INSERT INTO rep (ts, lat, lon, tipo, h) VALUES (?, ?, ?, ?, ?)').bind(ahora, lat, lon, tipo, persona),
            env.DB.prepare('UPDATE rep SET h = NULL WHERE h IS NOT NULL AND ts < ?').bind(ahora - 86400),
            ...comunes,
          ]);
        } catch (e) {
          if (!SIN_COLUMNA.test(String((e && e.message) || e))) throw e;                  // todavía no se ejecutó el ALTER TABLE rep ADD COLUMN h
          await env.DB.batch([env.DB.prepare('INSERT INTO rep (ts, lat, lon, tipo) VALUES (?, ?, ?, ?)').bind(ahora, lat, lon, tipo), ...comunes]);
        }
        return json({ ok: true });
      }

      if (url.pathname === '/resumen' && req.method === 'GET') {
        const lat = Number(url.searchParams.get('lat')), lon = Number(url.searchParams.get('lon'));
        if (!isFinite(lat) || !isFinite(lon)) return json({ error: 'datos' }, 400);
        return json(await resumen(env, lat, lon, ahora), 200, { 'Cache-Control': 'public, max-age=60' });
      }

      if (url.pathname === '/alertas' && req.method === 'GET') {
        const lat = Number(url.searchParams.get('lat')), lon = Number(url.searchParams.get('lon'));
        if (!isFinite(lat) || !isFinite(lon) || !enEcuador(lat, lon)) return json({ error: 'datos' }, 400);
        return json(await alertasGente(env, lat, lon, ahora), 200, { 'Cache-Control': 'public, max-age=60' });
      }

      if (url.pathname === '/export' && req.method === 'GET') {
        if (!autorizado()) return json({ error: 'no autorizado' }, 401);
        const despues = Math.max(0, parseInt(url.searchParams.get('despues') || '0', 10) || 0);
        const r = await env.DB.prepare('SELECT id, ts, lat, lon, tipo FROM rep WHERE id > ? ORDER BY id LIMIT 5000')
          .bind(despues).all();
        return json(r.results);
      }

      if (url.pathname === '/estado' && req.method === 'GET') {
        const e = await edadPagina(env);
        return json({ generado: e.generado, horas: e.horas != null ? Math.round(e.horas * 10) / 10 : null });
      }

      // ---------- avisos ----------
      if (url.pathname === '/vapid' && req.method === 'GET')
        return json({ publica: env.VAPID_PUBLIC || '' }, 200, { 'Cache-Control': 'public, max-age=3600' });

      if (url.pathname === '/vapid-nuevo' && req.method === 'GET') {
        if (!autorizado()) return json({ error: 'no autorizado' }, 401);
        const k = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
        const pub = new Uint8Array(await crypto.subtle.exportKey('raw', k.publicKey));
        const jwk = await crypto.subtle.exportKey('jwk', k.privateKey);
        return json({ VAPID_PUBLIC: b64u(pub), VAPID_PRIVATE: JSON.stringify(jwk) });
      }

      if (url.pathname === '/suscribir' && req.method === 'POST') {
        const b = await req.json().catch(() => null);
        const s = b && b.sub, k = s && s.keys;
        const lat = Math.round(Number(b && b.lat) * 100) / 100, lon = Math.round(Number(b && b.lon) * 100) / 100;
        if (!s || !k || !k.p256dh || !k.auth || !/^https:\/\//.test(s.endpoint || '')) return json({ error: 'datos' }, 400);
        if (!enEcuador(lat, lon)) return json({ error: 'fuera' }, 400);
        const admin = b.admin != null && env.TOKEN && String(b.admin) === env.TOKEN ? 1 : 0;
        if (b.admin != null && !admin) return json({ error: 'clave de administrador incorrecta' }, 403);
        const nombre = String((b && b.nombre) || 'tu zona').slice(0, 60);
        const pp = parseInt(b.prefs, 10), prefs = pp >= 1 && pp <= 7 ? pp : 7;
        try {
          await env.DB.prepare(
            'INSERT INTO sub (endpoint, p256dh, auth, lat, lon, nombre, creado, aviso, admin, pref) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?) ' +
            'ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth, lat = excluded.lat, lon = excluded.lon, ' +
            'nombre = excluded.nombre, admin = MAX(sub.admin, excluded.admin), pref = excluded.pref'
          ).bind(s.endpoint, k.p256dh, k.auth, lat, lon, nombre, ahora, admin, prefs).run();
        } catch (e) {
          if (!/no such column|no column named|has no column/i.test(String(e && e.message))) throw e;                 // la tabla todavía no tiene la columna "pref"
          await env.DB.prepare(
            'INSERT INTO sub (endpoint, p256dh, auth, lat, lon, nombre, creado, aviso, admin) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?) ' +
            'ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth, lat = excluded.lat, lon = excluded.lon, ' +
            'nombre = excluded.nombre, admin = MAX(sub.admin, excluded.admin)'
          ).bind(s.endpoint, k.p256dh, k.auth, lat, lon, nombre, ahora, admin).run();
        }
        return json({ ok: true, admin: !!admin, prefs });
      }

      if (url.pathname === '/desuscribir' && req.method === 'POST') {
        const b = await req.json().catch(() => null);
        if (b && b.endpoint) await env.DB.prepare('DELETE FROM sub WHERE endpoint = ?').bind(String(b.endpoint)).run();
        return json({ ok: true });
      }

      return json({ error: 'no encontrado' }, 404);
    } catch (e) {
      return json({ error: 'interno', detalle: String((e && e.message) || e) }, 500);
    }
  },

  async scheduled(evento, env, ctx) {
    if (esCronAhora(evento && evento.cron)) {            // cada 10 min: solo lanza la capa "ahora" (GitHub no la dispara solo)
      ctx.waitUntil(lanzarAhora(env));
      return;
    }
    ctx.waitUntil((async () => {
      const ah = await leerAhora(env);
      await revisarAvisos(env, ah).catch(e => console.log('avisos:', e && e.message));
      await avisosOficiales(env, ah).catch(e => console.log('oficial:', e && e.message));
      await vigilar(env).catch(e => console.log('vigilancia:', e && e.message));
      await vigilarAhora(env, ah).catch(e => console.log('vigilancia ahora:', e && e.message));
    })());
  },
};

async function resumen(env, lat, lon, ahora) {
  const r = await env.DB.prepare(
    "SELECT tipo, COUNT(*) AS n FROM rep WHERE ts > ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND tipo NOT IN ('truenos', 'inundado') GROUP BY tipo"
  ).bind(ahora - 3600, lat - 0.1, lat + 0.1, lon - 0.1, lon + 0.1).all();
  const o = { total: 0 };
  for (const x of r.results) { o[x.tipo] = x.n; o.total += x.n; }
  return o;
}

// =================== TRUENOS E INUNDACIÓN REPORTADOS POR LA GENTE ===================
// Un solo reporte NO se muestra: hacen falta al menos MIN_PERSONAS personas distintas cerca (y recientes). Todo es "no verificado".
async function filasExtra(env, tipo, lat, lon, ahora, caja, limite) {
  const args = [tipo, ahora - VENTANA_S[tipo], lat - caja, lat + caja, lon - caja, lon + caja, limite];
  let conH = true, r;
  try {
    r = await env.DB.prepare('SELECT lat, lon, ts, h FROM rep WHERE tipo = ? AND ts > ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? ORDER BY ts DESC LIMIT ?').bind(...args).all();
  } catch (e) {
    if (!SIN_COLUMNA.test(String((e && e.message) || e))) throw e;
    conH = false;
    r = await env.DB.prepare('SELECT lat, lon, ts FROM rep WHERE tipo = ? AND ts > ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? ORDER BY ts DESC LIMIT ?').bind(...args).all();
  }
  return { filas: r.results || [], conH };
}
function resumenCerca(filas, conH, lat, lon, radio) {
  const cerca = filas.filter(f => km(lat, lon, f.lat, f.lon) <= radio);
  const personas = conH ? new Set(cerca.map(f => f.h).filter(Boolean)).size + cerca.filter(f => !f.h).length : null;   // un reporte sin código cuenta como persona aparte
  const n = cerca.length;
  const ok = personas != null ? personas >= MIN_PERSONAS : n >= MIN_REPORTES_SIN_H;
  return { n, personas, ok, ts: cerca.reduce((m, f) => Math.max(m, f.ts), 0) || null };
}
async function alertasGente(env, lat, lon, ahora) {
  const tr = await filasExtra(env, 'truenos', lat, lon, ahora, 0.12, 300);
  const inu = await filasExtra(env, 'inundado', lat, lon, ahora, 0.3, 400);
  const truenos = resumenCerca(tr.filas, tr.conH, lat, lon, RADIO_KM.truenos);
  const inundado = resumenCerca(inu.filas.filter(f => Math.abs(f.lat - lat) <= 0.05 && Math.abs(f.lon - lon) <= 0.05), inu.conH, lat, lon, RADIO_KM.inundado);
  // puntos para el mapa: cada reporte cuyo vecindario de 2 km reúne a suficientes personas; se juntan los que están a menos de 1 km
  const puntos = [];
  for (const f of inu.filas) {
    const vec = inu.filas.filter(g => km(f.lat, f.lon, g.lat, g.lon) <= RADIO_KM.inundado);
    const r = resumenCerca(vec, inu.conH, f.lat, f.lon, RADIO_KM.inundado);
    if (!r.ok) continue;
    const previo = puntos.findIndex(p => km(p.lat, p.lon, f.lat, f.lon) < 1);
    const p = { lat: f.lat, lon: f.lon, personas: r.personas, n: r.n, ts: r.ts };
    if (previo < 0) puntos.push(p); else if ((p.personas ?? p.n) > (puntos[previo].personas ?? puntos[previo].n)) puntos[previo] = p;
  }
  return { truenos, inundado, puntos: puntos.slice(0, 40), minimo: { personas: MIN_PERSONAS, sin_codigo: MIN_REPORTES_SIN_H },
           vigencia_min: { truenos: VENTANA_S.truenos / 60, inundado: VENTANA_S.inundado / 60 }, radio_km: RADIO_KM, codigo_persona: tr.conH && inu.conH };
}

// =================== LA CAPA "AHORA" (ahora.json, cada 10 min desde GitHub Actions) ===================
const horaEc = (s) => Date.parse(String(s || '') + ':00-05:00');          // "2026-10-29T02:50" (hora de Ecuador) -> ms
async function leerAhora(env) {
  try {
    const r = await fetch(env.AHORA_URL || AHORA_URL, { cf: { cacheTtl: 120 } });
    if (!r.ok) return null;
    const j = await r.json();
    j._edadMin = (Date.now() - horaEc(j.generado)) / 60000;
    if (!(j._edadMin < 40)) { j.rayos = null; j.lluvia = null; j.movimiento = null; }   // dato viejo: no es "ahora"
    if (j.rayos && !((Date.now() - horaEc(j.rayos.t)) / 60000 < 25)) j.rayos = null;
    return j;
  } catch (e) { return null; }
}
function rayosEn(R, lat, lon) {
  if (!R || !R.celdas) return null;
  const radio = R.radio_km || 25;
  let n20 = 0, n30 = 0;
  for (const [la, lo, a, b] of R.celdas) if (km(lat, lon, la, lo) <= radio) { n20 += a; n30 += b; }
  return { n20, n30, radio };
}

// =================== AVISOS: el mismo cálculo que la página ===================
function probConsenso(v, pesos, u) { let sw = 0, s = 0; for (const m in v) { const w = pesos[m] ?? 1; sw += w; if (v[m] >= u) s += w; } return sw ? s / sw * 100 : 0; }
function calibrarProb(p, t) {
  if (!t || !t.length) return p;
  if (p <= t[0][0]) return t[0][1];
  for (let i = 0; i < t.length - 1; i++) { const [x0, y0] = t[i], [x1, y1] = t[i + 1];
    if (p <= x1) return x1 > x0 ? y0 + (y1 - y0) * (p - x0) / (x1 - x0) : y1; }
  return t[t.length - 1][1];
}
function km(a, b, c, d) { const R = Math.PI / 180, x = Math.sin((c - a) * R / 2) ** 2 + Math.cos(a * R) * Math.cos(c * R) * Math.sin((d - b) * R / 2) ** 2; return 12742 * Math.asin(Math.sqrt(x)); }
function referencia(conf, lat, lon, elev) {
  const zona = lon < -88 ? 'galapagos' : (elev >= 1500 ? 'sierra' : (lon > -78.3 ? 'oriente' : 'costa'));
  const verif = conf.ciudades.filter(c => c.propia), todas = verif.length ? verif : conf.ciudades;
  const misma = todas.filter(c => c.zona === zona), lista = misma.length ? misma : todas;
  return lista.reduce((a, b) => km(lat, lon, a.lat, a.lon) <= km(lat, lon, b.lat, b.lon) ? a : b);
}
function regionDe(conf, lat, lon) {
  if (lon < -88) return 'insular';
  const c = conf.ciudades.reduce((a, b) => km(lat, lon, a.lat, a.lon) <= km(lat, lon, b.lat, b.lon) ? a : b);
  return REGION_DE_ZONA[c.zona] || 'litoral';
}
function tablaHora(cfg, conf, hora) {
  const h = +hora.slice(11, 13);
  if (cfg.franjas && cfg.franjas.activa) for (const n in conf.franjas) { const [a, b] = conf.franjas[n];
    if (h >= a && h < b && cfg.franjas.tablas && cfg.franjas.tablas[n]) return cfg.franjas.tablas[n]; }
  return cfg.tabla_lluvia;
}
function satelite(A) {
  if (!A || !A.q) return null;
  if (!(Date.now() - horaEc(A.t) < 3 * 3600e3)) return null;
  const v = Uint8Array.from(atob(A.q), c => c.charCodeAt(0));
  return { t: A.t, edadMin: (Date.now() - horaEc(A.t)) / 60000, en(lat, lon, r = 1) {
    const iy = Math.floor((A.lat0 - lat) / A.paso), ix = Math.floor((lon - A.lon0) / A.paso); let m = null;
    for (let y = iy - r; y <= iy + r; y++) for (let x = ix - r; x <= ix + r; x++)
      if (y >= 0 && y < A.ny && x >= 0 && x < A.nx) m = Math.max(m ?? 0, v[y * A.nx + x] * A.escala);
    return m;
  } };
}
// ---- lluvia en camino: el mismo cálculo que la página (extiende el movimiento que midió el satélite) ----
function movEn(M, lat, lon) {
  if (!M || !M.u) return null;
  const ty = Math.floor((M.lat0 - lat) / M.tile), tx = Math.floor((lon - M.lon0) / M.tile);
  let su = 0, sv = 0, sw = 0;
  for (let y = ty - 1; y <= ty + 1; y++) for (let x = tx - 1; x <= tx + 1; x++) {
    if (y < 0 || x < 0 || y >= M.ny || x >= M.nx) continue;
    const u = M.u[y][x], w = M.v[y][x]; if (u == null || w == null) continue;
    const pe = (y === ty && x === tx) ? 3 : 1; su += u * pe; sv += w * pe; sw += pe;
  }
  return sw ? { u: su / sw, v: sv / sw } : null;
}
export function nowcast(sat, M, lat, lon) {
  const m = movEn(M, lat, lon); if (!sat || !m) return null;
  const vel = Math.hypot(m.u, m.v); if (vel < 4) return null;
  const edad = sat.edadMin; if (edad > 35) return null;
  const cl = Math.cos(lat * Math.PI / 180);
  const fuente = tt => { const t = (edad + tt) / 60; return [lat - m.v * t / 111.32, lon - m.u * t / (111.32 * cl)]; };
  const val = tt => { const [la, lo] = fuente(tt); return sat.en(la, lo, 0) ?? 0; };
  const pasos = []; for (let tt = 0; tt <= 90; tt += 5) pasos.push([tt, val(tt)]);
  if (pasos[0][1] >= 1) return null;                                        // ya llueve: eso lo cubre el aviso de "está lloviendo"
  for (let i = 1; i < pasos.length - 1; i++) if (pasos[i][1] >= 1 && pasos[i + 1][1] >= 1)
    return { min: pasos[i][0], mm: Math.max(...pasos.slice(i, i + 7).map(p => p[1])), vel: Math.round(vel) };
  return null;
}
async function revisarAvisos(env, ah) {
  if (!env.VAPID_PRIVATE || !env.VAPID_PUBLIC) return;
  const sitio = (env.SITIO || SITIO).replace(/\/+$/, '');
  const ahora = Math.floor(Date.now() / 1000);
  const subs = (await env.DB.prepare('SELECT * FROM sub WHERE aviso < ?').bind(ahora - HORAS_ENTRE_TORMENTAS * 3600).all()).results;
  if (!subs.length) return;
  const conf = await (await fetch(`${sitio}/push.json`, { cf: { cacheTtl: 300 } })).json();
  const sat = satelite((ah && ah.lluvia) || conf.ahora);
  const zonas = new Map();                                   // agrupa suscriptores cercanos (~5 km)
  for (const s of subs) { const k = `${(Math.round(s.lat * 20) / 20).toFixed(2)},${(Math.round(s.lon * 20) / 20).toFixed(2)}`;
    (zonas.get(k) || zonas.set(k, []).get(k)).push(s); }
  let envios = 0;
  for (const [k, lista] of [...zonas].slice(0, MAX_ZONAS)) {
    const [lat, lon] = k.split(',').map(Number);
    let aviso = null, destinatarios = lista;
    // 1) tormenta eléctrica: descargas detectadas por el satélite en los últimos 20 minutos
    const ry = rayosEn(ah && ah.rayos, lat, lon);
    if (ry && ry.n20 >= RAYOS_AVISO && lista.some(s => pref(s) & P_TORMENTA)) {
      destinatarios = lista.filter(s => pref(s) & P_TORMENTA);
      aviso = (nombre) => ({ title: `⚡ Tormenta eléctrica cerca de ${nombre}`,
        body: `${ry.n20} rayos en 20 min a menos de ${ry.radio} km. Busca refugio bajo techo.`, url: sitio + '/', tag: 'tormenta' });
    } else {
      destinatarios = lista.filter(s => (pref(s) & P_LLUVIA) && s.aviso < ahora - HORAS_ENTRE_AVISOS * 3600);   // la lluvia avisa como mucho cada 6 h
      if (!destinatarios.length) continue;
      // 2) ¿está lloviendo ya? satélite y reportes de la gente de la última hora
      const mm = sat ? sat.en(lat, lon) : null;
      const rep = await resumen(env, lat, lon, ahora);
      const moja = (rep.lluvia || 0) + (rep.lluvia_fuerte || 0) + (rep.tormenta || 0) + 0.5 * (rep.llovizna || 0);
      const lluviaRep = rep.total >= 3 && moja / rep.total >= 0.6;
      const verificada = !!(ah && ah.nowcast && ah.nowcast.estado === 'verificado');       // en pruebas u oculta: no se avisa
      const nc = verificada && (mm == null || mm < 1) && !lluviaRep ? nowcast(sat, ah && ah.movimiento, lat, lon) : null;
      if ((mm != null && mm >= 1) || lluviaRep) {
        const quien = [mm != null && mm >= 0.5 ? 'el satélite' : '', lluviaRep ? `${rep.total} reportes de la gente` : ''].filter(Boolean).join(' y ');
        aviso = (nombre) => ({ title: `🌧️ Está lloviendo cerca de ${nombre}`, body: `Según ${quien}. ¡Lleva paraguas!`, url: sitio + '/?q=ll', tag: 'lluvia' });
      } else if (nc && nc.min <= NOWCAST_MAX_MIN && nc.mm >= NOWCAST_MIN_MM) {
        // 3) la lluvia viene en camino (movimiento medido por el satélite)
        aviso = (nombre) => ({ title: `🌧️ La lluvia llegaría a ${nombre} en ~${Math.max(nc.min, 5)} min`,
          body: `Viene a ${nc.vel} km/h, según el satélite. ¡Lleva paraguas!`, url: sitio + '/?q=ll', tag: 'lluvia' });
      } else {
        // 4) pronóstico de las próximas 2 horas
        const om = await (await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&hourly=precipitation` +
          `&models=${conf.modelos.join(',')}&timezone=auto&forecast_hours=3`)).json();
        if (!om.hourly) continue;
        const cfg = referencia(conf, lat, lon, om.elevation ?? 0).cfg;
        let mejor = null;
        om.hourly.time.forEach((t, i) => {
          const v = {}; for (const m of conf.modelos) { const x = (om.hourly[`precipitation_${m}`] || [])[i]; if (x != null) v[m] = x; }
          if (Object.keys(v).length < 3) return;
          const p = Math.round(calibrarProb(probConsenso(v, cfg.pesos, cfg.umbral), tablaHora(cfg, conf, t)));
          if (p >= PROB_AVISO && (!mejor || p > mejor.p)) mejor = { p, t };
        });
        if (mejor) aviso = (nombre) => ({ title: `🌧️ Lluvia probable en ${nombre}`,
          body: `${mejor.p}% de probabilidad hacia las ${mejor.t.slice(11, 16)}. ¡Lleva paraguas!`, url: sitio + '/?q=ll', tag: 'lluvia' });
      }
    }
    if (!aviso) continue;
    for (const s of destinatarios) {
      if (envios >= MAX_ENVIOS) return;
      const r = await enviarPush(s, aviso(s.nombre.split(',')[0]), env);
      envios++;
      if (r.status === 404 || r.status === 410) await env.DB.prepare('DELETE FROM sub WHERE endpoint = ?').bind(s.endpoint).run();
      else if (r.ok) await env.DB.prepare('UPDATE sub SET aviso = ? WHERE endpoint = ?').bind(ahora, s.endpoint).run();
    }
  }
}

// =================== AVISO OFICIAL DEL INAMHI ===================
const nivelTxt = n => String(n || '').toLowerCase().replace(/muy alto/, 'muy alta').replace(/\balto\b/, 'alta')
  .replace(/medio/, 'media').replace(/bajo/, 'baja').replace(/\s*[–-]\s*/, ' a ');
async function avisosOficiales(env, ah) {
  const o = ah && ah.oficial;
  if (!o || !o.id || !o.desde || !o.hasta) return;
  if (!env.VAPID_PRIVATE || !env.VAPID_PUBLIC) return;
  const ahoraEc = new Date(Date.now() - 5 * 3600e3).toISOString().slice(0, 16);          // hora de Ecuador
  if (ahoraEc > o.hasta) return;                                                         // vencido
  if ((horaEc(o.desde) - Date.now()) / 3600e3 > 3) return;                               // se avisa desde 3 h antes del inicio
  const sitio = (env.SITIO || SITIO).replace(/\/+$/, '');
  const conf = await (await fetch(`${sitio}/push.json`, { cf: { cacheTtl: 300 } })).json();
  const clave = 'of:' + o.id;
  const prev = await env.DB.prepare('SELECT v FROM estado WHERE k = ?').bind(clave).first();
  const ultimo = prev ? Number(prev.v) : 0;
  const subs = (await env.DB.prepare('SELECT rowid AS rid, * FROM sub WHERE rowid > ? ORDER BY rowid LIMIT ?').bind(ultimo, MAX_ENVIOS).all()).results;
  if (!subs.length) return;
  const zonas = o.zonas || {};
  let maxRid = ultimo;
  for (const s of subs) {
    maxRid = Math.max(maxRid, s.rid);
    if (!(pref(s) & P_OFICIAL)) continue;                                                // no quiso avisos oficiales
    if (Object.keys(zonas).length && !zonas[regionDe(conf, s.lat, s.lon)]) continue;     // no afecta a su región
    const vig = `Vigente hasta ${o.hasta.slice(8, 10)}/${o.hasta.slice(5, 7)} ${o.hasta.slice(11, 16)}.`;
    const aviso = { title: '⚠️ Aviso oficial del INAMHI',
      body: `${o.estatus || 'Advertencia'} · amenaza ${nivelTxt(o.nivel)}: ${String(o.fenomeno || '').toLowerCase()}. ${vig}`,
      url: o.pagina || sitio + '/', tag: 'oficial-' + o.id };
    const r = await enviarPush(s, aviso, env);
    if (r.status === 404 || r.status === 410) await env.DB.prepare('DELETE FROM sub WHERE endpoint = ?').bind(s.endpoint).run();
  }
  await env.DB.prepare('INSERT INTO estado (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v').bind(clave, String(maxRid)).run();
}

// =================== VIGILANCIA: ¿la página y la capa "ahora" se están actualizando? ===================
async function edadPagina(env) {
  const sitio = (env.SITIO || SITIO).replace(/\/+$/, '');
  try {
    const conf = await (await fetch(`${sitio}/push.json?t=${Date.now()}`, { cf: { cacheTtl: 0 } })).json();
    return { generado: conf.generado, horas: (Date.now() - horaEc(conf.generado)) / 36e5 };
  } catch (e) { return { generado: null, horas: null }; }
}
// Lanza ahora.yml en GitHub y deja anotado en la tabla "estado" qué respondió (SELECT * FROM estado;)
async function lanzarAhora(env) {
  let resultado = 'sin GH_TOKEN';
  try {
    if (env.GH_TOKEN) {
      const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${REPO_AHORA}/dispatches`, { method: 'POST',
        headers: { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: 'application/vnd.github+json', 'User-Agent': 'ClearSky-cron',
                   'X-GitHub-Api-Version': '2022-11-28' }, body: JSON.stringify({ ref: 'main' }) });
      resultado = r.status === 204 ? '204 ok' : `${r.status} ${(await r.text()).replace(/\s+/g, ' ').slice(0, 140)}`;
    }
  } catch (e) { resultado = 'error ' + String((e && e.message) || e).slice(0, 140); }
  try {
    await env.DB.prepare("INSERT INTO estado (k, v) VALUES ('dispatch-ahora', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v")
      .bind(`${new Date().toISOString()} ${resultado}`).run();
  } catch (e) {}
  console.log('dispatch ahora.yml:', resultado);
}
async function relanzar(env, workflow) {
  if (!env.GH_TOKEN) return false;
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`, { method: 'POST',
    headers: { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: 'application/vnd.github+json', 'User-Agent': 'ClearSky-vigilancia',
               'X-GitHub-Api-Version': '2022-11-28' }, body: JSON.stringify({ ref: 'main' }) });
  return r.status === 204;
}
async function avisarAdmins(env, titulo, cuerpo, url, tag) {
  if (!env.VAPID_PRIVATE) return;
  const admins = (await env.DB.prepare('SELECT * FROM sub WHERE admin = 1').all()).results;
  for (const s of admins) await enviarPush(s, { title: titulo, body: cuerpo, url, tag }, env).catch(() => {});
}
async function puedeVigilar(env, clave, horas) {
  const ahora = Math.floor(Date.now() / 1000);
  const ult = await env.DB.prepare('SELECT v FROM estado WHERE k = ?').bind(clave).first().catch(() => null);
  if (ult && ahora - Number(ult.v) < horas * 3600) return false;                          // como mucho una vez cada N horas
  await env.DB.prepare('INSERT INTO estado (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v').bind(clave, String(ahora)).run();
  return true;
}
async function vigilar(env) {
  const e = await edadPagina(env);
  if (e.horas != null && e.horas < HORAS_VIEJA) return;
  if (!(await puedeVigilar(env, 'vigilancia', HORAS_VIEJA))) return;
  const relanzado = await relanzar(env, 'actualizar.yml');
  const cuanto = e.horas == null ? 'no responde' : `lleva ${Math.round(e.horas)} h sin actualizarse`;
  await avisarAdmins(env, '⚠️ ClearSky ' + cuanto,
    relanzado ? 'Relancé la actualización automáticamente. Revisa GitHub Actions si sigue igual.' : 'Revisa GitHub Actions.',
    `https://github.com/${REPO}/actions`, 'vigilancia');
}
async function vigilarAhora(env, ah) {
  // ahora.json se renueva cada 10 min: si lleva más de 1 h, algo falló (el archivo se lee sin el filtro de "dato viejo")
  let edad = null;
  try { const r = await fetch(env.AHORA_URL || AHORA_URL, { cf: { cacheTtl: 0 } }); if (r.ok) edad = (Date.now() - horaEc((await r.json()).generado)) / 36e5; } catch (e) {}
  if (edad != null && edad < 1) return;
  if (!(await puedeVigilar(env, 'vigilancia-ahora', HORAS_VIEJA))) return;
  const relanzado = await relanzar(env, 'ahora.yml');
  await avisarAdmins(env, '⚠️ ClearSky: la capa "ahora" ' + (edad == null ? 'no responde' : `lleva ${Math.round(edad)} h sin actualizarse`),
    relanzado ? 'Relancé el workflow ahora.yml automáticamente.' : 'Revisa el workflow ahora.yml en GitHub Actions.',
    `https://github.com/${REPO}/actions`, 'vigilancia-ahora');
}

// =================== WEB PUSH (RFC 8291 + VAPID RFC 8292), solo WebCrypto ===================
const enc = new TextEncoder();
function b64u(buf) { let s = ''; for (const b of new Uint8Array(buf)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }
function deB64u(s) { const p = '='.repeat((4 - s.length % 4) % 4); return Uint8Array.from(atob((s + p).replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0)); }
function unir(...partes) { const n = partes.reduce((a, p) => a + p.length, 0), o = new Uint8Array(n); let i = 0; for (const p of partes) { o.set(p, i); i += p.length; } return o; }
async function hkdf(salt, ikm, info, bytes) {
  const k = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt, info }, k, bytes * 8));
}
export async function cifrar(payload, p256dh, authSecret) {
  const uaPub = deB64u(p256dh), auth = deB64u(authSecret);
  const local = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
  const asPub = new Uint8Array(await crypto.subtle.exportKey('raw', local.publicKey));
  const uaKey = await crypto.subtle.importKey('raw', uaPub, { name: 'ECDH', namedCurve: 'P-256' }, false, []);
  const secreto = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: uaKey }, local.privateKey, 256));
  const ikm = await hkdf(auth, secreto, unir(enc.encode('WebPush: info\0'), uaPub, asPub), 32);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const cek = await hkdf(salt, ikm, enc.encode('Content-Encoding: aes128gcm\0'), 16);
  const nonce = await hkdf(salt, ikm, enc.encode('Content-Encoding: nonce\0'), 12);
  const clave = await crypto.subtle.importKey('raw', cek, 'AES-GCM', false, ['encrypt']);
  const cifrado = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce }, clave, unir(enc.encode(payload), new Uint8Array([2]))));
  return unir(salt, new Uint8Array([0, 0, 16, 0]), new Uint8Array([asPub.length]), asPub, cifrado);
}
export async function jwtVapid(audiencia, env) {
  const k = await crypto.subtle.importKey('jwk', JSON.parse(env.VAPID_PRIVATE), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  const cab = b64u(enc.encode(JSON.stringify({ typ: 'JWT', alg: 'ES256' })));
  const cuerpo = b64u(enc.encode(JSON.stringify({ aud: audiencia, exp: Math.floor(Date.now() / 1000) + 12 * 3600,
    sub: env.VAPID_SUB || 'mailto:clearsky@example.com' })));
  const firma = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, k, enc.encode(`${cab}.${cuerpo}`));
  return `${cab}.${cuerpo}.${b64u(firma)}`;
}
async function enviarPush(s, aviso, env) {
  const cuerpo = await cifrar(JSON.stringify(aviso), s.p256dh, s.auth);
  const jwt = await jwtVapid(new URL(s.endpoint).origin, env);
  return fetch(s.endpoint, { method: 'POST', body: cuerpo, headers: {
    'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream', TTL: '3600', Urgency: 'high',
    Authorization: `vapid t=${jwt}, k=${env.VAPID_PUBLIC}` } });
}

async function sha(s) {
  const d = await crypto.subtle.digest('SHA-256', enc.encode(s));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('');
}
