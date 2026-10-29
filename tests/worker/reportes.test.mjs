// Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
// Pruebas del servicio de reportes: clima, truenos e inundación (límites, personas distintas, radios, ventanas, privacidad).
import test from 'node:test';
import assert from 'node:assert/strict';
import { cargarWorker, nuevaBase, entorno, llamador, ahoraS, GYE } from './ayuda.mjs';

const W = await cargarWorker();
function servicio(opciones) {
  const { db, D1 } = nuevaBase(opciones);
  const env = entorno(D1);
  return { db, env, api: llamador(W, env) };
}
const reportar = (api, tipo, ip, extra = {}) => api('/reporte', { cuerpo: { lat: GYE.lat, lon: GYE.lon, tipo, ...extra }, ip });
const insertar = (db, tipo, lat, lon, haceS, h = null) =>
  db.prepare('INSERT INTO rep (ts, lat, lon, tipo, h) VALUES (?, ?, ?, ?, ?)').run(ahoraS() - haceS, lat, lon, tipo, h);

test('acepta los tipos de clima, "truenos" e "inundado", y rechaza lo demás', async () => {
  const { api } = servicio();
  let i = 0;
  for (const t of ['soleado', 'despejado', 'nublado', 'llovizna', 'lluvia', 'lluvia_fuerte', 'tormenta', 'truenos', 'inundado'])
    assert.deepEqual(await reportar(api, t, `10.0.0.${++i}`), [200, { ok: true }], t);
  assert.equal((await reportar(api, 'granizo', '10.0.1.1'))[0], 400);
  assert.equal((await reportar(api, '', '10.0.1.2'))[0], 400);
  assert.equal((await api('/reporte', { cuerpo: { lat: 40, lon: -3, tipo: 'inundado' }, ip: '10.0.1.3' }))[0], 400, 'fuera de Ecuador');
});

test('cada categoría tiene su propio límite: clima, truenos e inundación no se bloquean entre sí', async () => {
  const { api } = servicio();
  assert.equal((await reportar(api, 'lluvia', '2.2.2.2'))[0], 200);
  assert.equal((await reportar(api, 'truenos', '2.2.2.2'))[0], 200, 'después de un reporte de clima se puede reportar truenos');
  assert.equal((await reportar(api, 'inundado', '2.2.2.2'))[0], 200, 'y también inundación');
  assert.equal((await reportar(api, 'lluvia', '2.2.2.2'))[0], 429, 'otro de clima: espera');
  assert.equal((await reportar(api, 'truenos', '2.2.2.2'))[0], 429, 'otro de truenos: espera');
  assert.equal((await reportar(api, 'inundado', '2.2.2.2'))[0], 429, 'otro de inundación: espera');
  assert.equal((await reportar(api, 'inundado', '3.3.3.3'))[0], 200, 'otra persona sí puede');
});

test('ventanas de espera: clima 30 min; truenos e inundación 10 min', async () => {
  const { api, db } = servicio();
  await reportar(api, 'lluvia', '4.4.4.4'); await reportar(api, 'truenos', '4.4.4.4');
  db.exec(`UPDATE lim SET ts = ${ahoraS()} - 700`);                    // hace ~11 min
  assert.equal((await reportar(api, 'truenos', '4.4.4.4'))[0], 200, 'truenos ya puede (pasaron 10 min)');
  assert.equal((await reportar(api, 'lluvia', '4.4.4.4'))[0], 429, 'clima todavía no (faltan 30 min)');
  db.exec(`UPDATE lim SET ts = ${ahoraS()} - 1900`);                   // hace ~32 min
  assert.equal((await reportar(api, 'lluvia', '4.4.4.4'))[0], 200, 'clima ya puede');
});

test('"resumen" (la lluvia que ve la gente) NO cuenta truenos ni inundación', async () => {
  const { api } = servicio();
  await reportar(api, 'lluvia', '5.5.5.1'); await reportar(api, 'soleado', '5.5.5.2');
  for (let i = 0; i < 5; i++) { await reportar(api, 'truenos', `5.5.6.${i}`); await reportar(api, 'inundado', `5.5.7.${i}`); }
  const [, r] = await api(`/resumen?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.deepEqual(r, { total: 2, lluvia: 1, soleado: 1 }, 'solo clima: si no, diluiría la evidencia de lluvia');
});

test('inundación: un solo reporte NO se muestra; dos personas distintas sí', async () => {
  const { api } = servicio();
  await reportar(api, 'inundado', '6.0.0.1');
  let [, a] = await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.equal(a.inundado.n, 1); assert.equal(a.inundado.personas, 1); assert.equal(a.inundado.ok, false);
  assert.equal(a.puntos.length, 0, 'sin suficiente gente no hay punto en el mapa');
  await reportar(api, 'inundado', '6.0.0.2');
  [, a] = await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.equal(a.inundado.personas, 2); assert.equal(a.inundado.ok, true);
  assert.equal(a.puntos.length, 1); assert.equal(a.puntos[0].personas, 2);
});

test('la misma persona reportando varias veces cuenta como UNA (no se puede inflar)', async () => {
  const { api, db } = servicio();
  await reportar(api, 'inundado', '7.7.7.7');
  db.exec(`UPDATE lim SET ts = ${ahoraS()} - 1000`);
  await reportar(api, 'inundado', '7.7.7.7');
  db.exec(`UPDATE lim SET ts = ${ahoraS()} - 1000`);
  await reportar(api, 'inundado', '7.7.7.7');
  const [, a] = await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.equal(a.inundado.n, 3, 'tres reportes');
  assert.equal(a.inundado.personas, 1, 'de una sola persona');
  assert.equal(a.inundado.ok, false);
});

test('radios: inundación 2 km, truenos 10 km', async () => {
  const { api, db } = servicio();
  insertar(db, 'inundado', GYE.lat, GYE.lon, 60, 'a1');
  insertar(db, 'inundado', GYE.lat + 0.045, GYE.lon, 60, 'a2');        // ~5 km: fuera del radio de 2 km
  let [, a] = await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.equal(a.inundado.personas, 1, 'el reporte a 5 km no cuenta'); assert.equal(a.inundado.ok, false);
  insertar(db, 'inundado', GYE.lat + 0.01, GYE.lon, 60, 'a3');         // ~1,1 km
  [, a] = await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.equal(a.inundado.personas, 2); assert.equal(a.inundado.ok, true);
  insertar(db, 'truenos', GYE.lat + 0.07, GYE.lon, 60, 'b1');          // ~7,8 km: dentro de 10 km
  insertar(db, 'truenos', GYE.lat + 0.11, GYE.lon, 60, 'b2');          // ~12 km: fuera
  [, a] = await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.equal(a.truenos.personas, 1); assert.equal(a.truenos.ok, false);
  insertar(db, 'truenos', GYE.lat - 0.05, GYE.lon, 60, 'b3');          // ~5,5 km
  [, a] = await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.equal(a.truenos.ok, true); assert.equal(a.truenos.personas, 2);
});

test('vigencia: los truenos duran 30 min y la inundación 3 horas', async () => {
  const { api, db } = servicio();
  insertar(db, 'truenos', GYE.lat, GYE.lon, 20 * 60, 'c1'); insertar(db, 'truenos', GYE.lat, GYE.lon, 40 * 60, 'c2');
  insertar(db, 'inundado', GYE.lat, GYE.lon, 2 * 3600, 'd1'); insertar(db, 'inundado', GYE.lat, GYE.lon, 4 * 3600, 'd2');
  const [, a] = await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.equal(a.truenos.personas, 1, 'el de hace 40 min ya no cuenta'); assert.equal(a.truenos.ok, false);
  assert.equal(a.inundado.personas, 1, 'el de hace 4 h ya no cuenta'); assert.equal(a.inundado.ok, false);
  assert.ok(a.truenos.ts <= ahoraS() - 19 * 60 && a.truenos.ts >= ahoraS() - 21 * 60, 'devuelve la hora del último reporte');
});

test('privacidad: el código de persona solo se guarda en truenos/inundación y se borra a las 24 h; /export nunca lo incluye', async () => {
  const { api, db } = servicio();
  await reportar(api, 'lluvia', '8.8.8.1'); await reportar(api, 'truenos', '8.8.8.2'); await reportar(api, 'inundado', '8.8.8.3');
  const filas = db.prepare('SELECT tipo, h FROM rep ORDER BY id').all();
  assert.equal(filas[0].h, null, 'el clima no guarda código');
  assert.match(filas[1].h, /^[0-9a-f]{16}$/); assert.match(filas[2].h, /^[0-9a-f]{16}$/);
  assert.notEqual(filas[1].h, filas[2].h, 'personas distintas, códigos distintos');
  assert.ok(!JSON.stringify(db.prepare('SELECT * FROM rep').all()).includes('8.8.8'), 'la IP no aparece en ningún lado de la tabla de reportes');
  assert.ok(!JSON.stringify(db.prepare('SELECT * FROM lim').all()).includes('8.8.8'), 'ni en la de límites');
  insertar(db, 'inundado', GYE.lat, GYE.lon, 25 * 3600, 'viejo');       // hace 25 h con código
  await reportar(api, 'inundado', '8.8.8.4');                           // un reporte nuevo dispara la limpieza
  assert.equal(db.prepare("SELECT h FROM rep WHERE h = 'viejo'").all().length, 0, 'el código de hace 25 h se borró');
  const [e1, sinClave] = await api('/export?despues=0'); assert.equal(e1, 401); assert.ok(sinClave.error);
  const [e2, exp] = await api('/export?despues=0', { cabeceras: { Authorization: 'Bearer T' } });
  assert.equal(e2, 200); assert.ok(exp.length >= 4);
  assert.ok(exp.every(f => !('h' in f)), '/export no expone el código de persona');
  assert.deepEqual(Object.keys(exp[0]).sort(), ['id', 'lat', 'lon', 'tipo', 'ts']);
});

test('mapa: junta reportes a menos de 1 km y solo muestra puntos con suficientes personas', async () => {
  const { api, db } = servicio();
  insertar(db, 'inundado', -2.170, -79.900, 60, 'p1'); insertar(db, 'inundado', -2.172, -79.901, 60, 'p2'); insertar(db, 'inundado', -2.171, -79.899, 60, 'p3');   // un punto con 3 personas
  insertar(db, 'inundado', -2.230, -79.950, 60, 'q1'); insertar(db, 'inundado', -2.231, -79.951, 60, 'q2');                                                  // otro punto con 2
  insertar(db, 'inundado', -2.300, -79.800, 60, 'r1');                                                                                                      // uno solitario: no se muestra
  const [, a] = await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.equal(a.puntos.length, 2, 'dos puntos con suficiente gente; el solitario no');
  assert.deepEqual(a.puntos.map(p => p.personas).sort(), [2, 3]);
  assert.ok(a.puntos.every(p => typeof p.lat === 'number' && typeof p.ts === 'number'));
});

test('si todavía no existe la columna "h": los reportes se guardan igual y se exigen 3 reportes (no se pueden distinguir personas)', async () => {
  const { api, db } = servicio({ sinColumnaH: true });
  assert.equal((await reportar(api, 'inundado', '9.0.0.1'))[0], 200, 'el reporte se guarda sin la columna');
  await reportar(api, 'inundado', '9.0.0.2');
  let [, a] = await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.equal(a.inundado.n, 2); assert.equal(a.inundado.personas, null); assert.equal(a.inundado.ok, false, 'con 2 reportes sin código no alcanza');
  assert.equal(a.codigo_persona, false);
  await reportar(api, 'inundado', '9.0.0.3');
  [, a] = await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`);
  assert.equal(a.inundado.ok, true, 'con 3 sí');
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM rep').get().n, 3, 'nada se perdió por la columna que falta');
});

test('entradas inválidas en /alertas', async () => {
  const { api } = servicio();
  assert.equal((await api('/alertas'))[0], 400);
  assert.equal((await api('/alertas?lat=abc&lon=1'))[0], 400);
  assert.equal((await api('/alertas?lat=40.7&lon=-74'))[0], 400, 'fuera de Ecuador');
  assert.equal((await api(`/alertas?lat=${GYE.lat}&lon=${GYE.lon}`))[0], 200);
});
