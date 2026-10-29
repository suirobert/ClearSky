-- ClearSky: reportes + avisos (con preferencias) + vigilancia
-- Para una base NUEVA: ejecutar este archivo completo en D1 -> Console.
-- Para una base que ya existe: ejecutar solo las dos líneas marcadas "MIGRACIÓN" (una sola vez cada una).
CREATE TABLE IF NOT EXISTS rep (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, tipo TEXT NOT NULL, h TEXT);
CREATE INDEX IF NOT EXISTS idx_rep_ts ON rep (ts);
CREATE INDEX IF NOT EXISTS idx_rep_tipo_ts ON rep (tipo, ts);
CREATE TABLE IF NOT EXISTS lim (h TEXT PRIMARY KEY, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sub (endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, nombre TEXT, creado INTEGER NOT NULL, aviso INTEGER NOT NULL DEFAULT 0, admin INTEGER NOT NULL DEFAULT 0, pref INTEGER NOT NULL DEFAULT 7);
CREATE TABLE IF NOT EXISTS estado (k TEXT PRIMARY KEY, v TEXT);
-- MIGRACIÓN (v20): código irreversible de la persona, solo en reportes de truenos e inundación; se borra a las 24 h.
-- ALTER TABLE rep ADD COLUMN h TEXT;
-- MIGRACIÓN (v16): preferencias de avisos.
-- ALTER TABLE sub ADD COLUMN pref INTEGER NOT NULL DEFAULT 7;
