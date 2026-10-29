// ClearSky · servicio de reportes y avisos (Cloudflare Workers + D1, plan gratuito) · v12
// POST /reporte     {lat, lon, tipo}            -> guarda un reporte (máx. 1 cada 30 min por persona y zona)
// GET  /resumen?lat=&lon=                       -> reportes de la última hora a ~10 km (público)
// GET  /export?despues=<id>                     -> reportes nuevos para el script (requiere TOKEN)
// GET  /vapid                                   -> clave pública para los avisos
// POST /suscribir   {sub, lat, lon, nombre, admin?} -> activa avisos (admin = TOKEN: avisos de mantenimiento)
// POST /desuscribir {endpoint}                  -> los desactiva
// GET  /vapid-nuevo                             -> genera claves VAPID (requiere TOKEN; se usa una sola vez)
// GET  /estado                                  -> antigüedad de la página publicada (público)
// Tarea programada (cron, cada 30 min):
//   1. Avisos de lluvia: si el satélite o los reportes de la gente dicen que está lloviendo en la zona de un
//      suscriptor, o si el pronóstico da 60% o más en las próximas 2 horas (máximo un aviso cada 6 horas).
//   2. Vigilancia: si la página lleva más de 3 horas sin actualizarse, relanza el workflow de GitHub
//      (si hay GH_TOKEN) y avisa a los suscriptores administradores.
const TIPOS = ['soleado', 'nublado', 'llovizna', 'lluvia', 'lluvia_fuerte', 'tormenta'];
const SITIO = 'https://suirobert.github.io/ClearSky';
const REPO = 'suirobert/ClearSky', WORKFLOW = 'actualizar.yml';
const PROB_AVISO = 60, HORAS_ENTRE_AVISOS = 6, MAX_ZONAS = 15, MAX_ENVIOS = 25, HORAS_VIEJA = 3;
const enEcuador = (lat, lon) => lat >= -6 && lat <= 2.5 && lon >= -92.5 && lon <= -75;

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
        return json(await resumen(env, lat, lon, ahora), 200, { 'Cache-Control': 'public, max-age=60' });
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
        await env.DB.prepare(
          'INSERT INTO sub (endpoint, p256dh, auth, lat, lon, nombre, creado, aviso, admin) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?) ' +
          'ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth, lat = excluded.lat, lon = excluded.lon, ' +
          'nombre = excluded.nombre, admin = MAX(sub.admin, excluded.admin)'
        ).bind(s.endpoint, k.p256dh, k.auth, lat, lon, nombre, ahora, admin).run();
        return json({ ok: true, admin: !!admin });
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
    ctx.waitUntil(Promise.all([
      revisarAvisos(env).catch(e => console.log('avisos:', e && e.message)),
      vigilar(env).catch(e => console.log('vigilancia:', e && e.message)),
    ]));
  },
};

async function resumen(env, lat, lon, ahora) {
  const r = await env.DB.prepare(
    'SELECT tipo, COUNT(*) AS n FROM rep WHERE ts > ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? GROUP BY tipo'
  ).bind(ahora - 3600, lat - 0.1, lat + 0.1, lon - 0.1, lon + 0.1).all();
  const o = { total: 0 };
  for (const x of r.results) { o[x.tipo] = x.n; o.total += x.n; }
  return o;
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
function tablaHora(cfg, conf, hora) {
  const h = +hora.slice(11, 13);
  if (cfg.franjas && cfg.franjas.activa) for (const n in conf.franjas) { const [a, b] = conf.franjas[n];
    if (h >= a && h < b && cfg.franjas.tablas && cfg.franjas.tablas[n]) return cfg.franjas.tablas[n]; }
  return cfg.tabla_lluvia;
}
function satelite(conf) {
  const A = conf.ahora; if (!A || !A.q) return null;
  const t = Date.parse(A.t + ':00-05:00'); if (!(Date.now() - t < 3 * 3600e3)) return null;
  const v = Uint8Array.from(atob(A.q), c => c.charCodeAt(0));
  return { t: A.t, en(lat, lon) {
    const iy = Math.floor((A.lat0 - lat) / A.paso), ix = Math.floor((lon - A.lon0) / A.paso); let m = null;
    for (let y = iy - 1; y <= iy + 1; y++) for (let x = ix - 1; x <= ix + 1; x++)
      if (y >= 0 && y < A.ny && x >= 0 && x < A.nx) m = Math.max(m ?? 0, v[y * A.nx + x] * A.escala);
    return m;
  } };
}
async function revisarAvisos(env) {
  if (!env.VAPID_PRIVATE || !env.VAPID_PUBLIC) return;
  const sitio = (env.SITIO || SITIO).replace(/\/+$/, '');
  const ahora = Math.floor(Date.now() / 1000);
  const subs = (await env.DB.prepare('SELECT * FROM sub WHERE aviso < ?').bind(ahora - HORAS_ENTRE_AVISOS * 3600).all()).results;
  if (!subs.length) return;
  const conf = await (await fetch(`${sitio}/push.json`, { cf: { cacheTtl: 300 } })).json();
  const sat = satelite(conf);
  const zonas = new Map();                                   // agrupa suscriptores cercanos (~5 km)
  for (const s of subs) { const k = `${(Math.round(s.lat * 20) / 20).toFixed(2)},${(Math.round(s.lon * 20) / 20).toFixed(2)}`;
    (zonas.get(k) || zonas.set(k, []).get(k)).push(s); }
  let envios = 0;
  for (const [k, lista] of [...zonas].slice(0, MAX_ZONAS)) {
    const [lat, lon] = k.split(',').map(Number);
    // 1) ¿está lloviendo ya? satélite y reportes de la gente de la última hora
    const mm = sat ? sat.en(lat, lon) : null;
    const rep = await resumen(env, lat, lon, ahora);
    const moja = (rep.lluvia || 0) + (rep.lluvia_fuerte || 0) + (rep.tormenta || 0) + 0.5 * (rep.llovizna || 0);
    const lluviaRep = rep.total >= 3 && moja / rep.total >= 0.6;
    let aviso = null;
    if ((mm != null && mm >= 1) || lluviaRep) {
      const quien = [mm != null && mm >= 0.5 ? 'el satélite' : '', lluviaRep ? `${rep.total} reportes de la gente` : ''].filter(Boolean).join(' y ');
      aviso = (nombre) => ({ title: `🌧️ Está lloviendo cerca de ${nombre}`, body: `Según ${quien}. ¡Lleva paraguas!`, url: sitio + '/', tag: 'lluvia' });
    } else {
      // 2) pronóstico de las próximas 2 horas
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
        body: `${mejor.p}% de probabilidad hacia las ${mejor.t.slice(11, 16)}. ¡Lleva paraguas!`, url: sitio + '/', tag: 'lluvia' });
    }
    if (!aviso) continue;
    for (const s of lista) {
      if (envios >= MAX_ENVIOS) return;
      const r = await enviarPush(s, aviso(s.nombre.split(',')[0]), env);
      envios++;
      if (r.status === 404 || r.status === 410) await env.DB.prepare('DELETE FROM sub WHERE endpoint = ?').bind(s.endpoint).run();
      else if (r.ok) await env.DB.prepare('UPDATE sub SET aviso = ? WHERE endpoint = ?').bind(ahora, s.endpoint).run();
    }
  }
}

// =================== VIGILANCIA: ¿la página se está actualizando? ===================
async function edadPagina(env) {
  const sitio = (env.SITIO || SITIO).replace(/\/+$/, '');
  try {
    const conf = await (await fetch(`${sitio}/push.json?t=${Date.now()}`, { cf: { cacheTtl: 0 } })).json();
    const t = Date.parse(conf.generado + ':00-05:00');
    return { generado: conf.generado, horas: (Date.now() - t) / 36e5 };
  } catch (e) { return { generado: null, horas: null }; }
}
async function vigilar(env) {
  const e = await edadPagina(env);
  if (e.horas != null && e.horas < HORAS_VIEJA) return;
  const ahora = Math.floor(Date.now() / 1000);
  const ult = await env.DB.prepare("SELECT v FROM estado WHERE k = 'vigilancia'").first().catch(() => null);
  if (ult && ahora - Number(ult.v) < HORAS_VIEJA * 3600) return;           // como mucho una vez cada 3 horas
  await env.DB.prepare("INSERT INTO estado (k, v) VALUES ('vigilancia', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v").bind(String(ahora)).run();
  let relanzado = false;
  if (env.GH_TOKEN) {
    const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, { method: 'POST',
      headers: { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: 'application/vnd.github+json', 'User-Agent': 'ClearSky-vigilancia',
                 'X-GitHub-Api-Version': '2022-11-28' }, body: JSON.stringify({ ref: 'main' }) });
    relanzado = r.status === 204;
  }
  if (!env.VAPID_PRIVATE) return;
  const admins = (await env.DB.prepare('SELECT * FROM sub WHERE admin = 1').all()).results;
  const cuanto = e.horas == null ? 'no responde' : `lleva ${Math.round(e.horas)} h sin actualizarse`;
  for (const s of admins) await enviarPush(s, { title: '⚠️ ClearSky ' + cuanto,
    body: relanzado ? 'Relancé la actualización automáticamente. Revisa GitHub Actions si sigue igual.' : 'Revisa GitHub Actions.',
    url: `https://github.com/${REPO}/actions`, tag: 'vigilancia' }, env).catch(() => {});
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
