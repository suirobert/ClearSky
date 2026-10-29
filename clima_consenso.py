# -*- coding: utf-8 -*-
"""
ClearSky - pronóstico de lluvia verificado para Ecuador
-------------------------------------------------------
Datos:
  - 7 modelos meteorológicos (Open-Meteo) combinados con pesos según su acierto en cada ciudad.
  - Verificación diaria contra los METAR de los aeropuertos (±1 h), con examen sin trampa:
    solo se aceptan pesos y calibraciones que mejoran el pronóstico en días no usados para aprender.
  - Calibración adaptable a cambios de temporada (completa / reciente / mezcla / sin ajuste).
  - Memoria permanente de observaciones y seguimiento real de lo que se publicó.
  - Tormentas eléctricas verificadas y calibradas con los truenos (TS) que reportan los aeropuertos.
Página (index.html, generada aquí y dibujada con JavaScript):
  - Primero "Mi ubicación" (el navegador calcula el pronóstico del punto del usuario con la
    configuración aprendida en la ciudad verificada más parecida); las ciudades van en una lista aparte.
  - Ícono del cielo según el sol que llega de verdad (las nubes altas finas ya no dan "nublado").
  - Modo claro y oscuro automático según el dispositivo, con botón para elegirlo a mano.
  - 7 días (del 4 al 7 orientativos) y reportes de usuarios verificados contra lo publicado.
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
  CC_FEEDBACK_URL  dirección del servicio de reportes (vacío = botón oculto)
"""
import csv, glob, io, json, math, os, sys, time, statistics, webbrowser, urllib.request, urllib.parse
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
}
AEROPUERTOS = {"SEGU": "aeropuerto de Guayaquil", "SEQM": "aeropuerto de Quito (Tababela)",
               "SEMT": "aeropuerto de Manta", "SESA": "aeropuerto de Salinas",
               "SETN": "aeropuerto de Esmeraldas (Tachina)", "SECU": "aeropuerto de Cuenca"}
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
VERSION             = 7          # versión de la lógica de datos (si cambia, se re-verifica)
MIN_HORAS_TORMENTA  = 4          # mínimo de horas con tormenta observada para calibrar tormentas
# Variables del pronóstico actual (ciudades, mapa y "mi ubicación" usan las mismas)
VARS_PRON = ["precipitation", "temperature_2m", "cloud_cover", "cloud_cover_low", "cloud_cover_mid",
             "shortwave_radiation", "weather_code", "wind_gusts_10m", "uv_index"]
# Mapa: cuadrícula sobre Ecuador continental
MAPA_LATS = [round(1.25 - 0.75 * i, 2) for i in range(9)]      # 1.25 … -4.75
MAPA_LONS = [round(-80.75 + 0.75 * j, 2) for j in range(8)]    # -80.75 … -75.5
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


def descargar_obs(icao, dias):
    fin = date.today() + timedelta(days=1)
    ini = fin - timedelta(days=dias + 1)
    params = [("station", icao), ("data", "wxcodes"),
              ("year1", ini.year), ("month1", ini.month), ("day1", ini.day),
              ("year2", fin.year), ("month2", fin.month), ("day2", fin.day),
              ("tz", ZONA), ("format", "onlycomma"), ("latlon", "no"),
              ("missing", "M"), ("direct", "no"), ("report_type", "3"), ("report_type", "4")]
    return parsear_obs(http_get(URL_IEM, params))


def parsear_obs(texto):
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
    return obs


def descargar_previos(lat, lon, dias):
    """Devuelve (lluvia, códigos): lo que cada modelo pronosticó 24 h antes de cada hora."""
    params = {"latitude": lat, "longitude": lon, "hourly": "precipitation_previous_day1,weather_code_previous_day1",
              "models": ",".join(MODELOS), "timezone": ZONA, "past_days": dias, "forecast_days": 1}
    try:
        h = json.loads(http_get(URL_PREVIOUS, params))["hourly"]
    except Exception as e:                 # si los códigos fallan, la lluvia se verifica igual
        log_error(f"códigos de tiempo no disponibles ({e}); solo se verifica la lluvia")
        params["hourly"] = "precipitation_previous_day1"
        h = json.loads(http_get(URL_PREVIOUS, params))["hourly"]
    out, cods = {}, {}
    for m in MODELOS:
        serie = serie_modelo(h, "precipitation_previous_day1", m)
        if serie and any(v is not None for v in serie):
            out[m] = {t: v for t, v in zip(h["time"], serie) if v is not None}
        sc = serie_modelo(h, "weather_code_previous_day1", m)
        if sc and any(v is not None for v in sc):
            cods[m] = {t: v for t, v in zip(h["time"], sc) if v is not None}
    return out, cods


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
        try:
            obs = descargar_obs(cfg["metar"], DIAS_VERIFICACION)
            time.sleep(1)
            cobertura = len(obs) / (DIAS_VERIFICACION * 24)
            arch.setdefault(c, {}).update({k: list(v) for k, v in obs.items()})
            if cobertura < cfg["cob_min"]:
                log_error(f"{c}: el aeropuerto solo reportó {cobertura:.0%} de las horas "
                          f"(mínimo {cfg['cob_min']:.0%}); se mantiene la configuración anterior")
                continue
            prev, cods = descargar_previos(cfg["est"][0], cfg["est"][1], DIAS_VERIFICACION)
            if len(prev) < MIN_MODELOS:
                log_error(f"{c}: solo {len(prev)} modelos con historial; se mantiene la configuración anterior")
                continue
            r = calibrar(obs, prev)
            r["cobertura_obs_pct"] = round(cobertura * 100)
            try:
                r["tormenta"] = calibrar_tormenta(obs, cods)
            except Exception as e:
                log_error(f"tormentas {c}: {e}")
                r["tormenta"] = (p["ciudades"].get(c) or {}).get("tormenta") or {"verificado": False}
            p["ciudades"][c] = r
            ex = r.get("examen") or {}
            print(f"  {c}: {r['horas']} h, {r['horas_lluvia']} con lluvia, umbral {r['umbral']} mm/h, "
                  f"HSS {r['consenso']['hss']} | examen: clima {ex.get('brier_clima')} / sistema "
                  f"{ex.get('brier_sistema')} | calibración {r.get('modo_calibracion')}")
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
    def serie(var, m, i):
        sv = serie_modelo(h, var, m)
        return sv[i] if sv and i < len(sv) else None
    filas = []
    for i, t in enumerate(h["time"]):
        lluvia, temp, nubes, nbm, rad, cods, raf, uv = {}, {}, {}, [], [], {}, [], []
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
        if not lluvia:
            continue
        claro = radiacion_despejado(lat, lon, t, offset_s)
        sol = round(min(mediana(rad) / claro, 1.2), 2) if claro > 0 and len(rad) >= 2 else None
        bruta = prob_consenso(lluvia, pesos, umbral)
        bruta_dz = prob_consenso(lluvia, pesos, UMBRAL_LLOVIZNA)
        prob = calibrar_prob(bruta, cfg.get("tabla_lluvia"))
        prob_dz = max(calibrar_prob(bruta_dz, cfg.get("tabla_llovizna")), prob)
        acuerdo = max(bruta, 100 - bruta)
        filas.append({
            "hora": t, "prob": round(prob), "prob_dz": round(prob_dz), "prob_bruta": round(bruta),
            "mm": round(mediana_ponderada(lluvia, pesos), 1),
            "min": round(min(lluvia.values()), 1), "max": round(max(lluvia.values()), 1),
            "temp": round(statistics.mean(temp.values()), 1) if temp else None,
            "nubes": round(statistics.mean(nubes.values())) if nubes else None,
            "nubes_bm": round(statistics.mean(nbm)) if nbm else None,
            "sol": sol,
            "prob_ts": round(calibrar_prob(prob_tormenta(cods), tabla_ts)) if cods else 0,
            "niebla": round(sum(c in (45, 48) for c in cods.values()) / len(cods) * 100) if cods else 0,
            "rafaga": round(mediana(raf)) if raf else None,
            "uv": round(mediana(uv), 1) if uv else None,
            "conf": "Alta" if acuerdo >= 80 else "Media" if acuerdo >= 60 else "Baja",
            "detalle": {MODELOS[m]: round(v, 1) for m, v in lluvia.items()}, "n": len(lluvia),
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


def datos_ciudad(p, ciudad, filas):
    cfg, ref = cfg_efectivo(p, ciudad)
    propia = bool(p["ciudades"].get(ciudad, {}).get("verificado"))
    verif = html_verificacion(cfg, ref) if ref else html_verificacion(cfg, ciudad)
    verif += html_tormenta(cfg.get("tormenta"), ref or ciudad)
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
                    "tabla_tormenta": (cfg.get("tormenta") or {}).get("tabla") or []},
            "filas": [f if i < 96 else dict(f, detalle={}) for i, f in enumerate(filas)], "verif_html": verif}


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


SW_JS = """const C='clearsky-v2';
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.addAll(['./','manifest.webmanifest','icon-192.png'])).catch(()=>{}));});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==C).map(x=>caches.delete(x)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',e=>{const r=e.request;if(r.method!=='GET'||new URL(r.url).origin!==location.origin)return;
e.respondWith(fetch(r).then(res=>{const cp=res.clone();caches.open(C).then(c=>c.put(r,cp));return res;}).catch(()=>caches.match(r).then(m=>m||caches.match('./'))));});
"""


def escribir_pwa(carpeta):
    manifest = {"name": "ClearSky · Pronóstico verificado", "short_name": "ClearSky", "lang": "es",
                "start_url": "./", "scope": "./", "display": "standalone", "background_color": "#0b0f17",
                "theme_color": "#0b0f17", "description": "Pronóstico de lluvia para Ecuador que mide en público cuánto acierta.",
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


def generar_html(resultados, p, mapa=None, iconos=None):
    datos = {"generado": datetime.now().strftime("%Y-%m-%dT%H:%M"), "offset": OFFSET_S,
             "modelos": MODELOS, "umbral_llovizna": UMBRAL_LLOVIZNA, "feedback_url": FEEDBACK_URL,
             "repo": REPO_URL, "verificacion": p.get("actualizado"), "vars": VARS_PRON,
             "ciudades": [datos_ciudad(p, c, f) for c, f in resultados.items()],
             "mapa": mapa, "iconos": iconos or {}}
    js_datos = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    doc = PLANTILLA.replace("__CSS__", CSS).replace("__JS__", JS).replace("__DATOS__", js_datos)
    carpeta = os.path.dirname(ARCHIVO_HTML)
    os.makedirs(carpeta, exist_ok=True)
    tmp = ARCHIVO_HTML + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, ARCHIVO_HTML)
    if os.environ.get("CC_HTML"):          # solo en la publicación (GitHub Pages), no en la carpeta del repo
        escribir_pwa(carpeta)


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
<meta name="description" content="Pronóstico del tiempo para Ecuador que combina 7 modelos y mide en público cuánto acierta: lluvia y tormentas eléctricas verificadas cada día.">
<title>ClearSky · Pronóstico verificado</title>
<style>__CSS__</style></head><body>
<header class="top"><div class="brand">ClearSky</div><div class="topder"><button class="temabtn" id="temabtn" aria-label="Cambiar tema"></button><button class="lugarbtn" id="lugarbtn" aria-haspopup="dialog"><span id="lugarnom">Tu ubicación</span><span aria-hidden="true">▾</span></button></div></header>
<div class="ptr" id="ptr" aria-hidden="true">↻</div>
<main class="wrap"><div id="aviso"></div><div id="vista"><div class="vacio"><span class="cargando"></span>Cargando…</div></div><div class="pie" id="pie"></div></main>
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
let estado = {vista: null, dia: null, ocultas: new Set(), ubic: null, pos: null, tlSel: null, buscando: false};

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
function filasDesdeOM(h, cfg, lat, lon, off) {
  const filas = [], n = h.time.length, g = (v, m, i) => { const s = h[v + '_' + m]; return s && i < s.length ? s[i] : null; };
  h.time.forEach((t, i) => {
    const ll = {}, te = [], nu = [], nbm = [], rad = [], cods = [], raf = [], uv = [];
    for (const m of KEYS) {
      let v = g('precipitation', m, i); if (v != null) ll[m] = v;
      v = g('temperature_2m', m, i); if (v != null) te.push(v);
      v = g('cloud_cover', m, i); if (v != null) nu.push(v);
      const lo = g('cloud_cover_low', m, i), mi = g('cloud_cover_mid', m, i); if (lo != null || mi != null) nbm.push(Math.max(lo || 0, mi || 0));
      v = i + 1 < n ? g('shortwave_radiation', m, i + 1) : null; if (v != null) rad.push(v);
      v = g('weather_code', m, i); if (v != null) cods.push(v);
      v = g('wind_gusts_10m', m, i); if (v != null) raf.push(v);
      v = g('uv_index', m, i); if (v != null) uv.push(v);
    }
    if (!Object.keys(ll).length) return;
    const bruta = probConsenso(ll, cfg.pesos, cfg.umbral), brutaDz = probConsenso(ll, cfg.pesos, D.umbral_llovizna);
    const prob = calibrarProb(bruta, cfg.tabla_lluvia), probDz = Math.max(calibrarProb(brutaDz, cfg.tabla_llovizna), prob);
    const ac = Math.max(bruta, 100 - bruta), vals = Object.values(ll), det = {};
    for (const m in ll) det[D.modelos[m]] = r1(ll[m]);
    const claro = radDespejado(lat, lon, t, off);
    const pts = cods.length ? cods.filter(c => c >= 95).length / cods.length * 100 : 0;
    filas.push({hora: t, prob: Math.round(prob), prob_dz: Math.round(probDz), prob_bruta: Math.round(bruta),
      mm: r1(medianaPond(ll, cfg.pesos)), min: r1(Math.min(...vals)), max: r1(Math.max(...vals)),
      temp: te.length ? r1(media(te)) : null, nubes: nu.length ? Math.round(media(nu)) : null,
      nubes_bm: nbm.length ? Math.round(media(nbm)) : null,
      sol: claro > 0 && rad.length >= 2 ? Math.round(Math.min(mediana(rad) / claro, 1.2) * 100) / 100 : null,
      prob_ts: cods.length ? Math.round(calibrarProb(pts, cfg.tabla_tormenta)) : 0,
      niebla: cods.length ? Math.round(cods.filter(c => c === 45 || c === 48).length / cods.length * 100) : 0,
      rafaga: raf.length ? Math.round(mediana(raf)) : null, uv: uv.length ? r1(mediana(uv)) : null,
      conf: ac >= 80 ? 'Alta' : ac >= 60 ? 'Media' : 'Baja', detalle: det, n: Object.keys(ll).length});
  });
  return filas;
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
    sub = `${top.prob}% de probabilidad entre las ${prox[i0].hora.slice(11, 16)} y las ${finHora(prox[i1].hora)}. ` +
      `Si llueve, sería ${intensidad(top.mm)}. ${TXT_CONF[top.conf]}.`;
  } else if (top.prob >= 10) {
    tit = 'Baja probabilidad de lluvia';
    sub = `Lo más probable es que no llueva. El momento con más opciones es ${franja(top.hora)} (${top.prob}%)${top.hora.slice(0, 10) === hoy ? '' : ' de mañana'}.`;
  } else {
    const dz = prox.reduce((a, b) => b.prob_dz > a.prob_dz ? b : a);
    if (dz.prob_dz >= 40) { tit = `Posible llovizna ${franja(dz.hora)}`; sub = `Podría caer una llovizna ligera (${dz.prob_dz}%). Lluvia de verdad, poco probable.`; }
    else { tit = condicion(prox[0])[1]; sub = 'No se espera lluvia en las próximas 24 horas.'; }
  }
  if ((tsTop.prob_ts || 0) >= 30) {
    const dia = tsTop.hora.slice(0, 10) === hoy ? '' : ' mañana';
    tit = `${tsTop.prob_ts >= 50 ? 'Tormenta eléctrica probable' : 'Posible tormenta eléctrica'}${dia} ${franja(tsTop.hora)}`;
    sub = `${tsTop.prob_ts}% de probabilidad de tormenta cerca de las ${tsTop.hora.slice(11, 16)}. ` + sub;
  }
  return [tit, sub];
}
function nivelUV(u) { return u < 3 ? 'bajo' : u < 6 ? 'moderado' : u < 8 ? 'alto' : u < 11 ? 'muy alto' : 'extremo'; }

// ---------- línea de tiempo (se puede arrastrar el dedo) ----------
const CORTOS = {'Parcialmente nublado': 'Nubes', 'Sol y nubes': 'Nubes', 'Lluvia probable': 'Lluvia', 'Posible lluvia': 'Lluvia',
  'Soleado': 'Sol', 'Posible tormenta eléctrica': 'Tormenta', 'Tormenta eléctrica probable': 'Tormenta'};
function lineaTiempo(rows, ahora) {
  const n = rows.length; if (!n) return '';
  const conds = rows.map(condicion);
  const segs = rows.map((r, i) => `<div class="seg${estado.tlSel === r.hora ? ' sel' : ''}" data-i="${i}" style="background:${colSeg(conds[i][2])}"></div>`).join('');
  const tks = rows.map((r, i) => i % 2 ? '' : `<div class="tk" style="left:${i / n * 100}%"><b>${r.hora === ahora ? 'Ahora' : r.hora.slice(11, 13) + 'h'}</b><span>${r.temp != null ? Math.round(r.temp) : '-'}°</span></div>`).join('');
  const fam = (r, i) => conds[i][0] === 'tormenta' ? 'tormenta' : r.prob >= 20 ? 'lluvia' : conds[i][1];
  const runs = [];
  rows.forEach((r, i) => { const f = fam(r, i), u = runs[runs.length - 1];
    if (u && u[0] === f) { u[2] = i + 1; u[3] = Math.max(u[3], f === 'tormenta' ? r.prob_ts : r.prob); } else runs.push([f, i, i + 1, f === 'tormenta' ? r.prob_ts : r.prob]); });
  const unidos = [];
  for (const run of runs) { const u = unidos[unidos.length - 1], fuerte = x => x === 'lluvia' || x === 'tormenta';
    if (u && !fuerte(run[0]) && !fuerte(u[0]) && (run[2] - run[1] < 3 || u[0] === run[0])) u[2] = run[2];
    else unidos.push(run.slice()); }
  const blq = unidos.map(([f, i0, i1, pm]) => {
    const largo = i1 - i0;
    let t = f === 'lluvia' ? (pm >= 40 ? 'Lluvia probable' : 'Posible lluvia') : f === 'tormenta' ? (pm >= 50 ? 'Tormenta eléctrica probable' : 'Posible tormenta eléctrica') : f;
    if (largo < 2) t = ''; else if (largo < 4) t = CORTOS[t] || (t.length <= 8 ? t : '');
    return t ? `<div class="blq" style="left:${i0 / n * 100}%;width:${largo / n * 100}%">${t}</div>` : '';
  }).join('');
  const LEY = {'Soleado': 'Despejado', 'Sol y nubes': 'Algo nublado', 'Parcialmente nublado': 'Algo nublado',
    'Tormenta eléctrica probable': 'Tormenta', 'Posible tormenta eléctrica': 'Tormenta'};
  const usados = new Map(); conds.forEach(c => usados.set(LEY[c[1]] || c[1], c[2]));
  const ley = [...usados].map(([t, c]) => `<span><i style="background:${colSeg(c)};height:8px"></i>${t}</span>`).join('');
  return `<div class="tl"><div class="blqs">${blq}</div><div class="bar" role="slider" aria-label="Próximas horas">${segs}</div><div class="tks">${tks}</div>` +
    `<div class="tldet">Toca o desliza el dedo sobre la barra para ver cada hora.</div><div class="ley">${ley}</div></div>`;
}
function detalleHora(r) {
  const c = condicion(r), extra = [];
  if ((r.prob_ts || 0) >= 10) extra.push(`tormenta ${r.prob_ts}%`);
  if ((r.rafaga || 0) >= 40) extra.push(`ráfagas ${r.rafaga} km/h`);
  return `<b>${r.hora.slice(11, 16)}</b> · ${c[1]} · lluvia ${r.prob}%` + (extra.length ? ' · ' + extra.join(' · ') : '') +
    (r.temp != null ? ` · ${Math.round(r.temp)}°` : '');
}
function activarLinea(cont, rows) {
  const bar = cont.querySelector('.bar'), det = cont.querySelector('.tldet'); if (!bar) return;
  let ult = -1;
  const elegir = ev => {
    const rc = bar.getBoundingClientRect(), i = Math.max(0, Math.min(rows.length - 1, Math.floor((ev.clientX - rc.left) / rc.width * rows.length)));
    if (i === ult) return; ult = i;
    estado.tlSel = rows[i].hora;
    bar.querySelectorAll('.seg').forEach((x, j) => x.classList.toggle('sel', j === i));
    det.innerHTML = detalleHora(rows[i]);
    if (ev.pointerType === 'touch') tocar();
  };
  let activo = false;
  bar.addEventListener('pointerdown', e => { activo = true; ult = -1; elegir(e); });
  bar.addEventListener('pointermove', e => { if (activo || e.pointerType === 'mouse') elegir(e); });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(t => bar.addEventListener(t, () => { activo = false; }));
}

// ---------- gráfica (cursor con el mouse o arrastrando el dedo) ----------
function grafica(cont, filas, ahora, hoy) {
  const W = Math.max(cont.clientWidth, 280), H = 210, pl = 34, pr = 10, pt = 16, pb = 26, w = W - pl - pr, h = H - pt - pb;
  const n = filas.length, dx = w / Math.max(n - 1, 1), X = i => pl + i * dx, Y = p => pt + h - h * p / 100;
  const mmMax = Math.max(2, ...filas.map(r => r.mm)), oc = estado.ocultas, paso = n > 30 ? 6 : 3;
  const hayTs = filas.some(r => (r.prob_ts || 0) >= 5);
  let s = `<svg class="graf" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Probabilidad de lluvia por hora">`;
  s += '<defs><linearGradient id="gl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3b82f6" stop-opacity=".75"/><stop offset="1" stop-color="#3b82f6" stop-opacity=".05"/></linearGradient></defs>';
  for (const p of [25, 50, 75, 100]) s += `<line x1="${pl}" x2="${W - pr}" y1="${Y(p)}" y2="${Y(p)}" class="grid"/><text x="${pl - 6}" y="${Y(p) + 4}" class="ejey">${p}%</text>`;
  filas.forEach((r, i) => {
    const hh = r.hora.slice(11, 13);
    if (hh === '00' && i) s += `<line x1="${X(i)}" x2="${X(i)}" y1="${pt}" y2="${pt + h}" class="dia"/><text x="${X(i) + 4}" y="${pt + 10}" class="etdia">${nombreDia(r.hora.slice(0, 10), hoy)}</text>`;
    if (+hh % paso === 0) s += `<text x="${X(i)}" y="${H - 8}" class="ejex">${hh}h</text>`;
    if (r.hora === ahora) s += `<line x1="${X(i)}" x2="${X(i)}" y1="${pt}" y2="${pt + h}" class="ahora"/>`;
    if (!oc.has('mm') && r.mm > 0) { const bh = h * 0.45 * Math.min(r.mm, mmMax) / mmMax;
      s += `<rect x="${X(i) - dx * 0.35}" y="${pt + h - bh}" width="${dx * 0.7}" height="${bh}" class="mm"/>`; }
  });
  const pts = filas.map((r, i) => `${X(i)},${Y(r.prob)}`).join(' ');
  if (!oc.has('lluvia')) s += `<polygon points="${X(0)},${Y(0)} ${pts} ${X(n - 1)},${Y(0)}" fill="url(#gl)"/><polyline points="${pts}" class="linea"/>`;
  if (!oc.has('llovizna')) s += `<polyline points="${filas.map((r, i) => `${X(i)},${Y(r.prob_dz)}`).join(' ')}" class="lineadz"/>`;
  if (hayTs && !oc.has('tormenta')) s += `<polyline points="${filas.map((r, i) => `${X(i)},${Y(r.prob_ts || 0)}`).join(' ')}" class="lineats"/>`;
  s += `<line class="cursor" y1="${pt}" y2="${pt + h}" visibility="hidden"/><circle class="punto" r="5" visibility="hidden"/></svg><div class="tip" hidden></div>`;
  cont.innerHTML = s;
  const svg = cont.querySelector('svg'), tip = cont.querySelector('.tip'), cur = svg.querySelector('.cursor'), pto = svg.querySelector('.punto');
  let ult = -1, activo = false;
  const mostrar = ev => {
    const rc = svg.getBoundingClientRect(), i = Math.max(0, Math.min(n - 1, Math.round((ev.clientX - rc.left - pl) / dx))), r = filas[i];
    if (i !== ult && ev.pointerType === 'touch') tocar();
    ult = i;
    cur.setAttribute('x1', X(i)); cur.setAttribute('x2', X(i)); cur.setAttribute('visibility', 'visible');
    pto.setAttribute('cx', X(i)); pto.setAttribute('cy', Y(r.prob)); pto.setAttribute('visibility', oc.has('lluvia') ? 'hidden' : 'visible');
    tip.innerHTML = `<b>${nombreDia(r.hora.slice(0, 10), hoy)} ${r.hora.slice(11, 16)}</b><br>Lluvia: <b>${r.prob}%</b> · Llovizna: ${r.prob_dz}%` +
      ((r.prob_ts || 0) >= 5 ? `<br>Tormenta eléctrica: <b>${r.prob_ts}%</b>` : '') +
      `<br>${r.prob >= 10 ? 'Si llueve: ' + intensidad(r.mm) : 'Sin lluvia prevista'}<br>${r.temp != null ? Math.round(r.temp) + '°C · ' : ''}${condicion(r)[1]}`;
    tip.hidden = false;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = Math.max(0, Math.min(W - tw, X(i) - tw / 2)) + 'px';
    // con el dedo, el globo va arriba para que no lo tape; si arriba lo taparía la barra superior, va abajo
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
function htmlDias(dias, filas, ahora, hoy) {
  const ts = filas.filter(r => r.temp != null).map(r => r.temp);
  const tmn = Math.min(...ts), tmx = Math.max(...ts), rng = Math.max(tmx - tmn, 1);
  return Object.entries(dias).map(([d, rs], k) => {
    const lejano = k >= 3;
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
    const sel = estado.dia === d;
    let html = `<div class="drow${sel ? ' sel' : ''}" data-dia="${d}" role="button" tabindex="0" aria-expanded="${sel}"><div class="dn">${nombreDia(d, hoy)}${lejano ? '<small>orientativo</small>' : ''}</div><div class="ic">${icono(ic, 28)}</div>${pct}` +
      `<div class="dmm">${(tsTop.prob_ts || 0) >= 30 ? 'Tormenta' : top.prob >= 20 ? 'Lluvia ' + intensidad(top.mm) : ''}</div><div class="tmin">${Math.round(mn)}°</div>` +
      `<div class="trng"><div style="left:${(mn - tmn) / rng * 100}%;width:${Math.max((mx - mn) / rng * 100, 3)}%"></div></div><div class="tmax">${Math.round(mx)}°</div></div>`;
    if (sel) {
      const [tt, sub] = top.prob >= 25 ? [`Lluvia ${intensidad(top.mm)} ${franja(top.hora)}`, `Máximo ${top.prob}% a las ${top.hora.slice(11, 16)}.`]
        : top.prob >= 10 ? ['Baja probabilidad de lluvia', `Máximo ${top.prob}% ${franja(top.hora)}.`] : ['Sin lluvia prevista', ''];
      const uvs = rs.filter(r => r.uv != null).map(r => r.uv), uvMax = uvs.length ? Math.max(...uvs) : null;
      const rfs = rs.filter(r => r.rafaga != null).map(r => r.rafaga), rfMax = rfs.length ? Math.max(...rfs) : null;
      const extra = [(tsTop.prob_ts || 0) >= 10 ? `Tormenta eléctrica: hasta ${tsTop.prob_ts}% ${franja(tsTop.hora)}.` : '',
        uvMax != null && uvMax >= 3 ? `Índice UV máximo ${Math.round(uvMax)} (${nivelUV(uvMax)}).` : '',
        rfMax != null && rfMax >= 40 ? `Ráfagas de hasta ${rfMax} km/h.` : ''].filter(Boolean).join(' ');
      html += `<div class="dexp"><p><b>${tt}.</b> ${sub} Temperatura entre ${Math.round(mn)}° y ${Math.round(mx)}°. ${extra}${lejano ? ' <i>A más de 3 días el pronóstico es menos preciso y todavía no se verifica; tómalo como orientativo.</i>' : ''}</p>${lineaTiempo(rs, ahora)}</div>`;
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

// ---------- mapa de lluvia ----------
const COLMAPA = [[10, '#1e3a8a'], [20, '#1d4ed8'], [40, '#3b82f6'], [60, '#7c3aed'], [80, '#c026d3']];
const colorP = p => { let c = null; for (const [u, col] of COLMAPA) if (p >= u) c = col; return c; };
let mapa = null;
function cargarLeaflet() {
  if (window.L) return Promise.resolve();
  return new Promise((ok, mal) => {
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'; document.head.appendChild(css);
    const js = document.createElement('script'); js.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'; js.onload = ok; js.onerror = mal; document.head.appendChild(js);
  });
}
function htmlMapa() {
  if (!D.mapa || !D.mapa.horas || !D.mapa.horas.length) return '';
  return `<div class="card" id="cmapa"><h3><span>Mapa de lluvia</span><span class="sub">pronóstico calibrado</span></h3><div id="mapa"><div class="vacio"><span class="cargando"></span>Cargando mapa…</div></div>` +
    `<div class="mapctl"><button id="mplay" aria-label="Reproducir">▶</button><input type="range" id="mslider" min="0" max="${D.mapa.horas.length - 1}" value="0" aria-label="Hora"><div class="mapht" id="mhora"></div></div>` +
    `<div class="mapley">Probabilidad de lluvia: ${COLMAPA.map(([u, c]) => `<span><i style="background:${c}"></i> ${u}%+</span>`).join(' ')}</div>` +
    `<p class="nota">Cada cuadro muestra la probabilidad de lluvia de esa zona en la hora elegida. Es un pronóstico, no un radar. Con dos dedos puedes acercar el mapa.</p></div>`;
}
function activarMapa(v) {
  const card = $('#cmapa'); if (!card) return;
  const iniciar = async () => {
    try { await cargarLeaflet(); } catch (e) { $('#mapa').innerHTML = '<div class="vacio">No se pudo cargar el mapa. Revisa tu conexión.</div>'; return; }
    const M = D.mapa, tactil = matchMedia('(pointer:coarse)').matches, hoy = ahoraISO(D.offset).slice(0, 10);
    $('#mapa').innerHTML = '';
    const map = L.map('mapa', {zoomControl: !tactil, dragging: !tactil, tap: false, scrollWheelZoom: false, attributionControl: true})
      .setView([v.lat ?? -1.6, v.lon ?? -78.6], 6.4);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 10,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(map);
    const rects = [], d = M.paso / 2;
    M.lats.forEach(la => M.lons.forEach(lo => rects.push(L.rectangle([[la - d, lo - d], [la + d, lo + d]], {weight: 0, fillOpacity: 0, interactive: true}).addTo(map))));
    if (v.lat != null) L.circleMarker([v.lat, v.lon], {radius: 6, color: '#fff', weight: 2, fillColor: '#f59e0b', fillOpacity: 1}).addTo(map);
    const sl = $('#mslider'), lab = $('#mhora');
    const pintar = j => {
      const ps = M.p[j]; rects.forEach((r, k) => { const p = ps[k], c = p >= 0 ? colorP(p) : null;
        r.setStyle({fillColor: c || '#000', fillOpacity: c ? (claro() ? 0.45 : 0.55) : 0}); r.unbindTooltip(); if (p >= 0) r.bindTooltip(`${p}% de lluvia`, {sticky: true}); });
      const hr = M.horas[j]; lab.textContent = `${nombreDia(hr.slice(0, 10), hoy)} ${hr.slice(11, 16)}`;
    };
    const ahora = ahoraISO(D.offset); let j0 = M.horas.findIndex(h => h >= ahora); if (j0 < 0) j0 = 0;
    sl.value = j0; pintar(j0);
    sl.addEventListener('input', () => pintar(+sl.value));
    let tm = null; const play = $('#mplay');
    play.onclick = () => { if (tm) { clearInterval(tm); tm = null; play.textContent = '▶'; return; }
      play.textContent = '❚❚'; tm = setInterval(() => { sl.value = (+sl.value + 1) % M.horas.length; pintar(+sl.value); }, 500); };
    mapa = map;
  };
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) { io.disconnect(); iniciar(); } }, {rootMargin: '300px'});
    io.observe(card);
  } else iniciar();
}

// ---------- satélite ----------
function htmlSatelite() {
  const t = Math.floor(Date.now() / 6e5);
  return `<details class="sat" id="dsat"><summary>Satélite ahora: nubes sobre el norte de Sudamérica</summary>` +
    `<a href="https://www.star.nesdis.noaa.gov/GOES/sector.php?sat=G19&sector=nsa" target="_blank" rel="noopener">` +
    `<img data-src="https://cdn.star.nesdis.noaa.gov/GOES19/ABI/SECTOR/nsa/GEOCOLOR/600x600.jpg?t=${t}" alt="Imagen de satélite GOES-19" loading="lazy"></a>` +
    `<p class="nota">Imagen GeoColor del satélite GOES-19 (se actualiza cada pocos minutos). De día se ve como a simple vista; de noche las nubes altas, que suelen ser las de tormenta, aparecen en blanco. Toca la imagen para ampliarla. Crédito: CIRA/NOAA.</p></details>`;
}
function activarSatelite() {
  const d = $('#dsat'); if (!d) return;
  d.addEventListener('toggle', () => { const img = d.querySelector('img'); if (d.open && img.dataset.src) { img.src = img.dataset.src; delete img.dataset.src;
    img.onerror = () => { img.replaceWith(Object.assign(document.createElement('p'), {className: 'nota', textContent: 'La imagen de satélite no está disponible en este momento. Toca aquí para abrir la página de NOAA.'})); }; } });
}

// ---------- reportes de usuarios ----------
const TIPOS_REP = [['soleado', '☀️', 'Soleado'], ['nublado', '☁️', 'Nublado'], ['llovizna', '🌦️', 'Llovizna'],
                   ['lluvia', '🌧️', 'Lluvia'], ['lluvia_fuerte', '🌊', 'Aguacero'], ['tormenta', '⛈️', 'Tormenta']];
const NOMBRE_REP = Object.fromEntries(TIPOS_REP.map(([t, , n]) => [t, n.toLowerCase()]));
function htmlFeedback() {
  if (!D.feedback_url) return '';
  return `<div class="card" id="cfb"><h3>¿Cómo está el clima donde estás?</h3><div class="fbb">` +
    TIPOS_REP.map(([t, e, n]) => `<button data-t="${t}"><span class="fbe">${e}</span>${n}</button>`).join('') +
    `</div><div class="fbres" id="fbres"></div><div class="fbres" id="fbsum"></div><p class="nota">Tu reporte nos ayuda a comprobar el pronóstico en tu barrio. ` +
    `Solo guardamos la hora, lo que reportas y tu ubicación aproximada; nunca datos personales.</p></div>`;
}
const enEcuador = p => p && p.lat >= -6 && p.lat <= 2.5 && p.lon >= -92.5 && p.lon <= -75;
const TXT_FUERA = 'Tu navegador te ubica fuera de Ecuador. Si usas una VPN o la red de una empresa, desactívala para esta página o reporta desde el celular con datos móviles.';
function obtenerPos(forzar) {
  return new Promise((ok, mal) => {
    if (estado.pos && !forzar) return ok(estado.pos);
    if (!navigator.geolocation) return mal(new Error('sin geolocalización'));
    navigator.geolocation.getCurrentPosition(p => { estado.pos = {lat: p.coords.latitude, lon: p.coords.longitude}; ok(estado.pos); },
      mal, {enableHighAccuracy: false, timeout: 12000, maximumAge: 600000});
  });
}
async function resumenFeedback() {
  const box = $('#fbsum'); if (!box || !D.feedback_url || !estado.pos) return;
  try {
    const r = await fetch(`${D.feedback_url}/resumen?lat=${estado.pos.lat.toFixed(2)}&lon=${estado.pos.lon.toFixed(2)}`);
    const j = await r.json();
    if (j.total >= 1) {
      const partes = TIPOS_REP.filter(([t]) => j[t]).map(([t, e]) => `${e} ${j[t]} ${NOMBRE_REP[t]}`);
      box.innerHTML = `En la última hora, cerca de ti (≈10 km): ${partes.join(' · ')}.`;
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
  try {
    const r = await fetch(`https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lon}&localityLanguage=es`);
    const j = await r.json(); return j.locality || j.city || j.principalSubdivision || null;
  } catch (e) { return null; }
}
function construirUbic(c) {
  const {lat, lon, om, nombre} = c;
  const {ref, dist, aprox} = referencia(lat, lon, om.elevation ?? 0);
  const nota = aprox
    ? `Ajuste aproximado: todavía no tenemos una estación verificada cerca de ti. Usamos lo aprendido en ${ref.nombre} (${dist} km).`
    : `Pronóstico de tu ubicación exacta, ajustado con lo aprendido en ${ref.nombre} (${dist} km).`;
  const off = om.utc_offset_seconds ?? D.offset;
  return {nombre: nombre || 'Tu ubicación', lat, lon, filas: filasDesdeOM(om.hourly, ref.cfg, lat, lon, off), cfg: ref.cfg, offset: off,
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
    const [om, nombre] = await Promise.all([fetch(url).then(r => r.json()), nombreLugar(lat, lon)]);
    if (!om.hourly) throw new Error(om.reason || 'sin datos');
    const cache = {lat, lon, om, nombre, t: Date.now()};
    guardar('cc_ubic_cache', JSON.stringify(cache));
    estado.ubic = construirUbic(cache);
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
  let h = `<div class="grab" id="grab"></div><h2>Elegir lugar</h2>` +
    `<button class="lit${estado.vista === 'ubic' ? ' on' : ''}" data-v="ubic"><span style="font-size:22px">📍</span><span class="nm">Mi ubicación<small>${u ? esc(u.nombre) + ' · pronóstico exacto de donde estás' : 'Pronóstico exacto de donde estás'}</small></span>` +
    (u && u.filas.length ? `<span class="tt">${(() => { const p = u.filas.find(r => r.hora >= ahoraISO(u.offset)); return p && p.temp != null ? Math.round(p.temp) + '°' : ''; })()}</span>` : '') + `</button>` +
    `<div class="sh">Otras ciudades</div>`;
  h += D.ciudades.map((c, i) => { const r = resumenCiudad(c);
    return `<button class="lit${estado.vista === i ? ' on' : ''}" data-v="${i}"><span class="ic">${icono(r.ic, 30)}</span><span class="nm">${esc(c.nombre)}<small>${esc(r.txt)}</small></span><span class="tt">${r.t}</span></button>`; }).join('');
  $('#sheetc').innerHTML = h;
  const sh = $('#sheet'), bg = $('#sheetbg');
  sh.hidden = false; bg.hidden = false;
  requestAnimationFrame(() => { sh.classList.add('on'); bg.classList.add('on'); });
  history.pushState({hoja: 1}, '');
  sh.querySelectorAll('.lit').forEach(b => b.onclick = () => {
    tocar(); cerrarSelector();
    estado.dia = null; estado.tlSel = null;
    if (b.dataset.v === 'ubic') { estado.ubic ? (estado.vista = 'ubic', avisoTop(''), render()) : cargarUbicacion({forzar: true}); }
    else { estado.vista = +b.dataset.v; render(); }
    window.scrollTo({top: 0, behavior: 'smooth'});
  });
  // deslizar hacia abajo para cerrar
  const grab = $('#grab'); let y0 = null;
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
  if (!desdeHistorial && history.state && history.state.hoja) history.back();
}
window.addEventListener('popstate', () => cerrarSelector(true));
document.addEventListener('keydown', e => { if (e.key === 'Escape') cerrarSelector(); });

// ---------- página ----------
function vistaActual() {
  if (estado.vista === 'ubic') return estado.ubic;
  const c = D.ciudades[estado.vista]; if (!c) return null;
  return {nombre: c.nombre, lat: c.lat, lon: c.lon, filas: c.filas, cfg: c.cfg, offset: D.offset, nivel: c.nivel, verif_html: c.verif_html,
    lugar: c.propia ? '' : (c.ref ? `Pronóstico de ${c.nombre} ajustado con lo aprendido en ${c.ref} mientras reunimos datos propios.` : '')};
}
function cabecera() {
  const v = estado.vista === 'ubic' ? (estado.ubic ? estado.ubic.nombre : 'Tu ubicación') : (D.ciudades[estado.vista] || {}).nombre || 'Elegir lugar';
  $('#lugarnom').textContent = (estado.vista === 'ubic' ? '📍 ' : '') + v;
}
function leyenda(hayTs) {
  const k = claro();
  return [['lluvia', k ? '#2563eb' : '#60a5fa', 'Lluvia', '3px'], ['llovizna', k ? '#64748b' : '#94a3b8', 'Llovizna', '3px'], ...(hayTs ? [['tormenta', k ? '#7c3aed' : '#a78bfa', 'Tormenta', '3px']] : []), ['mm', k ? '#93c5fd' : '#1e40af', 'Cantidad', '8px']]
    .map(([k, c, t, hh]) => `<span class="lg${estado.ocultas.has(k) ? ' off' : ''}" data-s="${k}" role="button" tabindex="0"><i style="background:${c};height:${hh}"></i>${t}</span>`).join('') +
    '<span class="lg" style="cursor:default"><i style="background:#f59e0b"></i>Ahora</span>';
}
let ctx = null;
function pintarGrafica() {
  const {v, ahora, hoy, dias, prox} = ctx;
  const filasG = estado.dia ? dias[estado.dia] : prox.slice(0, 72);
  $('#titg').textContent = estado.dia ? `Probabilidad de lluvia · ${nombreDia(estado.dia, hoy)}` : 'Probabilidad de lluvia · próximos 3 días';
  $('#todo').hidden = !estado.dia;
  grafica($('#graf'), filasG, ahora, hoy);
  $('#leyg').innerHTML = leyenda(filasG.some(r => (r.prob_ts || 0) >= 5));
  $$('#leyg .lg[data-s]').forEach(l => l.onclick = () => { const k = l.dataset.s; estado.ocultas.has(k) ? estado.ocultas.delete(k) : estado.ocultas.add(k); pintarGrafica(); });
}
function pintarDias() {
  const {v, ahora, hoy, dias} = ctx;
  $('#dias').innerHTML = htmlDias(dias, v.filas, ahora, hoy);
  const exp = document.querySelector('.dexp'); if (exp) { activarLinea(exp, dias[estado.dia]); }
  $$('.drow').forEach(d => { const f = () => { tocar(); estado.dia = estado.dia === d.dataset.dia ? null : d.dataset.dia; estado.tlSel = null; pintarDias(); pintarGrafica();
      const s = document.querySelector('.drow.sel'); if (s) s.scrollIntoView({block: 'nearest', behavior: 'smooth'}); };
    d.onclick = f; d.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); f(); } }; });
}
function render() {
  cabecera();
  const v = vistaActual(); if (!v) return;
  const ahora = ahoraISO(v.offset), hoy = ahora.slice(0, 10), prox = v.filas.filter(r => r.hora >= ahora);
  if (!prox.length) { $('#vista').innerHTML = '<div class="vacio">El pronóstico está desactualizado. Desliza hacia abajo o vuelve a cargar la página.</div>'; return; }
  const act = prox[0], [ic, ctxt] = condicion(act), [tit, sub] = titular(prox.slice(0, 24), hoy);
  const hoyF = v.filas.filter(r => r.hora.slice(0, 10) === hoy && r.temp != null);
  const tprox = act.prob >= 20 ? `${ctxt.toLowerCase()} (${act.prob}%)` : `${ctxt}, sin lluvia`;
  const dias = porDia(v.filas, ahora);
  if (estado.dia && !dias[estado.dia]) estado.dia = null;
  ctx = {v, ahora, hoy, dias, prox};
  const p24 = prox.slice(0, 24), tsMax = p24.reduce((a, b) => (b.prob_ts || 0) > (a.prob_ts || 0) ? b : a);
  const uvs = hoyF.filter(r => r.uv != null).map(r => r.uv), uvMax = uvs.length ? Math.max(...uvs) : null;
  const rf = prox.slice(0, 12).filter(r => r.rafaga != null).map(r => r.rafaga), rfMax = rf.length ? Math.max(...rf) : null;
  const chips = [`<span class="chip">Próxima hora: <b>${tprox}</b></span>`,
    hoyF.length ? `<span class="chip">Hoy: <b>${Math.round(Math.max(...hoyF.map(r => r.temp)))}°</b> / ${Math.round(Math.min(...hoyF.map(r => r.temp)))}°</span>` : '',
    (tsMax.prob_ts || 0) >= 10 ? `<span class="chip ts">⚡ Tormenta: <b>${tsMax.prob_ts}%</b> ${franja(tsMax.hora)}</span>` : '',
    uvMax != null && uvMax >= 3 && !esNoche(ahora) ? `<span class="chip">UV: <b>${Math.round(uvMax)}</b> (${nivelUV(uvMax)})</span>` : '',
    rfMax != null && rfMax >= 40 ? `<span class="chip">Ráfagas: <b>${rfMax} km/h</b></span>` : '',
    `<span class="chip">Fiabilidad: <b>${esc(v.nivel)}</b></span>`].join('');
  $('#vista').innerHTML =
    `<div class="hero"><div class="ic">${icono(ic, 72)}</div><div><div class="t">${act.temp != null ? Math.round(act.temp) : '-'}<sup>°</sup></div></div><div><h1>${tit}</h1><p>${sub}</p></div></div>` +
    (v.lugar ? `<div class="lugar">📍 ${esc(v.lugar)}</div>` : '') + `<div class="chips">${chips}</div>` +
    `<div class="card" id="c24"><h3>Próximas 24 horas</h3>${lineaTiempo(p24, ahora)}</div>` + htmlFeedback() +
    `<div class="card"><h3><span id="titg"></span><button id="todo" hidden>Ver 3 días</button></h3><div id="graf" style="position:relative"></div><div class="ley" id="leyg"></div></div>` +
    `<div class="card"><h3>Próximos 7 días <span class="sub">Toca un día para ver el detalle</span></h3><div id="dias"></div></div>` +
    htmlMapa() + htmlSatelite() +
    `<details><summary>Qué dice cada modelo meteorológico</summary>${tablaModelos(prox.slice(0, 72), v.cfg, hoy)}</details>` +
    `<details><summary>¿Qué tan confiable es este pronóstico?</summary>${v.verif_html}</details>`;
  activarLinea($('#c24'), p24);
  pintarGrafica(); pintarDias();
  $('#todo').onclick = () => { estado.dia = null; pintarDias(); pintarGrafica(); };
  activarMapa(v); activarSatelite(); activarFeedback(); resumenFeedback();
}
function pie() {
  const gen = new Date(D.generado + ':00Z').getTime() - D.offset * 1000, horas = (Date.now() - gen) / 36e5;
  const [f, h] = D.generado.split('T');
  const mc = Object.keys(MC_URL).length ? ' · Íconos: <a href="https://github.com/basmilius/meteocons" rel="noopener">Meteocons</a> (MIT, © Bas Milius)' : '';
  $('#pie').innerHTML = `Actualizado: ${f.split('-').reverse().join('/')} ${h}` + (horas > 3 ? ` <b style="color:#fcd34d">(hace ${Math.round(horas)} h)</b>` : '') +
    `<span id="instalar"></span><br>¿Cómo funciona? Combinamos ${KEYS.length} modelos meteorológicos internacionales y damos más peso a los que mejor han acertado en cada ciudad. ` +
    `Cada día comparamos el pronóstico con lo que realmente pasó en los aeropuertos (lluvia y tormentas eléctricas) y ajustamos los porcentajes, para que un 30% signifique de verdad 3 de cada 10.<br>` +
    `<b>Lluvia</b>: la que moja y deja las calles mojadas. <b>Llovizna</b>: gotas finas o garúa. El ícono del cielo se basa en cuánto sol llega de verdad, no solo en las nubes.<br>` +
    `Para emergencias sigue siempre las alertas oficiales de la Secretaría de Gestión de Riesgos y del INAMHI.<br>` +
    `Datos: <a href="https://open-meteo.com/" rel="noopener">Open-Meteo.com</a> (CC BY 4.0) · Observaciones: Iowa Environmental Mesonet · Satélite: CIRA/NOAA${mc} · ` +
    `<a href="${D.repo}" rel="noopener">Código abierto</a>`;
}
function toast(msg, accion, fn) {
  const t = $('#toast'); t.innerHTML = `<span>${esc(msg)}</span>` + (accion ? `<button id="tacc">${esc(accion)}</button>` : '') + '<button id="tcer" aria-label="Cerrar">✕</button>';
  t.hidden = false; const cerrar = () => { t.hidden = true; };
  $('#tcer').onclick = cerrar; if (accion) $('#tacc').onclick = () => { cerrar(); fn && fn(); };
  clearTimeout(t._tm); t._tm = setTimeout(cerrar, 9000);
}

// ---------- refrescar: deslizar hacia abajo (en la app instalada) y al volver a la pestaña ----------
const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
function refrescar() {
  if (estado.vista === 'ubic') { const gen = new Date(D.generado + ':00Z').getTime() - D.offset * 1000;
    if (Date.now() - gen > 70 * 60 * 1000) location.reload(); else cargarUbicacion({silencioso: true, forzar: true}); }
  else location.reload();
}
if (standalone) {
  let y0 = null, dy = 0; const ptr = $('#ptr');
  addEventListener('touchstart', e => { y0 = scrollY <= 0 && $('#sheet').hidden ? e.touches[0].clientY : null; dy = 0; }, {passive: true});
  addEventListener('touchmove', e => { if (y0 == null) return; dy = e.touches[0].clientY - y0; if (dy > 0) { const d = Math.min(dy, 120);
    ptr.style.opacity = Math.min(d / 80, 1); ptr.style.transform = `translateY(${d / 2 - 40}px) rotate(${d * 3}deg)`; ptr.classList.toggle('listo', dy > 80); } }, {passive: true});
  addEventListener('touchend', () => { if (y0 == null) return; y0 = null;
    if (dy > 80) { ptr.classList.add('gira'); tocar(); refrescar(); setTimeout(() => { ptr.classList.remove('gira', 'listo'); ptr.style.opacity = 0; }, 1500); }
    else { ptr.style.opacity = 0; ptr.style.transform = ''; } });
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  const gen = new Date(D.generado + ':00Z').getTime() - D.offset * 1000;
  if (Date.now() - gen > 70 * 60 * 1000) location.reload();
  else if (estado.ubic && Date.now() - (estado.ubic.t || CARGA) > 20 * 60 * 1000) cargarUbicacion({silencioso: true});
  else if (ctx) render();
});

// ---------- instalar como app ----------
let pedirInstalar = null;
addEventListener('beforeinstallprompt', e => { e.preventDefault(); pedirInstalar = e;
  const s = $('#instalar'); if (s) { s.innerHTML = ' · <a href="#" id="inst">Instalar ClearSky en tu teléfono</a>';
    $('#inst').onclick = ev => { ev.preventDefault(); pedirInstalar.prompt(); }; } });
function sugerirInstalar() {
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  if (!ios || standalone || leer('cc_ios_tip')) return;
  setTimeout(() => toast('Tenla como app: toca Compartir y luego "Agregar a inicio".', 'Entendido', () => guardar('cc_ios_tip', '1')), 20000);
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
  if (redibujar && antes !== t && ctx) { const y = scrollY; mapa = null; render(); scrollTo(0, y); }
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
  if (cache && cache.om && Date.now() - cache.t < 6 * 3600 * 1000) {
    try { estado.ubic = construirUbic(cache); estado.pos = {lat: cache.lat, lon: cache.lon}; estado.vista = 'ubic'; render(); } catch (e) { estado.ubic = null; }
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
  addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { if (innerWidth !== ancho && ctx) { ancho = innerWidth; pintarGrafica(); } }, 200); });
  sugerirInstalar();
}
inicio();
"""


# ----------------------- MAIN -----------------------
def main():
    p = cargar_pesos()
    if "--verificar" in sys.argv or toca_verificar(p):
        verificar(p)
    resultados = {}
    for ciudad, o in CIUDADES.items():
        print(f"Descargando pronóstico {ciudad}...")
        try:
            cfg, _ = cfg_efectivo(p, ciudad)
            filas = consenso(descargar(o["lat"], o["lon"]), cfg, o["lat"], o["lon"])
            if filas:
                resultados[ciudad] = filas
                guardar_log(ciudad, filas)
            print(f"  OK: {len(filas)} horas")
        except Exception as e:
            log_error(f"pronóstico {ciudad}: {e}")
    if not resultados:
        log_error("no se pudo descargar ningún pronóstico; se conserva la página anterior")
        sys.exit(1)
    try:
        n = descargar_reportes()
        if n:
            print(f"Reportes de usuarios nuevos: {n}")
    except Exception as e:
        log_error(f"reportes de usuarios: {e}")
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
    generar_html(resultados, p, mapa, iconos)
    print(f"Página: {ARCHIVO_HTML}")
    if "--no-open" not in sys.argv:
        webbrowser.open("file://" + ARCHIVO_HTML)


if __name__ == "__main__":
    main()
