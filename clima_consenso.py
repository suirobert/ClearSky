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
Página (index.html, generada aquí y dibujada con JavaScript):
  - Ciudades + "Mi ubicación" (el navegador calcula el pronóstico del punto del usuario
    con la configuración aprendida en la ciudad verificada más parecida).
  - Gráfica interactiva, días desplegables, botón "¿Está lloviendo donde estás?" (opcional).

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
DIAS_PRONOSTICO     = 3
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
VERSION             = 6          # versión de la lógica de datos (si cambia, se re-verifica)
ZONA = "America/Guayaquil"
OFFSET_S = -5 * 3600             # UTC-5 (Ecuador continental, sin horario de verano)

ARCHIVO_PESOS = os.path.join(DATOS, "pesos.json")
ARCHIVO_HTML  = os.path.abspath(os.environ.get("CC_HTML") or os.path.join(BASE, "dashboard.html"))
ARCHIVO_ERR   = os.path.join(DATOS, "errores.log")
FEEDBACK_URL  = (os.environ.get("CC_FEEDBACK_URL") or "").strip().rstrip("/")
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
            req = urllib.request.Request(full, headers={"User-Agent": f"ClearSky/2.0 (+{REPO_URL})"})
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
        codes = [] if wx in ("M", "") else wx.split()
        codes = [c for c in codes if not c.startswith("VC")]
        lluvia = int(any("RA" in c for c in codes))
        llovizna = int(lluvia or any("DZ" in c for c in codes))
        k = hora_clave(dt)
        a, b = obs.get(k, (0, 0))
        obs[k] = (max(a, lluvia), max(b, llovizna))
    return obs


def descargar_previos(lat, lon, dias):
    params = {"latitude": lat, "longitude": lon, "hourly": "precipitation_previous_day1",
              "models": ",".join(MODELOS), "timezone": ZONA, "past_days": dias, "forecast_days": 1}
    h = json.loads(http_get(URL_PREVIOUS, params))["hourly"]
    out = {}
    for m in MODELOS:
        serie = serie_modelo(h, "precipitation_previous_day1", m)
        if serie and any(v is not None for v in serie):
            out[m] = {t: v for t, v in zip(h["time"], serie) if v is not None}
    return out


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
            prev = descargar_previos(cfg["est"][0], cfg["est"][1], DIAS_VERIFICACION)
            if len(prev) < MIN_MODELOS:
                log_error(f"{c}: solo {len(prev)} modelos con historial; se mantiene la configuración anterior")
                continue
            r = calibrar(obs, prev)
            r["cobertura_obs_pct"] = round(cobertura * 100)
            p["ciudades"][c] = r
            ex = r.get("examen") or {}
            print(f"  {c}: {r['horas']} h, {r['horas_lluvia']} con lluvia, umbral {r['umbral']} mm/h, "
                  f"HSS {r['consenso']['hss']} | examen: clima {ex.get('brier_clima')} / sistema "
                  f"{ex.get('brier_sistema')} | calibración {r.get('modo_calibracion')}")
        except Exception as e:
            log_error(f"verificación {c}: {e} (se mantiene la configuración anterior)")
    guardar_archivo_obs(arch)
    for c in CIUDADES:
        try:
            p["ciudades"][c]["real"] = seguimiento_real(c, arch.get(c, {}))
        except Exception as e:
            log_error(f"seguimiento real {c}: {e}")
    p["actualizado"] = datetime.now().isoformat(timespec="seconds")
    p["version"] = VERSION
    guardar_pesos(p)


# ----------------------- PRONÓSTICO ACTUAL -----------------------
def descargar(lat, lon):
    params = {"latitude": lat, "longitude": lon, "hourly": "precipitation,temperature_2m,cloud_cover",
              "models": ",".join(MODELOS), "timezone": ZONA, "forecast_days": DIAS_PRONOSTICO}
    return json.loads(http_get(URL_FORECAST, params))


def consenso(data, cfg):
    """Misma lógica que filasDesdeOM() en el JavaScript de la página."""
    h = data["hourly"]
    pesos, umbral = cfg["pesos"], cfg["umbral"]
    filas = []
    for i, t in enumerate(h["time"]):
        lluvia, temp, nubes = {}, {}, {}
        for m in MODELOS:
            sp = serie_modelo(h, "precipitation", m)
            st = serie_modelo(h, "temperature_2m", m)
            sc = serie_modelo(h, "cloud_cover", m)
            if sp and sp[i] is not None: lluvia[m] = sp[i]
            if st and st[i] is not None: temp[m] = st[i]
            if sc and sc[i] is not None: nubes[m] = sc[i]
        if not lluvia:
            continue
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
            "conf": "Alta" if acuerdo >= 80 else "Media" if acuerdo >= 60 else "Baja",
            "detalle": {MODELOS[m]: round(v, 1) for m, v in lluvia.items()},
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


def datos_ciudad(p, ciudad, filas):
    cfg, ref = cfg_efectivo(p, ciudad)
    propia = bool(p["ciudades"].get(ciudad, {}).get("verificado"))
    verif = html_verificacion(cfg, ref) if ref else html_verificacion(cfg, ciudad)
    if ref and ref != ciudad:
        verif = (f"<p class='aviso'>{ciudad} todavía no tiene suficientes datos propios verificados. "
                 f"Mientras tanto usamos lo aprendido en {ref}.</p>" + verif)
    o = CIUDADES[ciudad]
    return {"nombre": ciudad, "lat": o["lat"], "lon": o["lon"], "zona": o["zona"], "propia": propia,
            "ref": ref, "nivel": nivel_fiabilidad(cfg),
            "cfg": {"umbral": cfg.get("umbral", UMBRAL_DEFECTO), "pesos": cfg.get("pesos", {}),
                    "tabla_lluvia": cfg.get("tabla_lluvia") or [], "tabla_llovizna": cfg.get("tabla_llovizna") or []},
            "filas": filas, "verif_html": verif}


def generar_html(resultados, p):
    datos = {"generado": datetime.now().strftime("%Y-%m-%dT%H:%M"), "offset": OFFSET_S,
             "modelos": MODELOS, "umbral_llovizna": UMBRAL_LLOVIZNA, "feedback_url": FEEDBACK_URL,
             "repo": REPO_URL, "verificacion": p.get("actualizado"),
             "ciudades": [datos_ciudad(p, c, f) for c, f in resultados.items()]}
    js_datos = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    doc = PLANTILLA.replace("__CSS__", CSS).replace("__JS__", JS).replace("__DATOS__", js_datos)
    os.makedirs(os.path.dirname(ARCHIVO_HTML), exist_ok=True)
    tmp = ARCHIVO_HTML + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, ARCHIVO_HTML)


PLANTILLA = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0f17">
<meta name="description" content="Pronóstico de lluvia para Ecuador que combina 7 modelos y se verifica todos los días con lo que realmente llovió.">
<title>ClearSky · Pronóstico de lluvia verificado</title>
<style>__CSS__</style></head><body><div class="wrap">
<div class="top"><div class="brand">ClearSky</div><div class="tabs" id="tabs"></div></div>
<div id="vista"></div>
<div class="pie" id="pie"></div>
</div>
<script id="datos" type="application/json">__DATOS__</script>
<script>__JS__</script></body></html>"""


CSS = r"""
*{box-sizing:border-box}
body{margin:0;background:#0b0f17;color:#e5e7eb;font-family:-apple-system,"SF Pro Text","Segoe UI",Roboto,Arial,sans-serif;-webkit-font-smoothing:antialiased}
button{font:inherit}
.wrap{max-width:760px;margin:0 auto;padding:16px 16px 40px}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px}
.brand{font-weight:700;font-size:16px;letter-spacing:.3px;color:#cbd5e1;flex:none}
.tabs{display:flex;gap:4px;background:#151b26;border-radius:999px;padding:3px;overflow-x:auto;scrollbar-width:none;max-width:100%}
.tabs::-webkit-scrollbar{display:none}
.tab{border:0;background:none;color:#9ca3af;padding:7px 14px;border-radius:999px;font-size:14px;cursor:pointer;white-space:nowrap}
.tab.on{background:#2563eb;color:#fff}
.hero{display:flex;align-items:center;gap:18px;margin:24px 0 6px}
.hero .t{font-size:64px;font-weight:200;line-height:1}
.hero .t sup{font-size:28px;vertical-align:top}
.hero h1{font-size:22px;font-weight:600;margin:0 0 6px}
.hero p{margin:0;color:#9ca3af;font-size:14px}
.lugar{color:#9ca3af;font-size:13px;margin-top:14px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 0}
.chip{background:#151b26;border-radius:999px;padding:5px 11px;font-size:12px;color:#cbd5e1}
.chip b{color:#fff}
.card{background:#111723;border-radius:16px;padding:16px;margin-top:16px;position:relative}
.card h3{margin:0 0 12px;font-size:13px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.card h3 button{background:#1f2937;border:0;color:#93c5fd;border-radius:999px;padding:3px 10px;font-size:12px;cursor:pointer;text-transform:none;letter-spacing:0}
.tl{position:relative;padding-top:20px}
.blqs{position:relative;height:18px;margin-top:-20px}
.blq{position:absolute;top:0;font-size:11px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-left:4px}
.bar{display:flex;height:24px;border-radius:6px;overflow:hidden}
.seg{flex:1;border-right:1px solid #0b0f17;cursor:pointer}
.seg.sel{outline:2px solid #f59e0b;outline-offset:-2px}
.tks{position:relative;height:38px}
.tk{position:absolute;top:6px;font-size:11px;color:#9ca3af;display:flex;flex-direction:column;align-items:flex-start}
.tk b{font-weight:500}.tk span{color:#e5e7eb;font-size:13px;margin-top:2px}
.tldet{font-size:13px;color:#cbd5e1;min-height:18px;margin-top:2px}
.graf{display:block;touch-action:pan-y;user-select:none}
.graf .grid{stroke:#1f2937;stroke-width:1}
.graf .dia{stroke:#374151;stroke-dasharray:3 3}
.graf .ahora{stroke:#f59e0b;stroke-width:1.5}
.graf .linea{fill:none;stroke:#60a5fa;stroke-width:2}
.graf .lineadz{fill:none;stroke:#94a3b8;stroke-width:1.3;stroke-dasharray:4 3}
.graf .mm{fill:#1e40af;opacity:.55}
.graf text{fill:#6b7280;font-size:11px}
.graf .ejey{text-anchor:end}.graf .ejex{text-anchor:middle}
.graf .etdia{fill:#9ca3af;font-weight:600}
.graf .cursor{stroke:#e5e7eb;stroke-width:1}
.graf .punto{fill:#60a5fa;stroke:#0b0f17;stroke-width:2}
.tip{position:absolute;pointer-events:none;background:#1f2937;border:1px solid #334155;border-radius:10px;padding:8px 10px;font-size:12px;line-height:1.5;min-width:150px;box-shadow:0 6px 20px rgba(0,0,0,.4)}
.tip b{color:#fff}
.ley{display:flex;gap:14px;font-size:12px;color:#9ca3af;margin-top:8px;flex-wrap:wrap}
.ley i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:5px}
.lg{cursor:pointer;user-select:none}.lg.off{opacity:.35;text-decoration:line-through}
.drow{display:grid;grid-template-columns:84px 30px 44px 104px 32px 1fr 32px;align-items:center;gap:8px;padding:10px 6px;border-top:1px solid #1a2230;font-size:15px;cursor:pointer;border-radius:8px}
.drow:first-child{border-top:0}
.drow:hover{background:#151d2b}.drow.sel{background:#16213a}
.dn{font-weight:500}.pp{color:#60a5fa;font-size:13px;font-weight:600}.pp.off{color:#4b5563}
.dmm{color:#6b7280;font-size:12px}.tmin{color:#9ca3af;text-align:right}.tmax{text-align:left}
.trng{position:relative;height:6px;background:#1f2937;border-radius:3px}
.trng div{position:absolute;top:0;height:6px;border-radius:3px;background:linear-gradient(90deg,#60a5fa,#fbbf24)}
.dexp{padding:6px 6px 14px;border-top:1px solid #1a2230}
.dexp p{margin:6px 0 10px;font-size:13px;color:#cbd5e1}
details{margin-top:16px;background:#111723;border-radius:16px;padding:14px 16px}
summary{cursor:pointer;font-size:14px;color:#93c5fd;list-style:none}
summary::-webkit-details-marker{display:none}
summary:before{content:"▸ ";color:#4b5563}details[open] summary:before{content:"▾ "}
.scroll{overflow-x:auto;margin-top:10px}
table{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap}
th,td{padding:5px 7px;text-align:center;border-bottom:1px solid #1a2230}
th{color:#9ca3af;font-weight:500;background:#0f1520;position:sticky;top:0}
tr.dsep td{background:#1a2230;text-align:left;font-weight:600;color:#cbd5e1}
tr.star td{background:#12351f}tr.alt td{background:#13233d}
.nota{font-size:12px;color:#9ca3af;line-height:1.6}
.aviso{font-size:13px;color:#fcd34d;background:#2a2312;border-radius:10px;padding:8px 10px;line-height:1.5}
h4{font-size:12px;color:#9ca3af;font-weight:600;margin:12px 0 6px}
.calib .cb{display:grid;grid-template-columns:110px 1fr 80px;gap:8px;align-items:center;font-size:12px;margin:4px 0}
.calib .cb div{height:8px;background:#1f2937;border-radius:4px;overflow:hidden}
.calib .cb i{display:block;height:8px;background:#3b82f6}
.calib em{color:#9ca3af;font-style:normal}
.plano{font-size:16px;margin:6px 0}.lst{font-size:14px;line-height:1.7;padding-left:18px;margin:6px 0}
details.tec{background:#0d121b;margin-top:14px}
.fbb{display:flex;gap:8px;flex-wrap:wrap}
.fbb button{flex:1;min-width:100px;background:#1f2937;border:1px solid #334155;color:#e5e7eb;border-radius:12px;padding:10px;cursor:pointer;font-size:14px}
.fbb button:hover{background:#273449}.fbb button:disabled{opacity:.5;cursor:default}
.fbres{font-size:13px;color:#cbd5e1;margin-top:8px}
.vacio{padding:40px 10px;text-align:center;color:#9ca3af}
.btn{background:#2563eb;border:0;color:#fff;border-radius:999px;padding:10px 18px;font-size:15px;cursor:pointer}
.pie{font-size:11px;color:#6b7280;margin-top:22px;line-height:1.6}
.pie a{color:#93c5fd}
@media(max-width:560px){
  .hero .t{font-size:52px}.hero h1{font-size:19px}
  .drow{grid-template-columns:62px 26px 38px 26px 1fr 28px;gap:7px;font-size:14px}.dmm{display:none}
  .tk:nth-child(even){display:none}
  .top{flex-direction:column;align-items:stretch}.brand{text-align:center}
}
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
 lluvia:'<path d="M7 13h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6 6a3.5 3.5 0 0 0 1 7z" fill="#64748b"/><g stroke="#38bdf8" stroke-width="2" stroke-linecap="round"><path d="M8 16l-1.5 4M12 16l-1.5 4M16 16l-1.5 4"/></g>'
};
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icono = (n, s) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" aria-hidden="true">${ICONOS[n]}</svg>`;
const r1 = x => Math.round(x * 10) / 10;
const guardar = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
const leer = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };

let estado = {vista: null, dia: null, ocultas: new Set(), ubic: null, pos: null, tlSel: null};

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
function filasDesdeOM(h, cfg) {
  const filas = [];
  h.time.forEach((t, i) => {
    const ll = {}, te = [], nu = [];
    for (const m of KEYS) {
      const p = (h['precipitation_' + m] || [])[i], tt = (h['temperature_2m_' + m] || [])[i], c = (h['cloud_cover_' + m] || [])[i];
      if (p != null) ll[m] = p; if (tt != null) te.push(tt); if (c != null) nu.push(c);
    }
    if (!Object.keys(ll).length) return;
    const bruta = probConsenso(ll, cfg.pesos, cfg.umbral), brutaDz = probConsenso(ll, cfg.pesos, D.umbral_llovizna);
    const prob = calibrarProb(bruta, cfg.tabla_lluvia), probDz = Math.max(calibrarProb(brutaDz, cfg.tabla_llovizna), prob);
    const ac = Math.max(bruta, 100 - bruta), vals = Object.values(ll), det = {};
    for (const m in ll) det[D.modelos[m]] = r1(ll[m]);
    filas.push({hora: t, prob: Math.round(prob), prob_dz: Math.round(probDz), prob_bruta: Math.round(bruta),
      mm: r1(medianaPond(ll, cfg.pesos)), min: r1(Math.min(...vals)), max: r1(Math.max(...vals)),
      temp: te.length ? r1(media(te)) : null, nubes: nu.length ? Math.round(media(nu)) : null,
      conf: ac >= 80 ? 'Alta' : ac >= 60 ? 'Media' : 'Baja', detalle: det});
  });
  return filas;
}

// ---------- textos ----------
function ahoraISO(off) { return new Date(Date.now() + off * 1000).toISOString().slice(0, 13) + ':00'; }
const esNoche = h => { const x = +h.slice(11, 13); return x < 6 || x >= 18; };
function condicion(r) {
  const n = esNoche(r.hora), nub = r.nubes ?? 50;
  if (r.prob >= 40) return ['lluvia', 'Lluvia probable', '#1d4ed8'];
  if (r.prob >= 20) return ['lluvia', 'Posible lluvia', '#3b82f6'];
  if (r.prob_dz >= 50) return ['llovizna', 'Llovizna', '#60a5fa'];
  if (nub >= 75) return ['nube', 'Nublado', '#64748b'];
  if (nub >= 35) return [n ? 'parcial_noche' : 'parcial_dia', 'Parcialmente nublado', '#94a3b8'];
  return [n ? 'luna' : 'sol', 'Despejado', '#cbd5e1'];
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
  const top = prox.reduce((a, b) => (b.prob > a.prob || (b.prob === a.prob && b.mm > a.mm)) ? b : a);
  if (top.prob >= 25) {
    const corte = Math.max(20, top.prob * 0.6), idx = prox.indexOf(top);
    let i0 = idx, i1 = idx;
    while (i0 > 0 && prox[i0 - 1].prob >= corte) i0--;
    while (i1 < prox.length - 1 && prox[i1 + 1].prob >= corte) i1++;
    const f0 = franja(prox[i0].hora), f1 = franja(prox[i1].hora);
    const cuando = f0 === f1 ? f0 : f0 + ' y ' + f1.replace(/^en /, '');
    const dia = top.hora.slice(0, 10) === hoy ? '' : ' mañana';
    return [`${top.prob >= 40 ? 'Lluvia probable' : 'Posible lluvia'}${dia} ${cuando}`,
      `${top.prob}% de probabilidad entre las ${prox[i0].hora.slice(11, 16)} y las ${finHora(prox[i1].hora)}. ` +
      `Si llueve, sería ${intensidad(top.mm)}. ${TXT_CONF[top.conf]}.`];
  }
  if (top.prob >= 10) return ['Baja probabilidad de lluvia', `Lo más probable es que no llueva. El momento con más opciones es ${franja(top.hora)} (${top.prob}%)${top.hora.slice(0, 10) === hoy ? '' : ' de mañana'}.`];
  const dz = prox.reduce((a, b) => b.prob_dz > a.prob_dz ? b : a);
  if (dz.prob_dz >= 40) return [`Posible llovizna ${franja(dz.hora)}`, `Podría caer una llovizna ligera (${dz.prob_dz}%). Lluvia de verdad, poco probable.`];
  return [condicion(prox[0])[1], 'No se espera lluvia en las próximas 24 horas.'];
}

// ---------- línea de tiempo ----------
const CORTOS = {'Parcialmente nublado': 'Nubes', 'Lluvia probable': 'Lluvia', 'Posible lluvia': 'Lluvia', 'Despejado': 'Sol'};
function lineaTiempo(rows, ahora) {
  const n = rows.length; if (!n) return '';
  const segs = rows.map((r, i) => { const [, txt, col] = condicion(r);
    return `<div class="seg${estado.tlSel === r.hora ? ' sel' : ''}" data-i="${i}" style="background:${col}" title="${r.hora.slice(11, 16)} · ${txt} · lluvia ${r.prob}%"></div>`; }).join('');
  const tks = rows.map((r, i) => i % 2 ? '' : `<div class="tk" style="left:${i / n * 100}%"><b>${r.hora === ahora ? 'Ahora' : r.hora.slice(11, 13) + 'h'}</b><span>${r.temp != null ? Math.round(r.temp) : '-'}°</span></div>`).join('');
  const fam = r => r.prob >= 20 ? 'lluvia' : condicion(r)[1];
  const runs = [];
  rows.forEach((r, i) => { const f = fam(r), u = runs[runs.length - 1];
    if (u && u[0] === f) { u[2] = i + 1; u[3] = Math.max(u[3], r.prob); } else runs.push([f, i, i + 1, r.prob]); });
  const unidos = [];
  for (const run of runs) { const u = unidos[unidos.length - 1];
    if (u && run[0] !== 'lluvia' && u[0] !== 'lluvia' && (run[2] - run[1] < 3 || u[0] === run[0])) u[2] = run[2];
    else unidos.push(run.slice()); }
  const blq = unidos.map(([f, i0, i1, pm]) => {
    const largo = i1 - i0;
    let t = f === 'lluvia' ? (pm >= 40 ? 'Lluvia probable' : 'Posible lluvia') : f;
    if (largo < 2) t = ''; else if (largo < 4) t = CORTOS[t] || (t.length <= 8 ? t : '');
    return t ? `<div class="blq" style="left:${i0 / n * 100}%;width:${largo / n * 100}%">${t}</div>` : '';
  }).join('');
  return `<div class="tl"><div class="blqs">${blq}</div><div class="bar">${segs}</div><div class="tks">${tks}</div>` +
    `<div class="tldet"></div><div class="ley"><span><i style="background:#cbd5e1;height:8px"></i>Despejado</span>` +
    `<span><i style="background:#94a3b8;height:8px"></i>Algo nublado</span><span><i style="background:#64748b;height:8px"></i>Nublado</span>` +
    `<span><i style="background:#60a5fa;height:8px"></i>Llovizna</span><span><i style="background:#3b82f6;height:8px"></i>Posible lluvia</span>` +
    `<span><i style="background:#1d4ed8;height:8px"></i>Lluvia probable</span></div></div>`;
}
function activarLinea(cont, rows) {
  cont.querySelectorAll('.seg').forEach(s => s.addEventListener('click', () => {
    const r = rows[+s.dataset.i]; estado.tlSel = r.hora;
    cont.querySelectorAll('.seg').forEach(x => x.classList.toggle('sel', x === s));
    cont.querySelector('.tldet').innerHTML = `<b>${r.hora.slice(11, 16)}</b> · ${condicion(r)[1]} · lluvia ${r.prob}% · llovizna ${r.prob_dz}% · ${r.temp != null ? Math.round(r.temp) + '°' : ''}`;
  }));
}

// ---------- gráfica interactiva ----------
function grafica(cont, filas, ahora, hoy) {
  const W = Math.max(cont.clientWidth, 280), H = 210, pl = 34, pr = 10, pt = 16, pb = 26, w = W - pl - pr, h = H - pt - pb;
  const n = filas.length, dx = w / Math.max(n - 1, 1), X = i => pl + i * dx, Y = p => pt + h - h * p / 100;
  const mmMax = Math.max(2, ...filas.map(r => r.mm)), oc = estado.ocultas, paso = n > 30 ? 6 : 3;
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
  s += `<line class="cursor" y1="${pt}" y2="${pt + h}" visibility="hidden"/><circle class="punto" r="4" visibility="hidden"/></svg><div class="tip" hidden></div>`;
  cont.innerHTML = s;
  const svg = cont.querySelector('svg'), tip = cont.querySelector('.tip'), cur = svg.querySelector('.cursor'), pto = svg.querySelector('.punto');
  const mostrar = ev => {
    const rc = svg.getBoundingClientRect(), i = Math.max(0, Math.min(n - 1, Math.round((ev.clientX - rc.left - pl) / dx))), r = filas[i];
    cur.setAttribute('x1', X(i)); cur.setAttribute('x2', X(i)); cur.setAttribute('visibility', 'visible');
    pto.setAttribute('cx', X(i)); pto.setAttribute('cy', Y(r.prob)); pto.setAttribute('visibility', oc.has('lluvia') ? 'hidden' : 'visible');
    tip.innerHTML = `<b>${nombreDia(r.hora.slice(0, 10), hoy)} ${r.hora.slice(11, 16)}</b><br>Lluvia: <b>${r.prob}%</b> · Llovizna: ${r.prob_dz}%<br>` +
      `${r.prob >= 10 ? 'Si llueve: ' + intensidad(r.mm) : 'Sin lluvia prevista'}<br>${r.temp != null ? Math.round(r.temp) + '°C · ' : ''}${condicion(r)[1]}`;
    tip.hidden = false;
    const tw = tip.offsetWidth; tip.style.left = Math.max(0, Math.min(W - tw, X(i) - tw / 2)) + 'px'; tip.style.top = '-6px';
  };
  const ocultar = () => { tip.hidden = true; cur.setAttribute('visibility', 'hidden'); pto.setAttribute('visibility', 'hidden'); };
  svg.addEventListener('pointermove', mostrar); svg.addEventListener('pointerdown', mostrar); svg.addEventListener('pointerleave', ocultar);
}

// ---------- días ----------
function porDia(filas, ahora) { const d = {}; for (const r of filas) if (r.hora >= ahora) (d[r.hora.slice(0, 10)] ||= []).push(r); return d; }
function htmlDias(dias, filas, ahora, hoy) {
  const ts = filas.filter(r => r.temp != null).map(r => r.temp);
  const tmn = Math.min(...ts), tmx = Math.max(...ts), rng = Math.max(tmx - tmn, 1);
  return Object.entries(dias).map(([d, rs]) => {
    const top = rs.reduce((a, b) => (b.prob > a.prob || (b.prob === a.prob && b.mm > a.mm)) ? b : a);
    const t = rs.filter(r => r.temp != null).map(r => r.temp), mn = Math.min(...t), mx = Math.max(...t);
    const diur = rs.filter(r => !esNoche(r.hora)); const base = diur.length ? diur : rs;
    let ic = (top.prob >= 20 || top.prob_dz >= 50) ? condicion(top)[0] : condicion(base.reduce((a, b) => (b.nubes || 0) > (a.nubes || 0) ? b : a))[0];
    ic = {luna: 'sol', parcial_noche: 'parcial_dia'}[ic] || ic;
    const pct = top.prob >= 10 ? `<span class="pp">${top.prob}%</span>` : '<span class="pp off">–</span>';
    const sel = estado.dia === d;
    let html = `<div class="drow${sel ? ' sel' : ''}" data-dia="${d}" role="button" tabindex="0"><div class="dn">${nombreDia(d, hoy)}</div>${icono(ic, 26)}${pct}` +
      `<div class="dmm">${top.prob >= 20 ? 'Lluvia ' + intensidad(top.mm) : ''}</div><div class="tmin">${Math.round(mn)}°</div>` +
      `<div class="trng"><div style="left:${(mn - tmn) / rng * 100}%;width:${Math.max((mx - mn) / rng * 100, 3)}%"></div></div><div class="tmax">${Math.round(mx)}°</div></div>`;
    if (sel) {
      const [tt, sub] = top.prob >= 25 ? [`Lluvia ${intensidad(top.mm)} ${franja(top.hora)}`, `Máximo ${top.prob}% a las ${top.hora.slice(11, 16)}.`]
        : top.prob >= 10 ? ['Baja probabilidad de lluvia', `Máximo ${top.prob}% ${franja(top.hora)}.`] : ['Sin lluvia prevista', ''];
      html += `<div class="dexp"><p><b>${tt}.</b> ${sub} Temperatura entre ${Math.round(mn)}° y ${Math.round(mx)}°.</p>${lineaTiempo(rs, ahora)}</div>`;
    }
    return html;
  }).join('');
}

// ---------- tabla por modelo ----------
function tablaModelos(filas, cfg, hoy) {
  const cols = KEYS.map(k => D.modelos[k]), u = cfg.umbral;
  let t = '<p class="nota">Milímetros de lluvia que pronostica cada modelo internacional. En azul, los que anuncian lluvia; en gris, solo llovizna. ' +
    'El número bajo cada nombre es su peso: cuanto más alto, más ha acertado en esta zona.</p><div class="scroll"><table><tr><th>Hora</th><th>Lluvia</th><th>Llovizna</th><th>mm</th><th>Rango</th><th>Coinciden</th>' +
    KEYS.map(k => `<th>${D.modelos[k]}<br><small>${cfg.pesos[k] ?? 1}</small></th>`).join('') + '</tr>';
  let dia = null;
  for (const r of filas) {
    if (r.hora.slice(0, 10) !== dia) { dia = r.hora.slice(0, 10); t += `<tr class="dsep"><td colspan="${6 + cols.length}">${nombreDia(dia, hoy)} · ${dia}</td></tr>`; }
    t += `<tr><td>${r.hora.slice(11, 16)}</td><td title="Sin calibrar: ${r.prob_bruta}%"><b>${r.prob}%</b></td><td>${r.prob_dz}%</td><td>${r.mm}</td><td>${r.min}–${r.max}</td><td>${r.conf}</td>` +
      cols.map(c => { const v = r.detalle[c]; return `<td style="color:${(v || 0) >= u ? '#60a5fa' : (v || 0) >= D.umbral_llovizna ? '#94a3b8' : '#475569'}">${v ?? '-'}</td>`; }).join('') + '</tr>';
  }
  return t + '</table></div>';
}

// ---------- reportes de usuarios ----------
function htmlFeedback() {
  if (!D.feedback_url) return '';
  return `<div class="card"><h3>¿Está lloviendo donde estás?</h3><div class="fbb">` +
    `<button data-t="lluvia">🌧️ Llueve</button><button data-t="llovizna">🌦️ Llovizna</button><button data-t="seca">🌤️ No llueve</button></div>` +
    `<div class="fbres" id="fbres"></div><div class="fbres" id="fbsum"></div><p class="nota">Tu reporte nos ayuda a comprobar el pronóstico en tu barrio. ` +
    `Solo guardamos la hora y tu ubicación aproximada (~1 km), nunca datos personales.</p></div>`;
}
function obtenerPos() {
  return new Promise((ok, mal) => {
    if (estado.pos) return ok(estado.pos);
    if (!navigator.geolocation) return mal(new Error('sin geolocalización'));
    navigator.geolocation.getCurrentPosition(p => { estado.pos = {lat: p.coords.latitude, lon: p.coords.longitude}; ok(estado.pos); },
      mal, {enableHighAccuracy: false, timeout: 15000, maximumAge: 600000});
  });
}
async function resumenFeedback() {
  const box = $('#fbsum'); if (!box || !D.feedback_url || !estado.pos) return;
  try {
    const r = await fetch(`${D.feedback_url}/resumen?lat=${estado.pos.lat.toFixed(2)}&lon=${estado.pos.lon.toFixed(2)}`);
    const j = await r.json();
    if (j.total >= 2) {
      const partes = [];
      if (j.lluvia) partes.push(`${j.lluvia} reportan lluvia`); if (j.llovizna) partes.push(`${j.llovizna} llovizna`); if (j.seca) partes.push(`${j.seca} sin lluvia`);
      box.innerHTML = `En la última hora, cerca de ti: ${partes.join(', ')}.`;
    }
  } catch (e) {}
}
function activarFeedback() {
  document.querySelectorAll('.fbb button').forEach(b => b.addEventListener('click', async () => {
    const box = $('#fbres'), ult = +(leer('cc_fb_ts') || 0);
    if (Date.now() - ult < 30 * 60 * 1000) { box.textContent = 'Ya registramos tu reporte hace poco. ¡Gracias! Puedes volver a reportar en 30 minutos.'; return; }
    box.textContent = 'Obteniendo tu ubicación aproximada…';
    let pos; try { pos = await obtenerPos(); } catch (e) { box.textContent = 'Para reportar necesitamos tu ubicación. Revisa el permiso del navegador.'; return; }
    document.querySelectorAll('.fbb button').forEach(x => x.disabled = true);
    try {
      const r = await fetch(`${D.feedback_url}/reporte`, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({lat: +pos.lat.toFixed(2), lon: +pos.lon.toFixed(2), tipo: b.dataset.t})});
      if (r.status === 429) box.textContent = 'Ya registramos tu reporte hace poco. ¡Gracias!';
      else if (!r.ok) throw new Error(r.status);
      else { box.textContent = '¡Gracias! Tu reporte quedó registrado.'; guardar('cc_fb_ts', Date.now()); resumenFeedback(); }
    } catch (e) { box.textContent = 'No pudimos enviar tu reporte. Inténtalo más tarde.'; }
    finally { document.querySelectorAll('.fbb button').forEach(x => x.disabled = false); }
  }));
}

// ---------- mi ubicación ----------
function km(a, b, c, d) { const R = Math.PI / 180, x = Math.sin((c - a) * R / 2) ** 2 + Math.cos(a * R) * Math.cos(c * R) * Math.sin((d - b) * R / 2) ** 2; return 12742 * Math.asin(Math.sqrt(x)); }
function referencia(lat, lon, elev) {
  const zona = lon < -88 ? 'galapagos' : (elev >= 1500 ? 'sierra' : (lon > -78.3 ? 'oriente' : 'costa'));
  const verif = D.ciudades.filter(c => c.propia), todas = verif.length ? verif : D.ciudades;
  const misma = todas.filter(c => c.zona === zona), lista = misma.length ? misma : todas;
  const ref = lista.reduce((a, b) => km(lat, lon, a.lat, a.lon) <= km(lat, lon, b.lat, b.lon) ? a : b);
  const dist = Math.round(km(lat, lon, ref.lat, ref.lon));
  return {ref, dist, aprox: !misma.length || dist > 120 || !ref.propia};
}
async function cargarUbicacion() {
  estado.vista = 'ubic'; guardar('cc_vista', 'ubic'); tabs();
  $('#vista').innerHTML = '<div class="vacio">Buscando tu ubicación…</div>';
  let pos;
  try { pos = await obtenerPos(); }
  catch (e) {
    $('#vista').innerHTML = '<div class="vacio"><p>No pudimos obtener tu ubicación.</p><p class="nota">Revisa que el navegador tenga permiso de ubicación para esta página, o elige una ciudad arriba.</p><button class="btn" id="reint">Intentar de nuevo</button></div>';
    $('#reint').onclick = cargarUbicacion; return;
  }
  guardar('cc_ubic', '1');
  $('#vista').innerHTML = '<div class="vacio">Calculando el pronóstico para tu ubicación…</div>';
  try {
    const lat = +pos.lat.toFixed(2), lon = +pos.lon.toFixed(2);
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&hourly=precipitation,temperature_2m,cloud_cover&models=${KEYS.join(',')}&timezone=auto&forecast_days=3`;
    const om = await (await fetch(url)).json();
    if (!om.hourly) throw new Error(om.reason || 'sin datos');
    const {ref, dist, aprox} = referencia(lat, lon, om.elevation ?? 0);
    const nota = aprox
      ? `Ajuste aproximado: todavía no tenemos una estación verificada cerca de ti. Usamos lo aprendido en ${ref.nombre} (${dist} km).`
      : `Pronóstico calculado para tu ubicación y ajustado con lo aprendido en ${ref.nombre} (${dist} km).`;
    estado.ubic = {nombre: 'Tu ubicación', filas: filasDesdeOM(om.hourly, ref.cfg), cfg: ref.cfg, offset: om.utc_offset_seconds ?? D.offset,
      nivel: ref.nivel + (aprox ? ' (aprox.)' : ''), lugar: nota,
      verif_html: `<p class="aviso">${esc(nota)}</p>` + ref.verif_html};
    if (estado.vista === 'ubic') render();
  } catch (e) {
    $('#vista').innerHTML = '<div class="vacio"><p>No pudimos descargar el pronóstico para tu ubicación.</p><button class="btn" id="reint">Intentar de nuevo</button></div>';
    $('#reint').onclick = cargarUbicacion;
  }
}

// ---------- página ----------
function tabs() {
  const v = estado.vista;
  $('#tabs').innerHTML = `<button class="tab${v === 'ubic' ? ' on' : ''}" data-v="ubic">📍 Mi ubicación</button>` +
    D.ciudades.map((c, i) => `<button class="tab${v === i ? ' on' : ''}" data-v="${i}">${esc(c.nombre)}</button>`).join('');
  $('#tabs').querySelectorAll('.tab').forEach(b => b.onclick = () => {
    estado.dia = null; estado.tlSel = null;
    if (b.dataset.v === 'ubic') { estado.ubic ? (estado.vista = 'ubic', guardar('cc_vista', 'ubic'), render()) : cargarUbicacion(); }
    else { estado.vista = +b.dataset.v; guardar('cc_vista', b.dataset.v); render(); }
  });
  const on = $('#tabs .tab.on'); if (on) on.scrollIntoView({block: 'nearest', inline: 'nearest'});
}
function vistaActual() {
  if (estado.vista === 'ubic') return estado.ubic;
  const c = D.ciudades[estado.vista];
  return {nombre: c.nombre, filas: c.filas, cfg: c.cfg, offset: D.offset, nivel: c.nivel, verif_html: c.verif_html,
    lugar: c.propia ? '' : (c.ref ? `Pronóstico de ${c.nombre} ajustado con lo aprendido en ${c.ref} mientras reunimos datos propios.` : '')};
}
function render() {
  tabs();
  const v = vistaActual(); if (!v) return;
  const ahora = ahoraISO(v.offset), hoy = ahora.slice(0, 10), prox = v.filas.filter(r => r.hora >= ahora);
  if (!prox.length) { $('#vista').innerHTML = '<div class="vacio">El pronóstico está desactualizado. Vuelve a cargar la página en unos minutos.</div>'; return; }
  const act = prox[0], [ic, ctxt] = condicion(act), [tit, sub] = titular(prox.slice(0, 24), hoy);
  const hoyF = v.filas.filter(r => r.hora.slice(0, 10) === hoy && r.temp != null);
  const tprox = act.prob >= 20 ? `${ctxt.toLowerCase()} (${act.prob}%)` : `${ctxt}, sin lluvia`;
  const dias = porDia(v.filas, ahora);
  if (estado.dia && !dias[estado.dia]) estado.dia = null;
  const filasG = estado.dia ? dias[estado.dia] : prox;
  const titG = estado.dia ? `Probabilidad de lluvia · ${nombreDia(estado.dia, hoy)}` : 'Probabilidad de lluvia · próximos 3 días';
  const ley = [['lluvia', '#60a5fa', 'Lluvia', '3px'], ['llovizna', '#94a3b8', 'Llovizna', '3px'], ['mm', '#1e40af', 'Cantidad esperada', '8px']]
    .map(([k, c, t, hh]) => `<span class="lg${estado.ocultas.has(k) ? ' off' : ''}" data-s="${k}"><i style="background:${c};height:${hh}"></i>${t}</span>`).join('') +
    '<span><i style="background:#f59e0b"></i>Ahora</span>';
  $('#vista').innerHTML =
    `<div class="hero">${icono(ic, 72)}<div><div class="t">${act.temp != null ? Math.round(act.temp) : '-'}<sup>°</sup></div></div><div><h1>${tit}</h1><p>${sub}</p></div></div>` +
    (v.lugar ? `<div class="lugar">📍 ${esc(v.lugar)}</div>` : '') +
    `<div class="chips"><span class="chip">Próxima hora: <b>${tprox}</b></span>` +
    (hoyF.length ? `<span class="chip">Hoy: <b>${Math.round(Math.max(...hoyF.map(r => r.temp)))}°</b> / ${Math.round(Math.min(...hoyF.map(r => r.temp)))}°</span>` : '') +
    `<span class="chip">Fiabilidad del pronóstico: <b>${esc(v.nivel)}</b></span></div>` +
    `<div class="card" id="c24"><h3>Próximas 24 horas</h3>${lineaTiempo(prox.slice(0, 24), ahora)}</div>` +
    `<div class="card"><h3><span>${titG}</span>${estado.dia ? '<button id="todo">Ver 3 días</button>' : ''}</h3><div id="graf" style="position:relative"></div><div class="ley">${ley}</div></div>` +
    `<div class="card"><h3>Próximos días <span class="nota" style="text-transform:none;letter-spacing:0;font-weight:400">Toca un día para ver el detalle</span></h3><div id="dias">${htmlDias(dias, v.filas, ahora, hoy)}</div></div>` +
    htmlFeedback() +
    `<details><summary>Qué dice cada modelo meteorológico</summary>${tablaModelos(prox, v.cfg, hoy)}</details>` +
    `<details><summary>¿Qué tan confiable es este pronóstico?</summary>${v.verif_html}</details>`;
  grafica($('#graf'), filasG, ahora, hoy);
  activarLinea($('#c24'), prox.slice(0, 24));
  const exp = document.querySelector('.dexp'); if (exp) activarLinea(exp, dias[estado.dia]);
  document.querySelectorAll('.drow').forEach(d => { const f = () => { estado.dia = estado.dia === d.dataset.dia ? null : d.dataset.dia; render(); };
    d.onclick = f; d.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); f(); } }; });
  document.querySelectorAll('.lg').forEach(l => l.onclick = () => { const k = l.dataset.s; estado.ocultas.has(k) ? estado.ocultas.delete(k) : estado.ocultas.add(k); render(); });
  const todo = $('#todo'); if (todo) todo.onclick = () => { estado.dia = null; render(); };
  activarFeedback(); resumenFeedback();
}
function pie() {
  const gen = new Date(D.generado + ':00Z').getTime() - D.offset * 1000, horas = (Date.now() - gen) / 36e5;
  const [f, h] = D.generado.split('T');
  $('#pie').innerHTML = `Actualizado: ${f.split('-').reverse().join('/')} ${h}` + (horas > 3 ? ` <b style="color:#fcd34d">(hace ${Math.round(horas)} h)</b>` : '') +
    `<br>¿Cómo funciona? Combinamos ${KEYS.length} modelos meteorológicos internacionales y damos más peso a los que mejor han acertado en cada ciudad. ` +
    `Cada día comparamos el pronóstico con lo que realmente llovió y ajustamos los porcentajes, para que un 30% signifique de verdad 3 de cada 10.<br>` +
    `<b>Lluvia</b>: la que moja y deja las calles mojadas. <b>Llovizna</b>: gotas finas o garúa. Pasa el dedo o el mouse sobre la gráfica para ver cada hora.<br>` +
    `Para emergencias sigue siempre las alertas oficiales de la Secretaría de Gestión de Riesgos y del INAMHI.<br>` +
    `Datos: <a href="https://open-meteo.com/" rel="noopener">Open-Meteo.com</a> (CC BY 4.0) · Observaciones: Iowa Environmental Mesonet · ` +
    `<a href="${D.repo}" rel="noopener">Código abierto</a>`;
}
function inicio() {
  pie();
  const g = leer('cc_vista');
  if (g === 'ubic' && leer('cc_ubic') === '1') { estado.vista = 'ubic'; cargarUbicacion(); return; }
  estado.vista = g !== null && g !== 'ubic' && D.ciudades[+g] ? +g : 0;
  render();
}
let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { if (vistaActual()) render(); }, 200); });
setInterval(() => { if (document.visibilityState === 'visible') location.reload(); }, 30 * 60 * 1000);
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
            filas = consenso(descargar(o["lat"], o["lon"]), cfg)
            if filas:
                resultados[ciudad] = filas
                guardar_log(ciudad, filas)
            print(f"  OK: {len(filas)} horas")
        except Exception as e:
            log_error(f"pronóstico {ciudad}: {e}")
    if not resultados:
        log_error("no se pudo descargar ningún pronóstico; se conserva la página anterior")
        sys.exit(1)
    generar_html(resultados, p)
    print(f"Página: {ARCHIVO_HTML}")
    if "--no-open" not in sys.argv:
        webbrowser.open("file://" + ARCHIVO_HTML)


if __name__ == "__main__":
    main()
