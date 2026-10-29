// Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
// Pruebas de lo que ya existía: cifrado de las notificaciones, suscripciones, cron de 10 min, avisos de tormenta, oficiales y lluvia en camino.
import test from 'node:test';
import assert from 'node:assert/strict';
import nodecrypto from 'node:crypto';
import { cargarWorker, nuevaBase, entorno, llamador, GYE } from './ayuda.mjs';

const W = await cargarWorker();
const hEc = (min) => new Date(Date.now() - 5 * 3600e3 + min * 60e3).toISOString().slice(0, 16);   // "2026-10-29T02:50" en hora de Ecuador

// ---- un "teléfono" de prueba: genera claves y descifra lo que le llega (RFC 8291) ----
const tel = nodecrypto.createECDH('prime256v1'); tel.generateKeys();
const authTel = nodecrypto.randomBytes(16);
const claves = { p256dh: tel.getPublicKey('base64url'), auth: authTel.toString('base64url') };
function descifrar(cuerpo) {
  const salt = cuerpo.subarray(0, 16), idlen = cuerpo[20], asPub = cuerpo.subarray(21, 21 + idlen), ct = cuerpo.subarray(21 + idlen);
  const ikm = Buffer.from(nodecrypto.hkdfSync('sha256', tel.computeSecret(asPub), authTel, Buffer.concat([Buffer.from('WebPush: info\0'), tel.getPublicKey(), asPub]), 32));
  const cek = Buffer.from(nodecrypto.hkdfSync('sha256', ikm, salt, Buffer.from('Content-Encoding: aes128gcm\0'), 16));
  const nonce = Buffer.from(nodecrypto.hkdfSync('sha256', ikm, salt, Buffer.from('Content-Encoding: nonce\0'), 12));
  const d = nodecrypto.createDecipheriv('aes-128-gcm', cek, nonce); d.setAuthTag(ct.subarray(ct.length - 16));
  const pt = Buffer.concat([d.update(ct.subarray(0, ct.length - 16)), d.final()]);
  return JSON.parse(pt.subarray(0, pt.lastIndexOf(2)).toString());
}

async function conVapid(opciones) {
  const { db, D1 } = nuevaBase(opciones); const env = entorno(D1); const api = llamador(W, env);
  const [, k] = await api('/vapid-nuevo', { cabeceras: { Authorization: 'Bearer T' } });
  env.VAPID_PUBLIC = k.VAPID_PUBLIC; env.VAPID_PRIVATE = k.VAPID_PRIVATE;
  return { db, env, api };
}
const suscribir = (api, nombre, lat, lon, extra = {}) =>
  api('/suscribir', { cuerpo: { sub: { endpoint: 'https://push.test/' + nombre, keys: claves }, lat, lon, nombre, ...extra } });

// ---- red simulada ----
let AH = null, ENVIADOS = [], GITHUB = [];
const conf = { modelos: ['a', 'b', 'c', 'd', 'e', 'f', 'g'], franjas: {}, ahora: null, ciudades: [
  { nombre: 'Guayaquil', lat: -2.1894, lon: -79.8891, zona: 'costa', propia: true, cfg: { pesos: {}, umbral: 0.5, tabla_lluvia: [] } },
  { nombre: 'Quito', lat: -0.1807, lon: -78.4678, zona: 'sierra', propia: true, cfg: { pesos: {}, umbral: 0.5, tabla_lluvia: [] } }] };
const fetchOriginal = globalThis.fetch;
globalThis.fetch = async (u, o = {}) => {
  u = String(u);
  if (u.includes('raw.githubusercontent.com')) return new Response(JSON.stringify(AH));
  if (u.includes('/push.json')) return new Response(JSON.stringify({ ...conf, generado: hEc(-5) }));
  if (u.includes('open-meteo')) { const hh = { time: ['2026-10-29T19:00', '2026-10-29T20:00', '2026-10-29T21:00'] }; for (const m of conf.modelos) hh[`precipitation_${m}`] = [0, 0, 0]; return new Response(JSON.stringify({ elevation: 6, hourly: hh })); }
  if (u.startsWith('https://push.test')) { ENVIADOS.push({ a: u.split('/').pop(), aviso: descifrar(Buffer.from(o.body)) }); return new Response('', { status: 201 }); }
  if (u.includes('api.github.com')) { GITHUB.push({ url: u, auth: o.headers && o.headers.Authorization, body: o.body }); return new Response(null, { status: 204 }); }
  throw new Error('red no simulada: ' + u);
};
test.after(() => { globalThis.fetch = fetchOriginal; });
const correr = async (env, cron) => { ENVIADOS = []; GITHUB = []; await new Promise(ok => W.default.scheduled({ cron }, env, { waitUntil: p => p.then(ok) })); return ENVIADOS; };

function grilla(centroLat, centroLon, sigma = 9, pico = 6) {            // lluvia del satélite: una mancha gaussiana
  const LAT0 = 2.5, LON0 = -92.5, P = 0.1, ny = 80, nx = 175, q = new Uint8Array(ny * nx);
  for (let y = 0; y < ny; y++) for (let x = 0; x < nx; x++) {
    const la = LAT0 - (y + 0.5) * P, lo = LON0 + (x + 0.5) * P;
    const d2 = ((la - centroLat) * 111.32) ** 2 + ((lo - centroLon) * 111.32 * Math.cos(la * Math.PI / 180)) ** 2, f = pico * Math.exp(-d2 / (2 * sigma * sigma));
    q[y * nx + x] = f < 0.3 ? 0 : Math.min(255, Math.round(f * 2));
  }
  return { t: hEc(-8), lat0: LAT0, lon0: LON0, paso: P, ny, nx, escala: 0.5, q: Buffer.from(q).toString('base64') };
}
const movimiento = (u) => ({ tile: 2, lat0: 2.5, lon0: -92.5, ny: 4, nx: 9, u: Array(4).fill(Array(9).fill(u)), v: Array(4).fill(Array(9).fill(0)) });
const ahoraBase = (extra = {}) => ({ generado: hEc(-1), lluvia: null, movimiento: null, rayos: null, oficial: null, nowcast: null, errores: [], ...extra });

test('las notificaciones se cifran de forma que el teléfono puede leerlas (RFC 8291)', async () => {
  const mensaje = JSON.stringify({ title: '⚡ Prueba', body: 'con tildes y emojis 🌧️' });
  const cuerpo = Buffer.from(await W.cifrar(mensaje, claves.p256dh, claves.auth));
  assert.deepEqual(descifrar(cuerpo), JSON.parse(mensaje));
});

test('suscripciones: preferencias, valores inválidos, base antigua sin la columna "pref", y cancelar', async () => {
  const { db, api } = await conVapid();
  assert.equal((await suscribir(api, 'todo', GYE.lat, GYE.lon))[1].prefs, 7, 'sin prefs: todos los avisos');
  assert.equal((await suscribir(api, 'solo2', GYE.lat, GYE.lon, { prefs: 2 }))[1].prefs, 2);
  for (const mal of [99, 0, 'x', -3]) assert.equal((await suscribir(api, 'raro', GYE.lat, GYE.lon, { prefs: mal }))[1].prefs, 7, `prefs ${mal} -> 7`);
  assert.equal((await suscribir(api, 'afuera', 40, -3))[0], 400, 'fuera de Ecuador');
  assert.equal((await suscribir(api, 'adm', GYE.lat, GYE.lon, { admin: 'mala' }))[0], 403);
  assert.equal((await suscribir(api, 'adm', GYE.lat, GYE.lon, { admin: 'T' }))[1].admin, true);
  await api('/desuscribir', { cuerpo: { endpoint: 'https://push.test/solo2' } });
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM sub WHERE endpoint LIKE '%solo2'").get().n, 0);
  const viejo = await conVapid({ sinColumnaPref: true });
  assert.deepEqual((await suscribir(viejo.api, 'viejo', GYE.lat, GYE.lon, { prefs: 1 }))[1].ok, true, 'la base sin "pref" sigue funcionando');
});

test('cron de 10 min: solo lanza ahora.yml en GitHub (con el token) y anota el resultado; el de 30 min hace los avisos', async () => {
  const { db, env } = await conVapid(); AH = ahoraBase();
  const enviados = await correr(env, '*/10 * * * *');
  assert.equal(GITHUB.length, 1); assert.ok(GITHUB[0].url.endsWith('/actions/workflows/ahora.yml/dispatches'));
  assert.equal(GITHUB[0].auth, 'Bearer gh'); assert.equal(JSON.parse(GITHUB[0].body).ref, 'main'); assert.equal(enviados.length, 0);
  assert.match(db.prepare("SELECT v FROM estado WHERE k = 'dispatch-ahora'").get().v, /204 ok$/);
  await correr(env, '*/30 * * * *');
  assert.ok(!GITHUB.some(g => g.url.includes('/ahora.yml/')), 'el cron de 30 min no lanza ahora.yml');
  env.GH_TOKEN = ''; await correr(env, '*/10 * * * *');
  assert.match(db.prepare("SELECT v FROM estado WHERE k = 'dispatch-ahora'").get().v, /sin GH_TOKEN$/, 'sin token: lo dice, no rompe');
});

test('tormenta: 5 o más rayos en 20 min avisan solo a quien eligió tormentas, y no se repite antes de 2 h', async () => {
  const { env, api } = await conVapid();
  await suscribir(api, 'tormentas', -2.21, -80.96, { nombre: 'Salinas', prefs: 2 });
  await suscribir(api, 'solo_lluvia', -2.21, -80.96, { nombre: 'Salinas', prefs: 1 });
  AH = ahoraBase({ rayos: { t: hEc(-2), radio_km: 25, celdas: [[-2.2, -80.95, 15, 18]] } });
  const e1 = await correr(env, '*/30 * * * *');
  assert.deepEqual(e1.map(e => e.a), ['tormentas']); assert.match(e1[0].aviso.title, /Tormenta eléctrica cerca de Salinas/);
  assert.equal((await correr(env, '*/30 * * * *')).length, 0, 'no se repite');
  AH = ahoraBase({ rayos: { t: hEc(-40), radio_km: 25, celdas: [[-2.2, -80.95, 15, 18]] } });   // dato de hace 40 min: no es "ahora"
  assert.equal((await correr(env, '*/30 * * * *')).length, 0);
});

test('aviso oficial del INAMHI: una sola vez por boletín, solo a quien lo quiere y si afecta su región', async () => {
  const { db, env, api } = await conVapid();
  await suscribir(api, 'gye', GYE.lat, GYE.lon, { prefs: 4 }); await suscribir(api, 'sin_oficial', GYE.lat, GYE.lon, { prefs: 3 });
  await suscribir(api, 'quito', -0.18, -78.47, { prefs: 4 });
  const oficial = (id, zonas) => ({ id, estatus: 'Advertencia', nivel: 'MEDIO – MUY ALTO', fenomeno: 'LLUVIAS', desde: hEc(60), hasta: hEc(3000), zonas });
  AH = ahoraBase({ oficial: oficial('84', { litoral: 'x' }) });
  const e1 = await correr(env, '*/30 * * * *');
  assert.deepEqual(e1.map(e => e.a), ['gye'], 'Quito (sierra) no está en el aviso; el otro no quiso avisos oficiales');
  assert.match(e1[0].aviso.body, /amenaza media a muy alta/);
  assert.equal((await correr(env, '*/30 * * * *')).length, 0, 'el mismo boletín no se repite');
  AH = ahoraBase({ oficial: { ...oficial('85', { litoral: 'x' }), hasta: hEc(-10) } });
  assert.equal((await correr(env, '*/30 * * * *')).length, 0, 'un boletín vencido no avisa');
});

test('lluvia en camino: solo avisa si la función está "verificada" (en pruebas u oculta no)', async () => {
  const { db, env, api } = await conVapid();
  await suscribir(api, 'gye', GYE.lat, GYE.lon, { prefs: 1 });
  const base = { lluvia: grilla(-2.17, -79.9 - 42 / 111.3), movimiento: movimiento(30) };       // lluvia a ~42 km al oeste, hacia el este a 30 km/h
  for (const estadoNc of ['pruebas', 'oculto', null]) {
    db.exec('UPDATE sub SET aviso = 0'); AH = ahoraBase({ ...base, nowcast: estadoNc && { estado: estadoNc } });
    assert.equal((await correr(env, '*/30 * * * *')).length, 0, `estado ${estadoNc}: no avisa`);
  }
  db.exec('UPDATE sub SET aviso = 0'); AH = ahoraBase({ ...base, nowcast: { estado: 'verificado' } });
  const e = await correr(env, '*/30 * * * *');
  assert.equal(e.length, 1); assert.match(e[0].aviso.title, /La lluvia llegaría a gye en ~40 min/); assert.match(e[0].aviso.url, /\?q=ll$/, 'abre la pregunta ¿Llovió donde estás?');
  db.exec('UPDATE sub SET aviso = 0'); AH = ahoraBase({ ...base, movimiento: null, nowcast: { estado: 'verificado' } });
  assert.equal((await correr(env, '*/30 * * * *')).length, 0, 'sin movimiento validado tampoco avisa');
});
