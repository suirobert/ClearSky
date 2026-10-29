// Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
// Utilidades de las pruebas del servicio: carga feedback/worker.js y simula la base D1 con SQLite en memoria (con transacciones).
import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const aqui = path.dirname(fileURLToPath(import.meta.url));
export const RAIZ = path.resolve(aqui, '..', '..');

export async function cargarWorker() {
  // worker.js usa sintaxis de módulos pero se pega tal cual en Cloudflare; para importarlo aquí se copia con extensión .mjs
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'clearsky-worker-'));
  const destino = path.join(tmp, 'worker.mjs');
  fs.copyFileSync(path.join(RAIZ, 'feedback', 'worker.js'), destino);
  return import(pathToFileURL(destino).href);
}

export function esquema({ sinColumnaH = false, sinColumnaPref = false } = {}) {
  let sql = fs.readFileSync(path.join(RAIZ, 'feedback', 'schema.sql'), 'utf8');
  if (sinColumnaH) sql = sql.replace(', tipo TEXT NOT NULL, h TEXT);', ', tipo TEXT NOT NULL);');
  if (sinColumnaPref) sql = sql.replace(', pref INTEGER NOT NULL DEFAULT 7);', ');');
  return sql;
}

export function nuevaBase(opciones = {}) {
  const db = new DatabaseSync(':memory:');
  db.exec(esquema(opciones));
  const preparar = (sql) => {
    const st = {
      args: [],
      bind(...a) { st.args = a; return st; },
      first: async () => db.prepare(sql).get(...st.args) ?? null,
      all: async () => ({ results: db.prepare(sql).all(...st.args).map(r => ({ ...r })) }),
      run: async () => db.prepare(sql).run(...st.args),
    };
    return st;
  };
  const D1 = {
    prepare: preparar,
    batch: async (lista) => {                          // D1 ejecuta el lote como una transacción: si algo falla, no queda nada a medias
      db.exec('BEGIN');
      try { for (const s of lista) await s.run(); db.exec('COMMIT'); }
      catch (e) { db.exec('ROLLBACK'); throw e; }
    },
  };
  return { db, D1 };
}

export function entorno(D1, extra = {}) {
  return { DB: D1, TOKEN: 'T', SAL: 'sal-de-prueba', ORIGEN: '*', GH_TOKEN: 'gh', ...extra };
}

export function llamador(W, env) {
  return async (ruta, { metodo = 'GET', cuerpo, ip = '1.1.1.1', cabeceras = {} } = {}) => {
    const init = { method: cuerpo ? 'POST' : metodo, headers: { 'CF-Connecting-IP': ip, ...cabeceras } };
    if (cuerpo) init.body = JSON.stringify(cuerpo);
    const r = await W.default.fetch(new Request('https://w.test' + ruta, init), env);
    return [r.status, await r.json()];
  };
}

export const ahoraS = () => Math.floor(Date.now() / 1000);
// Guayaquil
export const GYE = { lat: -2.17, lon: -79.9 };
