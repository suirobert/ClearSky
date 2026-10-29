-- ClearSky reportes v2
CREATE TABLE IF NOT EXISTS rep (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  ts   INTEGER NOT NULL,          -- hora UTC (segundos)
  lat  REAL    NOT NULL,          -- redondeada a 0,01° (~1 km)
  lon  REAL    NOT NULL,
  tipo TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rep_ts ON rep (ts);
CREATE TABLE IF NOT EXISTS lim (
  h  TEXT PRIMARY KEY,            -- código irreversible para limitar reportes repetidos
  ts INTEGER NOT NULL
);
