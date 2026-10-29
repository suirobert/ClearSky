# -*- coding: utf-8 -*-
"""
ClearSky - pronóstico del tiempo verificado para Ecuador
--------------------------------------------------------
Datos:
  - 7 modelos meteorológicos (Open-Meteo) combinados con pesos según su acierto en cada ciudad.
  - Verificación diaria contra los METAR de los aeropuertos (±1 h), con examen sin trampa:
    solo se aceptan pesos y calibraciones que mejoran el pronóstico en días no usados para aprender.
  - Calibración adaptable a cambios de temporada (completa / reciente / mezcla / sin ajuste).
  - Memoria permanente de observaciones y seguimiento real de lo que se publicó.
  - Tormentas eléctricas verificadas y calibradas con los truenos (TS) que reportan los aeropuertos.
  - Verificación a 1-7 días de anticipación y calibración por franja horaria (solo si mejora en el examen).
  - Alertas de cambio brusco (datos/alertas.json, con texto listo para redes) y respaldo semanal de reportes.
Página (index.html, generada aquí y dibujada con JavaScript):
  - Primero "Mi ubicación" (el navegador calcula el pronóstico del punto del usuario con la
    configuración aprendida en la ciudad verificada más parecida); las ciudades van en una lista aparte.
  - Ícono del cielo según el sol que llega de verdad (las nubes altas finas ya no dan "nublado").
  - Modo claro y oscuro automático según el dispositivo, con botón para elegirlo a mano.
  - 7 días (del 4 al 7 orientativos) y reportes de usuarios verificados contra lo publicado.
  - Diseño compacto (dos columnas en PC), barrio por OpenStreetMap, avisos en el teléfono y compartir.
  - Temperatura y humedad verificadas cada hora contra el aeropuerto y corregidas (solo si mejora en el examen).
  - Calor: sensación térmica (índice de calor NOAA), alerta desde 32° y onda de calor frente a lo normal
    de los últimos 30 días; humedad, viento y UV por hora. Botón para instalar como app.
  - Mapa de lluvia por horas, satélite GOES, íconos animados Meteocons, gráfica y barra que se
    pueden arrastrar con el dedo, instalable como app (PWA) y con uso sin conexión.

Solo librería estándar de Python 3.8+.
Uso:
  python clima_consenso.py              -> genera y abre la página
  python clima_consenso.py --no-open    -> solo genera (GitHub Actions)
  python clima_consenso.py --verificar  -> fuerza la verificación ahora
Variables de entorno:
  CC_DATOS         carpeta de datos (por defecto ./datos)
  CC_HTML          página generada  (por defecto ./dashboard.html)
  CC_FEEDBACK_URL    dirección del servicio de reportes y avisos (vacío = botones ocultos)
  CC_FEEDBACK_TOKEN  clave para descargar los reportes
"""
import csv, glob, io, json, math, os, re, sys, time, statistics, webbrowser, urllib.request, urllib.parse
from datetime import datetime, date, timedelta

os.environ["TZ"] = "America/Guayaquil"          # hora de Ecuador aunque el servidor esté en UTC
if hasattr(time, "tzset"):
    time.tzset()

BASE = os.path.dirname(os.path.abspath(sys.argv[0]))
DATOS = os.path.abspath(os.environ.get("CC_DATOS") or os.path.join(BASE, "datos"))
os.makedirs(os.path.join(DATOS, "historial"), exist_ok=True)
os.makedirs(os.path.join(DATOS, "observaciones"), exist_ok=True)

# ======================= CONFIGURACIÓN =======================
# lat/lon = punto del pronóstico | metar/est = aeropuerto usado para verificar
# zona = costa / sierra (para elegir la ciudad de referencia de otros lugares)
# cob_min = mínimo de horas con reporte del aeropuerto para aceptar cambios
#           (los aeropuertos regionales cierran de noche, por eso es menor)
CIUDADES = {
    "Guayaquil":  {"lat": -2.1700, "lon": -79.9224, "metar": "SEGU", "est": (-2.1578, -79.8836), "zona": "costa",  "cob_min": 0.70},
    "Quito":      {"lat": -0.1807, "lon": -78.4678, "metar": "SEQM", "est": (-0.1411, -78.4881), "zona": "sierra", "cob_min": 0.70},
    "Manta":      {"lat": -0.9677, "lon": -80.7089, "metar": "SEMT", "est": (-0.9458, -80.6786), "zona": "costa",  "cob_min": 0.30},
    "Salinas":    {"lat": -2.2146, "lon": -80.9520, "metar": "SESA", "est": (-2.2050, -80.9889), "zona": "costa",  "cob_min": 0.30},
    "Esmeraldas": {"lat":  0.9682, "lon": -79.6517, "metar": "SETN", "est": ( 0.9786, -79.6267), "zona": "costa",  "cob_min": 0.30},
    "Cuenca":     {"lat": -2.9001, "lon": -79.0059, "metar": "SECU", "est": (-2.8894, -78.9844), "zona": "sierra", "cob_min": 0.30},
    # v12: más ciudades. Si un aeropuerto no tiene suficientes reportes, la ciudad usa lo aprendido en la
    # verificada más cercana de su zona. metar None = solo pronóstico (sin aeropuerto propio).
    "Machala":       {"lat": -3.2581, "lon": -79.9554, "metar": "SETR", "est": (-3.4419, -79.9969), "zona": "costa",     "cob_min": 0.25},
    "Portoviejo":    {"lat": -1.0546, "lon": -80.4545, "metar": None,   "est": None,                "zona": "costa",     "cob_min": 0},
    "Santo Domingo": {"lat": -0.2530, "lon": -79.1716, "metar": "SESD", "est": (-0.2482, -79.2144), "zona": "costa",     "cob_min": 0.25},
    "Loja":          {"lat": -3.9931, "lon": -79.2042, "metar": "SETM", "est": (-3.9959, -79.3719), "zona": "sierra",    "cob_min": 0.25},
    "Ambato":        {"lat": -1.2491, "lon": -78.6168, "metar": "SEAM", "est": (-1.2121, -78.5740), "zona": "sierra",    "cob_min": 0.25},
    "Latacunga":     {"lat": -0.9352, "lon": -78.6155, "metar": "SELT", "est": (-0.9068, -78.6158), "zona": "sierra",    "cob_min": 0.25},
    "Coca":          {"lat": -0.4664, "lon": -76.9871, "metar": "SECO", "est": (-0.4629, -76.9868), "zona": "oriente",   "cob_min": 0.25},
    "Lago Agrio":    {"lat":  0.0849, "lon": -76.8828, "metar": "SENL", "est": ( 0.0931, -76.8675), "zona": "oriente",   "cob_min": 0.25},
    "Macas":         {"lat": -2.3087, "lon": -78.1171, "metar": "SEMC", "est": (-2.2992, -78.1208), "zona": "oriente",   "cob_min": 0.25},
    "Galápagos":     {"lat": -0.7432, "lon": -90.3136, "metar": "SEGS", "est": (-0.4538, -90.2659), "zona": "galapagos", "cob_min": 0.25},
}
AEROPUERTOS = {"SEGU": "aeropuerto de Guayaquil", "SEQM": "aeropuerto de Quito (Tababela)",
               "SEMT": "aeropuerto de Manta", "SESA": "aeropuerto de Salinas",
               "SETN": "aeropuerto de Esmeraldas (Tachina)", "SECU": "aeropuerto de Cuenca",
               "SETR": "aeropuerto de Santa Rosa (El Oro)", "SESD": "aeropuerto de Santo Domingo",
               "SETM": "aeropuerto de Catamayo (Loja)", "SEAM": "aeropuerto de Ambato", "SELT": "aeropuerto de Latacunga",
               "SECO": "aeropuerto del Coca", "SENL": "aeropuerto de Lago Agrio", "SEMC": "aeropuerto de Macas",
               "SEGS": "aeropuerto de Baltra (Galápagos)"}
MODELOS = {
    "ecmwf_ifs025":         "ECMWF",
    "gfs_seamless":         "GFS",
    "icon_seamless":        "ICON",
    "gem_seamless":         "GEM",
    "jma_seamless":         "JMA",
    "meteofrance_seamless": "MeteoFrance",
    "ukmo_seamless":        "UKMO",
}
UMBRAL_LLOVIZNA     = 0.2
UMBRAL_DEFECTO      = 1.0
UMBRALES_CANDIDATOS = [0.2, 0.5, 1.0, 2.0]
DIAS_PRONOSTICO     = 7          # días que muestra la página (los días 4-7 son orientativos)
DIAS_MAPA           = 3
DIAS_VERIFICACION   = 45
HORAS_ENTRE_VERIF   = 24
MIN_HORAS_LLUVIA    = 5
TOLERANCIA_H        = 1
BINS_CALIB          = 10
SUAVIZADO_CALIB     = 5
DIAS_PRUEBA         = 10         # últimos días reservados para el examen (no se usan para aprender)
DIAS_RECIENTES      = 21         # días de la calibración que se adapta a la temporada
MIN_MODELOS         = 3
MIN_HORAS_REALES    = 72
VERSION             = 10         # versión de la lógica de datos (si cambia, se re-verifica)
PLAZOS              = [1, 2, 3, 4, 5, 6, 7]   # días de anticipación que se verifican
FRANJAS             = {"madrugada": (0, 6), "mañana": (6, 12), "tarde": (12, 18), "noche": (18, 24)}
ALERTA_MIN          = 60         # % a partir del cual un salto se considera alerta
ALERTA_SALTO        = 30         # puntos que tiene que subir respecto a la ejecución anterior
ALERTA_HORAS        = 6          # horas hacia adelante que se vigilan
SEMANAS_RESPALDO    = 8
MIN_HORAS_TORMENTA  = 4          # mínimo de horas con tormenta observada para calibrar tormentas
# Variables del pronóstico actual (ciudades, mapa y "mi ubicación" usan las mismas)
VARS_PRON = ["precipitation", "temperature_2m", "cloud_cover", "cloud_cover_low", "cloud_cover_mid",
               "shortwave_radiation", "weather_code", "wind_gusts_10m", "uv_index",
             "relative_humidity_2m", "wind_speed_10m"]
# Mapa: cuadrícula sobre Ecuador continental
MAPA_LATS = [round(1.75 - 0.75 * i, 2) for i in range(10)]     # 1.75 … -5.0
MAPA_LONS = [round(-81.5 + 0.75 * j, 2) for j in range(9)]     # -81.5 … -75.5
# Íconos animados Meteocons (MIT, © Bas Milius). Se descargan una vez y se guardan en datos/iconos.
ICONOS_METEOCONS = ["clear-day", "clear-night", "partly-cloudy-day", "partly-cloudy-night", "overcast",
                    "drizzle", "rain", "thunderstorms-rain", "fog"]
URLS_ICONOS = ["https://cdn.jsdelivr.net/gh/basmilius/weather-icons@2.0.0/production/fill/all/{n}.svg",
               "https://cdn.jsdelivr.net/npm/@meteocons/svg/fill/{n}.svg"]
ZONA = "America/Guayaquil"
OFFSET_S = -5 * 3600             # UTC-5 (Ecuador continental, sin horario de verano)

ARCHIVO_PESOS = os.path.join(DATOS, "pesos.json")
ARCHIVO_HTML  = os.path.abspath(os.environ.get("CC_HTML") or os.path.join(BASE, "dashboard.html"))
ARCHIVO_ERR   = os.path.join(DATOS, "errores.log")
FEEDBACK_URL  = (os.environ.get("CC_FEEDBACK_URL") or "").strip().rstrip("/")
FEEDBACK_TOKEN = (os.environ.get("CC_FEEDBACK_TOKEN") or "").strip()   # para descargar los reportes
TIPOS_REPORTE = {"soleado": 0, "nublado": 0, "llovizna": 0, "lluvia": 1, "lluvia_fuerte": 1, "tormenta": 1}  # 1 = llueve
RADIO_REPORTES_KM = 30
MIN_REPORTES  = 30           # reportes comparables necesarios para mostrar resultados
REPO_URL      = "https://github.com/suirobert/ClearSky"
HORAS_LOG     = 30
DIAS_HISTORIAL_LECTURA = DIAS_VERIFICACION + 5

URL_FORECAST = "https://api.open-meteo.com/v1/forecast"
URL_PREVIOUS = "https://previous-runs-api.open-meteo.com/v1/forecast"
URL_IEM      = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
ARCHIVO_ALERTAS = os.path.join(DATOS, "alertas.json")
# Lluvia observada ahora por satélite: NOAA Enterprise Rain Rate (GOES + Meteosat + Himawari, cada 10 min)
URL_ERR      = "https://noaa-enterprise-rainrate-pds.s3.amazonaws.com"
AHORA_CAJA   = (-5.5, 2.5, -92.5, -75.0)     # sur, norte, oeste, este (Ecuador continental y Galápagos)
AHORA_PASO   = 0.1
AHORA_MAX_H  = 3                             # más antigua que esto no se muestra
# =============================================================


def archivo_log(dia=None):
    return os.path.join(DATOS, "historial", f"{dia or date.today():%Y-%m-%d}.csv")


def archivo_obs(mes):
    return os.path.join(DATOS, "observaciones", f"{mes}.json")


def log_error(msg):
    print("  ERROR:", msg)
    lineas = []
    if os.path.exists(ARCHIVO_ERR):
        with open(ARCHIVO_ERR, encoding="utf-8") as f:
            lineas = f.readlines()[-499:]
    lineas.append(f"{datetime.now():%Y-%m-%d %H:%M} {msg}\n")
    with open(ARCHIVO_ERR, "w", encoding="utf-8") as f:
        f.writelines(lineas)


def http_get(url, params, intentos=3):
    full = url + "?" + urllib.parse.urlencode(params, doseq=True)
    for i in range(intentos):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": f"ClearSky/3.0 (+{REPO_URL})"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(5 * (i + 1))


def serie_modelo(hourly, variable, modelo):
    return hourly.get(f"{variable}_{modelo}") or (hourly.get(variable) if len(MODELOS) == 1 else None)


def distancia_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


# ----------------------- CONFIGURACIÓN APRENDIDA -----------------------
def cfg_defecto():
    return {"umbral": UMBRAL_DEFECTO, "pesos": {m: 1.0 for m in MODELOS}, "verificado": False}


def cargar_pesos():
    p = {"actualizado": None, "version": None, "ciudades": {}}
    if os.path.exists(ARCHIVO_PESOS):
        try:
            with open(ARCHIVO_PESOS, encoding="utf-8") as f:
                leido = json.load(f)
            if "ciudades" in leido:
                p = leido
        except Exception as e:
            log_error(f"pesos.json ilegible, se regenera: {e}")
    for c in CIUDADES:
        p["ciudades"].setdefault(c, cfg_defecto())
    return p


def guardar_pesos(p):
    tmp = ARCHIVO_PESOS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2, ensure_ascii=False)
    os.replace(tmp, ARCHIVO_PESOS)


def toca_verificar(p):
    if not p.get("actualizado") or p.get("version") != VERSION:
        return True
    return datetime.now() - datetime.fromisoformat(p["actualizado"]) >= timedelta(hours=HORAS_ENTRE_VERIF)


def cfg_efectivo(p, ciudad):
    """Configuración a usar: la propia si está verificada; si no, la de la ciudad verificada
       más cercana de la misma zona. Devuelve (cfg, ciudad_de_referencia o None)."""
    propia = p["ciudades"].get(ciudad) or cfg_defecto()
    if propia.get("verificado"):
        return propia, ciudad
    cands = [c for c in CIUDADES if c != ciudad and p["ciudades"].get(c, {}).get("verificado")]
    if not cands:
        return propia, None
    z = CIUDADES[ciudad]["zona"]
    misma = [c for c in cands if CIUDADES[c]["zona"] == z] or cands
    o = CIUDADES[ciudad]
    ref = min(misma, key=lambda c: distancia_km(o["lat"], o["lon"], CIUDADES[c]["lat"], CIUDADES[c]["lon"]))
    return p["ciudades"][ref], ref


# ----------------------- OBSERVACIONES (METAR) -----------------------
def hora_clave(dt):
    if dt.minute or dt.second:
        dt = dt.replace(minute=0, second=0) + timedelta(hours=1)
    return dt.strftime("%Y-%m-%dT%H:00")


def descargar_obs(icao, dias, temps=None):
    fin = date.today() + timedelta(days=1)
    ini = fin - timedelta(days=dias + 1)
    params = [("station", icao), ("data", "wxcodes"), ("data", "tmpf"), ("data", "relh"),
              ("year1", ini.year), ("month1", ini.month), ("day1", ini.day),
              ("year2", fin.year), ("month2", fin.month), ("day2", fin.day),
              ("tz", ZONA), ("format", "onlycomma"), ("latlon", "no"),
              ("missing", "M"), ("direct", "no"), ("report_type", "3"), ("report_type", "4")]
    return parsear_obs(http_get(URL_IEM, params), temps)


def parsear_obs(texto, temps=None):
    obs = {}
    for row in csv.DictReader(io.StringIO(texto)):
        try:
            dt = datetime.strptime(row["valid"].strip(), "%Y-%m-%d %H:%M")
        except Exception:
            continue
        wx = (row.get("wxcodes") or "M").strip()
        todos = [] if wx in ("M", "") else wx.split()
        tormenta = int(any("TS" in c for c in todos))           # TS o VCTS: se oyen truenos
        codes = [c for c in todos if not c.startswith("VC")]
        lluvia = int(any("RA" in c for c in codes))
        llovizna = int(lluvia or any("DZ" in c for c in codes))
        k = hora_clave(dt)
        a, b, t = (list(obs.get(k, (0, 0, 0))) + [0])[:3]
        obs[k] = (max(a, lluvia), max(b, llovizna), max(t, tormenta))
        if temps is not None:                                    # temperatura (°C) y humedad (%) medidas
            try:
                tc = round((float(row.get("tmpf")) - 32) * 5 / 9, 1)
            except (TypeError, ValueError):
                continue
            try:
                hr = float(row.get("relh"))
            except (TypeError, ValueError):
                hr = None
            if -10 <= tc <= 50:
                temps[k] = (tc, hr if hr is not None and 0 <= hr <= 100 else None)
    return obs


def descargar_previos(lat, lon, dias):
    """Devuelve ({plazo: {modelo: {hora: mm}}}, códigos): lo que cada modelo pronosticó 1..7 días antes
       de cada hora, y los códigos de tiempo de 24 h antes (para las tormentas)."""
    base = {"latitude": lat, "longitude": lon, "models": ",".join(MODELOS), "timezone": ZONA,
            "past_days": dias, "forecast_days": 1}
    h, plazos = None, None
    for intento in (PLAZOS, [1, 2, 3], [1]):
        vars_ = [f"precipitation_previous_day{d}" for d in intento] + ["weather_code_previous_day1",
                 "temperature_2m_previous_day1", "relative_humidity_2m_previous_day1"]
        try:
            h = json.loads(http_get(URL_PREVIOUS, dict(base, hourly=",".join(vars_)), intentos=2))["hourly"]
            plazos = intento
            break
        except Exception as e:
            log_error(f"historial de pronósticos ({len(intento)} plazos) no disponible: {e}")
    if h is None:                                        # último recurso: solo la lluvia a 1 día
        h = json.loads(http_get(URL_PREVIOUS, dict(base, hourly="precipitation_previous_day1")))["hourly"]
        plazos = [1]
    prev, cods = {}, {}
    for d in plazos:
        dd = {}
        for m in MODELOS:
            serie = serie_modelo(h, f"precipitation_previous_day{d}", m)
            if serie and any(v is not None for v in serie):
                dd[m] = {t: v for t, v in zip(h["time"], serie) if v is not None}
        if dd:
            prev[d] = dd
    for m in MODELOS:
        sc = serie_modelo(h, "weather_code_previous_day1", m)
        if sc and any(v is not None for v in sc):
            cods[m] = {t: v for t, v in zip(h["time"], sc) if v is not None}
    tprev = {"t": {}, "h": {}}
    for clave, var in (("t", "temperature_2m_previous_day1"), ("h", "relative_humidity_2m_previous_day1")):
        for m in MODELOS:
            sv = serie_modelo(h, var, m)
            if sv and any(v is not None for v in sv):
                tprev[clave][m] = {t: v for t, v in zip(h["time"], sv) if v is not None}
    return prev, cods, tprev


# ----------------------- MÉTRICAS -----------------------
def vecinos(k, tol):
    dt = datetime.strptime(k, "%Y-%m-%dT%H:00")
    return [(dt + timedelta(hours=d)).strftime("%Y-%m-%dT%H:00") for d in range(-tol, tol + 1)]


def metricas(pron, obs, tol=TOLERANCIA_H):
    a = b = c = d = 0
    for k, f in pron.items():
        o = obs.get(k, 0)
        vs = vecinos(k, tol)
        if f:
            if any(obs.get(v, 0) for v in vs): a += 1
            else: b += 1
        elif o:
            if not any(pron.get(v, 0) for v in vs): c += 1
        else:
            d += 1
    n = a + b + c + d
    esp = ((a + c) * (a + b) + (d + c) * (d + b)) / n if n else 0
    hss = (a + d - esp) / (n - esp) if n and n != esp else 0.0
    return {"n": n, "aciertos": a, "falsas": b, "perdidas": c, "neg_ok": d,
            "pod": round(a / (a + c) * 100) if a + c else None,
            "far": round(b / (a + b) * 100) if a + b else None,
            "exactitud": round((a + d) / n * 100, 1) if n else None,
            "hss": round(hss, 3)}


def puntaje(m, horas_lluvia):
    if horas_lluvia >= MIN_HORAS_LLUVIA:
        return m["hss"]
    return (m["exactitud"] or 0) / 100


def mediana_ponderada(valores, pesos):
    items = sorted((v, pesos.get(m, 1.0)) for m, v in valores.items())
    total, acum = sum(w for _, w in items), 0.0
    for v, w in items:
        acum += w
        if acum >= total / 2:
            return v
    return items[-1][0]


def prob_consenso(valores, pesos, umbral):
    sw = sum(pesos.get(m, 1.0) for m in valores)
    return sum(pesos.get(m, 1.0) * (v >= umbral) for m, v in valores.items()) / sw * 100 if sw else 0


# ----------------------- CALIBRACIÓN -----------------------
def ajustar_calibracion(probs, obs):
    if not obs:
        return []
    base = sum(obs) / len(obs)
    tramos = {}
    for p, o in zip(probs, obs):
        t = min(int(p // (100 / BINS_CALIB)), BINS_CALIB - 1)
        n, s, sp = tramos.get(t, (0, 0, 0.0))
        tramos[t] = (n + 1, s + o, sp + p)
    bloques = []
    for t in sorted(tramos):
        n, s, sp = tramos[t]
        bloques.append([sp / n, (s + base * SUAVIZADO_CALIB) / (n + SUAVIZADO_CALIB), n])
    i = 0
    while i < len(bloques) - 1:
        if bloques[i][1] > bloques[i + 1][1]:
            x0, v0, w0 = bloques[i]; x1, v1, w1 = bloques[i + 1]
            w = w0 + w1
            bloques[i:i + 2] = [[(x0 * w0 + x1 * w1) / w, (v0 * w0 + v1 * w1) / w, w]]
            i = max(i - 1, 0)
        else:
            i += 1
    return [[round(x, 1), round(v * 100, 1)] for x, v, _ in bloques]


def calibrar_prob(p, tabla):
    if not tabla:
        return p
    if p <= tabla[0][0]:
        return tabla[0][1]
    for (x0, y0), (x1, y1) in zip(tabla, tabla[1:]):
        if p <= x1:
            return y0 + (y1 - y0) * (p - x0) / (x1 - x0) if x1 > x0 else y1
    return tabla[-1][1]


def brier(probs, obs):
    return round(sum((p / 100 - o) ** 2 for p, o in zip(probs, obs)) / len(obs), 4) if obs else None


def tabla_tramos(probs, obs):
    tramos = {}
    for p, o in zip(probs, obs):
        t = min(int(p // 20), 4)
        n, s = tramos.get(t, (0, 0))
        tramos[t] = (n + 1, s + o)
    return {f"{t*20}-{t*20+20}%": {"horas": n, "llovio_pct": round(s / n * 100)}
            for t, (n, s) in sorted(tramos.items())}


def tabla_mezcla(tabla, alfa=0.5):
    if not tabla:
        return []
    return [[float(x), round(alfa * calibrar_prob(x, tabla) + (1 - alfa) * x, 1)] for x in range(0, 101, 5)]


def opciones_calibracion(hs, pr, ob):
    completa = ajustar_calibracion([pr[k] for k in hs], [ob[k] for k in hs])
    rec = hs[-24 * DIAS_RECIENTES:]
    reciente = ajustar_calibracion([pr[k] for k in rec], [ob[k] for k in rec])
    return {"sin_ajuste": [], "completa": completa, "reciente": reciente,
            "mezcla": tabla_mezcla(completa), "mezcla_reciente": tabla_mezcla(reciente)}


ORDEN_PRUDENCIA = ["mezcla_reciente", "reciente", "mezcla", "completa", "sin_ajuste"]


def calibrar(obs, prev):
    """Aprende pesos, umbral y calibración. Prueba varias combinaciones con días que NO usó
       para aprender y se queda con la que mejor acierta."""
    ahora = datetime.now().strftime("%Y-%m-%dT%H:00")
    horas = sorted(k for k in obs if k < ahora and sum(k in prev[m] for m in prev) >= 2)
    if not horas or not prev:
        raise RuntimeError("sin horas comunes entre observaciones y pronósticos")
    o_lluvia = {k: obs[k][0] for k in horas}
    o_dz = {k: obs[k][1] for k in horas}
    iguales = {m: 1.0 for m in prev}

    def probs(hs, umbral, pesos):
        return {k: prob_consenso({m: prev[m][k] for m in prev if k in prev[m]}, pesos, umbral) for k in hs}

    def binario(pr):
        return {k: int(v >= 50) for k, v in pr.items()}

    def ajustar(hs):
        ol = {k: o_lluvia[k] for k in hs}
        hl = sum(ol.values())
        ev = {u: puntaje(metricas(binario(probs(hs, u, iguales)), ol), hl) for u in UMBRALES_CANDIDATOS}
        umbral = max(UMBRALES_CANDIDATOS, key=lambda u: (round(ev[u], 4), u == UMBRAL_DEFECTO))
        met_mod, hss_e, pesos = {}, {}, {}
        for m in prev:
            hm = [k for k in hs if k in prev[m]]
            pr = {k: int(prev[m][k] >= umbral) for k in hm}
            ob = {k: o_lluvia[k] for k in hm}
            met = metricas(pr, ob)
            met_mod[MODELOS[m]] = met
            hss_e[MODELOS[m]] = metricas(pr, ob, tol=0)["hss"]
            pesos[m] = max(puntaje(met, hl), 0) + 0.05
        media = statistics.mean(pesos.values())
        pesos = {m: round(w / media, 3) for m, w in pesos.items()}
        return {"umbral": umbral, "pesos": pesos, "modelos": met_mod, "hss_estricto": hss_e,
                "umbrales_eval": ev, "horas_lluvia": hl}

    # 1) EXAMEN
    corte = (datetime.strptime(horas[-1], "%Y-%m-%dT%H:00") - timedelta(days=DIAS_PRUEBA)).strftime("%Y-%m-%dT%H:00")
    ent = [k for k in horas if k <= corte]
    pru = [k for k in horas if k > corte]
    examen, usar_pesos, modo = None, True, "completa"
    if len(ent) >= 24 * 14 * 0.4 and len(pru) >= 24 * 3 * 0.4:
        aj = ajustar(ent)
        real = [o_lluvia[k] for k in pru]
        base = sum(o_lluvia[k] for k in ent) / len(ent)
        resultados = {}
        for con_pesos, pesos in ((True, aj["pesos"]), (False, iguales)):
            p_ent = probs(ent, aj["umbral"], pesos)
            p_pru = probs(pru, aj["umbral"], pesos)
            for nombre, tabla in opciones_calibracion(ent, p_ent, o_lluvia).items():
                resultados[(con_pesos, nombre)] = brier([calibrar_prob(p_pru[k], tabla) for k in pru], real)
        mejor_b = min(resultados.values())
        usar_pesos, modo = min((k for k, b in resultados.items() if b <= mejor_b + 0.001),
                               key=lambda k: (not k[0], ORDEN_PRUDENCIA.index(k[1])))
        b_sis = resultados[(usar_pesos, modo)]
        b_clima = brier([base * 100] * len(pru), real)
        b_crudo = resultados[(False, "sin_ajuste")]
        pesos_ex = aj["pesos"] if usar_pesos else iguales
        p_pru = probs(pru, aj["umbral"], pesos_ex)
        tabla_ex = opciones_calibracion(ent, probs(ent, aj["umbral"], pesos_ex), o_lluvia)[modo]
        examen = {"desde": pru[0][:10], "hasta": pru[-1][:10], "horas": len(pru), "horas_lluvia": sum(real),
                  "brier_clima": b_clima, "brier_crudo": b_crudo, "brier_sistema": b_sis,
                  "mejora_vs_clima_pct": round((1 - b_sis / b_clima) * 100) if b_clima else None,
                  "mejora_vs_crudo_pct": round((1 - b_sis / b_crudo) * 100) if b_crudo else None,
                  "tramos": tabla_tramos([calibrar_prob(p_pru[k], tabla_ex) for k in pru], real),
                  "usar_pesos": usar_pesos, "usar_calibracion": modo != "sin_ajuste", "modo_calibracion": modo,
                  "brier_opciones": {f"{'pesos' if a else 'iguales'}/{b}": v for (a, b), v in sorted(resultados.items())}}

    # 2) AJUSTE FINAL con todos los días
    aj = ajustar(horas)
    pesos = aj["pesos"] if usar_pesos else {m: 1.0 for m in prev}
    sin_datos = [m for m in MODELOS if m not in pesos]
    for m in sin_datos:
        pesos[m] = round(min(pesos.values()), 3)
    p_lluvia = probs(horas, aj["umbral"], pesos)
    tabla_lluvia = opciones_calibracion(horas, p_lluvia, o_lluvia)[modo]
    p_dz = probs(horas, UMBRAL_LLOVIZNA, pesos)
    tabla_dz = opciones_calibracion(horas, p_dz, o_dz)[modo]
    bruta = [p_lluvia[k] for k in horas]
    real = [o_lluvia[k] for k in horas]
    cal = [calibrar_prob(p, tabla_lluvia) for p in bruta]
    hl = aj["horas_lluvia"]
    return {"umbral": aj["umbral"], "pesos": pesos, "verificado": True, "tolerancia_h": TOLERANCIA_H,
            "periodo": f"{horas[0][:10]} a {horas[-1][:10]}", "horas": len(horas),
            "horas_lluvia": hl, "criterio": "HSS" if hl >= MIN_HORAS_LLUVIA else "% aciertos",
            "modelos": aj["modelos"], "hss_estricto": aj["hss_estricto"],
            "consenso": metricas(binario(p_lluvia), o_lluvia),
            "consenso_igual": metricas(binario(probs(horas, aj["umbral"], {m: 1.0 for m in prev})), o_lluvia),
            "consenso_hss_estricto": metricas(binario(p_lluvia), o_lluvia, tol=0)["hss"],
            "tabla_lluvia": tabla_lluvia, "tabla_llovizna": tabla_dz, "modo_calibracion": modo,
            "frecuencia_base_pct": round(sum(real) / len(real) * 100, 1),
            "brier_bruto": brier(bruta, real), "brier_calibrado": brier(cal, real),
            "calibracion": tabla_tramos(bruta, real), "calibracion_post": tabla_tramos(cal, real),
            "sin_datos": [MODELOS[m] for m in sin_datos], "examen": examen,
            "umbrales_eval": {str(u): round(v, 3) for u, v in aj["umbrales_eval"].items()}}


# ----------------------- PLAZOS (1 a 7 días) Y FRANJAS HORARIAS -----------------------
def separar_examen(horas):
    corte = (datetime.strptime(horas[-1], "%Y-%m-%dT%H:00") - timedelta(days=DIAS_PRUEBA)).strftime("%Y-%m-%dT%H:00")
    return [k for k in horas if k <= corte], [k for k in horas if k > corte]


def hay_examen(ent, pru):
    return len(ent) >= 24 * 14 * 0.4 and len(pru) >= 24 * 3 * 0.4


def evaluar_plazo(obs, prev, pesos, umbral):
    """Mide y calibra el pronóstico hecho N días antes, con el mismo examen sin trampa."""
    ahora = datetime.now().strftime("%Y-%m-%dT%H:00")
    horas = sorted(k for k in obs if k < ahora and sum(k in prev[m] for m in prev) >= 2)
    if not horas:
        return {"verificado": False, "horas": 0}
    o = {k: obs[k][0] for k in horas}
    pr = {k: prob_consenso({m: prev[m][k] for m in prev if k in prev[m]}, pesos, umbral) for k in horas}
    ent, pru = separar_examen(horas)
    if not hay_examen(ent, pru) or sum(o.values()) < MIN_HORAS_LLUVIA:
        return {"verificado": False, "horas": len(horas), "horas_lluvia": sum(o.values())}
    real = [o[k] for k in pru]
    res = {n: brier([calibrar_prob(pr[k], t) for k in pru], real) for n, t in opciones_calibracion(ent, pr, o).items()}
    mejor = min(res.values())
    modo = min((n for n, b in res.items() if b <= mejor + 0.001), key=ORDEN_PRUDENCIA.index)
    fb = sum(o[k] for k in ent) / len(ent)
    bc = brier([fb * 100] * len(pru), real)
    mejora = round((1 - res[modo] / bc) * 100) if bc else None
    tabla = opciones_calibracion(horas, pr, o)[modo]
    cal = {k: calibrar_prob(pr[k], tabla) for k in horas}
    return {"verificado": bool(mejora and mejora > 0), "horas": len(horas), "horas_lluvia": sum(o.values()),
            "tabla": tabla, "modo": modo, "brier": res[modo], "brier_clima": bc, "mejora_vs_clima_pct": mejora,
            "consenso": metricas({k: int(v >= 50) for k, v in cal.items()}, o)}


def franja_de(hora):
    h = int(hora[11:13])
    return next(n for n, (a, b) in FRANJAS.items() if a <= h < b)


def calibrar_franjas(obs, prev, pesos, umbral, modo):
    """Calibración separada para madrugada, mañana, tarde y noche. Solo se activa si en el examen
       acierta más que la calibración única."""
    ahora = datetime.now().strftime("%Y-%m-%dT%H:00")
    horas = sorted(k for k in obs if k < ahora and sum(k in prev[m] for m in prev) >= 2)
    if not horas:
        return {"activa": False}
    o = {k: obs[k][0] for k in horas}
    pr = {k: prob_consenso({m: prev[m][k] for m in prev if k in prev[m]}, pesos, umbral) for k in horas}
    ent, pru = separar_examen(horas)
    if not hay_examen(ent, pru):
        return {"activa": False}
    def tablas(hs):
        out = {}
        for f in FRANJAS:
            hf = [k for k in hs if franja_de(k) == f]
            ob = [o[k] for k in hf]
            out[f] = tabla_mezcla(ajustar_calibracion([pr[k] for k in hf], ob)) if len(hf) >= 48 and sum(ob) >= 3 else None
        return out
    t_glob = opciones_calibracion(ent, pr, o)[modo]
    t_fr = tablas(ent)
    real = [o[k] for k in pru]
    b_glob = brier([calibrar_prob(pr[k], t_glob) for k in pru], real)
    b_fr = brier([calibrar_prob(pr[k], t_fr[franja_de(k)] if t_fr[franja_de(k)] is not None else t_glob) for k in pru], real)
    activa = b_fr is not None and b_glob is not None and b_fr < b_glob - 0.001
    return {"activa": activa, "brier_unica": b_glob, "brier_franjas": b_fr,
            "tablas": {f: t for f, t in tablas(horas).items() if t} if activa else {}}


def tabla_para(cfg, plazo, hora):
    """Tabla de calibración a usar para una hora: la del plazo (si está verificada) o la de su franja."""
    if plazo >= 2:
        pz = (cfg.get("plazos") or {}).get(str(plazo)) or {}
        if pz.get("verificado") and pz.get("tabla"):
            return pz["tabla"]
    fr = cfg.get("franjas") or {}
    if plazo <= 1 and fr.get("activa"):
        t = (fr.get("tablas") or {}).get(franja_de(hora))
        if t:
            return t
    return cfg.get("tabla_lluvia")


def plazo_de(hora, ahora):
    horas = (datetime.strptime(hora, "%Y-%m-%dT%H:%M") - ahora).total_seconds() / 3600
    return max(1, min(7, math.ceil(horas / 24)))


# ----------------------- TEMPERATURA Y HUMEDAD -----------------------
def calibrar_temp(obs_t, tprev):
    """Compara cada hora la temperatura y la humedad pronosticadas 24 h antes con las del aeropuerto.
       Aprende el error típico de los modelos (por franja horaria) y solo lo corrige si en el examen
       sin trampa (últimos días no usados para aprender) acierta más."""
    ahora = datetime.now().strftime("%Y-%m-%dT%H:00")
    mt, mh = tprev.get("t") or {}, tprev.get("h") or {}
    filas = []
    for k, (to, ho) in obs_t.items():
        if k >= ahora:
            continue
        vt = [mt[m][k] for m in mt if k in mt[m]]
        if len(vt) < 3:
            continue
        vh = [mh[m][k] for m in mh if k in mh[m]]
        filas.append((k, statistics.mean(vt), to, statistics.mean(vh) if len(vh) >= 3 else None, ho))
    if len(filas) < 24 * 7:
        return {"verificado": False, "activa": False, "horas": len(filas)}
    filas.sort()
    ent, pru = separar_examen([f[0] for f in filas])
    ent, pru = set(ent), set(pru)
    f_ent = [f for f in filas if f[0] in ent]
    f_pru = [f for f in filas if f[0] in pru]
    if len(f_ent) < 24 * 5 or len(f_pru) < 24 * 2:
        return {"verificado": False, "activa": False, "horas": len(filas)}

    def sesgos(fs):
        s = {}
        for fr in FRANJAS:
            e = [p - o for k, p, o, _, _ in fs if franja_de(k) == fr]
            s[fr] = round(statistics.mean(e), 2) if len(e) >= 12 else 0.0
        eh = [ph - oh for _, _, _, ph, oh in fs if ph is not None and oh is not None]
        return s, (round(statistics.mean(eh), 1) if len(eh) >= 24 else 0.0)

    def mae_t(fs, s):
        return statistics.mean(abs(p - s.get(franja_de(k), 0) - o) for k, p, o, _, _ in fs)

    def mae_h(fs, sh):
        e = [abs(min(100, max(0, ph - sh)) - oh) for _, _, _, ph, oh in fs if ph is not None and oh is not None]
        return statistics.mean(e) if len(e) >= 24 else None

    s_ent, h_ent = sesgos(f_ent)
    t_crudo, t_corr = mae_t(f_pru, {}), mae_t(f_pru, s_ent)
    h_crudo, h_corr = mae_h(f_pru, 0), mae_h(f_pru, h_ent)
    usar_t = t_corr < t_crudo - 0.05
    usar_h = h_corr is not None and h_crudo is not None and h_corr < h_crudo - 0.5
    s_all, h_all = sesgos(filas)
    return {"verificado": True, "activa": usar_t, "horas": len(filas),
            "sesgo": s_all if usar_t else {fr: 0.0 for fr in FRANJAS},
            "sesgo_hum": h_all if usar_h else 0.0,
            "mae": round(t_corr if usar_t else t_crudo, 1), "mae_crudo": round(t_crudo, 1),
            "mae_hum": round(h_corr if usar_h else h_crudo) if h_crudo is not None else None}


def correccion_temp(cfg, hora):
    tc = cfg.get("temp") or {}
    return ((tc.get("sesgo") or {}).get(franja_de(hora), 0) if tc.get("activa") else 0), (tc.get("sesgo_hum") or 0)


# ----------------------- TORMENTAS ELÉCTRICAS -----------------------
def es_tormenta(codigo):
    return codigo is not None and codigo >= 95


def prob_tormenta(cods):
    """% de modelos que pronostican tormenta (códigos WMO 95-99). Pesos iguales: hay pocos casos."""
    return sum(es_tormenta(v) for v in cods.values()) / len(cods) * 100 if cods else 0


def calibrar_tormenta(obs, cods):
    """Igual que la lluvia: aprende la tabla "cuando dijimos X%, ¿hubo tormenta?" y la examina con
       los últimos días no usados para aprender. Cuenta como acierto una tormenta a ±1 h."""
    ahora = datetime.now().strftime("%Y-%m-%dT%H:00")
    horas = sorted(k for k in obs if k < ahora and len(obs[k]) > 2 and sum(k in cods[m] for m in cods) >= 2)
    if not horas:
        return {"verificado": False, "motivo": "sin datos de tormenta"}
    ts = {k: obs[k][2] for k in horas}
    o = {k: int(any(ts.get(v, 0) for v in vecinos(k, TOLERANCIA_H))) for k in horas}
    pr = {k: prob_tormenta({m: cods[m][k] for m in cods if k in cods[m]}) for k in horas}
    eventos = sum(ts.values())
    base = {"horas": len(horas), "horas_tormenta": eventos, "frecuencia_pct": round(sum(o.values()) / len(o) * 100, 1)}
    if eventos < MIN_HORAS_TORMENTA:
        return dict(base, verificado=False, motivo="pocas tormentas observadas", tabla=[], modo="sin_ajuste")
    corte = (datetime.strptime(horas[-1], "%Y-%m-%dT%H:00") - timedelta(days=DIAS_PRUEBA)).strftime("%Y-%m-%dT%H:00")
    ent = [k for k in horas if k <= corte]
    pru = [k for k in horas if k > corte]
    modo, examen = "sin_ajuste", None
    if len(ent) >= 24 * 14 * 0.4 and len(pru) >= 24 * 3 * 0.4:
        real = [o[k] for k in pru]
        res = {n: brier([calibrar_prob(pr[k], t) for k in pru], real)
               for n, t in opciones_calibracion(ent, pr, o).items()}
        mejor = min(res.values())
        modo = min((n for n, b in res.items() if b <= mejor + 0.0005), key=ORDEN_PRUDENCIA.index)
        fb = sum(o[k] for k in ent) / len(ent)
        bc = brier([fb * 100] * len(pru), real)
        examen = {"brier_sistema": res[modo], "brier_crudo": res["sin_ajuste"], "brier_clima": bc,
                  "mejora_vs_clima_pct": round((1 - res[modo] / bc) * 100) if bc else None}
    tabla = opciones_calibracion(horas, pr, o)[modo]
    cal = [calibrar_prob(pr[k], tabla) for k in horas]
    real = [o[k] for k in horas]
    anuncios = [i for i, p in enumerate(cal) if p >= 30]
    return dict(base, verificado=True, tabla=tabla, modo=modo, examen=examen,
                brier_bruto=brier([pr[k] for k in horas], real), brier_calibrado=brier(cal, real),
                anuncios=len(anuncios), anuncios_acertados=sum(real[i] for i in anuncios),
                calibracion_post=tabla_tramos(cal, real))


# ----------------------- MEMORIA PERMANENTE DE OBSERVACIONES -----------------------
def cargar_archivo_obs():
    arch = {}
    for ruta in sorted(glob.glob(os.path.join(DATOS, "observaciones", "*.json"))):
        try:
            with open(ruta, encoding="utf-8") as f:
                for c, horas in json.load(f).items():
                    arch.setdefault(c, {}).update(horas)
        except Exception as e:
            log_error(f"observaciones ilegibles en {os.path.basename(ruta)}: {e}")
    return arch


def guardar_archivo_obs(arch):
    por_mes = {}
    for c, horas in arch.items():
        for k, v in horas.items():
            por_mes.setdefault(k[:7], {}).setdefault(c, {})[k] = v
    for mes, contenido in por_mes.items():
        ruta = archivo_obs(mes)
        nuevo = json.dumps({c: dict(sorted(h.items())) for c, h in sorted(contenido.items())}, separators=(",", ":"))
        if os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as f:
                if f.read() == nuevo:
                    continue
        tmp = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(nuevo)
        os.replace(tmp, ruta)


# ----------------------- SEGUIMIENTO REAL -----------------------
def leer_pronosticos_publicados(ciudad):
    archivos = [archivo_log(date.today() - timedelta(days=d)) for d in range(DIAS_HISTORIAL_LECTURA, -1, -1)]
    pub = {}
    for ruta in archivos:
        if not os.path.exists(ruta):
            continue
        with open(ruta, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("ciudad") != ciudad or row.get("modelo") != "CONSENSO" or not row.get("prob_lluvia"):
                    continue
                try:
                    ej = datetime.strptime(row["ejecucion"], "%Y-%m-%d %H:%M")
                    hv = datetime.strptime(row["hora_valida"], "%Y-%m-%dT%H:00")
                except Exception:
                    continue
                if 1 <= (hv - ej).total_seconds() / 3600 <= 24:
                    k = row["hora_valida"]
                    if k not in pub or ej > pub[k][0]:
                        pub[k] = (ej, float(row["prob_lluvia"]))
    return {k: v[1] for k, v in pub.items()}


def seguimiento_real(ciudad, obs_ciudad):
    pub = leer_pronosticos_publicados(ciudad)
    hs = sorted(k for k in pub if k in obs_ciudad)
    if not hs:
        return {"horas": 0}
    p = [pub[k] for k in hs]
    o = [obs_ciudad[k][0] for k in hs]
    base = sum(o) / len(o)
    anuncios = [k for k in hs if pub[k] >= 40]
    acert = sum(1 for k in anuncios if any(obs_ciudad.get(v, [0])[0] for v in vecinos(k, TOLERANCIA_H)))
    b, bc = brier(p, o), brier([base * 100] * len(o), o)
    return {"horas": len(hs), "desde": hs[0][:10], "hasta": hs[-1][:10], "horas_lluvia": sum(o),
            "brier": b, "brier_clima": bc,
            "mejora_vs_clima_pct": round((1 - b / bc) * 100) if bc else None,
            "anuncios": len(anuncios), "anuncios_acertados": acert, "tramos": tabla_tramos(p, o)}


# ----------------------- VERIFICACIÓN DIARIA -----------------------
def verificar(p):
    print("Verificando modelos contra lo que realmente pasó...")
    arch = cargar_archivo_obs()
    for c, cfg in CIUDADES.items():
        if not cfg.get("metar"):
            continue
        try:
            temps = {}
            obs = descargar_obs(cfg["metar"], DIAS_VERIFICACION, temps)
            time.sleep(1)
            cobertura = len(obs) / (DIAS_VERIFICACION * 24)
            arch.setdefault(c, {}).update({k: list(v) for k, v in obs.items()})
            if cobertura < cfg["cob_min"]:
                log_error(f"{c}: el aeropuerto solo reportó {cobertura:.0%} de las horas "
                          f"(mínimo {cfg['cob_min']:.0%}); se mantiene la configuración anterior")
                continue
            previos, cods, tprev = descargar_previos(cfg["est"][0], cfg["est"][1], DIAS_VERIFICACION)
            prev = previos.get(1, {})
            if len(prev) < MIN_MODELOS:
                log_error(f"{c}: solo {len(prev)} modelos con historial; se mantiene la configuración anterior")
                continue
            r = calibrar(obs, prev)
            r["cobertura_obs_pct"] = round(cobertura * 100)
            try:
                r["franjas"] = calibrar_franjas(obs, prev, r["pesos"], r["umbral"], r.get("modo_calibracion", "completa"))
            except Exception as e:
                log_error(f"franjas {c}: {e}")
            r["plazos"] = {}
            for d, pv in sorted(previos.items()):
                if d >= 2 and len(pv) >= MIN_MODELOS:
                    try:
                        r["plazos"][str(d)] = evaluar_plazo(obs, pv, r["pesos"], r["umbral"])
                    except Exception as e:
                        log_error(f"plazo {d} días {c}: {e}")
            try:
                r["tormenta"] = calibrar_tormenta(obs, cods)
            except Exception as e:
                log_error(f"tormentas {c}: {e}")
                r["tormenta"] = (p["ciudades"].get(c) or {}).get("tormenta") or {"verificado": False}
            try:
                r["temp"] = calibrar_temp(temps, tprev)
            except Exception as e:
                log_error(f"temperatura {c}: {e}")
                r["temp"] = (p["ciudades"].get(c) or {}).get("temp") or {"verificado": False}
            p["ciudades"][c] = r
            ex = r.get("examen") or {}
            print(f"  {c}: {r['horas']} h, {r['horas_lluvia']} con lluvia, umbral {r['umbral']} mm/h, "
                  f"HSS {r['consenso']['hss']} | examen: clima {ex.get('brier_clima')} / sistema "
                  f"{ex.get('brier_sistema')} | calibración {r.get('modo_calibracion')} | franjas "
                  f"{(r.get('franjas') or {}).get('activa')} | plazos verificados "
                  f"{[d for d, z in r['plazos'].items() if z.get('verificado')]}")
        except Exception as e:
            log_error(f"verificación {c}: {e} (se mantiene la configuración anterior)")
    guardar_archivo_obs(arch)
    try:
        reps = leer_reportes(DIAS_VERIFICACION)
    except Exception as e:
        log_error(f"lectura de reportes: {e}")
        reps = []
    for c in CIUDADES:
        try:
            p["ciudades"][c]["real"] = seguimiento_real(c, arch.get(c, {}))
        except Exception as e:
            log_error(f"seguimiento real {c}: {e}")
        try:
            p["ciudades"][c]["reportes"] = verificar_reportes(c, reps)
        except Exception as e:
            log_error(f"verificación con reportes {c}: {e}")
    p["actualizado"] = datetime.now().isoformat(timespec="seconds")
    p["version"] = VERSION
    guardar_pesos(p)


# ----------------------- PRONÓSTICO ACTUAL -----------------------
def descargar(lat, lon):
    params = {"latitude": lat, "longitude": lon, "hourly": ",".join(VARS_PRON),
              "models": ",".join(MODELOS), "timezone": ZONA, "forecast_days": DIAS_PRONOSTICO}
    return json.loads(http_get(URL_FORECAST, params))


def radiacion_despejado(lat, lon, hora_local, offset_s, minutos=30):
    """Radiación solar (W/m²) que llegaría con cielo totalmente despejado (modelo de Haurwitz)
       a mitad de la hora. Sirve para saber si de verdad hay sol aunque haya nubes altas finas."""
    dt = datetime.strptime(hora_local, "%Y-%m-%dT%H:%M") + timedelta(minutes=minutos) - timedelta(seconds=offset_s)
    n = dt.timetuple().tm_yday
    dec = math.radians(23.44) * math.sin(2 * math.pi * (284 + n) / 365)
    b = 2 * math.pi * (n - 81) / 364
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    solar = dt.hour + dt.minute / 60 + lon / 15 + eot / 60
    ha = math.radians(15 * (solar - 12))
    la = math.radians(lat)
    cz = math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)
    return 1098 * cz * math.exp(-0.057 / cz) if cz > 0.17 else 0.0       # sol a menos de ~10°: no cuenta


def mediana(vals):
    return statistics.median(vals) if vals else None


def consenso(data, cfg, lat=None, lon=None, offset_s=OFFSET_S):
    """Misma lógica que filasDesdeOM() en el JavaScript de la página."""
    h = data["hourly"]
    lat = data.get("latitude", lat) if lat is None else lat
    lon = data.get("longitude", lon) if lon is None else lon
    pesos, umbral = cfg["pesos"], cfg["umbral"]
    tabla_ts = (cfg.get("tormenta") or {}).get("tabla") or []
    n = len(h["time"])
    ahora = datetime(1970, 1, 1) + timedelta(seconds=time.time() + offset_s)
    def serie(var, m, i):
        sv = serie_modelo(h, var, m)
        return sv[i] if sv and i < len(sv) else None
    filas = []
    for i, t in enumerate(h["time"]):
        lluvia, temp, nubes, nbm, rad, cods, raf, uv, hum, vto = {}, {}, {}, [], [], {}, [], [], [], []
        for m in MODELOS:
            v = serie("precipitation", m, i)
            if v is not None: lluvia[m] = v
            v = serie("temperature_2m", m, i)
            if v is not None: temp[m] = v
            v = serie("cloud_cover", m, i)
            if v is not None: nubes[m] = v
            lo, mi = serie("cloud_cover_low", m, i), serie("cloud_cover_mid", m, i)
            if lo is not None or mi is not None: nbm.append(max(lo or 0, mi or 0))
            # la radiación de Open-Meteo es el promedio de la hora ANTERIOR: para la hora t..t+1 se usa i+1
            v = serie("shortwave_radiation", m, i + 1) if i + 1 < n else None
            if v is not None: rad.append(v)
            v = serie("weather_code", m, i)
            if v is not None: cods[m] = v
            v = serie("wind_gusts_10m", m, i)
            if v is not None: raf.append(v)
            v = serie("uv_index", m, i)
            if v is not None: uv.append(v)
            v = serie("relative_humidity_2m", m, i)
            if v is not None: hum.append(v)
            v = serie("wind_speed_10m", m, i)
            if v is not None: vto.append(v)
        if not lluvia:
            continue
        corr_t, corr_h = correccion_temp(cfg, t)
        claro = radiacion_despejado(lat, lon, t, offset_s)
        sol = round(min(mediana(rad) / claro, 1.2), 2) if claro > 0 and len(rad) >= 2 else None
        bruta = prob_consenso(lluvia, pesos, umbral)
        bruta_dz = prob_consenso(lluvia, pesos, UMBRAL_LLOVIZNA)
        plazo = plazo_de(t, ahora)
        prob = calibrar_prob(bruta, tabla_para(cfg, plazo, t))
        prob_dz = max(calibrar_prob(bruta_dz, cfg.get("tabla_llovizna")), prob)
        acuerdo = max(bruta, 100 - bruta)
        filas.append({
            "hora": t, "prob": round(prob), "prob_dz": round(prob_dz), "prob_bruta": round(bruta),
            "mm": round(mediana_ponderada(lluvia, pesos), 1),
            "min": round(min(lluvia.values()), 1), "max": round(max(lluvia.values()), 1),
            "temp": round(statistics.mean(temp.values()) - corr_t, 1) if temp else None,
            "nubes": round(statistics.mean(nubes.values())) if nubes else None,
            "nubes_bm": round(statistics.mean(nbm)) if nbm else None,
            "sol": sol,
            "prob_ts": round(calibrar_prob(prob_tormenta(cods), tabla_ts)) if cods else 0,
            "niebla": round(sum(c in (45, 48) for c in cods.values()) / len(cods) * 100) if cods else 0,
            "rafaga": round(mediana(raf)) if raf else None,
            "uv": round(mediana(uv), 1) if uv else None,
            "hum": round(min(100, max(0, mediana(hum) - corr_h))) if hum else None,
            "viento": round(mediana(vto)) if vto else None,
            "conf": "Alta" if acuerdo >= 80 else "Media" if acuerdo >= 60 else "Baja",
            "detalle": {MODELOS[m]: round(v, 1) for m, v in lluvia.items()}, "n": len(lluvia), "plazo": plazo,
        })
    return filas


CAMPOS_LOG = ["ejecucion", "ciudad", "hora_valida", "modelo", "lluvia_mm", "prob_lluvia", "prob_bruta"]


def guardar_log(ciudad, filas):
    ruta = archivo_log()
    nuevo = not os.path.exists(ruta)
    ahora = datetime.now()
    desde = ahora.strftime("%Y-%m-%dT%H:00")
    limite = (ahora + timedelta(hours=HORAS_LOG)).strftime("%Y-%m-%dT%H:00")
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        if nuevo:
            wr.writerow(CAMPOS_LOG)
        for r in filas:
            if desde <= r["hora"] <= limite:
                wr.writerow([ahora.strftime("%Y-%m-%d %H:%M"), ciudad, r["hora"], "CONSENSO",
                             r["mm"], r["prob"], r["prob_bruta"]])


# =====================================================================
#                       PÁGINA (texto de verificación)
# =====================================================================
def fmt(v, suf=""):
    return "-" if v is None else f"{v}{suf}"


def nivel_fiabilidad(cfg):
    if not cfg.get("verificado"):
        return "En evaluación"
    hss = (cfg.get("consenso") or {}).get("hss")
    if hss is None: return "En evaluación"
    if hss >= 0.4:  return "Buena"
    if hss >= 0.25: return "Aceptable"
    return "Limitada"


def html_verificacion(v, ciudad):
    if not v.get("verificado"):
        return ("<p class='nota'>Todavía estamos reuniendo datos para medir qué tan confiable es el "
                "pronóstico aquí. Vuelve a revisar en unos días.</p>")
    c = v["consenso"]
    dijo = c["aciertos"] + c["falsas"]
    acierto_10 = round(c["aciertos"] / dijo * 10) if dijo else None
    detecta_10 = round(c["pod"] / 10) if c["pod"] is not None else None
    aero = AEROPUERTOS.get(CIUDADES[ciudad]["metar"], "aeropuerto")
    out = [f"<p class='plano'>Fiabilidad actual: <b>{nivel_fiabilidad(v)}</b></p>",
           f"<p class='nota'>Todos los días comparamos lo que pronosticamos con lo que realmente llovió en el "
           f"{aero} durante los últimos {DIAS_VERIFICACION} días. Con eso ajustamos el pronóstico.</p><ul class='lst'>"]
    if acierto_10 is not None:
        out.append(f"<li>Cuando anunciamos lluvia, llovió <b>{acierto_10} de cada 10 veces</b>.</li>")
    if detecta_10 is not None:
        out.append(f"<li>Anticipamos <b>{detecta_10} de cada 10 lluvias</b> que ocurrieron.</li>")
    mejor = max(m["hss"] for m in v["modelos"].values()) if v.get("modelos") else 0
    out.append(f"<li>Comparado con los {len(v.get('modelos', {}))} modelos por separado, el pronóstico combinado está "
               + ("<b>entre los más acertados</b>." if c["hss"] < mejor - 0.005 else "<b>el más acertado</b>.")
               + "</li></ul>")
    ex = v.get("examen")
    if ex and ex.get("mejora_vs_clima_pct") is not None:
        m = ex["mejora_vs_clima_pct"]
        frase = (f"fue <b>{m}% más preciso</b> que simplemente decir el promedio de siempre" if m > 0
                 else "<b>no superó</b> a simplemente decir el promedio de siempre; seguimos ajustando")
        out.append(f"<h4>Examen sin trampa</h4><p class='nota'>Guardamos los últimos {DIAS_PRUEBA} días sin usarlos "
                   f"para aprender, y luego comprobamos cómo le habría ido al pronóstico. Resultado: {frase}.</p>")
    rr = v.get("real") or {}
    if rr.get("horas", 0) >= MIN_HORAS_REALES:
        out.append("<h4>Resultados reales de lo que publicamos</h4><ul class='lst'>"
                   f"<li>Desde el {rr['desde']} hemos comprobado <b>{rr['horas']} horas</b> pronosticadas en esta página.</li>"
                   + (f"<li>Cuando mostramos 40% o más, llovió <b>{round(rr['anuncios_acertados']/rr['anuncios']*10)} de cada 10 veces</b>.</li>"
                      if rr.get("anuncios", 0) >= 5 else "")
                   + (f"<li>En total, {rr['mejora_vs_clima_pct']}% más preciso que decir siempre el promedio.</li>"
                      if (rr.get("mejora_vs_clima_pct") or 0) > 0 else "")
                   + "</ul>")
    else:
        out.append("<h4>Resultados reales de lo que publicamos</h4><p class='nota'>Estamos reuniendo datos: "
                   f"{rr.get('horas', 0)} de {MIN_HORAS_REALES} horas comprobadas. Cuando haya suficientes, "
                   "aquí verás cuánto acertó exactamente lo que mostró esta página.</p>")
    cal = "".join(f"<div class='cb'><span>Decimos {k}</span><div><i style='width:{x['llovio_pct']}%'></i></div>"
                  f"<em>llovió {x['llovio_pct']}%</em></div>" for k, x in v.get("calibracion_post", {}).items())
    out.append("<h4>¿Qué significan nuestros porcentajes?</h4><p class='nota'>Si decimos 30%, debería llover "
               f"más o menos 3 de cada 10 veces que lo decimos. Así nos ha ido:</p><div class='calib'>{cal}</div>")

    nombre_peso = {MODELOS[m]: w for m, w in v["pesos"].items()}
    filas = sorted(v["modelos"].items(), key=lambda x: -nombre_peso.get(x[0], 0))

    def fila(nombre, m, peso, cls=""):
        return (f"<tr class='{cls}'><td>{nombre}</td><td>{peso}</td><td>{m['aciertos']}</td><td>{m['falsas']}</td>"
                f"<td>{m['perdidas']}</td><td>{fmt(m['pod'],'%')}</td><td>{fmt(m['far'],'%')}</td>"
                f"<td>{fmt(m['exactitud'],'%')}</td><td><b>{m['hss']}</b></td></tr>")
    t = ['<div class="scroll"><table><tr><th>Modelo</th><th>Peso</th><th>Aciertos</th><th>Falsas<br>alarmas</th>'
         '<th>No<br>anticipadas</th><th>POD</th><th>FAR</th><th>% aciertos</th><th>HSS</th></tr>',
         fila("★ Combinado", v["consenso"], "-", "star"),
         fila("Combinado (pesos iguales)", v["consenso_igual"], "-", "alt")]
    t += [fila(nm, m, nombre_peso.get(nm, "-")) for nm, m in filas]
    t.append("</table></div>")
    extra = f" · Sin historial: {', '.join(v['sin_datos'])}" if v.get("sin_datos") else ""
    tec = (f"Periodo {v['periodo']} · {v['horas']} h evaluadas · {v['horas_lluvia']} h con lluvia · "
           f"umbral {v['umbral']} mm/h · criterio {v['criterio']} · tolerancia ±{v.get('tolerancia_h', 0)} h{extra} · "
           f"calibración {v.get('modo_calibracion', '-')} · Brier {v.get('brier_bruto')} → {v.get('brier_calibrado')} · "
           f"cobertura aeropuerto {v.get('cobertura_obs_pct', '-')}%")
    if ex:
        tec += (f"<br>Examen {ex['desde']} a {ex['hasta']} ({ex['horas']} h, {ex['horas_lluvia']} con lluvia): "
                f"Brier clima {ex['brier_clima']} · modelos sin ajustar {ex['brier_crudo']} · sistema {ex['brier_sistema']}")
    if rr.get("horas"):
        tec += f"<br>Seguimiento real: {rr['horas']} h · Brier {rr['brier']} (clima {rr['brier_clima']})"
    out.append("<details class='tec'><summary>Datos técnicos</summary>"
               f"<p class='nota'>{tec}</p>" + "".join(t) +
               "<p class='nota'>POD: % de lluvias anticipadas · FAR: % de falsas alarmas · HSS: habilidad frente al azar "
               "(0 = azar, 1 = perfecto) · Peso: influencia del modelo en el pronóstico combinado.</p></details>")
    return "".join(out)


def html_plazos(cfg):
    pz = cfg.get("plazos") or {}
    fr = cfg.get("franjas") or {}
    out = "<h4>Precisión según la anticipación</h4>"
    if not pz:
        return out + "<p class='nota'>Todavía estamos midiendo cómo acierta el pronóstico a más de un día.</p>"
    filas = []
    for d in sorted(pz, key=int):
        z = pz[d]
        m = z.get("mejora_vs_clima_pct")
        if m is None:
            txt = "midiendo…"
        elif m > 0:
            txt = f"<b>{m}% más preciso</b> que decir siempre el promedio"
        else:
            txt = "todavía no supera al promedio: orientativo"
        filas.append(f"<li>A {d} días: {txt}</li>")
    out += ("<p class='nota'>También comparamos lo que pronosticamos con varios días de anticipación con lo que pasó. "
            "Los días que todavía no superan al promedio aparecen como <i>orientativos</i>.</p><ul class='lst'>"
            + "".join(filas) + "</ul>")
    if fr.get("activa"):
        out += ("<p class='nota'>Los porcentajes de las próximas 24 horas se ajustan por separado para la madrugada, "
                "la mañana, la tarde y la noche, porque así acertaron más en el examen.</p>")
    return out


def html_temp(t, ciudad):
    out = "<h4>Temperatura, humedad y sensación térmica</h4>"
    if not t or not t.get("verificado"):
        return out + ("<p class='nota'>Todavía estamos reuniendo datos del aeropuerto para comprobar la temperatura "
                      "aquí. Mientras tanto la sensación térmica es orientativa.</p>")
    aero = AEROPUERTOS.get(CIUDADES[ciudad]["metar"], "aeropuerto")
    g = lambda x: f"{x:.1f}".replace(".", ",")
    li = [f"<li>Comparamos cada hora la temperatura y la humedad con las que mide el {aero}.</li>",
          f"<li>La temperatura acierta <b>±{g(t['mae'])}°</b> en promedio"
          + (f" (los modelos sin corregir: ±{g(t['mae_crudo'])}°)" if t.get("activa") else "") + ".</li>"]
    if t.get("mae_hum") is not None:
        li.append(f"<li>La humedad acierta <b>±{t['mae_hum']}%</b>.</li>")
    return (out + "<ul class='lst'>" + "".join(li) + "</ul><p class='nota'>Con esa temperatura y esa humedad "
            "calculamos la sensación térmica (índice de calor de la NOAA).</p>")


def html_tormenta(t, ciudad):
    aero = AEROPUERTOS.get(CIUDADES[ciudad]["metar"], "aeropuerto")
    out = "<h4>Tormentas eléctricas</h4>"
    if not t or not t.get("verificado"):
        n = (t or {}).get("horas_tormenta")
        return out + ("<p class='nota'>En los últimos días casi no hubo tormentas en el " + aero +
                      (f" ({n} horas con truenos)" if n is not None else "") +
                      ", así que todavía no podemos medir cuánto acertamos. Mientras tanto mostramos lo que dicen "
                      "los modelos, sin ajustar.</p>")
    li = [f"<li>Las comparamos con los truenos que registra el {aero}: hubo <b>{t['horas_tormenta']} horas con tormenta</b> "
          f"en los últimos {DIAS_VERIFICACION} días.</li>"]
    if t.get("anuncios", 0) >= 5:
        li.append(f"<li>Cuando dijimos 30% o más, hubo tormenta <b>{round(t['anuncios_acertados'] / t['anuncios'] * 10)} de cada 10 veces</b>.</li>")
    ex = t.get("examen") or {}
    if ex.get("mejora_vs_clima_pct") is not None:
        m = ex["mejora_vs_clima_pct"]
        li.append(f"<li>En el examen sin trampa fue <b>{m}% más preciso</b> que decir siempre el promedio.</li>" if m > 0 else
                  "<li>En el examen sin trampa todavía <b>no superó</b> a decir siempre el promedio; tómalo como orientativo.</li>")
    return out + "<ul class='lst'>" + "".join(li) + "</ul>"


def datos_ciudad(p, ciudad, filas, normal=None):
    cfg, ref = cfg_efectivo(p, ciudad)
    propia = bool(p["ciudades"].get(ciudad, {}).get("verificado"))
    verif = html_verificacion(cfg, ref) if ref else html_verificacion(cfg, ciudad)
    verif += html_plazos(cfg)
    verif += html_tormenta(cfg.get("tormenta"), ref or ciudad)
    verif += html_temp(cfg.get("temp"), ref or ciudad)
    verif += html_reportes(p["ciudades"].get(ciudad, {}).get("reportes"))
    verif += ("<p class='nota'>Niebla, ráfagas de viento e índice UV vienen de los modelos y todavía "
              "<b>no</b> se verifican; tómalos como referencia.</p>")
    if ref and ref != ciudad:
        verif = (f"<p class='aviso'>{ciudad} todavía no tiene suficientes datos propios verificados. "
                 f"Mientras tanto usamos lo aprendido en {ref}.</p>" + verif)
    o = CIUDADES[ciudad]
    return {"nombre": ciudad, "lat": o["lat"], "lon": o["lon"], "zona": o["zona"], "propia": propia,
            "ref": ref, "nivel": nivel_fiabilidad(cfg),
            "cfg": {"umbral": cfg.get("umbral", UMBRAL_DEFECTO), "pesos": cfg.get("pesos", {}),
                    "tabla_lluvia": cfg.get("tabla_lluvia") or [], "tabla_llovizna": cfg.get("tabla_llovizna") or [],
                    "tabla_tormenta": (cfg.get("tormenta") or {}).get("tabla") or [],
                    "franjas": cfg.get("franjas") if (cfg.get("franjas") or {}).get("activa") else None,
                    "plazos": {d: {"verificado": z.get("verificado", False), "tabla": z.get("tabla") or [],
                                   "mejora": z.get("mejora_vs_clima_pct")}
                                 for d, z in (cfg.get("plazos") or {}).items()},
                    "temp": {k: (cfg.get("temp") or {}).get(k) for k in ("activa", "sesgo", "sesgo_hum")}},
            "normal_max": None if normal is None else round(normal - correccion_temp(cfg, "2000-01-01T15:00")[0], 1),
            "filas": [f if i < 96 else dict(f, detalle={}) for i, f in enumerate(filas)], "verif_html": verif}


# ----------------------- CALOR: TEMPERATURA NORMAL -----------------------
def normales_calor():
    """Promedio de la temperatura máxima de los últimos 30 días en cada ciudad. Sirve de referencia
       para saber si los próximos días serán más calurosos de lo normal (onda de calor)."""
    nombres = list(CIUDADES)
    params = {"latitude": ",".join(str(CIUDADES[c]["lat"]) for c in nombres),
              "longitude": ",".join(str(CIUDADES[c]["lon"]) for c in nombres),
              "daily": "temperature_2m_max", "past_days": 30, "forecast_days": 1, "timezone": ZONA}
    resp = json.loads(http_get(URL_FORECAST, params))
    if isinstance(resp, dict):
        resp = [resp]
    hoy = date.today().isoformat()
    out = {}
    for c, d in zip(nombres, resp):
        vals = [v for t, v in zip(d["daily"]["time"], d["daily"]["temperature_2m_max"]) if t < hoy and v is not None]
        if len(vals) >= 15:
            out[c] = round(statistics.mean(vals), 1)
    return out


# ----------------------- REPORTES DE USUARIOS -----------------------
def archivo_rep(mes):
    return os.path.join(DATOS, "reportes", f"{mes}.json")


def hora_local_ts(ts):
    return datetime(1970, 1, 1) + timedelta(seconds=ts + OFFSET_S)


def descargar_reportes():
    """Trae los reportes nuevos del servicio y los guarda por mes. En el repositorio (público) la
       ubicación se guarda redondeada a 0,05° (~5 km), suficiente para comparar con cada ciudad."""
    if not (FEEDBACK_URL and FEEDBACK_TOKEN):
        return 0
    carpeta = os.path.join(DATOS, "reportes")
    os.makedirs(carpeta, exist_ok=True)
    marca = os.path.join(carpeta, "ultimo_id.txt")
    ultimo = int(open(marca, encoding="utf-8").read().strip() or 0) if os.path.exists(marca) else 0
    total = 0
    for _ in range(20):                                      # hasta 100 000 reportes por ejecución
        req = urllib.request.Request(f"{FEEDBACK_URL}/export?despues={ultimo}",
                                     headers={"Authorization": f"Bearer {FEEDBACK_TOKEN}", "User-Agent": f"ClearSky (+{REPO_URL})"})
        with urllib.request.urlopen(req, timeout=60) as r:
            filas = json.loads(r.read().decode("utf-8"))
        if not filas:
            break
        por_mes = {}
        for f in filas:
            if f.get("tipo") in TIPOS_REPORTE:
                ts = int(f["ts"])
                por_mes.setdefault(hora_local_ts(ts).strftime("%Y-%m"), []).append(
                    [ts, round(round(float(f["lat"]) * 20) / 20, 2), round(round(float(f["lon"]) * 20) / 20, 2), f["tipo"]])
        for mes, nuevos in por_mes.items():
            ruta = archivo_rep(mes)
            act = json.load(open(ruta, encoding="utf-8")) if os.path.exists(ruta) else []
            act = sorted(act + nuevos)
            tmp = ruta + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(act, fh, separators=(",", ":"))
            os.replace(tmp, ruta)
        ultimo = max(int(f["id"]) for f in filas)
        with open(marca, "w", encoding="utf-8") as fh:
            fh.write(str(ultimo))
        total += len(filas)
        if len(filas) < 5000:
            break
    return total


def leer_reportes(dias):
    hoy = date.today()
    meses = sorted({(hoy - timedelta(days=d)).strftime("%Y-%m") for d in range(dias + 1)})
    limite = time.time() - dias * 86400
    out = []
    for m in meses:
        ruta = archivo_rep(m)
        if os.path.exists(ruta):
            out += [r for r in json.load(open(ruta, encoding="utf-8")) if r[0] >= limite]
    return out


def verificar_reportes(ciudad, reportes):
    """Compara lo que publicó la página (24 h antes) con lo que reportó la gente cerca de la ciudad."""
    o = CIUDADES[ciudad]
    cerca = [r for r in reportes if distancia_km(o["lat"], o["lon"], r[1], r[2]) <= RADIO_REPORTES_KM]
    pub = leer_pronosticos_publicados(ciudad)
    pares = []
    for ts, _, _, tipo in cerca:
        k = hora_local_ts(ts).strftime("%Y-%m-%dT%H:00")
        if k in pub and tipo in TIPOS_REPORTE:
            pares.append((pub[k], TIPOS_REPORTE[tipo]))
    res = {"reportes": len(cerca), "comparables": len(pares)}
    if len(pares) < MIN_REPORTES:
        return res
    llueve = [p for p, x in pares if x]
    seco = [p for p, x in pares if not x]
    anuncios = [x for p, x in pares if p >= 40]
    res.update(con_lluvia=len(llueve),
               prob_media_lluvia=round(statistics.mean(llueve)) if llueve else None,
               prob_media_seco=round(statistics.mean(seco)) if seco else None,
               anuncios=len(anuncios), anuncios_acertados=sum(anuncios),
               brier=brier([p for p, _ in pares], [x for _, x in pares]))
    return res


def html_reportes(r):
    if not FEEDBACK_URL:
        return ""
    out = "<h4>Reportes de las personas</h4>"
    if not r or r.get("comparables", 0) < MIN_REPORTES:
        n = (r or {}).get("reportes", 0)
        return out + (f"<p class='nota'>Llevamos {n} reportes cerca de esta ciudad. Con {MIN_REPORTES} o más que coincidan "
                      "con un pronóstico publicado mostraremos aquí cuánto acierta según lo que ve la gente. "
                      "¡Reporta desde <b>¿Cómo está el clima donde estás?</b>!</p>")
    li = [f"<li>Comparamos el pronóstico con <b>{r['comparables']} reportes</b> de personas a menos de {RADIO_REPORTES_KM} km.</li>"]
    if r.get("prob_media_lluvia") is not None and r.get("prob_media_seco") is not None:
        li.append(f"<li>Cuando la gente reportó lluvia, habíamos dado en promedio <b>{r['prob_media_lluvia']}%</b>; "
                  f"cuando reportó que no llovía, <b>{r['prob_media_seco']}%</b>.</li>")
    if r.get("anuncios", 0) >= 5:
        li.append(f"<li>Cuando dijimos 40% o más, la gente vio lluvia <b>{round(r['anuncios_acertados'] / r['anuncios'] * 10)} de cada 10 veces</b>.</li>")
    return (out + "<ul class='lst'>" + "".join(li) + "</ul><p class='nota'>Por ahora los reportes sirven para comprobar "
            "el pronóstico en los barrios; todavía no cambian los porcentajes, porque necesitamos muchos para que sean "
            "fiables y no se puedan manipular.</p>")


# ----------------------- ALERTAS DE CAMBIO BRUSCO -----------------------
def ultima_publicacion(ciudad):
    """Probabilidades de la ejecución anterior (la más reciente con al menos 30 min de antigüedad)."""
    limite = datetime.now() - timedelta(minutes=30)
    filas = {}
    for d in (date.today() - timedelta(days=1), date.today()):
        ruta = archivo_log(d)
        if not os.path.exists(ruta):
            continue
        with open(ruta, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("ciudad") != ciudad or row.get("modelo") != "CONSENSO":
                    continue
                try:
                    ej = datetime.strptime(row["ejecucion"], "%Y-%m-%d %H:%M")
                except Exception:
                    continue
                if ej <= limite:
                    filas.setdefault(ej, {})[row["hora_valida"]] = float(row["prob_lluvia"] or 0)
    return filas[max(filas)] if filas else {}


def detectar_alertas(resultados):
    """Si en las próximas horas la lluvia sube de golpe (por ejemplo de 20% a 70%), guarda un aviso con
       un texto listo para publicar (redes sociales). Devuelve las alertas vigentes."""
    try:
        alertas = json.load(open(ARCHIVO_ALERTAS, encoding="utf-8")) if os.path.exists(ARCHIVO_ALERTAS) else []
    except Exception:
        alertas = []
    ahora = datetime.now()
    desde = ahora.strftime("%Y-%m-%dT%H:00")
    hasta = (ahora + timedelta(hours=ALERTA_HORAS)).strftime("%Y-%m-%dT%H:00")
    for c, filas in resultados.items():
        antes = ultima_publicacion(c)
        if not antes:
            continue
        cands = [r for r in filas if desde <= r["hora"] <= hasta and r["hora"] in antes
                 and r["prob"] >= ALERTA_MIN and r["prob"] - antes[r["hora"]] >= ALERTA_SALTO]
        if not cands:
            continue
        r = max(cands, key=lambda x: x["prob"])
        recientes = [a for a in alertas if a["ciudad"] == c and
                     ahora - datetime.fromisoformat(a["creado"]) < timedelta(hours=ALERTA_HORAS)]
        if recientes:
            continue
        tormenta = r.get("prob_ts", 0) >= 30
        texto = (f"{'⛈️ Tormenta' if tormenta else '🌧️ Lluvia'} probable en {c} hacia las {r['hora'][11:16]}: "
                 f"{r['prob']}% (antes {round(antes[r['hora']])}%). Pronóstico verificado: https://suirobert.github.io/ClearSky/")
        alertas.append({"creado": ahora.isoformat(timespec="seconds"), "ciudad": c, "hora": r["hora"],
                        "prob": r["prob"], "antes": round(antes[r["hora"]]), "tormenta": tormenta, "texto": texto})
        print(f"  ALERTA {c}: {texto}")
    alertas = alertas[-200:]
    tmp = ARCHIVO_ALERTAS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(alertas, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ARCHIVO_ALERTAS)
    return [a for a in alertas if a["hora"] >= desde and ahora - datetime.fromisoformat(a["creado"]) < timedelta(hours=3)]


# ----------------------- RESPALDO SEMANAL DE REPORTES -----------------------
def respaldo_reportes():
    """Una vez por semana descarga TODOS los reportes del servicio y guarda una copia comprimida
       (ubicación redondeada a ~5 km). Avisa si faltan reportes en el repositorio."""
    if not (FEEDBACK_URL and FEEDBACK_TOKEN):
        return
    import gzip
    carpeta = os.path.join(DATOS, "respaldos")
    os.makedirs(carpeta, exist_ok=True)
    a, w, _ = date.today().isocalendar()
    ruta = os.path.join(carpeta, f"reportes-{a}-S{w:02d}.json.gz")
    if os.path.exists(ruta):
        return
    todos, ultimo = [], 0
    for _ in range(200):
        req = urllib.request.Request(f"{FEEDBACK_URL}/export?despues={ultimo}",
                                     headers={"Authorization": f"Bearer {FEEDBACK_TOKEN}", "User-Agent": f"ClearSky (+{REPO_URL})"})
        with urllib.request.urlopen(req, timeout=60) as r:
            filas = json.loads(r.read().decode("utf-8"))
        if not filas:
            break
        todos += [[int(f["id"]), int(f["ts"]), round(round(float(f["lat"]) * 20) / 20, 2),
                   round(round(float(f["lon"]) * 20) / 20, 2), f["tipo"]] for f in filas]
        ultimo = max(int(f["id"]) for f in filas)
        if len(filas) < 5000:
            break
    with gzip.open(ruta, "wt", encoding="utf-8") as f:
        json.dump({"creado": datetime.now().isoformat(timespec="seconds"), "total": len(todos), "reportes": todos}, f)
    locales = sum(len(json.load(open(x, encoding="utf-8"))) for x in glob.glob(os.path.join(DATOS, "reportes", "*.json")))
    if locales < len(todos):
        log_error(f"respaldo: el servicio tiene {len(todos)} reportes y el repositorio {locales}")
    for viejo in sorted(glob.glob(os.path.join(carpeta, "reportes-*.json.gz")))[:-SEMANAS_RESPALDO]:
        os.remove(viejo)
    print(f"Respaldo semanal: {len(todos)} reportes")


# ----------------------- LLUVIA AHORA (SATÉLITE) -----------------------
def s3_listar(prefijo):
    """Lista un nivel del bucket público de NOAA (sin cuenta). Devuelve (subcarpetas, archivos)."""
    import xml.etree.ElementTree as ET
    carpetas, archivos, token = [], [], None
    for _ in range(20):
        params = {"list-type": "2", "prefix": prefijo, "delimiter": "/"}
        if token:
            params["continuation-token"] = token
        raiz = ET.fromstring(http_get(URL_ERR + "/", params, intentos=2))
        token = None
        for e in raiz:
            tag = e.tag.split("}")[-1]
            hijos = {h.tag.split("}")[-1]: h.text for h in e}
            if tag == "CommonPrefixes":
                carpetas.append(hijos.get("Prefix"))
            elif tag == "Contents":
                archivos.append({"key": hijos.get("Key"), "mod": hijos.get("LastModified") or "",
                                 "size": int(hijos.get("Size") or 0)})
            elif tag == "NextContinuationToken":
                token = e.text
        if not token:
            break
    return carpetas, archivos


# Satélites geoestacionarios que ven Ecuador, en orden de preferencia (GOES-Este está justo encima)
SAT_PREFERIDOS = ["G19", "GOES-19", "BLEND", "G16", "GOES-16", "G18", "GOES-18"]   # carpetas reales: G19, BLEND, G16, G18


def err_ultimo():
    """Busca el archivo más reciente de lluvia instantánea del satélite GOES que ve Ecuador."""
    carpetas, _ = s3_listar("")
    nombre = lambda c: c.rstrip("/").rsplit("/", 1)[-1]
    raices = [c for p in SAT_PREFERIDOS for c in carpetas if nombre(c).upper().startswith(p.upper())]
    if not raices:
        raise RuntimeError("no hay carpeta de GOES/BLEND en NOAA; carpetas: " + ", ".join(nombre(c) for c in carpetas))
    errores = []
    for raiz in raices[:3]:
        pref, recorrido = raiz, [nombre(raiz)]
        try:
            for _ in range(8):
                carpetas, archivos = s3_listar(pref)
                nc = [a for a in archivos if a["key"].endswith(".nc")]
                inst = [a for a in nc if "INST" in a["key"].upper()]
                if inst or nc:
                    return max(inst or nc, key=lambda a: (a["mod"], a["key"]))
                if not carpetas:
                    break
                lluvia = [c for c in carpetas if "RAIN" in nombre(c).upper() or "RRQPE" in nombre(c).upper()]
                cands = ([c for c in (lluvia or carpetas) if "INST" in nombre(c).upper()] or lluvia or
                         [c for c in carpetas if nombre(c).isdigit()] or carpetas)
                pref = max(cands)
                recorrido.append(nombre(pref))
            errores.append(" > ".join(recorrido))
        except Exception as e:
            errores.append(f"{' > '.join(recorrido)}: {e}")
    raise RuntimeError("no se encontró el archivo de lluvia; recorrido: " + " | ".join(errores))


def importar_netcdf():
    try:
        import netCDF4, numpy
        return netCDF4, numpy
    except ImportError:
        if not os.environ.get("GITHUB_ACTIONS"):
            raise
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "netCDF4", "numpy"], check=True, timeout=300)
        import netCDF4, numpy
        return netCDF4, numpy


def leer_lluvia_nc(ruta):
    """Lee la lluvia instantánea (mm/h) y la promedia en una cuadrícula de 0,1° sobre Ecuador.
       Acepta latitud/longitud en 1D o 2D (disco completo del satélite) y longitudes 0-360."""
    nc4, np = importar_netcdf()
    s0, n0, w0, e0 = AHORA_CAJA
    ny, nx = round((n0 - s0) / AHORA_PASO), round((e0 - w0) / AHORA_PASO)
    with nc4.Dataset(ruta) as ds:
        variables = {}
        def recorrer(g):
            variables.update({k.lower(): v for k, v in g.variables.items()})
            for sub in g.groups.values():
                recorrer(sub)
        recorrer(ds)
        lat = next((variables[k] for k in ("lat", "latitude", "lats") if k in variables), None)
        lon = next((variables[k] for k in ("lon", "longitude", "lons") if k in variables), None)
        if lat is None or lon is None:
            raise RuntimeError("no hay latitud/longitud; variables: " + ", ".join(sorted(variables))[:300])
        cands = [(k, v) for k, v in variables.items() if v.ndim >= 2 and k not in ("lat", "lon", "latitude", "longitude")
                 and any(t in k for t in ("rain", "rrqpe", "precip")) and not any(t in k for t in ("dqf", "qual", "flag", "mask"))]
        if not cands:
            raise RuntimeError("no hay variable de lluvia; variables: " + ", ".join(sorted(variables))[:300])
        nombre, var = max(cands, key=lambda kv: int(np.prod(kv[1].shape)))
        var.set_auto_maskandscale(True)
        a32 = lambda x: np.ma.filled(np.ma.asarray(x, dtype=np.float32), np.nan)
        if lat.ndim == 1 and lon.ndim == 1:
            la, lo = a32(lat[:]), a32(lon[:])
            lo = np.where(lo > 180, lo - 360, lo)
            iy = np.where((la >= s0) & (la <= n0))[0]
            ix = np.where((lo >= w0) & (lo <= e0))[0]
            if not len(iy) or not len(ix):
                raise RuntimeError(f"el archivo no cubre Ecuador (lat {np.nanmin(la):.1f}..{np.nanmax(la):.1f}, lon {np.nanmin(lo):.1f}..{np.nanmax(lo):.1f})")
            y0, y1, x0, x1 = iy.min(), iy.max() + 1, ix.min(), ix.max() + 1
            forma = [d for d in var.shape if d != 1]
            if forma == [len(la), len(lo)]:
                datos = a32(var[..., y0:y1, x0:x1]).reshape(y1 - y0, x1 - x0)
            elif forma == [len(lo), len(la)]:
                datos = a32(var[..., x0:x1, y0:y1]).reshape(x1 - x0, y1 - y0).T
            else:
                raise RuntimeError(f"forma inesperada de la lluvia {var.shape}")
            LA, LO = np.meshgrid(la[y0:y1], lo[x0:x1], indexing="ij")
        else:
            # disco completo (por ejemplo 5500 x 5500): se busca el recuadro de Ecuador por bloques
            # y solo se lee ese pedazo, para no cargar el disco entero en memoria
            filas, cols = lat.shape[-2], lat.shape[-1]
            r_ok, c_min, c_max = [], cols, -1
            for r0 in range(0, filas, 500):
                la = a32(lat[r0:r0 + 500, :]); lo = a32(lon[r0:r0 + 500, :])
                lo = np.where(lo > 180, lo - 360, lo)
                m = np.isfinite(la) & np.isfinite(lo) & (la >= s0) & (la <= n0) & (lo >= w0) & (lo <= e0)
                if m.any():
                    rr, cc = np.where(m)
                    r_ok += [r0 + rr.min(), r0 + rr.max()]
                    c_min, c_max = min(c_min, cc.min()), max(c_max, cc.max())
            if not r_ok:
                raise RuntimeError("el satélite de este archivo no ve Ecuador")
            y0, y1, x0, x1 = min(r_ok), max(r_ok) + 1, c_min, c_max + 1
            LA, LO = a32(lat[y0:y1, x0:x1]), a32(lon[y0:y1, x0:x1])
            LO = np.where(LO > 180, LO - 360, LO)
            datos = a32(var[..., y0:y1, x0:x1]).reshape(LA.shape)
    LA, LO, datos = LA.ravel(), LO.ravel(), datos.ravel()
    m = np.isfinite(LA) & np.isfinite(LO) & (LA >= s0) & (LA <= n0) & (LO >= w0) & (LO <= e0)
    LA, LO, datos = LA[m], LO[m], datos[m]
    if LA.size < 100:
        raise RuntimeError(f"el archivo no trae datos sobre Ecuador (variable {nombre})")
    datos = np.where(np.isfinite(datos), np.clip(datos, 0, 300), 0.0)     # 'sin dato' = no llueve
    gy = np.clip(((n0 - LA) / AHORA_PASO).astype(int), 0, ny - 1)
    gx = np.clip(((LO - w0) / AHORA_PASO).astype(int), 0, nx - 1)
    suma, cuenta = np.zeros(ny * nx), np.zeros(ny * nx)
    np.add.at(suma, gy * nx + gx, datos)
    np.add.at(cuenta, gy * nx + gx, 1)
    media = np.where(cuenta > 0, suma / np.maximum(cuenta, 1), 0)
    return media.reshape(ny, nx), None, float(LA.size)


def lluvia_ahora():
    """Lluvia estimada por satélite en este momento sobre Ecuador (cuadrícula de 0,1°)."""
    import base64, tempfile
    ult = err_ultimo()
    m = re.search(r"_s(\d{12})", ult["key"])
    utc = datetime.strptime(m.group(1), "%Y%m%d%H%M") if m else datetime.strptime(ult["mod"][:16], "%Y-%m-%dT%H:%M")
    local = utc + timedelta(seconds=OFFSET_S)
    edad_h = (datetime.now() - local).total_seconds() / 3600
    if edad_h > AHORA_MAX_H:
        raise RuntimeError(f"la imagen más reciente tiene {edad_h:.1f} h ({ult['key']})")
    if ult["size"] > 600 * 2 ** 20:
        raise RuntimeError(f"archivo demasiado grande ({ult['size'] // 2 ** 20} MB)")
    fd, ruta = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    try:
        req = urllib.request.Request(f"{URL_ERR}/{urllib.parse.quote(ult['key'])}", headers={"User-Agent": f"ClearSky (+{REPO_URL})"})
        with urllib.request.urlopen(req, timeout=180) as r, open(ruta, "wb") as f:
            while True:
                bloque = r.read(1 << 20)
                if not bloque:
                    break
                f.write(bloque)
        try:
            rejilla, _, pixeles = leer_lluvia_nc(ruta)
        except Exception as e:
            raise RuntimeError(f"{e} (archivo {ult['key']})")
    finally:
        os.remove(ruta)
    import numpy as np
    q = np.clip(np.round(rejilla * 2), 0, 255).astype("uint8")       # pasos de 0,5 mm/h
    s0, n0, w0, e0 = AHORA_CAJA
    res = {"t": local.strftime("%Y-%m-%dT%H:%M"), "lat0": n0, "lon0": w0, "paso": AHORA_PASO,
           "ny": int(q.shape[0]), "nx": int(q.shape[1]), "escala": 0.5,
           "q": base64.b64encode(q.tobytes()).decode("ascii"), "fuente": ult["key"].rsplit("/", 1)[-1]}
    guardar_lluvia_ciudades(res, rejilla)
    return res


def lluvia_en(rejilla, lat, lon, radio=1):
    s0, n0, w0, e0 = AHORA_CAJA
    iy, ix = int((n0 - lat) / AHORA_PASO), int((lon - w0) / AHORA_PASO)
    vals = [rejilla[y][x] for y in range(iy - radio, iy + radio + 1) for x in range(ix - radio, ix + radio + 1)
            if 0 <= y < len(rejilla) and 0 <= x < len(rejilla[0])]
    return round(float(max(vals)), 1) if vals else None


def guardar_lluvia_ciudades(res, rejilla):
    """Guarda la lluvia satelital en cada ciudad, hora por hora: servirá para verificar sin depender
       solo de los aeropuertos."""
    carpeta = os.path.join(DATOS, "satelite")
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, res["t"][:7] + ".json")
    act = json.load(open(ruta, encoding="utf-8")) if os.path.exists(ruta) else {}
    k = res["t"][:13] + ":00"
    for c, o in CIUDADES.items():
        v = lluvia_en(rejilla, o["lat"], o["lon"], 0)
        if v is not None:
            act.setdefault(c, {})[k] = v
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(act, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, ruta)


# ----------------------- MAPA DE LLUVIA -----------------------
def zona_de(lat, lon, elev):
    if lon < -88: return "galapagos"
    if elev >= 1500: return "sierra"
    return "oriente" if lon > -78.3 else "costa"


def cfg_para_punto(p, lat, lon, elev):
    """Igual que referencia() en el JavaScript: ciudad verificada más cercana de la misma zona."""
    verif = [c for c in CIUDADES if p["ciudades"].get(c, {}).get("verificado")] or list(CIUDADES)
    z = zona_de(lat, lon, elev)
    misma = [c for c in verif if CIUDADES[c]["zona"] == z] or verif
    ref = min(misma, key=lambda c: distancia_km(lat, lon, CIUDADES[c]["lat"], CIUDADES[c]["lon"]))
    return p["ciudades"].get(ref) or cfg_defecto()


def generar_mapa(p):
    """Probabilidad calibrada de lluvia en una cuadrícula sobre Ecuador, hora por hora."""
    pts = [(la, lo) for la in MAPA_LATS for lo in MAPA_LONS]
    params = {"latitude": ",".join(str(a) for a, _ in pts), "longitude": ",".join(str(b) for _, b in pts),
              "hourly": "precipitation", "models": ",".join(MODELOS), "timezone": ZONA, "forecast_days": DIAS_MAPA}
    resp = json.loads(http_get(URL_FORECAST, params))
    if isinstance(resp, dict):
        resp = [resp]
    ahora = datetime.now().strftime("%Y-%m-%dT%H:00")
    horas = [t for t in resp[0]["hourly"]["time"] if t >= ahora]
    p_horas = [[] for _ in horas]
    for (la, lo), d in zip(pts, resp):
        cfg = cfg_para_punto(p, la, lo, d.get("elevation") or 0)
        h = d["hourly"]
        idx = {t: i for i, t in enumerate(h["time"])}
        for j, t in enumerate(horas):
            i = idx.get(t)
            vals = {}
            for m in MODELOS:
                sv = serie_modelo(h, "precipitation", m)
                if i is not None and sv and sv[i] is not None:
                    vals[m] = sv[i]
            p_horas[j].append(round(calibrar_prob(prob_consenso(vals, cfg["pesos"], cfg["umbral"]),
                                                  cfg.get("tabla_lluvia"))) if vals else -1)
    return {"lats": MAPA_LATS, "lons": MAPA_LONS, "paso": 0.75, "horas": horas, "p": p_horas}


# ----------------------- ÍCONOS -----------------------
def obtener_iconos():
    carpeta = os.path.join(DATOS, "iconos")
    os.makedirs(carpeta, exist_ok=True)
    marca = os.path.join(carpeta, "ultimo_intento.txt")
    hoy = date.today().isoformat()
    intentar = not os.path.exists(marca) or open(marca, encoding="utf-8").read().strip() != hoy
    iconos, faltan = {}, False
    for n in ICONOS_METEOCONS:
        ruta = os.path.join(carpeta, n + ".svg")
        if not os.path.exists(ruta) and intentar:
            for u in URLS_ICONOS:
                try:
                    req = urllib.request.Request(u.format(n=n), headers={"User-Agent": f"ClearSky/3.0 (+{REPO_URL})"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        txt = r.read(300000).decode("utf-8", errors="replace")
                    if "<svg" in txt and "<script" not in txt.lower():
                        with open(ruta, "w", encoding="utf-8") as f:
                            f.write(txt)
                        break
                except Exception:
                    continue
        if os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as f:
                iconos[n] = f.read()
        else:
            faltan = True
    if faltan and intentar:
        with open(marca, "w", encoding="utf-8") as f:
            f.write(hoy)
        log_error("no se pudieron descargar algunos íconos Meteocons; se usan los íconos simples")
    return iconos


# ----------------------- APP INSTALABLE (PWA) -----------------------
def png(tam, pixel):
    import zlib, struct
    filas = b"".join(b"\x00" + bytes(c for x in range(tam) for c in pixel(x, y)) for y in range(tam))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", tam, tam, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(filas, 9)) + chunk(b"IEND", b""))


def icono_app(tam):
    """Ícono de la app: sol detrás de una nube sobre fondo oscuro (dibujado sin librerías)."""
    def pixel(x, y):
        u, v = x / tam, y / tam
        col = (11, 15, 23)
        if (u - 0.42) ** 2 + (v - 0.40) ** 2 < 0.20 ** 2:
            col = (251, 191, 36)
        nube = ((u - 0.40) ** 2 + (v - 0.62) ** 2 < 0.15 ** 2 or (u - 0.58) ** 2 + (v - 0.55) ** 2 < 0.19 ** 2 or
                (u - 0.74) ** 2 + (v - 0.64) ** 2 < 0.12 ** 2 or (0.40 <= u <= 0.74 and 0.60 <= v <= 0.76))
        if nube and v <= 0.76:
            col = (226, 232, 240)
        return col
    return png(tam, pixel)


SW_JS = """const C='clearsky-v3';
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.addAll(['./','manifest.webmanifest','icon-192.png'])).catch(()=>{}));});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==C).map(x=>caches.delete(x)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',e=>{const r=e.request;if(r.method!=='GET'||new URL(r.url).origin!==location.origin)return;
e.respondWith(fetch(r).then(res=>{const cp=res.clone();caches.open(C).then(c=>c.put(r,cp));return res;}).catch(()=>caches.match(r).then(m=>m||caches.match('./'))));});
self.addEventListener('push',e=>{let d={};try{d=e.data.json()}catch(x){d={title:'ClearSky',body:e.data?e.data.text():''}}
e.waitUntil(self.registration.showNotification(d.title||'ClearSky',{body:d.body||'',icon:'icon-192.png',badge:'icon-192.png',tag:d.tag||'clearsky',renotify:true,data:{url:d.url||'./'}}));});
self.addEventListener('notificationclick',e=>{e.notification.close();const u=(e.notification.data||{}).url||'./';
e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(cs=>{for(const c of cs){if('focus' in c)return c.focus();}return clients.openWindow(u);}));});
"""


def escribir_pwa(carpeta):
    manifest = {"name": "ClearSky · Pronóstico verificado", "short_name": "ClearSky", "lang": "es",
                "start_url": "./", "scope": "./", "display": "standalone", "background_color": "#0b0f17",
                "theme_color": "#0b0f17", "description": "Pronóstico del tiempo para Ecuador que mide en público cuánto acierta.",
                "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}
    archivos = {"manifest.webmanifest": json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"),
                "sw.js": SW_JS.encode("utf-8")}
    for nombre, tam in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        if not os.path.exists(os.path.join(carpeta, nombre)):
            archivos[nombre] = icono_app(tam)
    for nombre, contenido in archivos.items():
        with open(os.path.join(carpeta, nombre), "wb") as f:
            f.write(contenido)


def validar_pagina(doc, datos):
    """Pruebas antes de publicar: si algo falla, se conserva la página anterior y el workflow falla
       (GitHub avisa por correo)."""
    import shutil, subprocess, tempfile
    errores = []
    for marca in ("__CSS__", "__JS__", "__DATOS__"):
        if marca in doc:
            errores.append(f"quedó la marca {marca}")
    if not datos["ciudades"]:
        errores.append("no hay ciudades")
    for c in datos["ciudades"]:
        if len(c["filas"]) < 48:
            errores.append(f"{c['nombre']}: solo {len(c['filas'])} horas")
        for r in c["filas"][:48]:
            if not (0 <= r["prob"] <= 100) or (r["temp"] is not None and not -15 <= r["temp"] <= 50):
                errores.append(f"{c['nombre']}: valor fuera de rango a las {r['hora']}")
                break
    m = re.search(r'<script id="datos" type="application/json">(.*?)</script>', doc, re.S)
    try:
        json.loads(m.group(1).replace("<\\/", "</"))
    except Exception as e:
        errores.append(f"los datos de la página no son JSON válido: {e}")
    node = shutil.which("node")
    if node:
        fd, ruta = tempfile.mkstemp(suffix=".js")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(JS)
        try:
            r = subprocess.run([node, "--check", ruta], capture_output=True, text=True, timeout=60)
            if r.returncode:
                errores.append("error de sintaxis en el JavaScript: " + (r.stderr or "")[-400:])
        finally:
            os.remove(ruta)
    if errores:
        raise RuntimeError("la página no pasó las pruebas: " + " | ".join(errores[:8]))


def generar_html(resultados, p, mapa=None, iconos=None, alertas=None, ahora=None, normales=None):
    datos = {"generado": datetime.now().strftime("%Y-%m-%dT%H:%M"), "offset": OFFSET_S,
             "modelos": MODELOS, "umbral_llovizna": UMBRAL_LLOVIZNA, "feedback_url": FEEDBACK_URL,
             "repo": REPO_URL, "verificacion": p.get("actualizado"), "vars": VARS_PRON,
             "ciudades": [datos_ciudad(p, c, f, (normales or {}).get(c)) for c, f in resultados.items()],
             "mapa": mapa, "iconos": iconos or {}, "alertas": alertas or [], "ahora": ahora,
             "franjas": {k: list(v) for k, v in FRANJAS.items()}}
    js_datos = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    doc = PLANTILLA.replace("__CSS__", CSS).replace("__JS__", JS).replace("__DATOS__", js_datos)
    validar_pagina(doc, datos)
    carpeta = os.path.dirname(ARCHIVO_HTML)
    os.makedirs(carpeta, exist_ok=True)
    tmp = ARCHIVO_HTML + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, ARCHIVO_HTML)
    if os.environ.get("CC_HTML"):          # solo en la publicación (GitHub Pages), no en la carpeta del repo
        escribir_pwa(carpeta)
        # configuración que usa el servicio de avisos (Cloudflare) para calcular la lluvia de cada suscriptor
        push = {"generado": datos["generado"], "modelos": list(MODELOS), "franjas": datos["franjas"], "ahora": ahora,
                "ciudades": [{k: c[k] for k in ("nombre", "lat", "lon", "zona", "propia", "cfg")} for c in datos["ciudades"]]}
        with open(os.path.join(carpeta, "push.json"), "w", encoding="utf-8") as f:
            json.dump(push, f, ensure_ascii=False, separators=(",", ":"))


PLANTILLA = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0f17">
<meta name="color-scheme" content="dark light">
<script>(function(){var t=null;try{t=localStorage.getItem('cc_tema')}catch(e){}
if(t!=='claro'&&t!=='oscuro')t=matchMedia('(prefers-color-scheme: light)').matches?'claro':'oscuro';
document.documentElement.dataset.tema=t;document.querySelector('meta[name=theme-color]').content=t==='claro'?'#f4f6fa':'#0b0f17';})();</script>
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="ClearSky">
<meta name="format-detection" content="telephone=no">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="icon" href="icon-192.png">
<meta name="description" content="Pronóstico del tiempo para Ecuador que combina 7 modelos y mide en público cuánto acierta: lluvia, tormentas, calor y rayos UV, verificado cada día.">
<title>ClearSky · Pronóstico verificado</title>
<style>__CSS__</style></head><body>
<a class="saltar" href="#vista">Saltar al pronóstico</a>
<header class="top"><div class="brand">ClearSky</div><div class="topder"><button class="temabtn" id="temabtn" aria-label="Cambiar tema"></button><button class="lugarbtn" id="lugarbtn" aria-haspopup="dialog"><span id="lugarnom">Tu ubicación</span><span aria-hidden="true">▾</span></button></div></header>
<div class="ptr" id="ptr" aria-hidden="true">↻</div>
<main class="wrap"><div id="aviso"></div><div id="vista" tabindex="-1"><div class="vacio"><span class="cargando"></span>Cargando…</div></div><div class="pie" id="pie"></div></main>
<div class="sheet-bg" id="sheetbg" hidden></div>
<div class="sheet" id="sheet" role="dialog" aria-modal="true" aria-label="Elegir lugar" hidden><div id="sheetc"></div></div>
<div class="toast" id="toast" role="status" hidden></div>
<script id="datos" type="application/json">__DATOS__</script>
<script>__JS__</script></body></html>"""


CSS = r"""
:root{--bg:#0b0f17;--card:#111723;--card2:#151b26;--line:#1a2230;--tx:#e5e7eb;--tx2:#9ca3af;--tx3:#6b7280;--acc:#60a5fa;--acc2:#2563eb;--sat:env(safe-area-inset-top);--sab:env(safe-area-inset-bottom)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,"SF Pro Text","Segoe UI",Roboto,Arial,sans-serif;-webkit-font-smoothing:antialiased;overscroll-behavior-y:contain}
button,input,summary,.drow,.seg,.lg{touch-action:manipulation}
button{font:inherit;color:inherit}
.top{position:sticky;top:0;z-index:50;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:calc(10px + var(--sat)) 16px 10px;background:rgba(11,15,23,.82);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);border-bottom:1px solid rgba(255,255,255,.04)}
.top>*{max-width:760px}
.brand{font-weight:700;font-size:16px;letter-spacing:.3px;color:#cbd5e1}
.lugarbtn{display:flex;align-items:center;gap:6px;max-width:52vw;min-height:40px;border:0;background:var(--card2);border-radius:999px;padding:8px 14px;font-size:14px;cursor:pointer;transition:transform .12s,background .2s}
.lugarbtn span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lugarbtn:active{transform:scale(.96);background:#1d2533}
.wrap{max-width:760px;margin:0 auto;padding:6px 16px calc(40px + var(--sab))}
.ptr{position:fixed;left:50%;top:calc(56px + var(--sat));z-index:40;width:36px;height:36px;margin-left:-18px;border-radius:50%;background:var(--card2);display:flex;align-items:center;justify-content:center;color:var(--acc);opacity:0;transform:translateY(-40px);transition:opacity .2s;pointer-events:none}
.ptr.listo{color:#fbbf24}.ptr.gira{animation:gira 1s linear infinite;opacity:1}
@keyframes gira{to{transform:rotate(360deg)}}
.hero{display:flex;align-items:center;gap:14px;margin:20px 0 4px}
.hero .ic{flex:none}
.hero .t{font-size:64px;font-weight:200;line-height:1}
.hero .t sup{font-size:28px;vertical-align:top}
.hero h1{font-size:21px;font-weight:600;margin:0 0 6px;line-height:1.25}
.hero p{margin:0;color:var(--tx2);font-size:14px;line-height:1.45}
.lugar{color:var(--tx2);font-size:13px;margin-top:12px;line-height:1.5}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 0}
.chip{background:var(--card2);border-radius:999px;padding:6px 11px;font-size:12.5px;color:#cbd5e1}
.chip b{color:#fff}.chip.ts{background:#2a1f4a;color:#ddd6fe}.chip.ts b{color:#fff}
.card{background:var(--card);border-radius:18px;padding:16px;margin-top:14px;position:relative}
.card h3{margin:0 0 12px;font-size:13px;font-weight:600;color:var(--tx2);text-transform:uppercase;letter-spacing:.6px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.card h3 .sub{text-transform:none;letter-spacing:0;font-weight:400;font-size:12px;color:var(--tx3)}
.card h3 button{background:#1f2937;border:0;color:#93c5fd;border-radius:999px;padding:6px 12px;font-size:12px;cursor:pointer;text-transform:none;letter-spacing:0;min-height:32px}
.aviso-top{background:#13233d;border-radius:16px;padding:14px;margin-top:14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:14px;line-height:1.45}
.aviso-top p{margin:0;flex:1;min-width:180px}
.tl{position:relative;padding-top:20px;user-select:none;-webkit-user-select:none}
.blqs{position:relative;height:18px;margin-top:-20px}
.blq{position:absolute;top:0;font-size:11px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-left:4px}
.bar{display:flex;height:30px;border-radius:8px;overflow:hidden;touch-action:pan-y;cursor:pointer}
.seg{flex:1;border-right:1px solid var(--bg);transition:filter .15s}
.seg.sel{filter:brightness(1.35);box-shadow:inset 0 0 0 2px #f59e0b}
.tks{position:relative;height:38px}
.tk{position:absolute;top:6px;font-size:11px;color:var(--tx2);display:flex;flex-direction:column;align-items:flex-start}
.tk b{font-weight:500}.tk span{color:var(--tx);font-size:13px;margin-top:2px}
.tldet{font-size:13px;color:#cbd5e1;min-height:20px;margin-top:2px}
.graf{display:block;touch-action:pan-y;user-select:none;-webkit-user-select:none;cursor:crosshair}
.graf .grid{stroke:#1f2937;stroke-width:1}
.graf .dia{stroke:#374151;stroke-dasharray:3 3}
.graf .ahora{stroke:#f59e0b;stroke-width:1.5}
.graf .linea{fill:none;stroke:#60a5fa;stroke-width:2}
.graf .lineadz{fill:none;stroke:#94a3b8;stroke-width:1.3;stroke-dasharray:4 3}
.graf .lineats{fill:none;stroke:#a78bfa;stroke-width:1.6;stroke-dasharray:2 3}
.graf .mm{fill:#1e40af;opacity:.55}
.graf text{fill:var(--tx3);font-size:11px}
.graf .ejey{text-anchor:end}.graf .ejex{text-anchor:middle}
.graf .etdia{fill:var(--tx2);font-weight:600}
.graf .cursor{stroke:#e5e7eb;stroke-width:1}
.graf .punto{fill:#60a5fa;stroke:var(--bg);stroke-width:2}
.tip{position:absolute;z-index:5;pointer-events:none;background:#1f2937;border:1px solid #334155;border-radius:12px;padding:8px 11px;font-size:12.5px;line-height:1.5;min-width:150px;box-shadow:0 8px 24px rgba(0,0,0,.45)}
.tip b{color:#fff}
.ley{display:flex;gap:6px 14px;font-size:12px;color:var(--tx2);margin-top:10px;flex-wrap:wrap}
.ley i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:5px;border-radius:2px}
.lg{cursor:pointer;user-select:none;padding:6px 2px;min-height:30px;display:inline-flex;align-items:center}.lg.off{opacity:.35;text-decoration:line-through}
.drow{display:grid;grid-template-columns:84px 34px 44px 104px 32px 1fr 32px;align-items:center;gap:8px;padding:12px 6px;border-top:1px solid var(--line);font-size:15px;cursor:pointer;border-radius:10px;min-height:52px;transition:background .15s}
.drow:first-child{border-top:0}
.drow:active{background:#182133}.drow.sel{background:#16213a}
@media(hover:hover){.drow:hover{background:#151d2b}}
.dn{font-weight:500}.dn small{display:block;font-size:10.5px;color:var(--tx3);font-weight:400;margin-top:1px}.pp{color:#60a5fa;font-size:13px;font-weight:600}.pp.off{color:#4b5563}
.dmm{color:var(--tx3);font-size:12px}.tmin{color:var(--tx2);text-align:right}.tmax{text-align:left}
.trng{position:relative;height:6px;background:#1f2937;border-radius:3px}
.trng div{position:absolute;top:0;height:6px;border-radius:3px;background:linear-gradient(90deg,#60a5fa,#fbbf24)}
.dexp{padding:6px 6px 14px;border-top:1px solid var(--line);animation:abre .25s ease-out}
@keyframes abre{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
.dexp p{margin:6px 0 10px;font-size:13.5px;color:#cbd5e1;line-height:1.5}
details{margin-top:14px;background:var(--card);border-radius:18px;padding:0 16px}
summary{cursor:pointer;font-size:14.5px;color:#93c5fd;list-style:none;padding:16px 0;min-height:48px}
summary::-webkit-details-marker{display:none}
summary:before{content:"▸ ";color:#4b5563}details[open] summary:before{content:"▾ "}
details[open]{padding-bottom:14px}
.scroll{overflow-x:auto;margin-top:10px;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap}
th,td{padding:5px 7px;text-align:center;border-bottom:1px solid var(--line)}
th{color:var(--tx2);font-weight:500;background:#0f1520;position:sticky;top:0}
tr.dsep td{background:var(--line);text-align:left;font-weight:600;color:#cbd5e1}
tr.star td{background:#12351f}tr.alt td{background:#13233d}
.nota{font-size:12.5px;color:var(--tx2);line-height:1.6}
.aviso{font-size:13px;color:#fcd34d;background:#2a2312;border-radius:10px;padding:8px 10px;line-height:1.5}
h4{font-size:12px;color:var(--tx2);font-weight:600;margin:14px 0 6px}
.calib .cb{display:grid;grid-template-columns:110px 1fr 80px;gap:8px;align-items:center;font-size:12px;margin:4px 0}
.calib .cb div{height:8px;background:#1f2937;border-radius:4px;overflow:hidden}
.calib .cb i{display:block;height:8px;background:#3b82f6}
.calib em{color:var(--tx2);font-style:normal}
.plano{font-size:16px;margin:6px 0}.lst{font-size:14px;line-height:1.7;padding-left:18px;margin:6px 0}
details.tec{background:#0d121b;margin-top:14px}
.fbb{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.fbb button.elegido{outline:2px solid var(--acc)}.fbe{font-size:22px;line-height:1}
.fbb button{min-width:0;min-height:60px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;background:#1f2937;border:1px solid #334155;border-radius:14px;padding:10px;cursor:pointer;font-size:14px;transition:transform .12s}
.fbb button:active{transform:scale(.96)}.fbb button:disabled{opacity:.5;cursor:default}
.fbres{font-size:13px;color:#cbd5e1;margin-top:8px}
.vacio{padding:40px 10px;text-align:center;color:var(--tx2)}
.cargando{display:inline-block;width:22px;height:22px;border:2px solid #334155;border-top-color:var(--acc);border-radius:50%;animation:gira .8s linear infinite;vertical-align:middle;margin-right:8px}
.btn{background:var(--acc2);border:0;color:#fff;border-radius:999px;padding:11px 18px;font-size:15px;cursor:pointer;min-height:44px;transition:transform .12s}
.btn:active{transform:scale(.96)}.btn.sec{background:#1f2937;color:#cbd5e1}
#mapa{position:relative;z-index:0;isolation:isolate;height:340px;border-radius:12px;overflow:hidden;background:#0d121b}
html:not([data-tema=claro]) #mapa .leaflet-tile-pane{filter:invert(1) hue-rotate(180deg) brightness(.85) contrast(.9) saturate(.6)}
.mapctl{display:flex;align-items:center;gap:10px;margin-top:12px}
.mapctl button{flex:none;width:44px;height:44px;border-radius:50%;border:0;background:#1f2937;font-size:16px;cursor:pointer}
.mapctl input{flex:1;height:44px;accent-color:#3b82f6}
.mapht{font-size:13px;color:#cbd5e1;min-width:96px;text-align:right}
.mapley{display:flex;gap:4px;align-items:center;font-size:11px;color:var(--tx2);margin-top:8px;flex-wrap:wrap}
.mapley i{display:inline-block;width:22px;height:10px;border-radius:2px}
.sat img{width:100%;border-radius:12px;display:block;background:#0d121b;min-height:120px}
.sheet-bg{position:fixed;inset:0;z-index:90;background:rgba(0,0,0,.5);opacity:0;transition:opacity .25s}
.sheet-bg.on{opacity:1}
.sheet{position:fixed;left:0;right:0;bottom:0;z-index:100;max-width:560px;margin:0 auto;max-height:82vh;overflow-y:auto;background:#121a28;border-radius:22px 22px 0 0;padding:6px 14px calc(18px + var(--sab));transform:translateY(105%);transition:transform .28s cubic-bezier(.2,.8,.2,1);overscroll-behavior:contain}
.sheet.on{transform:none}
.grab{width:42px;height:5px;border-radius:3px;background:#334155;margin:6px auto 10px;touch-action:none}
.sheet h2{font-size:15px;margin:6px 4px 10px;color:#cbd5e1}
.sheet .sh{font-size:12px;color:var(--tx3);text-transform:uppercase;letter-spacing:.6px;margin:16px 4px 6px}
.lit{display:flex;align-items:center;gap:12px;width:100%;border:0;background:none;text-align:left;padding:10px 8px;border-radius:12px;cursor:pointer;min-height:56px;font-size:15px}
.lit:active{background:#1b2536}.lit.on{background:#16213a}
.lit .nm{flex:1}.lit .nm small{display:block;color:var(--tx3);font-size:12px;margin-top:2px}
.lit .tt{font-size:20px;font-weight:300}
.toast{position:fixed;left:50%;bottom:calc(20px + var(--sab));transform:translateX(-50%);z-index:120;background:#1f2937;border:1px solid #334155;border-radius:14px;padding:12px 14px;font-size:13.5px;max-width:calc(100% - 32px);box-shadow:0 8px 30px rgba(0,0,0,.5);display:flex;gap:12px;align-items:center}
.toast button{border:0;background:none;color:#93c5fd;font-size:13.5px;cursor:pointer;min-height:36px}
.pie{font-size:11.5px;color:var(--tx3);margin-top:22px;line-height:1.6}
.pie a{color:#93c5fd}
.ic img{display:block}
.topder{display:flex;gap:8px;align-items:center;min-width:0}
.temabtn{flex:none;width:40px;height:40px;border:0;border-radius:50%;background:var(--card2);font-size:17px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:transform .12s}
.temabtn:active{transform:scale(.92)}
html,body,.card,details,.chip,.top,.sheet{transition:background-color .25s,color .25s}
/* ---------- MODO CLARO ---------- */
html[data-tema=claro]{color-scheme:light;--bg:#f4f6fa;--card:#ffffff;--card2:#e9edf3;--line:#e3e8ef;--tx:#0f172a;--tx2:#475569;--tx3:#64748b;--acc:#2563eb;--acc2:#2563eb}
html[data-tema=claro] .top{background:rgba(244,246,250,.85);border-bottom-color:rgba(15,23,42,.06)}
html[data-tema=claro] .brand,html[data-tema=claro] .blq,html[data-tema=claro] .tldet,html[data-tema=claro] .dexp p,
html[data-tema=claro] .fbres,html[data-tema=claro] .mapht,html[data-tema=claro] .sheet h2{color:#334155}
html[data-tema=claro] .chip{color:#334155}html[data-tema=claro] .chip b{color:#0f172a}
html[data-tema=claro] .chip.ts{background:#ede9fe;color:#5b21b6}html[data-tema=claro] .chip.ts b{color:#4c1d95}
html[data-tema=claro] .card,html[data-tema=claro] details{box-shadow:0 1px 3px rgba(15,23,42,.06),0 1px 2px rgba(15,23,42,.04)}
html[data-tema=claro] .card h3 button,html[data-tema=claro] .btn.sec,html[data-tema=claro] .mapctl button{background:#e9edf3;color:#1d4ed8}
html[data-tema=claro] .aviso-top{background:#dbeafe}
html[data-tema=claro] .lugarbtn:active{background:#dde3eb}
html[data-tema=claro] .seg{border-right-color:#fff}
html[data-tema=claro] .graf .grid{stroke:#e5e7eb}html[data-tema=claro] .graf .dia{stroke:#cbd5e1}
html[data-tema=claro] .graf .linea{stroke:#2563eb}html[data-tema=claro] .graf .lineadz{stroke:#64748b}
html[data-tema=claro] .graf .lineats{stroke:#7c3aed}html[data-tema=claro] .graf .mm{fill:#93c5fd;opacity:.7}
html[data-tema=claro] .graf .cursor{stroke:#334155}html[data-tema=claro] .graf .punto{fill:#2563eb;stroke:#fff}
html[data-tema=claro] .tip{background:#fff;border-color:#e2e8f0;color:#334155;box-shadow:0 8px 24px rgba(15,23,42,.15)}
html[data-tema=claro] .tip b{color:#0f172a}
html[data-tema=claro] .pp{color:#2563eb}html[data-tema=claro] .pp.off{color:#cbd5e1}
html[data-tema=claro] .trng,html[data-tema=claro] .calib .cb div{background:#e5e7eb}
html[data-tema=claro] .drow:active{background:#eef2f7}html[data-tema=claro] .drow.sel{background:#e8f0fe}
@media(hover:hover){html[data-tema=claro] .drow:hover{background:#f1f5f9}}
html[data-tema=claro] summary,html[data-tema=claro] .pie a{color:#1d4ed8}
html[data-tema=claro] summary:before{color:#94a3b8}
html[data-tema=claro] th{background:#f1f5f9}html[data-tema=claro] tr.dsep td{background:#e9edf3;color:#334155}
html[data-tema=claro] tr.star td{background:#dcfce7}html[data-tema=claro] tr.alt td{background:#dbeafe}
html[data-tema=claro] .aviso{color:#92400e;background:#fef3c7}
html[data-tema=claro] details.tec{background:#f8fafc}
html[data-tema=claro] .fbb button{background:#f1f5f9;border-color:#e2e8f0}
html[data-tema=claro] #mapa,html[data-tema=claro] .sat img{background:#e9edf3}
html[data-tema=claro] .sheet-bg{background:rgba(15,23,42,.35)}
html[data-tema=claro] .sheet{background:#fff;box-shadow:0 -8px 30px rgba(15,23,42,.12)}
html[data-tema=claro] .grab{background:#cbd5e1}
html[data-tema=claro] .lit:active{background:#f1f5f9}html[data-tema=claro] .lit.on{background:#e8f0fe}
html[data-tema=claro] .toast{background:#fff;border-color:#e2e8f0;box-shadow:0 8px 30px rgba(15,23,42,.18)}
html[data-tema=claro] .toast button{color:#1d4ed8}
html[data-tema=claro] .cargando{border-color:#cbd5e1;border-top-color:#2563eb}
html[data-tema=claro] .ptr{box-shadow:0 2px 8px rgba(15,23,42,.15)}
@media(max-width:560px){
  .hero .t{font-size:54px}.hero h1{font-size:18.5px}
  .drow{grid-template-columns:64px 30px 38px 26px 1fr 28px;gap:7px;font-size:14px}.dmm{display:none}
  .tk:nth-child(even){display:none}
  #mapa{height:300px}
}
/* ================= v11: maqueta compacta tipo Dark Sky + vista de escritorio ================= */
:root{--tx3:#8b95a5}
html[data-tema=claro]{--tx3:#5b6678}
.saltar{position:absolute;left:-999px;top:8px;z-index:200;background:var(--acc2);color:#fff;padding:10px 14px;border-radius:10px}
.saltar:focus{left:8px}
:focus-visible{outline:2px solid #f59e0b;outline-offset:2px;border-radius:8px}
.top>*{max-width:none}
.top{padding-left:max(16px,calc((100vw - 1180px)/2));padding-right:max(16px,calc((100vw - 1180px)/2))}
.wrap{max-width:1180px}
.lay{display:block}
.hero{margin:14px 0 2px;gap:12px}
.hero .t{font-size:60px}
.hero .hs{color:var(--tx2);font-size:13px;margin-top:4px}
.tit{font-size:20px;font-weight:600;margin:10px 0 4px;line-height:1.3}
.sub{margin:0;color:var(--tx2);font-size:14px;line-height:1.45}
.lugar{margin-top:8px;font-size:12.5px;color:var(--tx3)}
.chips{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;margin:10px -16px 0;padding:0 16px;-webkit-overflow-scrolling:touch}
.chips::-webkit-scrollbar{display:none}
.chip{white-space:nowrap;flex:none}
.acciones{display:flex;gap:8px;margin-top:12px;overflow-x:auto;scrollbar-width:none}
.acciones::-webkit-scrollbar{display:none}
.acc{flex:none;border:1px solid var(--line);background:var(--card);border-radius:999px;padding:9px 14px;font-size:13.5px;cursor:pointer;min-height:40px;transition:transform .12s}
.acc:active{transform:scale(.96)}
.acc[aria-pressed=true]{border-color:var(--acc);color:var(--acc)}
.fbmini{font-size:12px;color:var(--tx3);margin-top:6px;min-height:0}
.fbmini:empty{display:none}
.alerta{margin-top:12px;background:linear-gradient(90deg,#1e3a8a,#4c1d95);color:#fff;border-radius:14px;padding:11px 14px;font-size:14px;line-height:1.45}
.card{margin-top:12px;padding:14px}
.card h3{margin-bottom:10px}
.cmulti{padding-top:8px}
.tabs{display:flex;gap:4px;background:var(--card2);border-radius:12px;padding:4px;margin-bottom:12px}
.tab{flex:1;border:0;background:none;border-radius:9px;padding:9px 6px;font-size:13.5px;color:var(--tx2);cursor:pointer;min-height:38px}
.tab.on{background:var(--card);color:var(--tx);font-weight:600;box-shadow:0 1px 3px rgba(0,0,0,.25)}
.panel h3{font-size:12.5px}
.lg{border:0;background:none;color:inherit;font:inherit;font-size:12px}
.drow{padding:10px 6px;min-height:48px}
.mapley i{flex:1;max-width:220px;height:8px;border-radius:4px}
.mapley{gap:8px}
.capa-lluvia{image-rendering:auto;mix-blend-mode:normal}
.cab,.col1,.col2,.cabA,.cabB{min-width:0}
.dmm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.tmin,.tmax{white-space:nowrap}
#satmap{position:relative;z-index:0;isolation:isolate;height:340px;border-radius:12px;overflow:hidden;background:#0d121b}
.satimg{width:100%;height:100%;object-fit:contain;display:block}
.leaflet-popup-content-wrapper{border-radius:12px}
.pop .leaflet-popup-content{margin:8px 12px;font-size:13px}
.sat img{max-height:520px;object-fit:contain}
.lugarnota{margin:10px 2px 0}
.sheet .fbb{margin-top:6px}
@media(max-width:560px){.hero .t{font-size:52px}.tit{font-size:18px}}
/* ---------- tableta y PC: dos columnas ---------- */
@media(min-width:980px){
  .wrap{padding:8px 24px 40px}
  .lay{display:grid;grid-template-columns:minmax(400px,460px) minmax(0,1fr);grid-template-areas:"cab cab" "col1 col2";gap:0 24px;align-items:start}
  .cab{grid-area:cab;display:grid;grid-template-columns:minmax(400px,460px) minmax(0,1fr);gap:0 24px;align-items:end}
  .cab .alerta{grid-column:1/-1}
  .cabB{padding-bottom:2px}.cabB .chips{margin-top:0}
  .col1{grid-area:col1}.col2{grid-area:col2}
  #satmap{height:min(62vh,600px)}
  .col2 .card:first-child{margin-top:12px}
  #mapa{height:min(62vh,600px)}
  .chips{flex-wrap:wrap;overflow:visible;margin:10px 0 0;padding:0}
  .hero .t{font-size:68px}
  .drow{grid-template-columns:minmax(60px,84px) 32px 42px minmax(0,1.1fr) 30px minmax(44px,1fr) 32px;gap:6px}
  .dmm{display:block}
  .sheet{max-width:480px;bottom:auto;top:50%;left:50%;right:auto;width:480px;border-radius:20px;transform:translate(-50%,-46%) scale(.98);opacity:0;transition:transform .2s,opacity .2s}
  .sheet.on{transform:translate(-50%,-50%);opacity:1}
  .grab{display:none}
  .pie{max-width:none}
}
@media(min-width:980px) and (hover:hover){.acc:hover,.tab:not(.on):hover{background:var(--card2)}}
html[data-tema=claro] .alerta{background:linear-gradient(90deg,#2563eb,#7c3aed)}
html[data-tema=claro] .acc{background:#fff;border-color:#e2e8f0}
html[data-tema=claro] .tab.on{background:#fff;box-shadow:0 1px 3px rgba(15,23,42,.12)}
/* ---------- v12: próximas 24 horas estilo Dark Sky ---------- */
.tl{padding-top:0}
.tlbar{position:relative;display:flex;gap:2px;height:30px;touch-action:pan-y;cursor:pointer;user-select:none;-webkit-user-select:none}
.tlb{min-width:0;display:flex;align-items:center;padding:0 8px;font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;border-radius:4px}
.tlb:first-child{border-radius:10px 4px 4px 10px}.tlb:last-of-type{border-radius:4px 10px 10px 4px}
.tlb span{overflow:hidden;white-space:nowrap}
.tlmark{position:absolute;top:-4px;bottom:-4px;border:2px solid #f59e0b;border-radius:7px;pointer-events:none;transition:left .08s}
.horas{display:flex;overflow-x:auto;gap:2px;margin:10px -6px 0;padding:0 6px 2px;scrollbar-width:none;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch}
.horas::-webkit-scrollbar{display:none}
.hc{flex:0 0 50px;display:flex;flex-direction:column;align-items:center;gap:4px;padding:8px 0;border:0;background:none;border-radius:12px;cursor:pointer;scroll-snap-align:start;color:var(--tx)}
.hc.sel{background:var(--card2)}
@media(hover:hover){.hc:hover{background:var(--card2)}}
.hh{font-size:11.5px;color:var(--tx2)}.hi{height:26px;display:flex;align-items:center}
.hp{font-size:11.5px;font-weight:600;color:#60a5fa;min-height:14px;line-height:14px}
.ht{font-size:15px}
.tldet{min-height:19px;font-size:12.5px;color:var(--tx2);margin-top:6px}
.tldet b{color:var(--tx)}.tldet:empty{display:none}
html[data-tema=claro] .hp{color:#2563eb}
.modos{display:flex;gap:4px;margin-bottom:10px}
.modo{flex:1;border:1px solid var(--line);background:none;border-radius:999px;padding:7px 10px;font-size:13px;cursor:pointer;color:var(--tx2);min-height:36px}
.modo.on{background:var(--acc2);border-color:var(--acc2);color:#fff}
/* ---------- v13: tira de horas, calor, instalar, cómo funciona ---------- */
.tlbar,.tlmark{display:none!important}
.horas{position:relative;display:block;overflow-x:auto;margin:4px -14px 0;padding:0 14px 4px;scrollbar-width:none;-webkit-overflow-scrolling:touch;scroll-snap-type:x proximity}
.hfila{display:flex}
.hc{flex:0 0 54px;width:54px;display:flex;flex-direction:column;align-items:center;gap:3px;padding:8px 0 4px;border:0;background:none;border-radius:12px;cursor:pointer;scroll-snap-align:start;color:var(--tx);transition:background .15s}
.hc.sel{background:var(--card2)}
.hc.cal .hh{color:#f87171}
.hh{font-size:12px;color:var(--tx2)}.hi{height:30px;display:flex;align-items:center}
.hp{font-size:11.5px;font-weight:600;color:#60a5fa;min-height:14px;line-height:14px}.hp.ts{color:#a78bfa}
.tline{display:block;margin-top:-2px;pointer-events:none}
.tline .tt{font-size:12.5px;fill:var(--tx);font-weight:500}
.tldet{min-height:0;font-size:12.5px;color:var(--tx2);margin-top:8px;line-height:1.5}
.hot{color:#f87171}
.chip.hot{background:#3a1d1d;color:#fecaca}.chip.hot b{color:#fff}
.alerta.calor{background:linear-gradient(90deg,#9a3412,#b91c1c)}
.hero .hs b{color:var(--tx);font-weight:600}
.dn small.hot{color:#f87171}
.acc.inst{border-color:var(--acc);color:var(--acc)}
.instp{list-style:none;padding:0;margin:12px 0}
.instp li{display:flex;gap:12px;align-items:center;padding:10px 4px;border-top:1px solid var(--line);font-size:14.5px;line-height:1.4}
.instp li:first-child{border-top:0}
.ii{flex:none;width:36px;height:36px;border-radius:10px;background:var(--card2);display:flex;align-items:center;justify-content:center;font-size:18px;color:var(--acc)}
.como{display:grid;gap:4px}
.paso{display:flex;gap:12px;padding:10px 0;border-top:1px solid var(--line)}
.paso:first-child{border-top:0}
.pn{flex:none;width:40px;height:40px;border-radius:12px;background:var(--card2);display:flex;align-items:center;justify-content:center;font-size:20px}
.paso b{font-size:14px}.paso p{margin:3px 0 0;font-size:13px;color:var(--tx2);line-height:1.5}
html[data-tema=claro] .hc.cal .hh,html[data-tema=claro] .hot,html[data-tema=claro] .dn small.hot{color:#dc2626}
html[data-tema=claro] .chip.hot{background:#fee2e2;color:#991b1b}html[data-tema=claro] .chip.hot b{color:#7f1d1d}
html[data-tema=claro] .alerta.calor{background:linear-gradient(90deg,#ea580c,#dc2626)}
@media(min-width:980px){.horas{margin:4px 0 0;padding:0 0 4px}}
/* ---------- v14: tarjetas de detalle, gráfica por variable ---------- */
.hc .ht{font-size:15px;font-weight:500;margin-top:2px}
.tline{margin-top:0}
.tldet{margin-top:10px}
.dhead{display:flex;gap:8px;align-items:baseline;font-size:14px;margin-bottom:8px}
.dhead span{color:var(--tx2)}
.dcards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
.dc{background:var(--card2);border-radius:12px;padding:8px 10px;min-width:0}
.dc span{display:block;font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--tx3)}
.dc b{display:block;font-size:17px;font-weight:600;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dc small{display:block;font-size:11.5px;color:var(--tx2);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dc.agua b{color:#60a5fa}.dc.ts b{color:#a78bfa}.dc.hot b{color:#f87171}
.dexp .dcards{margin:4px 0 6px}
.gsel{display:flex;gap:6px;margin:-2px 0 10px;overflow-x:auto;scrollbar-width:none}
.gsel::-webkit-scrollbar{display:none}
.gb{flex:none;border:1px solid var(--line);background:none;border-radius:999px;padding:6px 13px;font-size:13px;color:var(--tx2);cursor:pointer;min-height:34px}
.gb.on{background:var(--card2);border-color:var(--card2);color:var(--tx);font-weight:600}
.ley .lg{cursor:default}
html[data-tema=claro] .dc.agua b{color:#2563eb}html[data-tema=claro] .dc.ts b{color:#7c3aed}html[data-tema=claro] .dc.hot b{color:#dc2626}
@media(min-width:980px){.dcards{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
"""


JS = r"""
'use strict';
const D = JSON.parse(document.getElementById('datos').textContent);
const KEYS = Object.keys(D.modelos);
const DIAS = ['Domingo','Lunes','Martes','Miércoles','Jueves','Viernes','Sábado'];
const TXT_CONF = {Alta:'Los pronósticos coinciden', Media:'Hay algunas diferencias entre pronósticos',
                  Baja:'Los pronósticos no coinciden, podría cambiar'};
const ICONOS = {
 sol:'<circle cx="12" cy="12" r="4.5" fill="#fbbf24"/><g stroke="#fbbf24" stroke-width="2" stroke-linecap="round"><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/></g>',
 luna:'<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" fill="#cbd5e1"/>',
 nube:'<path d="M7 18h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6 11a3.5 3.5 0 0 0 1 7z" fill="#94a3b8"/>',
 parcial_dia:'<circle cx="8" cy="8" r="3.5" fill="#fbbf24"/><g stroke="#fbbf24" stroke-width="1.6" stroke-linecap="round"><path d="M8 1.5v1.5M1.5 8H3M3.4 3.4l1 1M12.6 3.4l-1 1"/></g><path d="M9 20h9a3.5 3.5 0 0 0 .4-6.97A5 5 0 0 0 8.6 13.5 3.3 3.3 0 0 0 9 20z" fill="#94a3b8"/>',
 parcial_noche:'<path d="M13 9.5A5.5 5.5 0 0 1 6 2.5a5.5 5.5 0 1 0 7 7z" fill="#cbd5e1"/><path d="M9 20h9a3.5 3.5 0 0 0 .4-6.97A5 5 0 0 0 8.6 13.5 3.3 3.3 0 0 0 9 20z" fill="#94a3b8"/>',
 llovizna:'<path d="M7 14h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6 7a3.5 3.5 0 0 0 1 7z" fill="#94a3b8"/><g fill="#7dd3fc"><circle cx="8" cy="18" r="1"/><circle cx="12" cy="20" r="1"/><circle cx="16" cy="18" r="1"/></g>',
 lluvia:'<path d="M7 13h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6 6a3.5 3.5 0 0 0 1 7z" fill="#64748b"/><g stroke="#38bdf8" stroke-width="2" stroke-linecap="round"><path d="M8 16l-1.5 4M12 16l-1.5 4M16 16l-1.5 4"/></g>',
 tormenta:'<path d="M7 13h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6 6a3.5 3.5 0 0 0 1 7z" fill="#475569"/><path d="M12.5 13l-3 5h3l-1.5 5 5-7h-3l1.5-3z" fill="#facc15"/>',
 niebla:'<path d="M7 11h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6 4a3.5 3.5 0 0 0 1 7z" fill="#94a3b8"/><g stroke="#cbd5e1" stroke-width="1.8" stroke-linecap="round"><path d="M4 15h16M6 18.5h12M8 22h8"/></g>'
};
// Íconos animados Meteocons (MIT © Bas Milius) si el servidor pudo descargarlos
const MC = {sol:'clear-day', luna:'clear-night', parcial_dia:'partly-cloudy-day', parcial_noche:'partly-cloudy-night', nube:'overcast',
            llovizna:'drizzle', lluvia:'rain', tormenta:'thunderstorms-rain', niebla:'fog'};
const MC_URL = {};
for (const n in MC) { const svg = (D.iconos || {})[MC[n]]; if (svg) try { MC_URL[n] = URL.createObjectURL(new Blob([svg], {type: 'image/svg+xml'})); } catch (e) {} }
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function icono(n, s) {
  if (MC_URL[n]) { const z = Math.round(s * 1.3); return `<img src="${MC_URL[n]}" width="${z}" height="${z}" alt="" style="margin:${-(z - s) / 2}px" draggable="false">`; }
  return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" aria-hidden="true">${ICONOS[n] || ICONOS.nube}</svg>`;
}
const r1 = x => Math.round(x * 10) / 10;
const guardar = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
const leer = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const tocar = () => { try { navigator.vibrate && navigator.vibrate(4); } catch (e) {} };
const CARGA = Date.now();
let estado = {vista: null, dia: null, ocultas: new Set(['llovizna']), ubic: null, pos: null, tlSel: null, buscando: false,
  tab: leer('cc_tab') || 'graf', vapid: undefined, mapaListo: false};

// ---------- misma lógica que el script de Python ----------
function probConsenso(v, pesos, u) { let sw = 0, s = 0; for (const m in v) { const w = pesos[m] ?? 1; sw += w; if (v[m] >= u) s += w; } return sw ? s / sw * 100 : 0; }
function calibrarProb(p, t) {
  if (!t || !t.length) return p;
  if (p <= t[0][0]) return t[0][1];
  for (let i = 0; i < t.length - 1; i++) { const [x0, y0] = t[i], [x1, y1] = t[i + 1];
    if (p <= x1) return x1 > x0 ? y0 + (y1 - y0) * (p - x0) / (x1 - x0) : y1; }
  return t[t.length - 1][1];
}
function medianaPond(v, pesos) {
  const it = Object.entries(v).map(([m, x]) => [x, pesos[m] ?? 1]).sort((a, b) => a[0] - b[0]);
  const tot = it.reduce((a, b) => a + b[1], 0); let ac = 0;
  for (const [x, w] of it) { ac += w; if (ac >= tot / 2) return x; }
  return it[it.length - 1][0];
}
const media = a => a.reduce((x, y) => x + y, 0) / a.length;
const mediana = a => { const b = [...a].sort((x, y) => x - y), n = b.length; return n % 2 ? b[(n - 1) / 2] : (b[n / 2 - 1] + b[n / 2]) / 2; };
function radDespejado(lat, lon, hora, off) {
  const ms = Date.UTC(+hora.slice(0, 4), +hora.slice(5, 7) - 1, +hora.slice(8, 10), +hora.slice(11, 13), 30) - off * 1000;
  const dt = new Date(ms), n = Math.floor((ms - Date.UTC(dt.getUTCFullYear(), 0, 1)) / 864e5) + 1, R = Math.PI / 180;
  const dec = 23.44 * R * Math.sin(2 * Math.PI * (284 + n) / 365), b = 2 * Math.PI * (n - 81) / 364;
  const eot = 9.87 * Math.sin(2 * b) - 7.53 * Math.cos(b) - 1.5 * Math.sin(b);
  const solar = dt.getUTCHours() + dt.getUTCMinutes() / 60 + lon / 15 + eot / 60, ha = 15 * (solar - 12) * R, la = lat * R;
  const cz = Math.sin(la) * Math.sin(dec) + Math.cos(la) * Math.cos(dec) * Math.cos(ha);
  return cz > 0.17 ? 1098 * cz * Math.exp(-0.057 / cz) : 0;
}
function franjaDe(hora) { const h = +hora.slice(11, 13); for (const n in D.franjas) { const [a, b] = D.franjas[n]; if (h >= a && h < b) return n; } return null; }
function plazoDe(hora, off) {
  const ms = Date.UTC(+hora.slice(0, 4), +hora.slice(5, 7) - 1, +hora.slice(8, 10), +hora.slice(11, 13)) - off * 1000;
  return Math.max(1, Math.min(7, Math.ceil((ms - Date.now()) / 36e5 / 24)));
}
function tablaPara(cfg, plazo, hora) {
  if (plazo >= 2) { const pz = (cfg.plazos || {})[String(plazo)]; if (pz && pz.verificado && pz.tabla && pz.tabla.length) return pz.tabla; }
  if (plazo <= 1 && cfg.franjas && cfg.franjas.activa) { const t = (cfg.franjas.tablas || {})[franjaDe(hora)]; if (t) return t; }
  return cfg.tabla_lluvia;
}
const verificadoA = (cfg, plazo) => plazo <= 1 || !!((cfg.plazos || {})[String(plazo)] || {}).verificado;
function filasDesdeOM(h, cfg, lat, lon, off) {
  const filas = [], n = h.time.length, g = (v, m, i) => { const s = h[v + '_' + m]; return s && i < s.length ? s[i] : null; };
  h.time.forEach((t, i) => {
    const ll = {}, te = [], nu = [], nbm = [], rad = [], cods = [], raf = [], uv = [], hu = [], vi = [];
    for (const m of KEYS) {
      let v = g('precipitation', m, i); if (v != null) ll[m] = v;
      v = g('temperature_2m', m, i); if (v != null) te.push(v);
      v = g('cloud_cover', m, i); if (v != null) nu.push(v);
      const lo = g('cloud_cover_low', m, i), mi = g('cloud_cover_mid', m, i); if (lo != null || mi != null) nbm.push(Math.max(lo || 0, mi || 0));
      v = i + 1 < n ? g('shortwave_radiation', m, i + 1) : null; if (v != null) rad.push(v);
      v = g('weather_code', m, i); if (v != null) cods.push(v);
      v = g('wind_gusts_10m', m, i); if (v != null) raf.push(v);
      v = g('uv_index', m, i); if (v != null) uv.push(v);
      v = g('relative_humidity_2m', m, i); if (v != null) hu.push(v);
      v = g('wind_speed_10m', m, i); if (v != null) vi.push(v);
    }
    if (!Object.keys(ll).length) return;
    const bruta = probConsenso(ll, cfg.pesos, cfg.umbral), brutaDz = probConsenso(ll, cfg.pesos, D.umbral_llovizna);
    const tc = cfg.temp && cfg.temp.activa ? (cfg.temp.sesgo || {})[franjaDe(t)] || 0 : 0, hc = (cfg.temp && cfg.temp.sesgo_hum) || 0;
    const plazo = plazoDe(t, off), prob = calibrarProb(bruta, tablaPara(cfg, plazo, t)), probDz = Math.max(calibrarProb(brutaDz, cfg.tabla_llovizna), prob);
    const ac = Math.max(bruta, 100 - bruta), vals = Object.values(ll), det = {};
    for (const m in ll) det[D.modelos[m]] = r1(ll[m]);
    const claro = radDespejado(lat, lon, t, off);
    const pts = cods.length ? cods.filter(c => c >= 95).length / cods.length * 100 : 0;
    filas.push({hora: t, prob: Math.round(prob), prob_dz: Math.round(probDz), prob_bruta: Math.round(bruta),
      mm: r1(medianaPond(ll, cfg.pesos)), min: r1(Math.min(...vals)), max: r1(Math.max(...vals)),
      temp: te.length ? r1(media(te) - tc) : null, nubes: nu.length ? Math.round(media(nu)) : null,
      nubes_bm: nbm.length ? Math.round(media(nbm)) : null,
      sol: claro > 0 && rad.length >= 2 ? Math.round(Math.min(mediana(rad) / claro, 1.2) * 100) / 100 : null,
      prob_ts: cods.length ? Math.round(calibrarProb(pts, cfg.tabla_tormenta)) : 0,
      niebla: cods.length ? Math.round(cods.filter(c => c === 45 || c === 48).length / cods.length * 100) : 0,
      rafaga: raf.length ? Math.round(mediana(raf)) : null, uv: uv.length ? r1(mediana(uv)) : null,
      hum: hu.length ? Math.round(Math.min(100, Math.max(0, mediana(hu) - hc))) : null, viento: vi.length ? Math.round(mediana(vi)) : null,
      conf: ac >= 80 ? 'Alta' : ac >= 60 ? 'Media' : 'Baja', detalle: det, n: Object.keys(ll).length, plazo});
  });
  return filas;
}

// ---------- lluvia observada ahora: satélite + reportes de la gente ----------
let SAT = null;
function satAhora() {
  if (SAT !== null) return SAT;
  const A = D.ahora; SAT = false;
  if (!A || !A.q) return SAT;
  const edad = (Date.now() - (Date.UTC(+A.t.slice(0, 4), +A.t.slice(5, 7) - 1, +A.t.slice(8, 10), +A.t.slice(11, 13), +A.t.slice(14, 16)) - D.offset * 1000)) / 36e5;
  if (edad > 3) return SAT;
  try { SAT = Object.assign({}, A, {v: Uint8Array.from(atob(A.q), c => c.charCodeAt(0)), edad}); } catch (e) {}
  return SAT;
}
function satEn(lat, lon, radio = 1) {
  const S = satAhora(); if (!S) return null;
  const iy = Math.floor((S.lat0 - lat) / S.paso), ix = Math.floor((lon - S.lon0) / S.paso);
  let m = null;
  for (let y = iy - radio; y <= iy + radio; y++) for (let x = ix - radio; x <= ix + radio; x++)
    if (y >= 0 && y < S.ny && x >= 0 && x < S.nx) m = Math.max(m ?? 0, S.v[y * S.nx + x] * S.escala);
  return m;
}
function evidencia(v) {
  const sat = satEn(v.lat, v.lon), j = (estado.rep && estado.rep.clave === claveRep(v)) ? estado.rep.j : null;
  const n = j ? j.total || 0 : 0;
  const moja = j ? (j.lluvia || 0) + (j.lluvia_fuerte || 0) + (j.tormenta || 0) + 0.5 * (j.llovizna || 0) : 0;
  const seco = j ? (j.soleado || 0) + (j.nublado || 0) : 0;
  const lluviaRep = n >= 3 && moja / n >= 0.6, secoRep = n >= 3 && seco / n >= 0.9;
  const llueve = (sat != null && sat >= 1) || lluviaRep || (sat != null && sat >= 0.5 && moja >= 1);
  const noLlueve = !llueve && secoRep && (sat == null || sat < 0.3);
  const fuentes = [sat != null && sat >= 0.5 ? 'el satélite' : '', lluviaRep ? `${n} reportes de la gente` : ''].filter(Boolean);
  return {llueve, noLlueve, sat, n, fuentes, mm: sat != null ? Math.max(sat, 0.5) : 1.5};
}
const claveRep = v => `${v.lat.toFixed(2)},${v.lon.toFixed(2)}`;
function conAhora(filas, ahora, ev) {
  if (!ev.llueve && !ev.noLlueve) return filas;
  let k = 0;
  return filas.map(r => {
    if (r.hora < ahora || k > 2) return r;
    const i = k++, c = Object.assign({}, r, {obs: true});
    if (ev.llueve) { c.prob = Math.max(r.prob, [85, 60, 40][i]); c.prob_dz = Math.max(c.prob_dz, c.prob); c.mm = Math.max(r.mm, i ? r.mm : ev.mm); }
    else if (i === 0) c.prob = Math.min(r.prob, 25);
    return c;
  });
}

// ---------- textos ----------
function ahoraISO(off) { return new Date(Date.now() + off * 1000).toISOString().slice(0, 13) + ':00'; }
const esNoche = h => { const x = +h.slice(11, 13); return x < 6 || x >= 18; };
function cielo(r) {
  const n = esNoche(r.hora);
  if (r.sol != null) {                       // de día: cuánto sol llega de verdad (las nubes altas finas no tapan el sol)
    if (r.sol >= 0.75) return [n ? 'luna' : 'sol', 'Soleado', '#cbd5e1'];
    if (r.sol >= 0.45) return [n ? 'parcial_noche' : 'parcial_dia', 'Sol y nubes', '#94a3b8'];
    return ['nube', 'Nublado', '#64748b'];
  }
  const nub = r.nubes_bm ?? r.nubes ?? 50;   // de noche o al amanecer: solo nubes bajas y medias
  if (nub >= 75) return ['nube', 'Nublado', '#64748b'];
  if (nub >= 35) return [n ? 'parcial_noche' : 'parcial_dia', 'Parcialmente nublado', '#94a3b8'];
  return [n ? 'luna' : 'sol', 'Despejado', '#cbd5e1'];
}
const claro = () => document.documentElement.dataset.tema === 'claro';
const COL_CLARO = {'#cbd5e1': '#fcd34d', '#94a3b8': '#cbd5e1', '#64748b': '#94a3b8', '#a8a29e': '#d6d3d1'};
const colSeg = c => claro() ? (COL_CLARO[c] || c) : c;
function condicion(r) {
  if ((r.prob_ts || 0) >= 30) return ['tormenta', r.prob_ts >= 50 ? 'Tormenta eléctrica probable' : 'Posible tormenta eléctrica', '#7c3aed'];
  if (r.prob >= 40) return ['lluvia', 'Lluvia probable', '#1d4ed8'];
  if (r.prob >= 20) return ['lluvia', 'Posible lluvia', '#3b82f6'];
  if (r.prob_dz >= 50) return ['llovizna', 'Llovizna', '#60a5fa'];
  if ((r.niebla || 0) >= 50) return ['niebla', 'Niebla', '#a8a29e'];
  return cielo(r);
}
function nombreDia(f, hoy) {
  if (f === hoy) return 'Hoy';
  const d = new Date(f + 'T12:00:00Z'), h = new Date(hoy + 'T12:00:00Z');
  if ((d - h) / 864e5 === 1) return 'Mañana';
  return DIAS[d.getUTCDay()];
}
function franja(h) { const x = +h.slice(11, 13); return x < 6 ? 'en la madrugada' : x < 12 ? 'en la mañana' : x < 18 ? 'en la tarde' : 'en la noche'; }
function intensidad(mm) { return mm < 0.5 ? 'muy ligera' : mm < 2 ? 'ligera' : mm < 5 ? 'moderada' : 'fuerte'; }
const finHora = h => String((+h.slice(11, 13) + 1) % 24).padStart(2, '0') + ':00';
function titular(prox, hoy) {
  if (!prox.length) return ['Sin datos por ahora', ''];
  const tsTop = prox.reduce((a, b) => (b.prob_ts || 0) > (a.prob_ts || 0) ? b : a);
  const top = prox.reduce((a, b) => (b.prob > a.prob || (b.prob === a.prob && b.mm > a.mm)) ? b : a);
  let tit, sub;
  if (top.prob >= 25) {
    const corte = Math.max(20, top.prob * 0.6), idx = prox.indexOf(top);
    let i0 = idx, i1 = idx;
    while (i0 > 0 && prox[i0 - 1].prob >= corte) i0--;
    while (i1 < prox.length - 1 && prox[i1 + 1].prob >= corte) i1++;
    const f0 = franja(prox[i0].hora), f1 = franja(prox[i1].hora);
    const cuando = f0 === f1 ? f0 : f0 + ' y ' + f1.replace(/^en /, '');
    const dia = top.hora.slice(0, 10) === hoy ? '' : ' mañana';
    tit = `${top.prob >= 40 ? 'Lluvia probable' : 'Posible lluvia'}${dia} ${cuando}`;
    sub = `${top.prob}% entre las ${prox[i0].hora.slice(11, 16)} y las ${finHora(prox[i1].hora)} · lluvia ${intensidad(top.mm)}.`;
  } else if (top.prob >= 10) {
    tit = 'Baja probabilidad de lluvia';
    sub = `Lo más probable es que no llueva. Máximo ${top.prob}% ${franja(top.hora)}${top.hora.slice(0, 10) === hoy ? '' : ' de mañana'}.`;
  } else {
    const dz = prox.reduce((a, b) => b.prob_dz > a.prob_dz ? b : a);
    if (dz.prob_dz >= 40) { tit = `Posible llovizna ${franja(dz.hora)}`; sub = `Llovizna ligera (${dz.prob_dz}%). Lluvia, poco probable.`; }
    else { tit = condicion(prox[0])[1]; sub = 'No se espera lluvia en las próximas 24 horas.'; }
  }
  if ((tsTop.prob_ts || 0) >= 30) {
    const dia = tsTop.hora.slice(0, 10) === hoy ? '' : ' mañana';
    tit = `${tsTop.prob_ts >= 50 ? 'Tormenta eléctrica probable' : 'Posible tormenta eléctrica'}${dia} ${franja(tsTop.hora)}`;
    sub = `${tsTop.prob_ts}% cerca de las ${tsTop.hora.slice(11, 16)}. ` + sub;
  }
  return [tit, sub];
}
// ---------- calor ----------
// Índice de calor (NOAA/NWS): la temperatura que siente el cuerpo según la humedad.
function indiceCalor(t, rh) {
  if (t == null || rh == null || t < 26.7) return t;
  const F = t * 9 / 5 + 32;
  let hi = 0.5 * (F + 61 + (F - 68) * 1.2 + rh * 0.094);
  if ((hi + F) / 2 >= 80) {
    hi = -42.379 + 2.04901523 * F + 10.14333127 * rh - .22475541 * F * rh - .00683783 * F * F - .05481717 * rh * rh +
         .00122874 * F * F * rh + .00085282 * F * rh * rh - .00000199 * F * F * rh * rh;
    if (rh < 13 && F >= 80 && F <= 112) hi -= ((13 - rh) / 4) * Math.sqrt((17 - Math.abs(F - 95)) / 17);
    else if (rh > 85 && F >= 80 && F <= 87) hi += ((rh - 85) / 10) * ((87 - F) / 5);
  }
  return Math.max(t, (hi - 32) * 5 / 9);
}
const sens = r => r.temp == null ? null : indiceCalor(r.temp, r.hum);
const UMBRAL_CALOR = 32;
function nivelCalor(s) {
  if (s == null || s < 27) return null;
  if (s < 32) return ['Calor moderado', 'Precaución: hidrátate si haces actividad al aire libre.'];
  if (s < 41) return ['Calor intenso', 'Hidrátate seguido y evita el sol directo de 11:00 a 15:00.'];
  if (s < 54) return ['Calor peligroso', 'Evita el esfuerzo físico al sol, busca sombra y bebe agua aunque no tengas sed.'];
  return ['Calor extremo', 'Peligro para la salud: quédate a la sombra o en un lugar fresco.'];
}
function horaPico(rows, f) { return rows.reduce((a, b) => (f(b) ?? -1e9) > (f(a) ?? -1e9) ? b : a); }
// onda de calor: 3 o más días seguidos con la máxima al menos 2° por encima de lo normal de los últimos 30 días
function ondaCalor(dias, normal) {
  if (normal == null) return null;
  let run = [], mejor = [];
  for (const [d, rs] of Object.entries(dias)) {
    const ts = rs.filter(r => r.temp != null).map(r => r.temp), mx = ts.length ? Math.max(...ts) : null;
    if (mx != null && rs.length >= 12 && mx >= normal + 2 && mx >= 28) { run.push([d, mx]); if (run.length > mejor.length) mejor = run.slice(); }
    else run = [];
  }
  return mejor.length >= 3 ? {dias: mejor.map(x => x[0]), max: Math.round(Math.max(...mejor.map(x => x[1]))), normal: Math.round(normal)} : null;
}

function nivelUV(u) { return u < 3 ? 'bajo' : u < 6 ? 'moderado' : u < 8 ? 'alto' : u < 11 ? 'muy alto' : 'extremo'; }

// ---------- próximas horas: tira de horas con línea de temperatura (estilo Dark Sky) ----------
const ANCHO_HORA = 54;
function lineaTiempo(rows, ahora) {
  const n = rows.length; if (!n) return '';
  const validas = rows.filter(r => r.temp != null).map(r => r.temp);
  const tmin = Math.min(...validas), tmax = Math.max(...validas), rng = Math.max(tmax - tmin, 2);
  const W = n * ANCHO_HORA, H = 22, Y = t => 17 - (t - tmin) / rng * 12;
  const pts = rows.map((r, i) => r.temp == null ? null : [i * ANCHO_HORA + ANCHO_HORA / 2, Y(r.temp)]).filter(Boolean);
  const col = claro() ? '#f59e0b' : '#fbbf24';
  const linea = pts.length > 1 ? `<path d="${curvaSuave(pts)}" fill="none" stroke="${col}" stroke-width="2" stroke-linecap="round" opacity=".85"/>` : '';
  const puntos = pts.map(([x, y]) => `<circle cx="${x}" cy="${y}" r="2.4" fill="${col}"/>`).join('');
  const horas = rows.map((r, i) => { const c = condicion(r), s = sens(r), cal = s >= UMBRAL_CALOR && !esNoche(r.hora);
    const pr = (r.prob_ts || 0) >= 30 ? `<span class="hp ts">⚡${r.prob_ts}%</span>` : r.prob >= 10 ? `<span class="hp">${r.prob}%</span>` : '<span class="hp"></span>';
    return `<button class="hc${estado.tlSel === r.hora ? ' sel' : ''}${cal ? ' cal' : ''}" data-i="${i}" aria-label="${r.hora.slice(11, 16)}: ${c[1]}, ${r.temp != null ? Math.round(r.temp) + ' grados' : ''}, lluvia ${r.prob}%">` +
      `<span class="hh">${r.hora === ahora ? 'Ahora' : +r.hora.slice(11, 13) + 'h'}</span><span class="hi">${icono(c[0], 28)}</span>${pr}` +
      `<span class="ht">${r.temp != null ? Math.round(r.temp) + '°' : '–'}</span></button>`; }).join('');
  return `<div class="tl"><div class="horas" role="list"><div class="hfila" style="width:${W}px">${horas}</div>` +
    `<svg class="tline" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">${linea}${puntos}</svg></div>` +
    `<div class="tldet" aria-live="polite"></div></div>`;
}
// tarjetas de detalle: etiqueta pequeña arriba, valor grande abajo; lo importante primero
function tarjetas(items) {
  return '<div class="dcards">' + items.filter(Boolean).map(([l, v, c, sub]) => `<div class="dc${c ? ' ' + c : ''}"><span>${l}</span><b>${v}</b>${sub ? `<small>${sub}</small>` : ''}</div>`).join('') + '</div>';
}
function detalleHora(r) {
  const c = condicion(r), s = sens(r), llueve = r.prob >= 20 || (r.prob_ts || 0) >= 10 || r.prob_dz >= 50;
  const lluvia = ['Lluvia', r.prob >= 10 ? `${r.prob}%` : 'No', r.prob >= 40 ? 'agua' : '', r.prob >= 20 ? intensidad(r.mm) : r.prob >= 10 ? 'poco probable' : 'prevista'];
  const ts = (r.prob_ts || 0) >= 10 ? ['Tormenta', `${r.prob_ts}%`, 'ts', 'eléctrica'] : null;
  const se = r.temp != null ? ['Se siente', `${Math.round(s)}°`, s >= UMBRAL_CALOR ? 'hot' : '', s >= UMBRAL_CALOR ? nivelCalor(s)[0].toLowerCase() : ''] : null;
  const uv = !esNoche(r.hora) && r.uv != null && r.uv >= 1 ? ['Índice UV', `${Math.round(r.uv)}`, r.uv >= 8 ? 'hot' : '', nivelUV(r.uv)] : null;
  const hu = r.hum != null ? ['Humedad', `${r.hum}%`] : null;
  const vi = r.viento != null ? ['Viento', `${r.viento} km/h`, '', (r.rafaga || 0) >= 30 ? `ráfagas ${r.rafaga}` : ''] : null;
  return `<div class="dhead"><b>${r.hora.slice(11, 16)}</b><span>${c[1]}${r.temp != null ? ' · ' + Math.round(r.temp) + '°' : ''}</span></div>` +
    tarjetas(llueve ? [lluvia, ts, se, hu, vi, uv] : [se, uv, hu, vi, lluvia, ts]);
}
function activarLinea(cont, rows) {
  const tira = cont.querySelector('.horas'), det = cont.querySelector('.tldet'); if (!tira) return;
  const cards = tira.querySelectorAll('.hc');
  const fijar = (i, foco) => {
    i = Math.max(0, Math.min(rows.length - 1, i));
    estado.tlSel = rows[i].hora;
    cards.forEach((x, j) => { x.classList.toggle('sel', j === i); x.setAttribute('aria-current', j === i ? 'true' : 'false'); });
    det.innerHTML = detalleHora(rows[i]);
    if (foco) cards[i].focus({preventScroll: true});
    const c = cards[i], izq = c.offsetLeft, der = izq + c.offsetWidth;
    if (izq < tira.scrollLeft + 10 || der > tira.scrollLeft + tira.clientWidth - 10)
      tira.scrollTo({left: izq - tira.clientWidth / 2 + c.offsetWidth / 2, behavior: 'smooth'});
  };
  cards.forEach((c, i) => {
    c.addEventListener('click', () => { tocar(); if (estado.tlSel === rows[i].hora) { estado.tlSel = null; c.classList.remove('sel'); det.innerHTML = ''; } else fijar(i); });
    c.addEventListener('keydown', e => { const d = {ArrowRight: 1, ArrowLeft: -1}[e.key]; if (d) { e.preventDefault(); fijar(i + d, true); } });
  });
  const sel = rows.findIndex(r => r.hora === estado.tlSel); if (sel >= 0) fijar(sel);
}

// ---------- gráfica (cursor con el mouse o arrastrando el dedo) ----------
// curva suave que no se pasa de los valores reales (monótona): no inventa picos entre horas
function curvaSuave(pts) {
  const n = pts.length; if (n < 2) return '';
  const dx = [], m = [], t = [];
  for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0]; m[i] = (pts[i + 1][1] - pts[i][1]) / dx[i]; }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : 3 * (dx[i - 1] + dx[i]) / ((2 * dx[i] + dx[i - 1]) / m[i - 1] + (dx[i] + 2 * dx[i - 1]) / m[i]);
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < n - 1; i++) { const h = dx[i] / 3;
    d += ` C${(pts[i][0] + h).toFixed(1)},${(pts[i][1] + t[i] * h).toFixed(1)} ${(pts[i + 1][0] - h).toFixed(1)},${(pts[i + 1][1] - t[i + 1] * h).toFixed(1)} ${pts[i + 1][0].toFixed(1)},${pts[i + 1][1].toFixed(1)}`; }
  return d;
}
function grafica(cont, filas, ahora, hoy, vr) {
  const k = claro(), n = filas.length, W = Math.max(cont.clientWidth, 280);
  const C = k ? {txt: '#64748b', fuerte: '#334155', grid: '#e5e7eb', noche: 'rgba(15,23,42,.05)', banda1: 'rgba(37,99,235,.07)', banda2: 'rgba(37,99,235,.035)',
                 linea: '#2563eb', area: '#3b82f6', ts: '#7c3aed', pico: '#0f172a', fondo: '#ffffff', temp: '#f59e0b', sens: '#dc2626', vto: '#0d9488', calor: 'rgba(239,68,68,.07)'}
               : {txt: '#7c8696', fuerte: '#cbd5e1', grid: '#1c2433', noche: 'rgba(255,255,255,.03)', banda1: 'rgba(96,165,250,.08)', banda2: 'rgba(96,165,250,.035)',
                 linea: '#60a5fa', area: '#3b82f6', ts: '#a78bfa', pico: '#ffffff', fondo: '#111723', temp: '#fbbf24', sens: '#f87171', vto: '#5eead4', calor: 'rgba(239,68,68,.09)'};
  const pl = 36, pr = 12, pt = 30, h = W < 500 ? 170 : 210, pb = 24, H = pt + h + pb, w = W - pl - pr, dx = w / Math.max(n - 1, 1);
  const X = i => pl + i * dx;
  const val = (r, c) => r[c] == null ? null : r[c];
  // escala de cada variable
  let lo = 0, hi = 100, ticks = [0, 20, 50, 100], fy = v => v + '%', principal = 'prob';
  if (vr === 'temp') {
    const vs = filas.flatMap(r => r.temp == null ? [] : [r.temp, sens(r)]);
    lo = Math.floor(Math.min(...vs)) - 1; hi = Math.ceil(Math.max(...vs)) + 1;
    ticks = [lo + 1, Math.round((lo + hi) / 2), hi - 1]; fy = v => v + '°'; principal = 'temp';
  } else if (vr === 'viento') {
    const vs = filas.flatMap(r => [r.viento, r.rafaga].filter(x => x != null));
    hi = Math.max(30, Math.ceil(Math.max(...vs, 0) / 10) * 10); ticks = [0, hi / 2, hi]; fy = v => v; principal = 'viento';
  } else if (vr === 'uv') {
    hi = Math.max(13, Math.ceil(Math.max(...filas.map(r => r.uv || 0))) + 2); ticks = [0, 3, 6, 8, 11]; fy = v => v; principal = 'uv';
  }
  const Y = v => pt + h - h * (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo);
  const xa = i => Math.max(pl, X(i) - dx / 2), xb = i => Math.min(W - pr, X(i) + dx / 2);
  const paso = n > 30 ? (W < 500 ? 12 : 6) : 3;
  let s = `<svg class="graf" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Gráfica hora por hora">`;
  s += `<defs><linearGradient id="gl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${C.area}" stop-opacity=".55"/><stop offset="1" stop-color="${C.area}" stop-opacity=".04"/></linearGradient>` +
       `<linearGradient id="gv" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${C.vto}" stop-opacity=".35"/><stop offset="1" stop-color="${C.vto}" stop-opacity=".03"/></linearGradient></defs>`;
  for (let i = 0; i < n;) { if (!esNoche(filas[i].hora)) { i++; continue; } let j = i; while (j + 1 < n && esNoche(filas[j + 1].hora)) j++;
    s += `<rect x="${xa(i)}" y="${pt}" width="${xb(j) - xa(i)}" height="${h}" fill="${C.noche}"/>`; i = j + 1; }
  const banda = (v0, v1, color, txt) => { const y0 = Y(v1), y1 = Y(v0);
    s += `<rect x="${pl}" y="${y0}" width="${w}" height="${Math.max(y1 - y0, 0)}" fill="${color}"/>`;
    if (txt) s += `<text x="${W - pr - 4}" y="${y0 + 13}" text-anchor="end" font-size="10.5" fill="${C.txt}" letter-spacing=".4">${txt}</text>`; };
  if (vr === 'lluvia') { banda(50, 100, C.banda1, 'PROBABLE'); banda(20, 50, C.banda2, 'POSIBLE'); }
  if (vr === 'temp' && hi > UMBRAL_CALOR) banda(Math.max(UMBRAL_CALOR, lo), hi, C.calor, 'CALOR INTENSO');
  if (vr === 'viento' && hi > 40) banda(40, hi, C.calor, 'RÁFAGAS FUERTES');
  for (const v of ticks) s += `<line x1="${pl}" x2="${W - pr}" y1="${Y(v)}" y2="${Y(v)}" stroke="${C.grid}" stroke-width="1"${v === lo ? '' : ' stroke-dasharray="2 4"'}/><text x="${pl - 6}" y="${Y(v) + 4}" text-anchor="end" font-size="10.5" fill="${C.txt}">${fy(v)}</text>`;
  if (vr === 'viento') s += `<text x="${pl}" y="${pt - 14}" font-size="10" fill="${C.txt}">km/h</text>`;
  const inicios = filas.map((r, i) => i === 0 || r.hora.slice(11, 13) === '00' ? i : -1).filter(i => i >= 0);
  inicios.forEach((i, q) => { const fin = q + 1 < inicios.length ? inicios[q + 1] : n, ancho = X(fin - 1) - X(i);
    if (i) s += `<line x1="${X(i)}" x2="${X(i)}" y1="${pt - 18}" y2="${pt + h}" stroke="${C.grid}" stroke-width="1"/>`;
    if (ancho > 38) s += `<text x="${X(i) + 5}" y="${pt - 12}" font-size="11.5" font-weight="600" fill="${C.fuerte}">${nombreDia(filas[i].hora.slice(0, 10), hoy)}</text>`; });
  filas.forEach((r, i) => { const hh = +r.hora.slice(11, 13); if (hh % paso === 0 && X(i) > pl + 8 && X(i) < W - pr - 8)
    s += `<text x="${X(i)}" y="${H - 7}" text-anchor="middle" font-size="10.5" fill="${C.txt}">${hh}h</text>`; });
  const serie = (f, min = -Infinity) => filas.map((r, i) => { const v = f(r); return v == null ? null : [X(i), Y(v >= min ? v : lo)]; }).filter(Boolean);
  const etiqueta = (x0, y0, txt, sub) => { const x = Math.max(pl + 22, Math.min(W - pr - 40, x0)), y = Math.max(pt + 12, y0 - 9);
    s += `<text x="${x}" y="${y}" text-anchor="middle" font-size="11.5" font-weight="700" fill="${C.pico}" paint-order="stroke" stroke="${C.fondo}" stroke-width="3">${txt}${sub ? `<tspan font-weight="400" fill="${C.txt}"> ${sub}</tspan>` : ''}</text>`; };
  const picoDia = f => inicios.map((i, q) => { const fin = q + 1 < inicios.length ? inicios[q + 1] : n; let b = i;
    for (let j = i; j < fin; j++) if ((f(filas[j]) ?? -1e9) > (f(filas[b]) ?? -1e9)) b = j; return fin - i >= 4 ? b : -1; }).filter(b => b >= 0);
  let leyenda = [];
  if (vr === 'lluvia') {
    const d = curvaSuave(serie(r => r.prob));
    s += `<path d="${d} L${X(n - 1)},${Y(0)} L${X(0)},${Y(0)} Z" fill="url(#gl)"/><path d="${d}" fill="none" stroke="${C.linea}" stroke-width="2.4" stroke-linejoin="round"/>`;
    leyenda.push([C.linea, 'Probabilidad de lluvia', '']);
    if (filas.some(r => (r.prob_ts || 0) >= 15)) {
      s += `<path d="${curvaSuave(serie(r => r.prob_ts || 0, 15))}" fill="none" stroke="${C.ts}" stroke-width="1.6" stroke-dasharray="1.5 3.5" stroke-linecap="round"/>`;
      filas.forEach((r, i) => { if ((r.prob_ts || 0) >= 30 && (i === 0 || (filas[i - 1].prob_ts || 0) < 30)) s += `<text x="${X(i)}" y="${Y(r.prob_ts) - 6}" text-anchor="middle" font-size="13">⚡</text>`; });
      leyenda.push([C.ts, 'Tormenta eléctrica', 'pt']);
    }
    picoDia(r => r.prob).forEach(b => { if (filas[b].prob >= 20) { s += `<circle cx="${X(b)}" cy="${Y(filas[b].prob)}" r="3.5" fill="${C.linea}" stroke="${C.fondo}" stroke-width="1.5"/>`;
      etiqueta(X(b), Y(filas[b].prob), filas[b].prob + '%', filas[b].hora.slice(11, 13) + 'h'); } });
    if (filas.every(r => r.prob < 10)) s += `<text x="${pl + w / 2}" y="${pt + h / 2}" text-anchor="middle" font-size="13" fill="${C.txt}">Sin lluvia prevista</text>`;
  } else if (vr === 'temp') {
    if (filas.some(r => r.temp != null && sens(r) - r.temp >= 1)) {
      s += `<path d="${curvaSuave(serie(sens))}" fill="none" stroke="${C.sens}" stroke-width="1.6" stroke-dasharray="4 3"/>`;
      leyenda.push([C.sens, 'Se siente', 'dash']);
    }
    s += `<path d="${curvaSuave(serie(r => r.temp))}" fill="none" stroke="${C.temp}" stroke-width="2.4"/>`;
    leyenda.unshift([C.temp, 'Temperatura', '']);
    picoDia(r => r.temp).forEach(b => { s += `<circle cx="${X(b)}" cy="${Y(filas[b].temp)}" r="3.5" fill="${C.temp}" stroke="${C.fondo}" stroke-width="1.5"/>`;
      etiqueta(X(b), Y(filas[b].temp), Math.round(filas[b].temp) + '°', filas[b].hora.slice(11, 13) + 'h'); });
  } else if (vr === 'viento') {
    const d = curvaSuave(serie(r => r.viento));
    if (filas.some(r => r.rafaga != null)) { s += `<path d="${curvaSuave(serie(r => r.rafaga))}" fill="none" stroke="${C.vto}" stroke-width="1.4" stroke-dasharray="4 3" opacity=".8"/>`; }
    s += `<path d="${d} L${X(n - 1)},${Y(0)} L${X(0)},${Y(0)} Z" fill="url(#gv)"/><path d="${d}" fill="none" stroke="${C.vto}" stroke-width="2.4"/>`;
    leyenda.push([C.vto, 'Viento', ''], [C.vto, 'Ráfagas', 'dash']);
    picoDia(r => r.rafaga ?? r.viento).forEach(b => { const v = filas[b].rafaga ?? filas[b].viento; etiqueta(X(b), Y(v), Math.round(v), filas[b].hora.slice(11, 13) + 'h'); });
  } else {
    const colUV = u => u < 3 ? '#4ade80' : u < 6 ? '#facc15' : u < 8 ? '#fb923c' : u < 11 ? '#ef4444' : '#a855f7';
    filas.forEach((r, i) => { if (!(r.uv > 0.4)) return; const x0 = Math.max(pl, X(i) - dx * 0.38), x1 = Math.min(W - pr, X(i) + dx * 0.38);
      s += `<rect x="${x0}" y="${Y(r.uv)}" width="${Math.max(x1 - x0, 1)}" height="${Y(0) - Y(r.uv)}" rx="1.5" fill="${colUV(r.uv)}" opacity=".85"/>`; });
    picoDia(r => r.uv).forEach(b => { if ((filas[b].uv || 0) >= 1) etiqueta(X(b), Y(filas[b].uv), Math.round(filas[b].uv), filas[b].hora.slice(11, 13) + 'h'); });
    leyenda = [['#4ade80', 'Bajo', 'sq'], ['#facc15', 'Moderado', 'sq'], ['#fb923c', 'Alto', 'sq'], ['#ef4444', 'Muy alto', 'sq'], ['#a855f7', 'Extremo', 'sq']];
    if (filas.every(r => !(r.uv > 0.4))) s += `<text x="${pl + w / 2}" y="${pt + h / 2}" text-anchor="middle" font-size="13" fill="${C.txt}">Sin sol en estas horas</text>`;
  }
  const ia = filas.findIndex(r => r.hora === ahora);
  if (ia >= 0) s += `<line x1="${X(ia)}" x2="${X(ia)}" y1="${pt - 18}" y2="${pt + h}" stroke="${C.fuerte}" stroke-width="1.5" stroke-dasharray="3 3"/>`;
  s += `<line class="cursor" y1="${pt}" y2="${pt + h}" visibility="hidden" stroke="${C.fuerte}" stroke-width="1"/><circle class="punto" r="5" visibility="hidden" fill="${vr === 'temp' ? C.temp : vr === 'viento' ? C.vto : C.linea}" stroke="${C.fondo}" stroke-width="2"/></svg><div class="tip" hidden></div>`;
  cont.innerHTML = s;
  cont._leyenda = leyenda;
  const svg = cont.querySelector('svg'), tip = cont.querySelector('.tip'), cur = svg.querySelector('.cursor'), pto = svg.querySelector('.punto');
  let ult = -1, activo = false;
  const texto = r => {
    const cab = `<b>${nombreDia(r.hora.slice(0, 10), hoy)} ${r.hora.slice(11, 16)}</b>`;
    if (vr === 'lluvia') return cab + `<br>Lluvia: <b>${r.prob}%</b> (${r.prob >= 50 ? 'probable' : r.prob >= 20 ? 'posible' : 'poco probable'})` +
      (r.prob >= 10 ? `<br>Si llueve: ${intensidad(r.mm)}${r.mm >= 0.1 ? ` · ${r.mm} mm/h` : ''}` : '') +
      ((r.prob_ts || 0) >= 5 ? `<br>Tormenta eléctrica: <b>${r.prob_ts}%</b>` : '') + (r.prob_dz > r.prob + 5 ? `<br>Llovizna: ${r.prob_dz}%` : '');
    if (vr === 'temp') return cab + `<br>Temperatura: <b>${Math.round(r.temp)}°</b>` + (sens(r) - r.temp >= 1 ? `<br>Se siente: <b>${Math.round(sens(r))}°</b>` : '') +
      (r.hum != null ? `<br>Humedad: ${r.hum}%` : '');
    if (vr === 'viento') return cab + `<br>Viento: <b>${r.viento ?? '–'} km/h</b>` + (r.rafaga != null ? `<br>Ráfagas: ${r.rafaga} km/h` : '');
    return cab + (r.uv > 0.4 ? `<br>Índice UV: <b>${Math.round(r.uv)}</b> (${nivelUV(r.uv)})` + (r.uv >= 6 ? '<br>Usa protector solar y sombrero.' : '') : '<br>Sin radiación UV');
  };
  const mostrar = ev => {
    const rc = svg.getBoundingClientRect(), i = Math.max(0, Math.min(n - 1, Math.round((ev.clientX - rc.left - pl) / dx))), r = filas[i];
    if (i !== ult && ev.pointerType === 'touch') tocar();
    ult = i;
    const v = principal === 'temp' ? r.temp : r[principal];
    cur.setAttribute('x1', X(i)); cur.setAttribute('x2', X(i)); cur.setAttribute('visibility', 'visible');
    pto.setAttribute('cx', X(i)); pto.setAttribute('cy', Y(v ?? lo)); pto.setAttribute('visibility', v == null || vr === 'uv' ? 'hidden' : 'visible');
    tip.innerHTML = texto(r); tip.hidden = false;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = Math.max(0, Math.min(W - tw, X(i) - tw / 2)) + 'px';
    const cab = ($('.top') || {}).offsetHeight || 60, arribaLibre = rc.top - th - 8 > cab + 4;
    tip.style.top = (ev.pointerType === 'mouse' ? -6 : arribaLibre ? -th - 8 : H + 6) + 'px';
  };
  const ocultar = () => { tip.hidden = true; cur.setAttribute('visibility', 'hidden'); pto.setAttribute('visibility', 'hidden'); ult = -1; };
  svg.addEventListener('pointerdown', e => { activo = true; mostrar(e); });
  svg.addEventListener('pointermove', e => { if (activo || e.pointerType === 'mouse') mostrar(e); });
  svg.addEventListener('pointerup', () => { activo = false; });
  svg.addEventListener('pointercancel', () => { activo = false; });
  svg.addEventListener('pointerleave', e => { activo = false; if (e.pointerType === 'mouse') ocultar(); });
  cont._ocultar = ocultar;
}
document.addEventListener('pointerdown', e => { const g = $('#graf'); if (g && g._ocultar && !g.contains(e.target)) g._ocultar(); }, {passive: true});

// ---------- días ----------
function porDia(filas, ahora) { const d = {}; for (const r of filas) if (r.hora >= ahora) (d[r.hora.slice(0, 10)] ||= []).push(r); return d; }
function htmlDias(dias, filas, ahora, hoy, cfg, off, onda) {
  const ts = filas.filter(r => r.temp != null).map(r => r.temp);
  const tmn = Math.min(...ts), tmx = Math.max(...ts), rng = Math.max(tmx - tmn, 1);
  return Object.entries(dias).map(([d, rs], k) => {
    const plazoDia = plazoDe(d + 'T12:00', off), lejano = !verificadoA(cfg || {}, plazoDia);
    const top = rs.reduce((a, b) => (b.prob > a.prob || (b.prob === a.prob && b.mm > a.mm)) ? b : a);
    const tsTop = rs.reduce((a, b) => (b.prob_ts || 0) > (a.prob_ts || 0) ? b : a);
    const t = rs.filter(r => r.temp != null).map(r => r.temp), mn = Math.min(...t), mx = Math.max(...t);
    const diur = rs.filter(r => !esNoche(r.hora)), base = diur.length ? diur : rs;
    let ic;
    if ((tsTop.prob_ts || 0) >= 30) ic = 'tormenta';
    else if (top.prob >= 20 || top.prob_dz >= 50) ic = condicion(top)[0];
    else { const cs = base.map(r => cielo(r)[0]), cuenta = {}; cs.forEach(c => cuenta[c] = (cuenta[c] || 0) + 1);
      ic = Object.entries(cuenta).sort((a, b) => b[1] - a[1])[0][0]; }
    ic = {luna: 'sol', parcial_noche: 'parcial_dia'}[ic] || ic;
    const pct = top.prob >= 10 ? `<span class="pp">${top.prob}%</span>` : '<span class="pp off">–</span>';
    const sPico = horaPico(rs.filter(r => !esNoche(r.hora)).length ? rs.filter(r => !esNoche(r.hora)) : rs, sens), sMax = sens(sPico);
    const enOnda = onda && onda.dias.includes(d);
    const etiqueta = [lejano ? 'orientativo' : '', enOnda ? 'onda de calor' : ''].filter(Boolean).join(' · ');
    const sel = estado.dia === d;
    let html = `<div class="drow${sel ? ' sel' : ''}" data-dia="${d}" role="button" tabindex="0" aria-expanded="${sel}"><div class="dn">${nombreDia(d, hoy)}${etiqueta ? `<small${enOnda ? ' class="hot"' : ''}>${etiqueta}</small>` : ''}</div><div class="ic">${icono(ic, 28)}</div>${pct}` +
      `<div class="dmm">${(tsTop.prob_ts || 0) >= 30 ? 'Tormenta' : top.prob >= 20 ? 'Lluvia ' + intensidad(top.mm) : sMax >= UMBRAL_CALOR ? '<span class="hot">Calor intenso</span>' : ''}</div><div class="tmin">${Math.round(mn)}°</div>` +
      `<div class="trng"><div style="left:${(mn - tmn) / rng * 100}%;width:${Math.max((mx - mn) / rng * 100, 3)}%"></div></div><div class="tmax">${Math.round(mx)}°</div></div>`;
    if (sel) {
      const [tt, sub] = top.prob >= 25 ? [`Lluvia ${intensidad(top.mm)} ${franja(top.hora)}`, `Máximo ${top.prob}% a las ${top.hora.slice(11, 16)}.`]
        : top.prob >= 10 ? ['Baja probabilidad de lluvia', `Máximo ${top.prob}% ${franja(top.hora)}.`] : ['Sin lluvia prevista', ''];
      const uvs = rs.filter(r => r.uv != null).map(r => r.uv), uvMax = uvs.length ? Math.max(...uvs) : null;
      const rfs = rs.filter(r => r.rafaga != null).map(r => r.rafaga), rfMax = rfs.length ? Math.max(...rfs) : null;
      const uvPico = horaPico(rs, r => r.uv), hums = rs.filter(r => r.hum != null).map(r => r.hum);
      const vts = rs.filter(r => r.viento != null).map(r => r.viento), nc = nivelCalor(sMax);
      const llueve = top.prob >= 20 || (tsTop.prob_ts || 0) >= 10;
      const cLluvia = ['Lluvia', top.prob >= 10 ? `${top.prob}%` : 'No', top.prob >= 40 ? 'agua' : '', top.prob >= 10 ? `hacia las ${top.hora.slice(11, 13)}h` : 'prevista'];
      const cTs = (tsTop.prob_ts || 0) >= 10 ? ['Tormenta', `${tsTop.prob_ts}%`, 'ts', `hacia las ${tsTop.hora.slice(11, 13)}h`] : null;
      const cSe = sMax != null ? ['Se siente', `${Math.round(sMax)}°`, sMax >= UMBRAL_CALOR ? 'hot' : '', `hacia las ${sPico.hora.slice(11, 13)}h`] : null;
      const cUv = uvMax != null && uvMax >= 1 ? ['Índice UV', `${Math.round(uvMax)}`, uvMax >= 8 ? 'hot' : '', `${nivelUV(uvMax)} · ${uvPico.hora.slice(11, 13)}h`] : null;
      const cHu = hums.length ? ['Humedad', `${Math.min(...hums)}–${Math.max(...hums)}%`] : null;
      const cVi = vts.length ? ['Viento', `${Math.max(...vts)} km/h`, '', rfMax != null && rfMax >= 30 ? `ráfagas ${rfMax}` : 'máximo'] : null;
      const consejo = nc && sMax >= UMBRAL_CALOR ? ` <b class="hot">${nc[0]}:</b> ${nc[1]}` : '';
      html += `<div class="dexp"><p><b>${tt}.</b> ${sub}${consejo}${lejano ? ' <i>Pronóstico lejano: puede cambiar.</i>' : ''}</p>` +
        tarjetas(llueve ? [cLluvia, cTs, cSe, cHu, cVi, cUv] : [cSe, cUv, cHu, cVi, cLluvia, cTs]) + `${lineaTiempo(rs, ahora)}</div>`;
    }
    return html;
  }).join('');
}

// ---------- tabla por modelo ----------
function tablaModelos(filas, cfg, hoy) {
  const cols = KEYS.map(k => D.modelos[k]), u = cfg.umbral;
  let t = '<p class="nota">Milímetros de lluvia que pronostica cada modelo internacional. En azul, los que anuncian lluvia; en gris, solo llovizna. ' +
    'El número bajo cada nombre es su peso: cuanto más alto, más ha acertado en esta zona.</p><div class="scroll"><table><tr><th>Hora</th><th>Lluvia</th><th>Llovizna</th><th>Tormenta</th><th>mm</th><th>Rango</th><th>Coinciden</th>' +
    KEYS.map(k => `<th>${D.modelos[k]}<br><small>${cfg.pesos[k] ?? 1}</small></th>`).join('') + '</tr>';
  let dia = null;
  for (const r of filas) {
    if (r.hora.slice(0, 10) !== dia) { dia = r.hora.slice(0, 10); t += `<tr class="dsep"><td colspan="${7 + cols.length}">${nombreDia(dia, hoy)} · ${dia}</td></tr>`; }
    t += `<tr><td>${r.hora.slice(11, 16)}</td><td title="Sin calibrar: ${r.prob_bruta}%"><b>${r.prob}%</b></td><td>${r.prob_dz}%</td><td>${r.prob_ts || 0}%</td><td>${r.mm}</td><td>${r.min}–${r.max}</td><td>${r.conf}</td>` +
      cols.map(c => { const v = r.detalle[c]; return `<td style="color:${(v || 0) >= u ? '#60a5fa' : (v || 0) >= D.umbral_llovizna ? '#94a3b8' : '#475569'}">${v ?? '-'}</td>`; }).join('') + '</tr>';
  }
  return t + '</table></div>';
}

// ---------- mapa de lluvia (capa continua y suave) ----------
const RAMPA = [[0, [56, 189, 248, 0]], [12, [56, 189, 248, 0]], [22, [56, 189, 248, .32]], [38, [37, 99, 235, .52]],
               [55, [109, 40, 217, .64]], [72, [192, 38, 211, .74]], [88, [236, 72, 153, .82]], [100, [244, 63, 94, .88]]];
function colorRampa(p) {
  for (let i = 1; i < RAMPA.length; i++) if (p <= RAMPA[i][0]) {
    const [a, ca] = RAMPA[i - 1], [b, cb] = RAMPA[i], f = (p - a) / (b - a || 1);
    return ca.map((x, j) => x + (cb[j] - x) * f);
  }
  return RAMPA[RAMPA.length - 1][1];
}
let mapa = null;
function cargarLeaflet() {
  if (window.L) return Promise.resolve();
  return new Promise((ok, mal) => {
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'; document.head.appendChild(css);
    const js = document.createElement('script'); js.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'; js.onload = ok; js.onerror = mal; document.head.appendChild(js);
  });
}
// probabilidad en cualquier punto, interpolando entre los 4 puntos de la cuadrícula más cercanos
function probEn(j, lat, lon) {
  const M = D.mapa, ny = M.lats.length, nx = M.lons.length, ps = M.p[j];
  const fy = Math.max(0, Math.min(ny - 1, (M.lats[0] - lat) / M.paso)), fx = Math.max(0, Math.min(nx - 1, (lon - M.lons[0]) / M.paso));
  const y0 = Math.floor(fy), x0 = Math.floor(fx), y1 = Math.min(y0 + 1, ny - 1), x1 = Math.min(x0 + 1, nx - 1), dy = fy - y0, dx = fx - x0;
  const v = (y, x) => Math.max(0, ps[y * nx + x]);
  return v(y0, x0) * (1 - dx) * (1 - dy) + v(y0, x1) * dx * (1 - dy) + v(y1, x0) * (1 - dx) * dy + v(y1, x1) * dx * dy;
}
function limitesMapa() { const M = D.mapa, h = M.paso / 2;
  return [[M.lats[M.lats.length - 1] - h, M.lons[0] - h], [M.lats[0] + h, M.lons[M.lons.length - 1] + h]]; }
function imagenMapa(j) {
  const [[s0, w0], [n0, e0]] = limitesMapa(), PX = 14;
  const W = Math.round((e0 - w0) / D.mapa.paso * PX), H = Math.round((n0 - s0) / D.mapa.paso * PX);
  const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
  const cx = cv.getContext('2d'), img = cx.createImageData(W, H);
  let V = new Float32Array(W * H);
  for (let y = 0; y < H; y++) { const lat = n0 - (y + 0.5) / H * (n0 - s0);
    for (let x = 0; x < W; x++) V[y * W + x] = probEn(j, lat, w0 + (x + 0.5) / W * (e0 - w0)); }
  // dos pasadas de desenfoque para que no se noten los cuadros de la cuadrícula
  const R = Math.round(PX * 0.7), T = new Float32Array(W * H);
  for (let pase = 0; pase < 2; pase++) {
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) { let a = 0, n = 0;
      for (let d = -R; d <= R; d++) { const xx = x + d; if (xx >= 0 && xx < W) { a += V[y * W + xx]; n++; } } T[y * W + x] = a / n; }
    for (let x = 0; x < W; x++) for (let y = 0; y < H; y++) { let a = 0, n = 0;
      for (let d = -R; d <= R; d++) { const yy = y + d; if (yy >= 0 && yy < H) { a += T[yy * W + x]; n++; } } V[y * W + x] = a / n; }
  }
  // los bordes de la cuadrícula se desvanecen poco a poco: ya no se ve una línea recta donde termina el mapa
  const BORDE = PX * 1.6, suave = x => x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x);
  for (let k = 0; k < W * H; k++) { const c = colorRampa(V[k]), x = k % W, y = (k - x) / W;
    const f = suave(Math.min(x, W - 1 - x, y, H - 1 - y) / BORDE);
    img.data[k * 4] = c[0]; img.data[k * 4 + 1] = c[1]; img.data[k * 4 + 2] = c[2]; img.data[k * 4 + 3] = Math.round(c[3] * 255 * f); }
  cx.putImageData(img, 0, 0);
  return cv.toDataURL();
}
function htmlMapa() {
  if (!D.mapa || !D.mapa.horas || !D.mapa.horas.length) return '<p class="nota">El mapa no está disponible en esta actualización.</p>';
  const grad = [10, 20, 40, 60, 80, 100].map(p => { const c = colorRampa(p); return `rgba(${c.map((x, i) => i < 3 ? Math.round(x) : Math.max(x, .35)).join(',')}) ${p}%`; }).join(',');
  const S = satAhora();
  return (S ? `<div class="modos" role="group" aria-label="Qué mostrar"><button class="modo on" data-m="pron" aria-pressed="true">Pronóstico</button>` +
      `<button class="modo" data-m="ahora" aria-pressed="false">Lloviendo ahora</button></div>` : '') +
    `<div id="mapa" role="application" aria-label="Mapa de probabilidad de lluvia"><div class="vacio"><span class="cargando"></span>Cargando mapa…</div></div>` +
    `<div class="mapctl" id="mctl"><button id="mplay" aria-label="Reproducir la animación">▶</button><input type="range" id="mslider" min="0" max="${D.mapa.horas.length - 1}" value="0" aria-label="Hora del mapa"><div class="mapht" id="mhora" aria-live="polite"></div></div>` +
    `<div class="mapley" id="mley"><span>Poca</span><i style="background:linear-gradient(90deg,${grad})"></i><span>Mucha</span></div>` +
    (S ? `<div class="mapley" id="mley2" hidden><span>Débil</span><i style="background:linear-gradient(90deg,${RAMPA_MM.slice(1).map(([u, c]) => `rgba(${c.slice(0, 3).join(',')},${Math.max(c[3], .4)})`).join(',')})"></i><span>Fuerte</span></div>` : '') +
    `<p class="nota" id="mnota">Probabilidad de lluvia en cada zona a la hora elegida. Toca el mapa para ver el porcentaje exacto. Es un pronóstico, no un radar.</p>`;
}
function activarMapa(v) {
  if (!$('#mapa') || mapa) { if (mapa) setTimeout(() => mapa.invalidateSize(), 50); return; }
  (async () => {
    try { await cargarLeaflet(); } catch (e) { $('#mapa').innerHTML = '<div class="vacio">No se pudo cargar el mapa. Revisa tu conexión.</div>'; return; }
    if (!$('#mapa') || mapa) return;
    const M = D.mapa, tactil = matchMedia('(pointer:coarse)').matches, hoy = ahoraISO(D.offset).slice(0, 10);
    $('#mapa').innerHTML = '';
    const map = L.map('mapa', {zoomControl: !tactil, dragging: !tactil, tap: false, scrollWheelZoom: false, attributionControl: true, zoomSnap: 0.25})
      .setView([v.lat ?? -1.6, v.lon ?? -78.6], v.lat != null ? 8 : 6.5);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 13,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(map);
    const capa = L.imageOverlay(imagenMapa(0), limitesMapa(), {opacity: 1, className: 'capa-lluvia'}).addTo(map);
    if (v.lat != null) L.circleMarker([v.lat, v.lon], {radius: 7, color: '#fff', weight: 2, fillColor: '#f59e0b', fillOpacity: 1}).addTo(map);
    const sl = $('#mslider'), lab = $('#mhora'), cache = {};
    let j = 0;
    const pintar = k => { j = k; capa.setUrl(cache[k] || (cache[k] = imagenMapa(k)));
      const hr = M.horas[k]; lab.textContent = `${nombreDia(hr.slice(0, 10), hoy)} ${hr.slice(11, 16)}`; };
    map.on('click', e => { const p = Math.round(probEn(j, e.latlng.lat, e.latlng.lng));
      L.popup({closeButton: false, className: 'pop'}).setLatLng(e.latlng).setContent(`<b>${p}%</b> de lluvia · ${lab.textContent}`).openOn(map); });
    let tm = null; const play = $('#mplay');
    const ahora = ahoraISO(D.offset); let j0 = M.horas.findIndex(h => h >= ahora); if (j0 < 0) j0 = 0;
    sl.value = j0; pintar(j0);
    sl.addEventListener('input', () => pintar(+sl.value));
    play.onclick = () => { if (tm) { clearInterval(tm); tm = null; play.textContent = '▶'; play.setAttribute('aria-label', 'Reproducir la animación'); return; }
      play.textContent = '❚❚'; play.setAttribute('aria-label', 'Pausar');
      tm = setInterval(() => { if (!$('#mapa')) return clearInterval(tm); sl.value = (+sl.value + 1) % M.horas.length; pintar(+sl.value); }, 600); };
    mapa = map;
    // modo "lloviendo ahora": estimación satelital de NOAA de los últimos minutos
    const S = satAhora(); let capaAhora = null;
    $$('.modo').forEach(b => b.onclick = () => {
      const ahoraM = b.dataset.m === 'ahora'; tocar();
      $$('.modo').forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b); });
      $('#mctl').hidden = ahoraM; $('#mley').hidden = ahoraM; $('#mley2').hidden = !ahoraM;
      if (tm) play.onclick();
      if (ahoraM) {
        map.removeLayer(capa);
        if (!capaAhora) capaAhora = L.imageOverlay(imagenAhora(), [[S.lat0 - S.ny * S.paso, S.lon0], [S.lat0, S.lon0 + S.nx * S.paso]], {className: 'capa-lluvia'});
        capaAhora.addTo(map);
        $('#mnota').textContent = `Lluvia estimada por satélite a las ${S.t.slice(11, 16)}. Toca el mapa para ver la intensidad. No es un radar: puede fallar con lluvias muy débiles.`;
      } else {
        if (capaAhora) map.removeLayer(capaAhora); capa.addTo(map);
        $('#mnota').textContent = 'Probabilidad de lluvia en cada zona a la hora elegida. Toca el mapa para ver el porcentaje exacto. Es un pronóstico, no un radar.';
      }
      map.off('click'); map.on('click', ahoraM ? clicAhora : clicPron);
    });
    const clicPron = e => { const p = Math.round(probEn(j, e.latlng.lat, e.latlng.lng));
      L.popup({closeButton: false, className: 'pop'}).setLatLng(e.latlng).setContent(`<b>${p}%</b> de lluvia · ${lab.textContent}`).openOn(map); };
    const clicAhora = e => { const mm = satEn(e.latlng.lat, e.latlng.lng, 0);
      L.popup({closeButton: false, className: 'pop'}).setLatLng(e.latlng).setContent(mm == null ? 'Sin datos' : mm < 0.5 ? 'Sin lluvia' : `<b>Lluvia ${intensidad(mm)}</b> · ${mm} mm/h`).openOn(map); };
  })();
}
const RAMPA_MM = [[0.4, [56, 189, 248, 0]], [1, [56, 189, 248, .45]], [3, [37, 99, 235, .6]], [6, [109, 40, 217, .7]], [12, [192, 38, 211, .8]], [25, [244, 63, 94, .88]]];
function colorMM(v) {
  if (v < RAMPA_MM[0][0]) return [0, 0, 0, 0];
  for (let i = 1; i < RAMPA_MM.length; i++) if (v <= RAMPA_MM[i][0]) { const [a, ca] = RAMPA_MM[i - 1], [b, cb] = RAMPA_MM[i], f = (v - a) / (b - a);
    return ca.map((x, k) => x + (cb[k] - x) * f); }
  return RAMPA_MM[RAMPA_MM.length - 1][1];
}
function imagenAhora() {
  const S = satAhora(), cv = document.createElement('canvas'); cv.width = S.nx; cv.height = S.ny;
  const cx = cv.getContext('2d'), img = cx.createImageData(S.nx, S.ny);
  for (let k = 0; k < S.nx * S.ny; k++) { const c = colorMM(S.v[k] * S.escala);
    img.data[k * 4] = c[0]; img.data[k * 4 + 1] = c[1]; img.data[k * 4 + 2] = c[2]; img.data[k * 4 + 3] = Math.round(c[3] * 255); }
  cx.putImageData(img, 0, 0);
  return cv.toDataURL();
}

// ---------- satélite (capa en el mapa, NASA GIBS; si falla, imagen de NOAA) ----------
let mapaSat = null;
function htmlSatelite() {
  return `<div id="satmap" role="application" aria-label="Imagen de satélite sobre Ecuador"><div class="vacio"><span class="cargando"></span>Cargando satélite…</div></div>` +
    `<div class="mapctl"><div class="mapht" id="sathora" aria-live="polite" style="text-align:left"></div></div>` +
    `<p class="nota">Imagen GeoColor del satélite GOES-19: de día se ve como a simple vista; de noche las nubes altas (las de tormenta) se ven en blanco. ` +
    `Crédito: CIRA/NOAA, servida por NASA GIBS. <a href="https://www.star.nesdis.noaa.gov/GOES/sector.php?sat=G19&sector=nsa" target="_blank" rel="noopener">Ver en NOAA</a></p>`;
}
const SAT_IMGS = ['SECTOR/nsa/GEOCOLOR/latest.jpg', 'SECTOR/nsa/GEOCOLOR/1200x1200.jpg', 'SECTOR/nsa/GEOCOLOR/1800x1080.jpg', 'FD/GEOCOLOR/1808x1808.jpg']
  .map(p => 'https://cdn.star.nesdis.noaa.gov/GOES19/ABI/' + p);
function satImagenNOAA() {
  const caja = $('#satmap'); if (!caja) return;
  if (mapaSat) { mapaSat.remove(); mapaSat = null; }
  caja.innerHTML = ''; const img = document.createElement('img'); img.alt = 'Imagen de satélite GOES-19'; img.className = 'satimg';
  let i = 0; const t = Math.floor(Date.now() / 6e5);
  const sig = () => { if (i >= SAT_IMGS.length) { caja.innerHTML = '<div class="vacio">La imagen de satélite no está disponible en este momento.</div>'; return; }
    img.src = `${SAT_IMGS[i++]}?t=${t}`; };
  img.onerror = sig; img.onload = () => { const h = $('#sathora'); if (h) h.textContent = 'Imagen más reciente de NOAA'; };
  caja.appendChild(img); sig();
}
function activarSatelite(v) {
  if (!$('#satmap')) return;
  if (mapaSat) { setTimeout(() => mapaSat.invalidateSize(), 50); return; }
  (async () => {
    try { await cargarLeaflet(); } catch (e) { satImagenNOAA(); return; }
    if (!$('#satmap') || mapaSat) return;
    $('#satmap').innerHTML = '';
    const tactil = matchMedia('(pointer:coarse)').matches;
    const map = L.map('satmap', {zoomControl: !tactil, dragging: !tactil, tap: false, scrollWheelZoom: false, minZoom: 4, maxZoom: 9})
      .setView([v && v.lat != null ? v.lat : -1.6, v && v.lon != null ? v.lon : -78.6], 6);
    mapaSat = map;
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 9, attribution: '&copy; OpenStreetMap'}).addTo(map);
    // GIBS publica con algo de retraso: se prueba la imagen de hace ~40 min y, si no hay, otras más antiguas
    const intentos = [];
    for (const min of [40, 70, 120, 180]) for (const ext of ['jpg', 'png']) intentos.push([min, ext]);
    let capa = null, k = 0;
    const probar = () => {
      if (capa) { map.removeLayer(capa); capa = null; }
      if (k >= intentos.length) { satImagenNOAA(); return; }
      const [min, ext] = intentos[k++];
      const d = new Date(Date.now() - min * 6e4); d.setUTCSeconds(0, 0); d.setUTCMinutes(Math.floor(d.getUTCMinutes() / 10) * 10);
      const tiempo = d.toISOString().slice(0, 19) + 'Z';
      let ok = 0, mal = 0, decidido = false;
      capa = L.tileLayer(`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_GeoColor/default/${tiempo}/GoogleMapsCompatible_Level7/{z}/{y}/{x}.${ext}`,
        {maxNativeZoom: 7, maxZoom: 9, opacity: 0.92, attribution: 'GOES-19 GeoColor: CIRA/NOAA · NASA GIBS'});
      capa.on('tileload', () => { ok++; if (!decidido && ok >= 2) { decidido = true;
        const h = $('#sathora'); if (h) h.textContent = 'Imagen de las ' + new Date(d.getTime() + D.offset * 1000).toISOString().slice(11, 16) + ' (hora de Ecuador)'; } });
      capa.on('tileerror', () => { mal++; if (!decidido && mal >= 3 && ok === 0) { decidido = true; probar(); } });
      capa.addTo(map);
    };
    probar();
    if (v && v.lat != null) L.circleMarker([v.lat, v.lon], {radius: 6, color: '#fff', weight: 2, fillColor: '#f59e0b', fillOpacity: 1}).addTo(map);
  })();
}

// ---------- reportes de usuarios ----------
const TIPOS_REP = [['soleado', '☀️', 'Soleado'], ['nublado', '☁️', 'Nublado'], ['llovizna', '🌦️', 'Llovizna'],
                   ['lluvia', '🌧️', 'Lluvia'], ['lluvia_fuerte', '🌊', 'Aguacero'], ['tormenta', '⛈️', 'Tormenta']];
const NOMBRE_REP = Object.fromEntries(TIPOS_REP.map(([t, , n]) => [t, n.toLowerCase()]));
function htmlFeedback() {
  return `<div class="grab"></div><h2>¿Cómo está el clima donde estás?</h2><div class="fbb">` +
    TIPOS_REP.map(([t, e, n]) => `<button data-t="${t}"><span class="fbe" aria-hidden="true">${e}</span>${n}</button>`).join('') +
    `</div><div class="fbres" id="fbres" aria-live="polite"></div><div class="fbres" id="fbsum"></div><p class="nota">Tu reporte nos ayuda a comprobar el pronóstico en tu barrio. ` +
    `Solo guardamos la hora, lo que reportas y tu ubicación aproximada; nunca datos personales.</p>`;
}
function abrirReporte() { abrirHoja(htmlFeedback(), 'Reportar el clima', () => { activarFeedback(); resumenFeedback(); }); }
const enEcuador = p => p && p.lat >= -6 && p.lat <= 2.5 && p.lon >= -92.5 && p.lon <= -75;
const TXT_FUERA = 'Tu navegador te ubica fuera de Ecuador. Si usas una VPN o la red de una empresa, desactívala para esta página o reporta desde el celular con datos móviles.';
function obtenerPos(forzar) {
  return new Promise((ok, mal) => {
    if (estado.pos && !forzar) return ok(estado.pos);
    if (!navigator.geolocation) return mal(new Error('sin geolocalización'));
    navigator.geolocation.getCurrentPosition(p => { estado.pos = {lat: p.coords.latitude, lon: p.coords.longitude}; ok(estado.pos); },
      mal, {enableHighAccuracy: true, timeout: 15000, maximumAge: 600000});
  });
}
async function resumenFeedback() {
  const box = $('#fbsum'), v = ctx && ctx.v; if (!D.feedback_url || !v) return;
  const clave = claveRep(v);
  try {
    let j;
    if (estado.rep && estado.rep.clave === clave && Date.now() - estado.rep.t < 5 * 60 * 1000) j = estado.rep.j;
    else {
      const r = await fetch(`${D.feedback_url}/resumen?lat=${v.lat.toFixed(2)}&lon=${v.lon.toFixed(2)}`);
      j = await r.json();
      const antes = JSON.stringify(evidencia(v));
      estado.rep = {clave, j, t: Date.now()};
      if (JSON.stringify(evidencia(v)) !== antes && ctx && ctx.v === v) { render(); return; }
    }
      const mini = $('#fbmini');
      if (j.total >= 1) {
        const partes = TIPOS_REP.filter(([t]) => j[t]).map(([t, e]) => `${e} ${j[t]} ${NOMBRE_REP[t]}`);
        box && (box.innerHTML = `En la última hora, cerca de ti (≈10 km): ${partes.join(' · ')}.`);
        mini && (mini.textContent = `Cerca de ti: ${TIPOS_REP.filter(([t]) => j[t]).map(([t, e]) => e + ' ' + j[t]).join(' ')}`);
      }
  } catch (e) {}
}
function activarFeedback() {
  $$('.fbb button').forEach(b => b.addEventListener('click', async () => {
    const box = $('#fbres'), ult = +(leer('cc_fb_ts') || 0);
    if (Date.now() - ult < 30 * 60 * 1000) { box.textContent = 'Ya registramos tu reporte hace poco. ¡Gracias! Puedes volver a reportar en 30 minutos.'; return; }
    box.textContent = 'Obteniendo tu ubicación aproximada…';
    let pos; try { pos = await obtenerPos(); } catch (e) { box.textContent = 'Para reportar necesitamos tu ubicación. Revisa el permiso del navegador.'; return; }
    if (!enEcuador(pos)) { box.textContent = TXT_FUERA; return; }
    $$('.fbb button').forEach(x => x.disabled = true);
    try {
      const r = await fetch(`${D.feedback_url}/reporte`, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({lat: +pos.lat.toFixed(2), lon: +pos.lon.toFixed(2), tipo: b.dataset.t})});
      if (r.status === 429) box.textContent = 'Ya registramos tu reporte hace poco. ¡Gracias!';
      else if (r.status === 400) box.textContent = TXT_FUERA;
      else if (!r.ok) throw new Error(r.status);
      else { box.textContent = `¡Gracias! Registramos «${NOMBRE_REP[b.dataset.t]}» en tu zona.`; guardar('cc_fb_ts', Date.now());
             $$('.fbb button').forEach(x => x.classList.toggle('elegido', x === b)); tocar(); resumenFeedback(); }
    } catch (e) { box.textContent = 'No pudimos enviar tu reporte. Inténtalo más tarde.'; }
    finally { $$('.fbb button').forEach(x => x.disabled = false); }
  }));
}

// ---------- mi ubicación (la vista principal) ----------
function km(a, b, c, d) { const R = Math.PI / 180, x = Math.sin((c - a) * R / 2) ** 2 + Math.cos(a * R) * Math.cos(c * R) * Math.sin((d - b) * R / 2) ** 2; return 12742 * Math.asin(Math.sqrt(x)); }
function referencia(lat, lon, elev) {
  const zona = lon < -88 ? 'galapagos' : (elev >= 1500 ? 'sierra' : (lon > -78.3 ? 'oriente' : 'costa'));
  const verif = D.ciudades.filter(c => c.propia), todas = verif.length ? verif : D.ciudades;
  const misma = todas.filter(c => c.zona === zona), lista = misma.length ? misma : todas;
  const ref = lista.reduce((a, b) => km(lat, lon, a.lat, a.lon) <= km(lat, lon, b.lat, b.lon) ? a : b);
  const dist = Math.round(km(lat, lon, ref.lat, ref.lon));
  return {ref, dist, aprox: !misma.length || dist > 120 || !ref.propia};
}
async function nombreLugar(lat, lon) {
  // Barrio o ciudadela (Urdesa, Alborada, La Carolina…) con OpenStreetMap; si no hay, la ciudad.
  const k = `cc_lugar_${lat.toFixed(3)},${lon.toFixed(3)}`, c = leer(k);
  if (c) return c;
  let nombre = null;
  try {
    const r = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat.toFixed(3)}&lon=${lon.toFixed(3)}&zoom=17&addressdetails=1&accept-language=es`);
    const a = (await r.json()).address || {};
    const barrio = a.neighbourhood || a.suburb || a.quarter || a.residential || a.city_district || a.hamlet || a.village;
    const ciudad = a.city || a.town || a.municipality || a.county || a.state;
    nombre = barrio && ciudad && barrio !== ciudad ? `${barrio}, ${ciudad}` : (barrio || ciudad || null);
  } catch (e) {}
  if (!nombre) try {
    const r = await fetch(`https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lon}&localityLanguage=es`);
    const j = await r.json(); nombre = j.locality || j.city || j.principalSubdivision || null;
  } catch (e) {}
  if (nombre) guardar(k, nombre);
  return nombre;
}
// temperatura máxima normal (últimos 30 días) en el punto exacto de la persona, para la onda de calor
const claveNormal = (lat, lon) => `cc_normal_${lat.toFixed(2)},${lon.toFixed(2)}`;
function normalGuardada(lat, lon) {
  try { const g = JSON.parse(leer(claveNormal(lat, lon)) || 'null'); return g && g.d === ahoraISO(D.offset).slice(0, 10) ? g.v : null; } catch (e) { return null; }
}
async function normalUbic(lat, lon) {
  if (normalGuardada(lat, lon) != null) return;
  try {
    const j = await (await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&daily=temperature_2m_max&past_days=30&forecast_days=1&timezone=auto`)).json();
    const hoy = ahoraISO(D.offset).slice(0, 10), vs = j.daily.time.map((t, i) => t < hoy ? j.daily.temperature_2m_max[i] : null).filter(v => v != null);
    if (vs.length < 15) return;
    guardar(claveNormal(lat, lon), JSON.stringify({d: hoy, v: Math.round(media(vs) * 10) / 10}));
    const u = estado.ubic;
    if (u && u.lat === lat && u.lon === lon) { u.normal_max = normalGuardada(lat, lon) - (u.tarde || 0); if (estado.vista === 'ubic' && ctx) render(); }
  } catch (e) {}
}
function construirUbic(c) {
  const {lat, lon, om, nombre} = c;
  const {ref, dist, aprox} = referencia(lat, lon, om.elevation ?? 0);
  const nota = aprox
    ? `Ajuste aproximado: todavía no tenemos una estación verificada cerca de ti. Usamos lo aprendido en ${ref.nombre} (${dist} km).`
    : `Pronóstico de tu ubicación exacta, ajustado con lo aprendido en ${ref.nombre} (${dist} km).`;
  const off = om.utc_offset_seconds ?? D.offset;
  // la corrección de temperatura del aeropuerto solo se usa si estás a menos de 60 km
  const cfgU = dist <= 60 ? ref.cfg : Object.assign({}, ref.cfg, {temp: null});
  const tarde = cfgU.temp && cfgU.temp.activa ? (cfgU.temp.sesgo || {}).tarde || 0 : 0;
  const nm = normalGuardada(lat, lon);
  return {nombre: nombre || 'Tu ubicación', ref: ref.nombre, lat, lon, filas: filasDesdeOM(om.hourly, cfgU, lat, lon, off), cfg: cfgU, offset: off,
    normal_max: nm == null ? undefined : nm - tarde, tarde,
    nivel: ref.nivel + (aprox ? ' (aprox.)' : ''), lugar: enEcuador({lat, lon}) ? nota : 'Tu navegador te ubica fuera de Ecuador (¿VPN activa?). ClearSky solo cubre Ecuador: elige una ciudad en el botón de arriba.', t: c.t, verif_html: `<p class="aviso">${esc(nota)}</p>` + ref.verif_html};
}
async function cargarUbicacion(opc = {}) {
  if (estado.buscando) return; estado.buscando = true;
  const silencioso = opc.silencioso && estado.ubic;
  if (!silencioso) { estado.vista = 'ubic'; cabecera(); avisoTop(''); $('#vista').innerHTML = '<div class="vacio"><span class="cargando"></span>Buscando tu ubicación…</div>'; }
  let pos;
  try { pos = await obtenerPos(opc.forzar); }
  catch (e) {
    estado.buscando = false;
    if (silencioso) return;
    const negado = e && e.code === 1;
    alternativa(negado ? 'Sin permiso de ubicación. Actívalo en la configuración del navegador para ver el pronóstico exacto de donde estás.'
                       : 'No pudimos obtener tu ubicación. Mientras tanto te mostramos la ciudad más cercana disponible.');
    return;
  }
  guardar('cc_ubic', '1');
  if (!silencioso) $('#vista').innerHTML = '<div class="vacio"><span class="cargando"></span>Calculando el pronóstico para tu ubicación…</div>';
  try {
    const lat = +pos.lat.toFixed(2), lon = +pos.lon.toFixed(2);
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&hourly=${D.vars.join(',')}&models=${KEYS.join(',')}&timezone=auto&forecast_days=7`;
    const [om, nombre] = await Promise.all([fetch(url).then(r => r.json()), nombreLugar(pos.lat, pos.lon)]);
    if (!om.hourly) throw new Error(om.reason || 'sin datos');
    const cache = {lat, lon, om, nombre, t: Date.now()};
    guardar('cc_ubic_cache', JSON.stringify(cache));
    estado.ubic = construirUbic(cache);
    actualizarSuscripcion(); normalUbic(lat, lon);
    if (estado.vista === 'ubic' || !silencioso) { estado.vista = 'ubic'; render(); }
  } catch (e) {
    if (!silencioso) alternativa('No pudimos descargar el pronóstico para tu ubicación. Revisa tu conexión.');
  } finally { estado.buscando = false; }
}
function ciudadCercana() {
  if (estado.pos) return D.ciudades.reduce((a, b, i) => km(estado.pos.lat, estado.pos.lon, b.lat, b.lon) < km(estado.pos.lat, estado.pos.lon, D.ciudades[a].lat, D.ciudades[a].lon) ? i : a, 0);
  return 0;
}
function alternativa(msg) {
  if (estado.ubic) { estado.vista = 'ubic'; render(); toast(msg); return; }
  estado.vista = ciudadCercana(); render();
  avisoTop(msg, true);
}
function avisoTop(msg, conBoton) {
  const a = $('#aviso'); if (!msg) { a.innerHTML = ''; return; }
  a.innerHTML = `<div class="aviso-top"><p>📍 ${esc(msg)}</p>${conBoton ? '<button class="btn" id="usarubic">Usar mi ubicación</button>' : ''}</div>`;
  const b = $('#usarubic'); if (b) b.onclick = () => cargarUbicacion({forzar: true});
}

// ---------- selector de lugar (hoja inferior) ----------
function resumenCiudad(c) {
  const ahora = ahoraISO(D.offset), hoy = ahora.slice(0, 10), prox = c.filas.filter(r => r.hora >= ahora);
  if (!prox.length) return {t: '-', ic: 'nube', txt: ''};
  const hoyR = prox.filter(r => r.hora.slice(0, 10) === hoy), top = (hoyR.length ? hoyR : prox.slice(0, 12)).reduce((a, b) => b.prob > a.prob ? b : a);
  return {t: prox[0].temp != null ? Math.round(prox[0].temp) + '°' : '-', ic: condicion(prox[0])[0],
    txt: top.prob >= 20 ? `Lluvia hoy: hasta ${top.prob}%` : condicion(prox[0])[1]};
}
function abrirSelector() {
  const u = estado.ubic;
  let h = `<div class="grab"></div><h2>Elegir lugar</h2>` +
    `<button class="lit${estado.vista === 'ubic' ? ' on' : ''}" data-v="ubic"><span style="font-size:22px">📍</span><span class="nm">Mi ubicación<small>${u ? esc(u.nombre) + ' · pronóstico exacto de donde estás' : 'Pronóstico exacto de donde estás'}</small></span>` +
    (u && u.filas.length ? `<span class="tt">${(() => { const p = u.filas.find(r => r.hora >= ahoraISO(u.offset)); return p && p.temp != null ? Math.round(p.temp) + '°' : ''; })()}</span>` : '') + `</button>` +
    `<div class="sh">Otras ciudades</div>`;
  h += D.ciudades.map((c, i) => { const r = resumenCiudad(c);
    return `<button class="lit${estado.vista === i ? ' on' : ''}" data-v="${i}"><span class="ic">${icono(r.ic, 30)}</span><span class="nm">${esc(c.nombre)}<small>${esc(r.txt)}</small></span><span class="tt">${r.t}</span></button>`; }).join('');
  abrirHoja(h, 'Elegir lugar', () => $('#sheet').querySelectorAll('.lit').forEach(b => b.onclick = () => {
    tocar(); cerrarSelector();
    estado.dia = null; estado.tlSel = null;
    if (b.dataset.v === 'ubic') { estado.ubic ? (estado.vista = 'ubic', avisoTop(''), render()) : cargarUbicacion({forzar: true}); }
    else { estado.vista = +b.dataset.v; render(); }
    window.scrollTo({top: 0, behavior: 'smooth'});
  }));
}
let focoAntes = null;
function abrirHoja(html, etiqueta, alAbrir) {
  const sh = $('#sheet'), bg = $('#sheetbg');
  focoAntes = document.activeElement;
  $('#sheetc').innerHTML = html; sh.setAttribute('aria-label', etiqueta);
  sh.hidden = false; bg.hidden = false;
  requestAnimationFrame(() => { sh.classList.add('on'); bg.classList.add('on'); const f = sh.querySelector('button'); f && f.focus({preventScroll: true}); });
  if (!(history.state && history.state.hoja)) history.pushState({hoja: 1}, '');
  alAbrir && alAbrir();
  // deslizar hacia abajo para cerrar
  const grab = sh.querySelector('.grab'); if (!grab) return; let y0 = null;
  const ini = e => { y0 = e.clientY; sh.style.transition = 'none'; };
  const mov = e => { if (y0 == null) return; const dy = Math.max(0, e.clientY - y0); sh.style.transform = `translateY(${dy}px)`; };
  const fin = e => { if (y0 == null) return; const dy = e.clientY - y0; y0 = null; sh.style.transition = ''; sh.style.transform = ''; if (dy > 70) cerrarSelector(); };
  grab.addEventListener('pointerdown', e => { grab.setPointerCapture(e.pointerId); ini(e); });
  grab.addEventListener('pointermove', mov); grab.addEventListener('pointerup', fin); grab.addEventListener('pointercancel', fin);
}
function cerrarSelector(desdeHistorial) {
  const sh = $('#sheet'), bg = $('#sheetbg'); if (sh.hidden) return;
  sh.classList.remove('on'); bg.classList.remove('on');
  setTimeout(() => { sh.hidden = true; bg.hidden = true; }, 280);
  if (focoAntes && focoAntes.focus) focoAntes.focus({preventScroll: true});
  if (!desdeHistorial && history.state && history.state.hoja) history.back();
}
window.addEventListener('popstate', () => cerrarSelector(true));
document.addEventListener('keydown', e => { if (e.key === 'Escape') cerrarSelector(); });

// ---------- página ----------
function vistaActual() {
  if (estado.vista === 'ubic') return estado.ubic;
  const c = D.ciudades[estado.vista]; if (!c) return null;
  return {nombre: c.nombre, ref: c.ref || c.nombre, lat: c.lat, lon: c.lon, filas: c.filas, cfg: c.cfg, offset: D.offset, nivel: c.nivel,
    verif_html: c.verif_html, t: null,
    lugar: c.propia ? '' : (c.ref ? `Pronóstico de ${c.nombre} ajustado con lo aprendido en ${c.ref} mientras reunimos datos propios.` : '')};
}
function cabecera() {
  const v = estado.vista === 'ubic' ? (estado.ubic ? estado.ubic.nombre.split(',')[0] : 'Tu ubicación') : (D.ciudades[estado.vista] || {}).nombre || 'Elegir lugar';
  $('#lugarnom').textContent = (estado.vista === 'ubic' ? '📍 ' : '') + v;
}
const VARS_G = [['lluvia', 'Lluvia'], ['temp', 'Temperatura'], ['viento', 'Viento'], ['uv', 'UV']];
// si va a llover, la gráfica abre en lluvia; si no, en temperatura
const varAuto = filas => filas.some(r => r.prob >= 20 || (r.prob_ts || 0) >= 30 || r.prob_dz >= 50) ? 'lluvia' : 'temp';
function leyendaG(items) {
  return items.map(([c, t, tipo]) => `<span class="lg"><i style="background:${tipo === 'dash' || tipo === 'pt' ? `repeating-linear-gradient(90deg,${c} 0 4px,transparent 4px 7px)` : c};height:${tipo === 'sq' ? '10px' : '3px'};${tipo === 'sq' ? 'width:10px;border-radius:2px' : ''}"></i>${t}</span>`).join('') +
    `<span class="lg"><i style="background:repeating-linear-gradient(90deg,${claro() ? '#334155' : '#cbd5e1'} 0 3px,transparent 3px 6px);height:2px"></i>Ahora</span>`;
}
let ctx = null;
function pintarGrafica() {
  if (!ctx || !$('#graf') || estado.tab !== 'graf') return;
  const {ahora, hoy, dias, prox} = ctx;
  const filasG = estado.dia ? dias[estado.dia] : prox.slice(0, 72);
  $('#titg').textContent = estado.dia ? `Hora por hora · ${nombreDia(estado.dia, hoy)}` : 'Hora por hora · 3 días';
  $('#todo').hidden = !estado.dia;
  const vr = estado.gvar || varAuto(filasG);
  $('#gsel').innerHTML = VARS_G.map(([k, n]) => `<button class="gb${k === vr ? ' on' : ''}" data-v="${k}" aria-pressed="${k === vr}">${n}</button>`).join('');
  $$('#gsel .gb').forEach(b => b.onclick = () => { tocar(); estado.gvar = b.dataset.v; pintarGrafica(); });
  grafica($('#graf'), filasG, ahora, hoy, vr);
  $('#leyg').innerHTML = leyendaG($('#graf')._leyenda || []);
}
function pintarDias() {
  const {v, ahora, hoy, dias} = ctx;
  $('#dias').innerHTML = htmlDias(dias, ctx.filas, ahora, hoy, v.cfg, v.offset, ctx.onda);
  const exp = document.querySelector('.dexp'); if (exp) { activarLinea(exp, dias[estado.dia]); }
  $$('.drow').forEach(d => { const f = () => { tocar(); estado.dia = estado.dia === d.dataset.dia ? null : d.dataset.dia; estado.tlSel = null; pintarDias(); pintarGrafica();
      const s = document.querySelector('.drow.sel'); if (s) s.scrollIntoView({block: 'nearest', behavior: 'smooth'}); };
    d.onclick = f; d.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); f(); } }; });
}
const TABS = [['graf', 'Por hora'], ['mapa', 'Mapa de lluvia'], ['sat', 'Satélite']];
function mostrarTab(t, foco) {
  estado.tab = t; guardar('cc_tab', t);
  $$('.tab').forEach(b => { const on = b.dataset.t === t; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1; if (on && foco) b.focus(); });
  $$('.panel').forEach(p => p.hidden = p.dataset.t !== t);
  if (t === 'graf') pintarGrafica();
  else if (t === 'mapa') activarMapa(ctx.v);
  else activarSatelite(ctx.v);
}
function alertaPara(v) {
  return (D.alertas || []).filter(a => a.ciudad === v.ref || a.ciudad === v.nombre).sort((a, b) => b.prob - a.prob)[0];
}
function hace(t) { const m = Math.round((Date.now() - t) / 6e4); return m < 2 ? 'ahora' : m < 60 ? `hace ${m} min` : `hace ${Math.round(m / 60)} h`; }
function render() {
  // recordar qué secciones desplegables estaban abiertas para dejarlas igual después de actualizar
  const abiertos = new Set([...document.querySelectorAll('#vista details')].filter(d => d.open).map(d => (d.querySelector('summary') || {}).textContent));
  cabecera();
  const v = vistaActual(); if (!v) return;
  const ahora = ahoraISO(v.offset), hoy = ahora.slice(0, 10), ev = evidencia(v);
  const filas = conAhora(v.filas, ahora, ev), prox = filas.filter(r => r.hora >= ahora);
  if (!prox.length) { $('#vista').innerHTML = '<div class="vacio"><span class="cargando"></span>Actualizando el pronóstico…</div>'; if (Date.now() - (estado.ultForzado || 0) > 5 * 60 * 1000) { estado.ultForzado = Date.now(); refrescarDatos(true); } return; }
  const act = prox[0], [ic, ctxt] = condicion(act);
  let [tit, sub] = titular(prox.slice(0, 24), hoy);
  const p12 = prox.slice(0, 24), dia12 = p12.filter(r => !esNoche(r.hora)), sPico = dia12.length ? horaPico(dia12, sens) : null;
  const sMax = sPico ? sens(sPico) : null, ncal = nivelCalor(sMax);
  const lluviaImporta = p12.some(r => r.prob >= 40 || (r.prob_ts || 0) >= 30);
  if (!lluviaImporta && sMax != null && sMax >= UMBRAL_CALOR) {
    const calor = dia12.filter(r => sens(r) >= UMBRAL_CALOR), h0 = calor[0].hora.slice(11, 16), h1 = finHora(calor[calor.length - 1].hora);
    tit = `${ncal[0]} ${sPico.hora.slice(0, 10) === hoy ? franja(sPico.hora) : 'mañana ' + franja(sPico.hora)}`;
    sub = `Sensación térmica de hasta ${Math.round(sMax)}° entre las ${h0} y las ${h1}. ${ncal[1]}` + (prox[0].prob < 10 && p12.every(r => r.prob < 20) ? ' Sin lluvia prevista.' : '');
  }
  if (ev.llueve) {
    const fin = prox.findIndex((r, i) => i > 0 && r.prob < 40);
    tit = estado.vista === 'ubic' ? 'Está lloviendo cerca de ti' : `Está lloviendo en ${v.nombre}`;
    sub = (fin > 0 ? `Probablemente siga hasta las ${prox[fin].hora.slice(11, 16)}. ` : 'Podría seguir varias horas. ') + `Lo ${ev.fuentes.length > 1 || /reportes/.test(ev.fuentes[0] || '') ? 'confirman' : 'confirma'} ${ev.fuentes.join(' y ')}.`;
  }
  const hoyF = v.filas.filter(r => r.hora.slice(0, 10) === hoy && r.temp != null);
  const tprox = act.prob >= 20 ? `${ctxt.toLowerCase()} (${act.prob}%)` : `${ctxt}, sin lluvia`;
  const dias = porDia(filas, ahora);
  if (estado.dia && !dias[estado.dia]) estado.dia = null;
  if (mapa) { mapa.remove(); mapa = null; }
  if (mapaSat) { mapaSat.remove(); mapaSat = null; }
  const y0 = scrollY;
  const normal = v.normal_max ?? (D.ciudades.find(c => c.nombre === v.ref) || {}).normal_max;
  const onda = ondaCalor(dias, normal);
  ctx = {v, ahora, hoy, dias, prox, filas, onda};
  const p24 = prox.slice(0, 24), tsMax = p24.reduce((a, b) => (b.prob_ts || 0) > (a.prob_ts || 0) ? b : a);
  const uvs = hoyF.filter(r => r.uv != null).map(r => r.uv), uvMax = uvs.length ? Math.max(...uvs) : null;
  const rf = prox.slice(0, 12).filter(r => r.rafaga != null).map(r => r.rafaga), rfMax = rf.length ? Math.max(...rf) : null;
  const tmax = hoyF.length ? Math.round(Math.max(...hoyF.map(r => r.temp))) : null, tmin = hoyF.length ? Math.round(Math.min(...hoyF.map(r => r.temp))) : null;
  const sAhora = sens(act), uvPico = uvs.length ? horaPico(hoyF.filter(r => r.uv != null), r => r.uv) : null;
  const chips = [`<span class="chip">Próxima hora: <b>${tprox}</b></span>`,
    sMax != null && sMax >= UMBRAL_CALOR ? `<span class="chip hot">🌡️ Sensación <b>${Math.round(sMax)}°</b> ${franja(sPico.hora)}</span>` : '',
    act.hum != null ? `<span class="chip">Humedad <b>${act.hum}%</b></span>` : '',
    act.viento != null && act.viento >= 15 ? `<span class="chip">Viento <b>${act.viento} km/h</b></span>` : '',
    (tsMax.prob_ts || 0) >= 10 ? `<span class="chip ts">⚡ Tormenta <b>${tsMax.prob_ts}%</b> ${franja(tsMax.hora)}</span>` : '',
    uvMax != null && uvMax >= 3 && !esNoche(ahora) ? `<span class="chip">UV <b>${Math.round(uvMax)}</b> ${nivelUV(uvMax)}${uvPico ? ' a las ' + uvPico.hora.slice(11, 16) : ''}</span>` : '',
    rfMax != null && rfMax >= 40 ? `<span class="chip">Ráfagas <b>${rfMax} km/h</b></span>` : ''].join('');
  const al = alertaPara(v);
  const push = leer('cc_push') ? '🔔 Avisos activos' : '🔔 Avisarme';
  $('#vista').innerHTML =
    `<div class="lay"><section class="cab" aria-label="Ahora">` +
    (onda ? `<div class="alerta calor" role="alert">🔥 <b>Onda de calor:</b> ${onda.dias.length} días seguidos con máximas de hasta ${onda.max}°, más de lo normal para esta época (${onda.normal}°). Hidrátate, usa protector solar y evita el sol de 11:00 a 15:00.</div>` : '') +
    (al ? `<div class="alerta" role="alert">${al.tormenta ? '⛈️' : '🌧️'} <b>El pronóstico cambió:</b> ${al.prob}% de lluvia hacia las ${al.hora.slice(11, 16)} (antes ${al.antes}%).</div>` : '') +
    `<div class="cabA"><div class="hero"><div class="ic">${icono(ic, 64)}</div><div class="ht"><div class="t">${act.temp != null ? Math.round(act.temp) : '-'}<sup>°</sup></div>` +
    `<div class="hs">${sAhora != null && sAhora - act.temp >= 1.5 ? `Se siente <b>${Math.round(sAhora)}°</b> · ` : ''}${tmax != null ? `Máx ${tmax}° · Mín ${tmin}°` : ''}</div>` + `</div></div>` +
    `<h1 class="tit">${tit}</h1><p class="sub">${sub}</p>` +
    `<div class="lugar">📍 ${esc(v.nombre)}</div>` +
    `</div><div class="cabB"><div class="chips">${chips}</div>` +
    `<div class="acciones">` + (D.feedback_url ? `<button class="acc" id="breporte">☂️ Reportar clima</button><button class="acc" id="bpush" aria-pressed="${!!leer('cc_push')}">${push}</button>` : '') +
    `<button class="acc" id="bcompartir">↗ Compartir</button>${standalone || leer('cc_inst_no') === '2' ? '' : '<button class="acc inst" id="binstalar">📲 Instalar app</button>'}</div><div class="fbmini" id="fbmini"></div></div></section><section class="col1" aria-label="Pronóstico">` +
    `<div class="card" id="c24"><h3>Próximas 24 horas</h3>${lineaTiempo(p24, ahora)}</div>` +
    `<div class="card"><h3>Próximos 7 días <span class="sub">Toca un día para ver el detalle</span></h3><div id="dias" role="list"></div></div>` +
    `</section><section class="col2" aria-label="Gráfica, mapa y satélite">` +
    `<div class="card cmulti"><div class="tabs" role="tablist" aria-label="Vistas">` +
    TABS.map(([t, n]) => `<button class="tab" role="tab" id="tab-${t}" data-t="${t}" aria-controls="panel-${t}">${n}</button>`).join('') + `</div>` +
    `<div class="panel" data-t="graf" id="panel-graf" role="tabpanel" aria-labelledby="tab-graf"><h3><span id="titg"></span><button id="todo" hidden>Ver 3 días</button></h3><div class="gsel" id="gsel" role="group" aria-label="Qué mostrar"></div><div id="graf" style="position:relative"></div><div class="ley" id="leyg"></div></div>` +
    `<div class="panel" data-t="mapa" id="panel-mapa" role="tabpanel" aria-labelledby="tab-mapa" hidden>${htmlMapa()}</div>` +
    `<div class="panel" data-t="sat" id="panel-sat" role="tabpanel" aria-labelledby="tab-sat" hidden>${htmlSatelite()}</div></div>` +
    `<details><summary>Qué dice cada modelo meteorológico</summary>${tablaModelos(prox.slice(0, 72), v.cfg, hoy)}</details>` +
    `<details><summary>¿Qué tan confiable es este pronóstico?</summary>${v.verif_html}</details>` +
    `<details id="como"><summary>Cómo funciona ClearSky</summary>${htmlComo()}</details>` +
    `</section></div>`;
  document.querySelectorAll('#vista details').forEach(d => { if (abiertos.has((d.querySelector('summary') || {}).textContent)) d.open = true; });
  activarLinea($('#c24'), p24);
  pintarDias();
  $$('.tab').forEach((b, i, todos) => { b.onclick = () => { tocar(); mostrarTab(b.dataset.t); };
    b.onkeydown = e => { const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0; if (d) { e.preventDefault(); mostrarTab(todos[(i + d + todos.length) % todos.length].dataset.t, true); } }; });
  mostrarTab(TABS.some(([t]) => t === estado.tab) ? estado.tab : 'graf');
  $('#todo').onclick = () => { estado.dia = null; pintarDias(); pintarGrafica(); };
  const br = $('#breporte'); if (br) br.onclick = () => { tocar(); abrirReporte(); };
  const bp = $('#bpush'); if (bp) bp.onclick = () => { tocar(); alternarAvisos(); };
  $('#bcompartir').onclick = () => { tocar(); compartir(); };
  const bi = $('#binstalar'); if (bi) bi.onclick = () => { tocar(); instalar(); };
  if (Math.abs(scrollY - y0) > 2) scrollTo(0, y0);
  resumenFeedback();
}

// ---------- cómo funciona ----------
function htmlComo() {
  const pasos = [
    ['🛰️', 'Reunimos los datos', `Cada hora descargamos ${KEYS.length} modelos meteorológicos internacionales (Europa, EE. UU., Alemania, Canadá, Japón, Francia y Reino Unido), la lluvia que ve el satélite GOES en ese momento y lo que reportan los aeropuertos y las personas.`],
    ['⚖️', 'Los combinamos', 'Cada modelo "vota" con más o menos peso según cuánto ha acertado en tu zona. Así un modelo que exagera no arrastra el resultado.'],
    ['✅', 'Comprobamos todos los días', 'Comparamos lo que dijimos con lo que pasó en los aeropuertos los últimos 45 días: lluvia, llovizna y tormentas eléctricas, a 1 y hasta 7 días de anticipación.'],
    ['📝', 'Hacemos un examen sin trampa', 'Guardamos los últimos 10 días sin usarlos para aprender y vemos cómo nos habría ido. Solo aceptamos un ajuste si acierta más que antes.'],
    ['🔁', 'Aprende solo', 'Todo se recalcula cada día con los últimos 45 días, así se adapta al invierno y al verano. Lo aprendido se guarda en público en el repositorio.'],
    ['👀', 'Corrige con lo que pasa ahora', 'Si el satélite o varias personas cerca de ti ven lluvia, las próximas horas se corrigen al instante.'],
    ['🌡️', 'Calor', 'Comparamos cada hora la temperatura y la humedad con las del aeropuerto y corregimos el error típico de los modelos. Con esos valores calculamos la sensación térmica (índice de calor de la NOAA). Desde 32° hay que tomar precauciones. Hay onda de calor cuando se esperan 3 días o más con máximas 2° por encima de lo normal del último mes en tu zona.'],
  ];
  return '<div class="como">' + pasos.map(([e, t, x], i) => `<div class="paso"><div class="pn" aria-hidden="true">${e}</div><div><b>${i + 1}. ${t}</b><p>${x}</p></div></div>`).join('') +
    '<p class="nota"><b>¿Qué significa 30%?</b> Que de cada 10 veces que lo decimos, llueve más o menos 3. <b>Lluvia</b> es la que moja las calles; <b>llovizna</b>, gotas finas o garúa. Niebla, viento e índice UV vienen de los modelos y todavía no se verifican.</p></div>';
}

// ---------- instalar como app ----------
function instalar() {
  if (pedirInstalar) { pedirInstalar.prompt(); pedirInstalar.userChoice && pedirInstalar.userChoice.then(r => { if (r.outcome === 'accepted') guardar('cc_inst_no', '2'); }); return; }
  const and = /android/i.test(navigator.userAgent);
  const iconoComp = '<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" style="vertical-align:-3px"><path d="M12 3v12M7 8l5-5 5 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
  const pasos = ios ? [[iconoComp, 'Toca el botón <b>Compartir</b> en la barra de Safari (abajo en iPhone, arriba en iPad).'],
                       ['➕', 'Desliza y elige <b>Agregar a inicio</b>.'], ['✅', 'Toca <b>Agregar</b>. ClearSky aparecerá en tu pantalla de inicio como una app.']]
    : and ? [['⋮', 'Toca el menú <b>⋮</b> del navegador (arriba a la derecha).'], ['📲', 'Elige <b>Instalar app</b> o <b>Agregar a pantalla principal</b>.'], ['✅', 'Confirma. ClearSky quedará junto a tus apps.']]
    : [['⊕', 'En la barra de direcciones, toca el ícono <b>Instalar</b> (o el menú ⋮ → <b>Instalar ClearSky</b>).'], ['✅', 'Se abrirá en su propia ventana, como una app.']];
  abrirHoja(`<div class="grab"></div><h2>Instala ClearSky</h2><p class="nota">Tenla a un toque, ábrela sin barra del navegador, recibe avisos de lluvia y consúltala sin conexión. No ocupa espacio ni pide datos.</p>` +
    `<ol class="instp">${pasos.map(([i, t]) => `<li><span class="ii">${i}</span><span>${t}</span></li>`).join('')}</ol>` +
    (ios ? '<p class="nota">Si usas Chrome en iPhone, también funciona desde su botón Compartir.</p>' : '') +
    `<button class="btn sec" id="instno" style="width:100%;margin-top:8px">No volver a mostrar</button>`, 'Instalar ClearSky',
    () => { $('#instno').onclick = () => { guardar('cc_inst_no', '2'); cerrarSelector(); render(); }; });
}

// ---------- avisos en el teléfono (notificaciones) ----------
const pushPosible = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
function b64u(s) { const p = '='.repeat((4 - s.length % 4) % 4), b = atob((s + p).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(b, c => c.charCodeAt(0)); }
async function clavePush() {
  if (estado.vapid !== undefined) return estado.vapid;
  try { estado.vapid = (await (await fetch(`${D.feedback_url}/vapid`)).json()).publica || null; } catch (e) { estado.vapid = null; }
  return estado.vapid;
}
async function enviarSuscripcion(sub) {
  const pos = estado.pos, u = estado.ubic;
  const r = await fetch(`${D.feedback_url}/suscribir`, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sub: sub.toJSON(), lat: +pos.lat.toFixed(2), lon: +pos.lon.toFixed(2), nombre: (u && u.nombre) || 'tu zona',
      admin: location.hash === '#admin' ? (leer('cc_admin') || (() => { const t = prompt('Clave de administrador (TOKEN)') || ''; guardar('cc_admin', t); return t; })()) : undefined})});
  if (!r.ok) throw new Error(r.status);
}
async function alternarAvisos() {
  if (!pushPosible()) {
    toast(ios && !standalone ? 'En iPhone, primero instala ClearSky: toca Compartir → "Agregar a inicio" y ábrela desde ahí.' : 'Tu navegador no permite avisos.');
    return;
  }
  const reg = await Promise.race([navigator.serviceWorker.ready, new Promise(ok => setTimeout(() => ok(null), 4000))]);
  if (!reg) { toast('Recarga la página e inténtalo de nuevo.'); return; }
  const actual = await reg.pushManager.getSubscription();
  if (leer('cc_push') && actual) {
    try { await fetch(`${D.feedback_url}/desuscribir`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({endpoint: actual.endpoint})}); } catch (e) {}
    await actual.unsubscribe().catch(() => {});
    try { localStorage.removeItem('cc_push'); } catch (e) {}
    toast('Avisos desactivados.'); render(); return;
  }
  const clave = await clavePush();
  if (!clave) { toast('Los avisos todavía no están disponibles.'); return; }
  let pos; try { pos = await obtenerPos(); } catch (e) { toast('Para avisarte necesitamos tu ubicación.'); return; }
  if (!enEcuador(pos)) { toast(TXT_FUERA); return; }
  if (await Notification.requestPermission() !== 'granted') { toast('No diste permiso para notificaciones. Puedes activarlo en la configuración del navegador.'); return; }
  try {
    const sub = actual || await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64u(clave)});
    await enviarSuscripcion(sub);
    guardar('cc_push', '1');
    toast('Listo: te avisaremos cuando sea probable que llueva donde estás en la próxima hora o dos.'); render();
  } catch (e) { toast('No pudimos activar los avisos. Inténtalo más tarde.'); }
}
async function actualizarSuscripcion() {           // si la persona se movió, se actualiza la zona del aviso
  if (!leer('cc_push') || !pushPosible() || !estado.pos || !enEcuador(estado.pos)) return;
  const ult = leer('cc_push_pos'), clave = `${estado.pos.lat.toFixed(2)},${estado.pos.lon.toFixed(2)}`;
  if (ult === clave) return;
  try { const reg = await navigator.serviceWorker.ready, sub = await reg.pushManager.getSubscription();
    if (sub) { await enviarSuscripcion(sub); guardar('cc_push_pos', clave); } } catch (e) {}
}

// ---------- compartir (imagen para WhatsApp y redes) ----------
async function cargarImg(src) { return new Promise(ok => { if (!src) return ok(null); const i = new Image(); i.onload = () => ok(i); i.onerror = () => ok(null); i.src = src; }); }
async function compartir() {
  const {v, ahora, hoy, dias, prox} = ctx, [ic] = condicion(prox[0]), tit = $('.tit').textContent, sub = $('.sub').textContent;
  const W = 1080, H = 1350, cv = document.createElement('canvas'); cv.width = W; cv.height = H;
  const c = cv.getContext('2d'), F = '-apple-system,"Segoe UI",Roboto,Arial,sans-serif';
  const g = c.createLinearGradient(0, 0, 0, H); g.addColorStop(0, '#0f1b33'); g.addColorStop(1, '#0b0f17'); c.fillStyle = g; c.fillRect(0, 0, W, H);
  c.fillStyle = '#93c5fd'; c.font = `600 40px ${F}`; c.fillText('ClearSky', 70, 100);
  c.fillStyle = '#cbd5e1'; c.font = `500 44px ${F}`; c.fillText(v.nombre, 70, 180);
  const svg = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="240" height="240">${ICONOS[ic] || ICONOS.nube}</svg>`)}`;
  const im = await cargarImg(MC_URL[ic]) || await cargarImg(svg); if (im) c.drawImage(im, 60, 220, 220, 220);
  c.fillStyle = '#fff'; c.font = `200 190px ${F}`; c.fillText(`${Math.round(prox[0].temp)}°`, 320, 400);
  c.font = `600 54px ${F}`; let y = 520;
  const partir = (t, ancho) => { const ps = t.split(' '), out = []; let l = ''; for (const p of ps) { if (c.measureText(l + p).width > ancho && l) { out.push(l.trim()); l = ''; } l += p + ' '; } out.push(l.trim()); return out; };
  partir(tit, W - 140).forEach(l => { c.fillText(l, 70, y); y += 66; });
  c.fillStyle = '#9ca3af'; c.font = `400 36px ${F}`; partir(sub, W - 140).slice(0, 3).forEach(l => { c.fillText(l, 70, y); y += 48; });
  y += 30; const p24 = prox.slice(0, 24), bw = (W - 140) / p24.length;
  p24.forEach((r, i) => { c.fillStyle = condicion(r)[2]; c.fillRect(70 + i * bw, y, bw - 3, 50); });
  c.fillStyle = '#9ca3af'; c.font = `400 28px ${F}`; [0, 6, 12, 18].forEach(i => p24[i] && c.fillText(i ? p24[i].hora.slice(11, 13) + 'h' : 'Ahora', 70 + i * bw, y + 90));
  y += 150;
  for (const [d, rs] of Object.entries(dias).slice(0, 5)) {
    const top = rs.reduce((a, b) => b.prob > a.prob ? b : a), t = rs.map(r => r.temp).filter(x => x != null);
    c.fillStyle = '#e5e7eb'; c.font = `500 40px ${F}`; c.fillText(nombreDia(d, hoy), 70, y);
    c.fillStyle = '#60a5fa'; c.fillText(top.prob >= 10 ? `${top.prob}%` : '–', 520, y);
    c.fillStyle = '#e5e7eb'; c.fillText(`${Math.round(Math.min(...t))}° / ${Math.round(Math.max(...t))}°`, 760, y); y += 70;
  }
  c.fillStyle = '#6b7280'; c.font = `400 28px ${F}`; c.fillText('Pronóstico verificado · suirobert.github.io/ClearSky', 70, H - 60);
  const blob = await new Promise(ok => cv.toBlob(ok, 'image/png'));
  const archivo = new File([blob], 'clearsky.png', {type: 'image/png'}), texto = `${tit} — ${v.nombre}. ${location.href.split('#')[0]}`;
  try { if (navigator.canShare && navigator.canShare({files: [archivo]})) { await navigator.share({files: [archivo], text: texto}); return; } } catch (e) { if (e.name === 'AbortError') return; }
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'clearsky.png'; a.click();
  toast('Imagen descargada. Ya puedes enviarla por WhatsApp.');
}
function pie() {
  const gen = new Date(D.generado + ':00Z').getTime() - D.offset * 1000, horas = (Date.now() - gen) / 36e5;
  const [f, h] = D.generado.split('T');
  const mc = Object.keys(MC_URL).length ? ' · Íconos: <a href="https://github.com/basmilius/meteocons" rel="noopener">Meteocons</a> (MIT, © Bas Milius)' : '';
  $('#pie').innerHTML = (horas > 3 ? `<b style="color:#fcd34d">Sin conexión: estos datos son de hace ${Math.round(horas)} h.</b><br>` : '') +
    `Pronóstico combinado de ${KEYS.length} modelos y verificado cada día. <a href="#como" id="vercomo">¿Cómo funciona?</a><br>` +
    `Para emergencias sigue siempre las alertas oficiales de la Secretaría de Gestión de Riesgos y del INAMHI.<br>` +
    `Datos: <a href="https://open-meteo.com/" rel="noopener">Open-Meteo.com</a> (CC BY 4.0) · Observaciones: Iowa Environmental Mesonet · Satélite y lluvia observada: NOAA (GOES, Enterprise Rain Rate) · Mapa y barrios: © OpenStreetMap${mc} · ` +
    `<a href="${D.repo}" rel="noopener">Código abierto</a>`;
  const vc = $('#vercomo'); if (vc) vc.onclick = e => { e.preventDefault(); const d = $('#como'); if (d) { d.open = true; d.scrollIntoView({behavior: 'smooth', block: 'start'}); } };
}
function toast(msg, accion, fn) {
  const t = $('#toast'); t.innerHTML = `<span>${esc(msg)}</span>` + (accion ? `<button id="tacc">${esc(accion)}</button>` : '') + '<button id="tcer" aria-label="Cerrar">✕</button>';
  t.hidden = false; const cerrar = () => { t.hidden = true; };
  $('#tcer').onclick = cerrar; if (accion) $('#tacc').onclick = () => { cerrar(); fn && fn(); };
  clearTimeout(t._tm); t._tm = setTimeout(cerrar, 9000);
}

// ---------- actualización automática (sin recargar la página) ----------
const generadoMs = () => Date.UTC(+D.generado.slice(0, 4), +D.generado.slice(5, 7) - 1, +D.generado.slice(8, 10), +D.generado.slice(11, 13), +D.generado.slice(14, 16)) - D.offset * 1000;
let refrescando = false;
async function refrescarDatos(forzar) {
  if (refrescando || document.visibilityState === 'hidden') return;
  refrescando = true;
  try {
    let cambio = !!forzar;
    if (forzar || Date.now() - generadoMs() > 65 * 60 * 1000) {
      const html = await (await fetch(location.pathname + '?t=' + Date.now(), {cache: 'no-store'})).text();
      const m = html.match(/<script id="datos" type="application\/json">([\s\S]*?)<\/script>/);
      if (m) { const nuevo = JSON.parse(m[1]);
        if (nuevo.generado > D.generado) { Object.assign(D, nuevo); SAT = null; cambio = true;
          if (estado.ubic) { try { const c = JSON.parse(leer('cc_ubic_cache') || 'null'); if (c) estado.ubic = construirUbic(c); } catch (e) {} } } }
    }
    if (estado.ubic && Date.now() - (estado.ubic.t || 0) > 20 * 60 * 1000) { refrescando = false; await cargarUbicacion({silencioso: true}); }
    if (ctx && ctx.ahora !== ahoraISO(ctx.v.offset)) cambio = true;      // pasó la hora: avanzar el pronóstico
    if (cambio && ctx && $('#sheet').hidden) render();
    if (estado.rep) { estado.rep.t = 0; resumenFeedback(); }             // solo redibuja si cambia lo que se ve
    pie();
  } catch (e) {} finally { refrescando = false; }
}
setInterval(() => refrescarDatos(), 10 * 60 * 1000);

// ---------- refrescar: deslizar hacia abajo (en la app instalada) y al volver a la pestaña ----------
const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
function refrescar() { if (estado.ubic) estado.ubic.t = 0; refrescarDatos(true); }
if (standalone) {
  let y0 = null, dy = 0; const ptr = $('#ptr');
  addEventListener('touchstart', e => { y0 = scrollY <= 0 && $('#sheet').hidden ? e.touches[0].clientY : null; dy = 0; }, {passive: true});
  addEventListener('touchmove', e => { if (y0 == null) return; dy = e.touches[0].clientY - y0; if (dy > 0) { const d = Math.min(dy, 120);
    ptr.style.opacity = Math.min(d / 80, 1); ptr.style.transform = `translateY(${d / 2 - 40}px) rotate(${d * 3}deg)`; ptr.classList.toggle('listo', dy > 80); } }, {passive: true});
  addEventListener('touchend', () => { if (y0 == null) return; y0 = null;
    if (dy > 80) { ptr.classList.add('gira'); tocar(); refrescar(); setTimeout(() => { ptr.classList.remove('gira', 'listo'); ptr.style.opacity = 0; }, 1500); }
    else { ptr.style.opacity = 0; ptr.style.transform = ''; } });
}
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refrescarDatos(); });

// ---------- instalar como app ----------
let pedirInstalar = null;
addEventListener('beforeinstallprompt', e => { e.preventDefault(); pedirInstalar = e; });
addEventListener('appinstalled', () => { guardar('cc_inst_no', '2'); const b = $('#binstalar'); if (b) b.remove(); });
function sugerirInstalar() {
  // en la segunda visita (y solo una vez) sugerimos instalarla
  const n = +(leer('cc_visitas') || 0) + 1; guardar('cc_visitas', n);
  if (standalone || leer('cc_inst_no') || n !== 2) return;
  setTimeout(() => { guardar('cc_inst_no', '1'); toast('Tenla a un toque: instala ClearSky en tu pantalla de inicio.', 'Ver cómo', instalar); }, 8000);
}
if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('sw.js').catch(() => {});

// ---------- tema claro / oscuro ----------
// Modos: 'auto' (sigue al teléfono o computadora), 'claro', 'oscuro'. El botón los recorre en ese orden.
const ICO_TEMA = {auto: '◐', claro: '☀️', oscuro: '🌙'}, TXT_TEMA = {auto: 'Tema automático', claro: 'Tema claro', oscuro: 'Tema oscuro'};
const prefClaro = matchMedia('(prefers-color-scheme: light)');
function modoTema() { const t = leer('cc_tema'); return t === 'claro' || t === 'oscuro' ? t : 'auto'; }
function aplicarTema(redibujar) {
  const m = modoTema(), t = m === 'auto' ? (prefClaro.matches ? 'claro' : 'oscuro') : m, antes = document.documentElement.dataset.tema;
  document.documentElement.dataset.tema = t;
  const mt = document.querySelector('meta[name=theme-color]'); if (mt) mt.content = t === 'claro' ? '#f4f6fa' : '#0b0f17';
  const b = $('#temabtn'); if (b) { b.textContent = ICO_TEMA[m]; b.title = TXT_TEMA[m] + ' (toca para cambiar)'; b.setAttribute('aria-label', b.title); }
  if (redibujar && antes !== t && ctx) { const y = scrollY; render(); scrollTo(0, y); }
}
function cambiarTema() {
  const sig = {auto: 'claro', claro: 'oscuro', oscuro: 'auto'}[modoTema()];
  if (sig === 'auto') { try { localStorage.removeItem('cc_tema'); } catch (e) {} } else guardar('cc_tema', sig);
  tocar(); aplicarTema(true); toast(TXT_TEMA[sig] + (sig === 'auto' ? ': sigue la configuración de tu dispositivo.' : '.'));
}
(prefClaro.addEventListener ? prefClaro.addEventListener('change', () => aplicarTema(true)) : prefClaro.addListener(() => aplicarTema(true)));

// ---------- inicio: primero la ubicación de la persona ----------
async function inicio() {
  aplicarTema(false); $('#temabtn').onclick = cambiarTema;
  pie();
  $('#lugarbtn').onclick = () => { tocar(); abrirSelector(); };
  $('#sheetbg').onclick = () => cerrarSelector();
  let cache = null;
  try { cache = JSON.parse(leer('cc_ubic_cache') || 'null'); } catch (e) {}
  if (cache && cache.om && Date.now() - cache.t < 24 * 3600 * 1000) {
    try { estado.ubic = construirUbic(cache); estado.pos = {lat: cache.lat, lon: cache.lon}; estado.vista = 'ubic'; render(); normalUbic(cache.lat, cache.lon); } catch (e) { estado.ubic = null; }
  }
  let permiso = 'prompt';
  try { if (navigator.permissions) permiso = (await navigator.permissions.query({name: 'geolocation'})).state; } catch (e) {}
  if (permiso === 'denied') { if (!estado.ubic) alternativa('Sin permiso de ubicación. Actívalo en la configuración del navegador para ver el pronóstico exacto de donde estás.'); }
  else if (permiso === 'granted' || leer('cc_ubic') === '1') cargarUbicacion({silencioso: !!estado.ubic});
  else if (!estado.ubic) {          // primera visita: explicamos por qué pedimos la ubicación
    estado.vista = 0; render();
    avisoTop('Para mostrarte el pronóstico exacto de donde estás, ClearSky necesita tu ubicación. No la guardamos.', true);
  }
  let rz, ancho = innerWidth;
  addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { if (innerWidth !== ancho && ctx) { ancho = innerWidth; pintarGrafica(); if (mapa) mapa.invalidateSize(); if (mapaSat) mapaSat.invalidateSize(); } }, 200); });
  sugerirInstalar();
}
inicio().catch(e => { console.error(e); $('#vista').innerHTML = '<div class="vacio">No pudimos mostrar el pronóstico. Vuelve a cargar la página.</div>'; });
"""


# ----------------------- MAIN -----------------------
def main():
    p = cargar_pesos()
    if "--verificar" in sys.argv or toca_verificar(p):
        verificar(p)
    resultados = {}
    alertas = []
    for ciudad, o in CIUDADES.items():
        print(f"Descargando pronóstico {ciudad}...")
        try:
            cfg, _ = cfg_efectivo(p, ciudad)
            filas = consenso(descargar(o["lat"], o["lon"]), cfg, o["lat"], o["lon"])
            if filas:
                resultados[ciudad] = filas
            print(f"  OK: {len(filas)} horas")
            time.sleep(0.3)
        except Exception as e:
            log_error(f"pronóstico {ciudad}: {e}")
    if not resultados:
        log_error("no se pudo descargar ningún pronóstico; se conserva la página anterior")
        sys.exit(1)
    try:
        alertas = detectar_alertas(resultados)
    except Exception as e:
        log_error(f"alertas: {e}")
    for ciudad, filas in resultados.items():
        guardar_log(ciudad, filas)
    try:
        n = descargar_reportes()
        if n:
            print(f"Reportes de usuarios nuevos: {n}")
    except Exception as e:
        log_error(f"reportes de usuarios: {e}")
    try:
        respaldo_reportes()
    except Exception as e:
        log_error(f"respaldo de reportes: {e}")
    mapa = None
    try:
        mapa = generar_mapa(p)
        print(f"Mapa: {len(mapa['horas'])} horas x {len(mapa['p'][0]) if mapa['p'] else 0} puntos")
    except Exception as e:
        log_error(f"mapa: {e} (la página se publica sin mapa)")
    iconos = {}
    try:
        iconos = obtener_iconos()
    except Exception as e:
        log_error(f"íconos: {e}")
    normales = {}
    try:
        normales = normales_calor()
    except Exception as e:
        log_error(f"temperaturas normales: {e} (sin aviso de onda de calor)")
    ahora = None
    try:
        ahora = lluvia_ahora()
        print(f"Lluvia ahora (satélite): {ahora['t']}")
    except Exception as e:
        log_error(f"lluvia por satélite: {e} (la página se publica sin esa capa)")
    try:
        generar_html(resultados, p, mapa, iconos, alertas, ahora, normales)
    except Exception as e:
        log_error(f"{e}; se conserva la página anterior")
        sys.exit(1)
    print(f"Página: {ARCHIVO_HTML}")
    if "--no-open" not in sys.argv:
        webbrowser.open("file://" + ARCHIVO_HTML)


if __name__ == "__main__":
    main()
