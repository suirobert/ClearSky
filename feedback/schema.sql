-- ClearSky: reportes de usuarios
CREATE TABLE IF NOT EXISTS reportes (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      INTEGER NOT NULL,                 -- hora del reporte (Unix, UTC)
  lat     REAL    NOT NULL,                 -- redondeada a 0.01° (~1 km)
  lon     REAL    NOT NULL,
  tipo    TEXT    NOT NULL CHECK (tipo IN ('lluvia', 'llovizna', 'seca')),
  h_celda TEXT,                             -- código anti-repetición (se borra a las 48 h)
  h_ip    TEXT                              -- código anti-repetición (se borra a las 48 h)
);
CREATE INDEX IF NOT EXISTS idx_reportes_ts    ON reportes (ts);
CREATE INDEX IF NOT EXISTS idx_reportes_celda ON reportes (h_celda, ts);
CREATE INDEX IF NOT EXISTS idx_reportes_ip    ON reportes (h_ip, ts);
CREATE INDEX IF NOT EXISTS idx_reportes_pos   ON reportes (lat, lon, ts);
