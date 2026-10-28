# -*- coding: utf-8 -*-
"""
Clima Consenso - v4
-------------------
Datos (igual que v3):
  - 7 modelos (Open-Meteo), consenso ponderado, llovizna vs lluvia.
  - Verificación diaria contra METAR (±1 h), pesos por modelo, umbral por ciudad.
  - Calibración de probabilidad (lo que dice = lo que pasa).
Visual (v4):
  - Interfaz estilo Dark Sky (modo oscuro), adaptable a celular.
  - Tarjeta principal, línea de tiempo 24 h, gráfica por hora, resumen por día,
    detalle de modelos y verificación en desplegables.

Solo librería estándar de Python 3.8+ (portable, sin pip install).
Uso:
  python clima_consenso.py              -> genera y abre el dashboard
  python clima_consenso.py --no-open    -> solo genera (tarea programada)
  python clima_consenso.py --verificar  -> fuerza la verificación ahora
  python clima_consenso.py --migrar CARPETA -> importa los datos de la versión de PC a ./datos

Variables de entorno (las usa GitHub Actions):
  CC_DATOS  carpeta de datos        (por defecto: ./datos junto al script)
  CC_HTML   archivo del dashboard   (por defecto: ./dashboard.html)
"""
import csv, glob, io, json, os, sys, time, statistics, webbrowser, urllib.request, urllib.parse
from datetime import datetime, date, timedelta

# Siempre hora de Ecuador, aunque el servidor esté en UTC (GitHub usa UTC)
os.environ["TZ"] = "America/Guayaquil"
if hasattr(time, "tzset"):
    time.tzset()

BASE  = os.path.dirname(os.path.abspath(sys.argv[0]))
DATOS = os.path.abspath(os.environ.get("CC_DATOS") or os.path.join(BASE, "datos"))
os.makedirs(os.path.join(DATOS, "historial"), exist_ok=True)
os.makedirs(os.path.join(DATOS, "observaciones"), exist_ok=True)

# ======================= CONFIGURACIÓN =======================
CIUDADES = {
    "Guayaquil": {"lat": -2.1700, "lon": -79.9224, "metar": "SEGU", "est": (-2.1574, -79.8836)},
    "Quito":     {"lat": -0.1807, "lon": -78.4678, "metar": "SEQM", "est": (-0.1292, -78.3575)},
}
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
VERSION             = 5          # versión de la lógica de datos (si cambia, se re-verifica)
DIAS_RECIENTES      = 21         # días recientes para la calibración que se adapta a la temporada
DIAS_PRUEBA         = 10         # últimos días reservados para examinar al sistema (no se usan para aprender)
MIN_COBERTURA_OBS   = 0.70       # si el aeropuerto reportó menos del 70% de las horas, no se actualiza
MIN_MODELOS         = 3          # mínimo de modelos con historial para actualizar
MIN_HORAS_REALES    = 72         # horas verificadas necesarias para publicar resultados reales
ZONA = "America/Guayaquil"

ARCHIVO_PESOS = os.path.join(DATOS, "pesos.json")
ARCHIVO_HTML  = os.path.abspath(os.environ.get("CC_HTML") or os.path.join(BASE, "dashboard.html"))
ARCHIVO_ERR   = os.path.join(DATOS, "errores.log")
HORAS_LOG     = 30          # horas futuras que se guardan del pronóstico publicado (para medir aciertos reales)
DIAS_HISTORIAL_LECTURA = DIAS_VERIFICACION + 5


def archivo_log(dia=None):
    """Un archivo pequeño por día: el repositorio crece poco aunque se actualice cada hora."""
    return os.path.join(DATOS, "historial", f"{dia or date.today():%Y-%m-%d}.csv")


def archivo_obs(mes):
    return os.path.join(DATOS, "observaciones", f"{mes}.json")

URL_FORECAST = "https://api.open-meteo.com/v1/forecast"
URL_PREVIOUS = "https://previous-runs-api.open-meteo.com/v1/forecast"
URL_IEM      = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
# =============================================================


def log_error(msg):
    print("  ERROR:", msg)
    lineas = []
    if os.path.exists(ARCHIVO_ERR):
        with open(ARCHIVO_ERR, encoding="utf-8") as f:
            lineas = f.readlines()[-499:]          # se guardan solo las últimas 500 líneas
    lineas.append(f"{datetime.now():%Y-%m-%d %H:%M} {msg}\n")
    with open(ARCHIVO_ERR, "w", encoding="utf-8") as f:
        f.writelines(lineas)


def http_get(url, params):
    full = url + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(full, headers={"User-Agent": "ClearSky/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", errors="replace")


def serie_modelo(hourly, variable, modelo):
    return hourly.get(f"{variable}_{modelo}") or (hourly.get(variable) if len(MODELOS) == 1 else None)


# ----------------------- PESOS -----------------------
def pesos_defecto():
    return {"actualizado": None, "version": None, "ciudades": {
        c: {"umbral": UMBRAL_DEFECTO, "pesos": {m: 1.0 for m in MODELOS}, "verificado": False}
        for c in CIUDADES}}


def cargar_pesos():
    if os.path.exists(ARCHIVO_PESOS):
        try:
            with open(ARCHIVO_PESOS, encoding="utf-8") as f:
                p = json.load(f)
            if "ciudades" in p:
                for c in CIUDADES:
                    p["ciudades"].setdefault(c, pesos_defecto()["ciudades"][c])
                return p
        except Exception as e:
            log_error(f"pesos.json ilegible, se regenera: {e}")
    return pesos_defecto()


def guardar_pesos(p):
    with open(ARCHIVO_PESOS, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2, ensure_ascii=False)


def toca_verificar(p):
    if not p.get("actualizado") or p.get("version") != VERSION:
        return True
    return datetime.now() - datetime.fromisoformat(p["actualizado"]) >= timedelta(hours=HORAS_ENTRE_VERIF)


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


# ----------------------- PRONÓSTICOS PASADOS -----------------------
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


# ----------------------- VERIFICACIÓN -----------------------
def verificar_ciudad(nombre, cfg):
    obs = descargar_obs(cfg["metar"], DIAS_VERIFICACION)
    prev = descargar_previos(cfg["est"][0], cfg["est"][1], DIAS_VERIFICACION)
    return calibrar(obs, prev)


def tabla_mezcla(tabla, alfa=0.5):
    """Punto medio entre la calibración y el valor sin ajustar (más prudente ante cambios de temporada)."""
    if not tabla:
        return []
    return [[float(x), round(alfa * calibrar_prob(x, tabla) + (1 - alfa) * x, 1)] for x in range(0, 101, 5)]


def opciones_calibracion(hs, pr, ob):
    """Candidatas de calibración aprendidas con las horas hs. pr: {hora: prob}, ob: {hora: 0/1}."""
    completa = ajustar_calibracion([pr[k] for k in hs], [ob[k] for k in hs])
    rec = hs[-24 * DIAS_RECIENTES:]
    reciente = ajustar_calibracion([pr[k] for k in rec], [ob[k] for k in rec])
    return {"sin_ajuste": [], "completa": completa, "reciente": reciente,
            "mezcla": tabla_mezcla(completa), "mezcla_reciente": tabla_mezcla(reciente)}


ORDEN_PRUDENCIA = ["mezcla_reciente", "reciente", "mezcla", "completa", "sin_ajuste"]


def calibrar(obs, prev):
    """Aprende pesos, umbral y calibración. Prueba varias formas de calibrar con días que NO usó
       para aprender y se queda con la que mejor acierta. Así se adapta a cambios de temporada."""
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

    # ---- 1) EXAMEN: aprender con los días viejos, evaluar con los últimos DIAS_PRUEBA días ----
    corte = (datetime.strptime(horas[-1], "%Y-%m-%dT%H:00") - timedelta(days=DIAS_PRUEBA)).strftime("%Y-%m-%dT%H:00")
    ent = [k for k in horas if k <= corte]
    pru = [k for k in horas if k > corte]
    examen, usar_pesos, modo = None, True, "completa"
    if len(ent) >= 24 * 14 and len(pru) >= 24 * 3:
        aj = ajustar(ent)
        real = [o_lluvia[k] for k in pru]
        base = sum(o_lluvia[k] for k in ent) / len(ent)
        resultados = {}                                   # (usar_pesos, modo) -> brier
        for con_pesos, pesos in ((True, aj["pesos"]), (False, iguales)):
            p_ent = probs(ent, aj["umbral"], pesos)
            p_pru = probs(pru, aj["umbral"], pesos)
            for nombre, tabla in opciones_calibracion(ent, p_ent, o_lluvia).items():
                resultados[(con_pesos, nombre)] = brier([calibrar_prob(p_pru[k], tabla) for k in pru], real)
        # el mejor; ante empate (diferencia < 0.001) se prefiere lo más prudente
        mejor_b = min(resultados.values())
        usar_pesos, modo = min((k for k, b in resultados.items() if b <= mejor_b + 0.001),
                               key=lambda k: (not k[0], ORDEN_PRUDENCIA.index(k[1])))
        b_sis = resultados[(usar_pesos, modo)]
        b_clima = brier([base * 100] * len(pru), real)
        b_crudo = resultados[(False, "sin_ajuste")]
        p_pru = probs(pru, aj["umbral"], aj["pesos"] if usar_pesos else iguales)
        p_ent = probs(ent, aj["umbral"], aj["pesos"] if usar_pesos else iguales)
        tabla_ex = opciones_calibracion(ent, p_ent, o_lluvia)[modo]
        examen = {"desde": pru[0][:10], "hasta": pru[-1][:10], "horas": len(pru), "horas_lluvia": sum(real),
                  "brier_clima": b_clima, "brier_crudo": b_crudo, "brier_sistema": b_sis,
                  "mejora_vs_clima_pct": round((1 - b_sis / b_clima) * 100) if b_clima else None,
                  "mejora_vs_crudo_pct": round((1 - b_sis / b_crudo) * 100) if b_crudo else None,
                  "tramos": tabla_tramos([calibrar_prob(p_pru[k], tabla_ex) for k in pru], real),
                  "usar_pesos": usar_pesos, "usar_calibracion": modo != "sin_ajuste",
                  "modo_calibracion": modo,
                  "brier_opciones": {f"{'pesos' if a else 'iguales'}/{b}": v for (a, b), v in sorted(resultados.items())}}

    # ---- 2) AJUSTE FINAL con todos los días, usando la combinación que ganó el examen ----
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


# ----------------------- ARCHIVO PERMANENTE DE OBSERVACIONES -----------------------
def cargar_archivo_obs():
    """Une todos los archivos mensuales: {ciudad: {hora: [lluvia, llovizna]}}."""
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
    """Guarda separado por mes; solo se reescriben los meses que cambiaron."""
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
        os.replace(tmp, ruta)            # escritura atómica


# ----------------------- SEGUIMIENTO REAL (lo que mostró el dashboard) -----------------------
def leer_pronosticos_publicados(ciudad):
    """Para cada hora, el pronóstico CONSENSO publicado entre 1 y 24 h antes (el más reciente)."""
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
                anticip = (hv - ej).total_seconds() / 3600
                if 1 <= anticip <= 24:
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


def verificar(p):
    print("Verificando modelos contra lo que realmente pasó...")
    arch = cargar_archivo_obs()
    for c, cfg in CIUDADES.items():
        try:
            obs = descargar_obs(cfg["metar"], DIAS_VERIFICACION)
            esperadas = DIAS_VERIFICACION * 24
            cobertura = len(obs) / esperadas
            # 1) guardar siempre las observaciones (memoria permanente, crece con el tiempo)
            arch.setdefault(c, {}).update({k: list(v) for k, v in obs.items()})
            # 2) controles de calidad antes de tocar la configuración
            if cobertura < MIN_COBERTURA_OBS:
                log_error(f"{c}: el aeropuerto solo reportó {cobertura:.0%} de las horas; se mantiene la configuración anterior")
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
                  f"HSS {r['consenso']['hss']} | examen ({ex.get('horas', 0)} h no vistas): "
                  f"Brier clima {ex.get('brier_clima')} / crudo {ex.get('brier_crudo')} / sistema {ex.get('brier_sistema')} "
                  f"| pesos {'sí' if ex.get('usar_pesos', True) else 'NO'}, calibración {ex.get('modo_calibracion', '-')}")
        except Exception as e:
            log_error(f"verificación {c}: {e} (se mantiene la configuración anterior)")
    guardar_archivo_obs(arch)
    for c in CIUDADES:                     # 3) resultados reales de lo que se publicó
        try:
            p["ciudades"][c]["real"] = seguimiento_real(c, arch.get(c, {}))
            rr = p["ciudades"][c]["real"]
            if rr["horas"]:
                print(f"  {c}: seguimiento real {rr['horas']} h publicadas y verificadas, Brier {rr['brier']} "
                      f"(clima {rr['brier_clima']})")
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


def consenso(data, cfg_ciudad):
    h = data["hourly"]
    pesos, umbral = cfg_ciudad["pesos"], cfg_ciudad["umbral"]
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
        sw = sum(pesos.get(m, 1.0) for m in lluvia)
        bruta = prob_consenso(lluvia, pesos, umbral)
        bruta_dz = prob_consenso(lluvia, pesos, UMBRAL_LLOVIZNA)
        prob = calibrar_prob(bruta, cfg_ciudad.get("tabla_lluvia"))
        prob_dz = max(calibrar_prob(bruta_dz, cfg_ciudad.get("tabla_llovizna")), prob)
        mm = mediana_ponderada(lluvia, pesos)
        acuerdo = max(bruta, 100 - bruta)
        filas.append({
            "hora": t, "prob": round(prob), "prob_dz": round(prob_dz), "prob_bruta": round(bruta),
            "mm": round(mm, 1), "min": round(min(lluvia.values()), 1), "max": round(max(lluvia.values()), 1),
            "temp": round(statistics.mean(temp.values()), 1) if temp else None,
            "nubes": round(statistics.mean(nubes.values())) if nubes else None,
            "conf": "Alta" if acuerdo >= 80 else "Media" if acuerdo >= 60 else "Baja",
            "n": len(lluvia), "detalle": {MODELOS[m]: round(v, 1) for m, v in lluvia.items()},
        })
    return filas


CAMPOS_LOG = ["ejecucion", "ciudad", "hora_valida", "modelo", "lluvia_mm", "prob_lluvia", "prob_bruta"]


def guardar_log(ciudad, filas):
    """Guarda solo el pronóstico combinado de las próximas horas (lo necesario para medir aciertos reales)."""
    ruta = archivo_log()
    nuevo = not os.path.exists(ruta)
    ahora = datetime.now()
    limite = (ahora + timedelta(hours=HORAS_LOG)).strftime("%Y-%m-%dT%H:00")
    desde = ahora.strftime("%Y-%m-%dT%H:00")
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        if nuevo:
            wr.writerow(CAMPOS_LOG)
        for r in filas:
            if desde <= r["hora"] <= limite:
                wr.writerow([ahora.strftime("%Y-%m-%d %H:%M"), ciudad, r["hora"], "CONSENSO",
                             r["mm"], r["prob"], r["prob_bruta"]])


# ----------------------- MIGRACIÓN DESDE LA VERSIÓN DE PC -----------------------
def migrar(origen):
    origen = os.path.abspath(origen)
    print(f"Migrando datos desde {origen} hacia {DATOS}")
    # 1) configuración aprendida
    src = os.path.join(origen, "pesos.json")
    if os.path.exists(src):
        with open(src, encoding="utf-8") as f:
            p = json.load(f)
        guardar_pesos(p)
        print("  pesos.json copiado")
    # 2) observaciones (archivo único -> mensual)
    src = os.path.join(origen, "observaciones_archivo.json")
    if os.path.exists(src):
        with open(src, encoding="utf-8") as f:
            leg = json.load(f)
        arch = cargar_archivo_obs()
        for c, horas in leg.items():
            arch.setdefault(c, {}).update(horas)
        guardar_archivo_obs(arch)
        print(f"  observaciones: {sum(len(h) for h in leg.values())} horas")
    # 3) historial (CSV grandes -> un archivo pequeño por día, solo el pronóstico combinado)
    filas_por_dia, n = {}, 0
    for ruta in sorted(glob.glob(os.path.join(origen, "historial_pronosticos*.csv"))):
        with open(ruta, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("modelo") != "CONSENSO" or not row.get("prob_lluvia"):
                    continue
                try:
                    ej = datetime.strptime(row["ejecucion"], "%Y-%m-%d %H:%M")
                    hv = datetime.strptime(row["hora_valida"], "%Y-%m-%dT%H:00")
                except Exception:
                    continue
                if 0 <= (hv - ej).total_seconds() / 3600 <= HORAS_LOG:
                    filas_por_dia.setdefault(ej.date(), []).append([row.get(c, "") for c in CAMPOS_LOG])
                    n += 1
    for dia, filas in filas_por_dia.items():
        ruta = archivo_log(dia)
        existentes = set()
        if os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as f:
                existentes = {tuple(r) for r in csv.reader(f)}
        with open(ruta, "a", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            if not existentes:
                wr.writerow(CAMPOS_LOG)
            for fila in filas:
                if tuple(fila) not in existentes:
                    wr.writerow(fila)
    print(f"  historial: {n} filas en {len(filas_por_dia)} días")
    print("Migración terminada.")


# =====================================================================
#                      INTERFAZ ESTILO DARK SKY
# =====================================================================
DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

ICONOS = {
    "sol": '<circle cx="12" cy="12" r="4.5" fill="#fbbf24"/><g stroke="#fbbf24" stroke-width="2" stroke-linecap="round">'
           '<path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/></g>',
    "luna": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" fill="#cbd5e1"/>',
    "nube": '<path d="M7 18h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6 11a3.5 3.5 0 0 0 1 7z" fill="#94a3b8"/>',
    "parcial_dia": '<circle cx="8" cy="8" r="3.5" fill="#fbbf24"/><g stroke="#fbbf24" stroke-width="1.6" stroke-linecap="round">'
                   '<path d="M8 1.5v1.5M1.5 8H3M3.4 3.4l1 1M12.6 3.4l-1 1"/></g>'
                   '<path d="M9 20h9a3.5 3.5 0 0 0 .4-6.97A5 5 0 0 0 8.6 13.5 3.3 3.3 0 0 0 9 20z" fill="#94a3b8"/>',
    "parcial_noche": '<path d="M13 9.5A5.5 5.5 0 0 1 6 2.5a5.5 5.5 0 1 0 7 7z" fill="#cbd5e1"/>'
                     '<path d="M9 20h9a3.5 3.5 0 0 0 .4-6.97A5 5 0 0 0 8.6 13.5 3.3 3.3 0 0 0 9 20z" fill="#94a3b8"/>',
    "llovizna": '<path d="M7 14h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6 7a3.5 3.5 0 0 0 1 7z" fill="#94a3b8"/>'
                '<g fill="#7dd3fc"><circle cx="8" cy="18" r="1"/><circle cx="12" cy="20" r="1"/><circle cx="16" cy="18" r="1"/></g>',
    "lluvia": '<path d="M7 13h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6 6a3.5 3.5 0 0 0 1 7z" fill="#64748b"/>'
              '<g stroke="#38bdf8" stroke-width="2" stroke-linecap="round"><path d="M8 16l-1.5 4M12 16l-1.5 4M16 16l-1.5 4"/></g>',
}


def icono(nombre, size=24):
    return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24">{ICONOS[nombre]}</svg>'


def es_noche(hora_iso):
    h = int(hora_iso[11:13])
    return h < 6 or h >= 18


def condicion(r):
    """Devuelve (clave_icono, texto, color_línea_tiempo)."""
    noche = es_noche(r["hora"])
    nub = r["nubes"] if r["nubes"] is not None else 50
    if r["prob"] >= 40:
        return "lluvia", "Lluvia probable", "#1d4ed8"
    if r["prob"] >= 20:
        return "lluvia", "Posible lluvia", "#3b82f6"
    if r["prob_dz"] >= 50:
        return "llovizna", "Llovizna", "#60a5fa"
    if nub >= 75:
        return "nube", "Nublado", "#64748b"
    if nub >= 35:
        return ("parcial_noche" if noche else "parcial_dia"), "Parcialmente nublado", "#94a3b8"
    return ("luna" if noche else "sol"), "Despejado", "#cbd5e1"


def nombre_dia(fecha_iso):
    d = date.fromisoformat(fecha_iso)
    hoy = date.today()
    if d == hoy: return "Hoy"
    if d == hoy + timedelta(days=1): return "Mañana"
    return DIAS_ES[d.weekday()]


def franja(hora_iso):
    h = int(hora_iso[11:13])
    if h < 6: return "en la madrugada"
    if h < 12: return "en la mañana"
    if h < 18: return "en la tarde"
    return "en la noche"


def intensidad(mm):
    if mm < 0.5: return "muy ligera"
    if mm < 2:   return "ligera"
    if mm < 5:   return "moderada"
    return "fuerte"


TXT_CONF = {"Alta": "Los pronósticos coinciden",
            "Media": "Hay algunas diferencias entre pronósticos",
            "Baja": "Los pronósticos no coinciden, podría cambiar"}


def nivel_fiabilidad(hss):
    if hss is None: return "En evaluación"
    if hss >= 0.4:  return "Buena"
    if hss >= 0.25: return "Aceptable"
    return "Limitada"


def titular(prox):
    """Frase principal en lenguaje sencillo para las próximas 12 horas."""
    if not prox:
        return "Sin datos por ahora", ""
    top = max(prox, key=lambda r: (r["prob"], r["mm"]))
    if top["prob"] >= 25:
        corte = max(20, top["prob"] * 0.6)
        idx = prox.index(top)
        i0 = idx
        while i0 > 0 and prox[i0 - 1]["prob"] >= corte: i0 -= 1
        i1 = idx
        while i1 < len(prox) - 1 and prox[i1 + 1]["prob"] >= corte: i1 += 1
        ini = prox[i0]["hora"][11:16]
        fin = (datetime.fromisoformat(prox[i1]["hora"]) + timedelta(hours=1)).strftime("%H:%M")
        palabra = "Lluvia probable" if top["prob"] >= 40 else "Posible lluvia"
        dia = "" if prox[idx]["hora"][:10] == date.today().isoformat() else " mañana"
        f0, f1 = franja(prox[i0]["hora"]), franja(prox[i1]["hora"])
        cuando = f0 if f0 == f1 else f"{f0[:-len(f0.split()[-1])]}{f0.split()[-1]} y {f1.split()[-2]} {f1.split()[-1]}"
        return (f"{palabra}{dia} {cuando}",
                f"{top['prob']}% de probabilidad entre las {ini} y las {fin}. "
                f"Si llueve, sería {intensidad(top['mm'])}. {TXT_CONF[top['conf']]}.")
    if top["prob"] >= 10:
        return ("Baja probabilidad de lluvia",
                f"Lo más probable es que no llueva. El momento con más opciones es {franja(top['hora'])} "
                f"({top['prob']}%){'' if top['hora'][:10] == date.today().isoformat() else ' de mañana'}.")
    dz = max(prox, key=lambda r: r["prob_dz"])
    if dz["prob_dz"] >= 40:
        return (f"Posible llovizna {franja(dz['hora'])}",
                f"Podría caer una llovizna ligera ({dz['prob_dz']}%). Lluvia de verdad, poco probable.")
    cond = condicion(prox[0])[1]
    return (cond, "No se espera lluvia en las próximas 24 horas.")

def svg_grafica(filas, ancho=900, alto=200):
    """Gráfica 72 h: área de probabilidad de lluvia, línea de llovizna, barras de mm."""
    if not filas:
        return ""
    n = len(filas)
    pl, pr, pt, pb = 34, 10, 14, 28
    w, h = ancho - pl - pr, alto - pt - pb
    dx = w / max(n - 1, 1)
    X = lambda i: pl + i * dx
    Y = lambda p: pt + h - h * p / 100
    mm_max = max(2.0, max(r["mm"] for r in filas))
    out = [f'<svg viewBox="0 0 {ancho} {alto}" class="graf">',
           '<defs><linearGradient id="gl" x1="0" y1="0" x2="0" y2="1">'
           '<stop offset="0" stop-color="#3b82f6" stop-opacity=".75"/>'
           '<stop offset="1" stop-color="#3b82f6" stop-opacity=".05"/></linearGradient></defs>']
    for p in (25, 50, 75, 100):
        out.append(f'<line x1="{pl}" x2="{ancho-pr}" y1="{Y(p):.1f}" y2="{Y(p):.1f}" class="grid"/>'
                   f'<text x="{pl-6}" y="{Y(p)+4:.1f}" class="ejey">{p}%</text>')
    ahora = datetime.now().strftime("%Y-%m-%dT%H:00")
    for i, r in enumerate(filas):
        hh = r["hora"][11:13]
        if hh == "00" and i:
            out.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{pt}" y2="{pt+h}" class="dia"/>'
                       f'<text x="{X(i)+4:.1f}" y="{pt+10}" class="etdia">{nombre_dia(r["hora"][:10])}</text>')
        if int(hh) % 6 == 0:
            out.append(f'<text x="{X(i):.1f}" y="{alto-8}" class="ejex">{hh}h</text>')
        if r["hora"] == ahora:
            out.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{pt}" y2="{pt+h}" class="ahora"/>')
        if r["mm"] > 0:
            bh = h * 0.45 * min(r["mm"], mm_max) / mm_max
            out.append(f'<rect x="{X(i)-dx*0.35:.1f}" y="{pt+h-bh:.1f}" width="{dx*0.7:.1f}" height="{bh:.1f}" class="mm"/>')
    area = " ".join(f"{X(i):.1f},{Y(r['prob']):.1f}" for i, r in enumerate(filas))
    out.append(f'<polygon points="{X(0):.1f},{Y(0):.1f} {area} {X(n-1):.1f},{Y(0):.1f}" fill="url(#gl)"/>')
    out.append(f'<polyline points="{area}" class="linea"/>')
    dz = " ".join(f"{X(i):.1f},{Y(r['prob_dz']):.1f}" for i, r in enumerate(filas))
    out.append(f'<polyline points="{dz}" class="lineadz"/>')
    for i, r in enumerate(filas):          # zonas invisibles con tooltip
        out.append(f'<rect x="{X(i)-dx/2:.1f}" y="{pt}" width="{dx:.1f}" height="{h}" fill="transparent">'
                   f'<title>{nombre_dia(r["hora"][:10])} {r["hora"][11:16]}\nLluvia: {r["prob"]}%  ·  Llovizna: {r["prob_dz"]}%'
                   f'\nSi llueve: {intensidad(r["mm"])}  ·  {r["temp"]}°C</title></rect>')
    out.append("</svg>")
    return "".join(out)


CORTOS = {"Parcialmente nublado": "Algo nublado", "Lluvia probable": "Lluvia", "Posible lluvia": "Lluvia"}


def html_linea_tiempo(prox24):
    """Barra de 24 h. Las horas con lluvia se agrupan en un solo tramo y los textos
       se acortan o se ocultan según el espacio, para que nunca queden cortados."""
    n = len(prox24)
    segs, etqs = [], []
    for i, r in enumerate(prox24):
        _, txt, col = condicion(r)
        segs.append(f'<div class="seg" style="background:{col}" '
                    f'title="{r["hora"][11:16]} · {txt} · lluvia {r["prob"]}%"></div>')
        if i % 2 == 0:
            etq = "Ahora" if i == 0 else r["hora"][11:13] + "h"
            etqs.append(f'<div class="tk" style="left:{i/n*100:.2f}%"><b>{etq}</b>'
                        f'<span>{round(r["temp"]) if r["temp"] is not None else "-"}°</span></div>')

    familia = lambda r: "lluvia" if r["prob"] >= 20 else condicion(r)[1]
    runs = []                                   # [familia, inicio, fin, prob_max]
    for i, r in enumerate(prox24):
        f = familia(r)
        if runs and runs[-1][0] == f:
            runs[-1][2] = i + 1
            runs[-1][3] = max(runs[-1][3], r["prob"])
        else:
            runs.append([f, i, i + 1, r["prob"]])
    unidos = []                                 # tramos cortos sin lluvia se unen al anterior
    for run in runs:
        if unidos and run[0] != "lluvia" and (run[2] - run[1] < 3 or unidos[-1][0] == run[0]) \
                and unidos[-1][0] != "lluvia":
            unidos[-1][2] = run[2]
        else:
            unidos.append(run)

    bloques = []
    for f, i0, i1, pmax in unidos:
        largo = i1 - i0
        texto = ("Lluvia probable" if pmax >= 40 else "Posible lluvia") if f == "lluvia" else f
        if largo >= 4:
            pass
        elif largo >= 2:
            texto = CORTOS.get(texto, texto if len(texto) <= 8 else "")
        else:
            texto = ""
        if texto:
            bloques.append(f'<div class="blq" style="left:{i0/n*100:.2f}%;width:{largo/n*100:.2f}%">{texto}</div>')

    ley = ('<div class="ley" style="margin-top:2px">'
           '<span><i style="background:#cbd5e1;height:8px"></i>Despejado</span>'
           '<span><i style="background:#94a3b8;height:8px"></i>Algo nublado</span>'
           '<span><i style="background:#64748b;height:8px"></i>Nublado</span>'
           '<span><i style="background:#60a5fa;height:8px"></i>Llovizna</span>'
           '<span><i style="background:#3b82f6;height:8px"></i>Posible lluvia</span>'
           '<span><i style="background:#1d4ed8;height:8px"></i>Lluvia probable</span></div>')
    return (f'<div class="tl"><div class="blqs">{"".join(bloques)}</div>'
            f'<div class="bar">{"".join(segs)}</div><div class="tks">{"".join(etqs)}</div>{ley}</div>')


def html_dias(filas):
    dias = {}
    ahora = datetime.now().strftime("%Y-%m-%dT%H:00")
    for r in filas:
        if r["hora"] >= ahora:
            dias.setdefault(r["hora"][:10], []).append(r)
    temps = [r["temp"] for r in filas if r["temp"] is not None]
    tmin_g, tmax_g = (min(temps), max(temps)) if temps else (0, 1)
    rng = max(tmax_g - tmin_g, 1)
    out = []
    for d, rs in dias.items():
        top = max(rs, key=lambda r: (r["prob"], r["mm"]))
        ts = [r["temp"] for r in rs if r["temp"] is not None]
        tmin, tmax = (min(ts), max(ts)) if ts else (0, 0)
        mm_tot = round(sum(r["mm"] for r in rs), 1)
        diurnas = [r for r in rs if not es_noche(r["hora"])] or rs
        ic = condicion(top)[0] if top["prob"] >= 20 or top["prob_dz"] >= 50 else \
             condicion(max(diurnas, key=lambda r: r["nubes"] or 0))[0]
        ic = {"luna": "sol", "parcial_noche": "parcial_dia"}.get(ic, ic)
        izq = (tmin - tmin_g) / rng * 100
        anc = max((tmax - tmin) / rng * 100, 3)
        pct = f'<span class="pp">{top["prob"]}%</span>' if top["prob"] >= 10 else '<span class="pp off">–</span>'
        out.append(f'<div class="drow"><div class="dn">{nombre_dia(d)}</div>{icono(ic, 26)}{pct}'
                   f'<div class="dmm">{("Lluvia " + intensidad(top["mm"])) if top["prob"] >= 20 else ""}</div><div class="tmin">{round(tmin)}°</div>'
                   f'<div class="trng"><div style="left:{izq:.1f}%;width:{anc:.1f}%"></div></div>'
                   f'<div class="tmax">{round(tmax)}°</div></div>')
    return "".join(out)


def html_modelos(filas, cfg):
    u = cfg["umbral"]
    pesos_n = {MODELOS[m]: w for m, w in cfg["pesos"].items()}
    cols = list(MODELOS.values())
    t = ['<p class="nota">Milímetros de lluvia que pronostica cada modelo internacional. En azul, los que anuncian lluvia; '
         'en gris, solo llovizna. El número bajo cada nombre es su peso: cuanto más alto, más ha acertado en esta ciudad.</p>'
         '<div class="scroll"><table><tr><th>Hora</th><th>Lluvia</th><th>Llovizna</th><th>mm</th><th>Rango</th>'
         '<th>Coinciden</th>' + "".join(f"<th>{c}<br><small>{pesos_n.get(c, 1.0)}</small></th>" for c in cols) + "</tr>"]
    dia = None
    for r in filas:
        if r["hora"][:10] != dia:
            dia = r["hora"][:10]
            t.append(f"<tr class='dsep'><td colspan='{6+len(cols)}'>{nombre_dia(dia)} · {dia}</td></tr>")
        celdas = "".join(
            "<td style='color:{}'>{}</td>".format(
                "#60a5fa" if (r["detalle"].get(m) or 0) >= u else
                "#94a3b8" if (r["detalle"].get(m) or 0) >= UMBRAL_LLOVIZNA else "#475569",
                r["detalle"].get(m, "-")) for m in cols)
        t.append(f"<tr><td>{r['hora'][11:16]}</td><td title='Sin calibrar: {r['prob_bruta']}%'><b>{r['prob']}%</b></td>"
                 f"<td>{r['prob_dz']}%</td><td>{r['mm']}</td><td>{r['min']}–{r['max']}</td><td>{r['conf']}</td>{celdas}</tr>")
    t.append("</table></div>")
    return "".join(t)


def fmt(v, suf=""):
    return "-" if v is None else f"{v}{suf}"


def html_verificacion(v, ciudad):
    if not v.get("verificado"):
        return ("<p class='nota'>Todavía estamos reuniendo datos para medir qué tan confiable es el pronóstico "
                "en esta ciudad. Vuelve a revisar mañana.</p>")
    c = v["consenso"]
    dijo = c["aciertos"] + c["falsas"]
    acierto_10 = round(c["aciertos"] / dijo * 10) if dijo else None
    detecta_10 = round(c["pod"] / 10) if c["pod"] is not None else None
    nivel = nivel_fiabilidad(c["hss"])
    aero = {"SEGU": "aeropuerto de Guayaquil", "SEQM": "aeropuerto de Quito (Tababela)"}.get(
        CIUDADES[ciudad]["metar"], "aeropuerto")
    resumen = [f"<p class='plano'>Fiabilidad actual: <b>{nivel}</b></p>",
               f"<p class='nota'>Todos los días comparamos lo que pronosticamos con lo que realmente llovió en el "
               f"{aero} durante los últimos {DIAS_VERIFICACION} días. Con eso ajustamos el pronóstico.</p><ul class='lst'>"]
    if acierto_10 is not None:
        resumen.append(f"<li>Cuando anunciamos lluvia, llovió <b>{acierto_10} de cada 10 veces</b>.</li>")
    if detecta_10 is not None:
        resumen.append(f"<li>Anticipamos <b>{detecta_10} de cada 10 lluvias</b> que ocurrieron.</li>")
    resumen.append(f"<li>Comparado con los {len(v['modelos'])} modelos por separado, "
                   + ("el pronóstico combinado es <b>el más acertado</b>." if c["hss"] >= max(m["hss"] for m in v["modelos"].values()) - 0.005
                      else "el pronóstico combinado está <b>entre los más acertados</b>.") + "</li></ul>")
    ex = v.get("examen")
    if ex and ex.get("mejora_vs_clima_pct") is not None:
        m = ex["mejora_vs_clima_pct"]
        frase = (f"fue <b>{m}% más preciso</b> que simplemente decir el promedio de siempre" if m > 0
                 else "<b>no superó</b> a simplemente decir el promedio de siempre; seguimos ajustando")
        resumen.append(f"<h4>Examen sin trampa</h4><p class='nota'>Guardamos los últimos {DIAS_PRUEBA} días sin usarlos "
                       f"para aprender, y luego comprobamos cómo le habría ido al pronóstico. Resultado: {frase}.</p>")
    rr = v.get("real") or {}
    if rr.get("horas", 0) >= MIN_HORAS_REALES:
        resumen.append(f"<h4>Resultados reales de lo que publicamos</h4><ul class='lst'>"
                       f"<li>Desde el {rr['desde']} hemos comprobado <b>{rr['horas']} horas</b> pronosticadas en esta página.</li>"
                       + (f"<li>Cuando mostramos 40% o más, llovió <b>{round(rr['anuncios_acertados']/rr['anuncios']*10)} de cada 10 veces</b>.</li>"
                          if rr["anuncios"] >= 5 else "")
                       + (f"<li>En total, {rr['mejora_vs_clima_pct']}% más preciso que decir siempre el promedio.</li>"
                          if rr.get("mejora_vs_clima_pct") is not None and rr["mejora_vs_clima_pct"] > 0 else "")
                       + "</ul>")
    else:
        resumen.append(f"<h4>Resultados reales de lo que publicamos</h4><p class='nota'>Estamos reuniendo datos: "
                       f"{rr.get('horas', 0)} de {MIN_HORAS_REALES} horas comprobadas. Cuando haya suficientes, "
                       f"aquí verás cuánto acertó exactamente lo que mostró esta página.</p>")
    cal = "".join(f"<div class='cb'><span>Decimos {k}</span><div><i style='width:{x['llovio_pct']}%'></i></div>"
                  f"<em>llovió {x['llovio_pct']}%</em></div>"
                  for k, x in v.get("calibracion_post", {}).items())
    resumen.append("<h4>¿Qué significan nuestros porcentajes?</h4>"
                   "<p class='nota'>Si decimos 30%, debería llover más o menos 3 de cada 10 veces que lo decimos. "
                   "Así nos ha ido:</p>" f"<div class='calib'>{cal}</div>")

    # ---- datos técnicos ----
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
    tecnico = (f"<details class='tec'><summary>Datos técnicos</summary>"
               f"<p class='nota'>Periodo {v['periodo']} · {v['horas']} h evaluadas · {v['horas_lluvia']} h con lluvia · "
               f"umbral {v['umbral']} mm/h · criterio {v['criterio']} · tolerancia ±{v.get('tolerancia_h', 0)} h{extra} · "
               f"Brier {v.get('brier_bruto')} → {v.get('brier_calibrado')} · cobertura aeropuerto {v.get('cobertura_obs_pct', '-')}%"
               + (f"<br>Examen {ex['desde']} a {ex['hasta']} ({ex['horas']} h, {ex['horas_lluvia']} con lluvia): Brier clima "
                  f"{ex['brier_clima']} · modelos sin ajustar {ex['brier_crudo']} · sistema {ex['brier_sistema']} · "
                  f"pesos {'aprobados' if ex['usar_pesos'] else 'rechazados (se usan iguales)'} · calibración "
                  f"{'aprobada' if ex['usar_calibracion'] else 'rechazada'}" if ex else "")
               + (f"<br>Seguimiento real: {rr['horas']} h · Brier {rr['brier']} (clima {rr['brier_clima']})" if rr.get("horas") else "")
               + "</p>" + "".join(t) +
               "<p class='nota'>POD: % de lluvias anticipadas · FAR: % de falsas alarmas · "
               "HSS: habilidad frente al azar (0 = azar, 1 = perfecto) · Peso: influencia del modelo en el pronóstico combinado.</p>"
               "</details>")
    return "".join(resumen) + tecnico

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#0b0f17;color:#e5e7eb;font-family:-apple-system,"SF Pro Text","Segoe UI",Roboto,Arial,sans-serif;
     -webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:16px 16px 40px}
.top{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.brand{font-weight:700;font-size:15px;letter-spacing:.3px;color:#9ca3af}
.tabs{display:flex;background:#151b26;border-radius:999px;padding:3px}
.tab{border:0;background:none;color:#9ca3af;padding:7px 16px;border-radius:999px;font-size:14px;cursor:pointer}
.tab.on{background:#2563eb;color:#fff}
.city{display:none}.city.on{display:block}
.hero{display:flex;align-items:center;gap:18px;margin:26px 0 6px}
.hero .t{font-size:64px;font-weight:200;line-height:1}
.hero .t sup{font-size:28px;vertical-align:top}
.hero h1{font-size:22px;font-weight:600;margin:0 0 6px}
.hero p{margin:0;color:#9ca3af;font-size:14px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0 0}
.chip{background:#151b26;border-radius:999px;padding:5px 11px;font-size:12px;color:#cbd5e1}
.chip b{color:#fff}
.card{background:#111723;border-radius:16px;padding:16px;margin-top:16px}
.card h3{margin:0 0 12px;font-size:13px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.6px}
.tl{position:relative;padding-top:20px}
.blqs{position:relative;height:18px;margin-top:-20px}
.blq{position:absolute;top:0;font-size:11px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-left:4px}
.bar{display:flex;height:22px;border-radius:6px;overflow:hidden}
.seg{flex:1;border-right:1px solid #0b0f17}
.tks{position:relative;height:38px}
.tk{position:absolute;top:6px;font-size:11px;color:#9ca3af;display:flex;flex-direction:column;align-items:flex-start}
.tk b{font-weight:500}.tk span{color:#e5e7eb;font-size:13px;margin-top:2px}
.gwrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
.graf{width:100%;min-width:640px;height:auto;display:block}
.graf .grid{stroke:#1f2937;stroke-width:1}
.graf .dia{stroke:#374151;stroke-dasharray:3 3}
.graf .ahora{stroke:#f59e0b;stroke-width:1.5}
.graf .linea{fill:none;stroke:#60a5fa;stroke-width:2}
.graf .lineadz{fill:none;stroke:#94a3b8;stroke-width:1.3;stroke-dasharray:4 3}
.graf .mm{fill:#1e40af;opacity:.55}
.graf text{fill:#6b7280;font-size:11px}
.graf .ejey{text-anchor:end}.graf .ejex{text-anchor:middle}
.graf .etdia{fill:#9ca3af;font-weight:600}
.ley{display:flex;gap:14px;font-size:12px;color:#9ca3af;margin-top:6px;flex-wrap:wrap}
.ley i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:5px}
.drow{display:grid;grid-template-columns:84px 30px 44px 104px 32px 1fr 32px;align-items:center;gap:8px;padding:10px 0;
      border-top:1px solid #1a2230;font-size:15px}
.drow:first-child{border-top:0}
.dn{font-weight:500}.pp{color:#60a5fa;font-size:13px;font-weight:600}.pp.off{color:#4b5563}
.dmm{color:#6b7280;font-size:12px}.tmin{color:#9ca3af;text-align:right}.tmax{text-align:left}
.trng{position:relative;height:6px;background:#1f2937;border-radius:3px}
.trng div{position:absolute;top:0;height:6px;border-radius:3px;background:linear-gradient(90deg,#60a5fa,#fbbf24)}
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
h4{font-size:12px;color:#9ca3af;font-weight:600;margin:12px 0 6px}
.calib .cb{display:grid;grid-template-columns:110px 1fr 80px;gap:8px;align-items:center;font-size:12px;margin:4px 0}
.calib .cb div{height:8px;background:#1f2937;border-radius:4px;overflow:hidden}
.calib .cb i{display:block;height:8px;background:#3b82f6}
.calib em{color:#9ca3af;font-style:normal}
.plano{font-size:16px;margin:6px 0}.lst{font-size:14px;line-height:1.7;padding-left:18px;margin:6px 0}
details.tec{background:#0d121b;margin-top:14px}
.pie{font-size:11px;color:#6b7280;margin-top:22px;line-height:1.6}
@media(max-width:560px){
  .hero .t{font-size:52px}.hero h1{font-size:19px}
  .drow{grid-template-columns:62px 26px 38px 26px 1fr 28px;gap:7px;font-size:14px}.dmm{display:none}
  .tk:nth-child(even){display:none}
}
"""


def html(resultados, p):
    ahora_txt = datetime.now().strftime("%d/%m/%Y %H:%M")
    ahora = datetime.now().strftime("%Y-%m-%dT%H:00")
    ciudades = list(resultados)
    tabs = "".join(f'<button class="tab{" on" if i == 0 else ""}" onclick="ver({i})">{c}</button>'
                   for i, c in enumerate(ciudades))
    secciones = []
    for i, ciudad in enumerate(ciudades):
        filas = resultados[ciudad]
        cfg = p["ciudades"][ciudad]
        prox = [r for r in filas if r["hora"] >= ahora]
        if not prox:
            continue
        actual = prox[0]
        ic, cond_txt, _ = condicion(actual)
        tit, sub = titular(prox[:24])
        hoy = [r for r in filas if r["hora"][:10] == date.today().isoformat() and r["temp"] is not None]
        nivel = nivel_fiabilidad(cfg.get("consenso", {}).get("hss")) if cfg.get("verificado") else "En evaluación"
        txt_prox = (f'{cond_txt.lower()} ({actual["prob"]}%)' if actual['prob'] >= 20
                    else f'{cond_txt}, sin lluvia')
        chips = (f'<span class="chip">Próxima hora: <b>{txt_prox}</b></span>'
                 + (f'<span class="chip">Hoy: <b>{round(max(r["temp"] for r in hoy))}°</b> / {round(min(r["temp"] for r in hoy))}°</span>' if hoy else "")
                 + f'<span class="chip">Fiabilidad del pronóstico: <b>{nivel}</b></span>')
        secciones.append(f"""
<section class="city{' on' if i == 0 else ''}">
  <div class="hero">{icono(ic, 72)}
    <div><div class="t">{round(actual['temp']) if actual['temp'] is not None else '-'}<sup>°</sup></div></div>
    <div><h1>{tit}</h1><p>{sub}</p></div>
  </div>
  <div class="chips">{chips}</div>

  <div class="card"><h3>Próximas 24 horas</h3>{html_linea_tiempo(prox[:24])}</div>

  <div class="card"><h3>Probabilidad de lluvia · próximos 3 días</h3><div class="gwrap">{svg_grafica(prox)}</div>
    <div class="ley"><span><i style="background:#60a5fa"></i>Lluvia</span>
    <span><i style="background:#94a3b8"></i>Llovizna</span>
    <span><i style="background:#1e40af;height:8px"></i>Cantidad esperada</span>
    <span><i style="background:#f59e0b"></i>Ahora</span></div></div>

  <div class="card"><h3>Próximos días</h3>{html_dias(filas)}</div>

  <details><summary>Qué dice cada modelo meteorológico</summary>{html_modelos(prox, cfg)}</details>
  <details><summary>¿Qué tan confiable es este pronóstico?</summary>
    {html_verificacion(cfg, ciudad)}</details>
</section>""")
    doc = f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="1800">
<title>ClearSky</title><style>{CSS}</style></head><body><div class="wrap">
<div class="top"><div class="brand">ClearSky</div><div class="tabs">{tabs}</div></div>
{''.join(secciones)}
<div class="pie">Actualizado: {ahora_txt}<br>
¿Cómo funciona? Combinamos {len(MODELOS)} modelos meteorológicos internacionales y damos más peso a los que mejor han
acertado en cada ciudad. Cada día comparamos el pronóstico con lo que realmente llovió y ajustamos los porcentajes,
para que un 30% signifique de verdad 3 de cada 10.<br>
<b>Lluvia</b>: la que moja y deja las calles mojadas. <b>Llovizna</b>: gotas finas o garúa.
Pasa el dedo o el mouse sobre la gráfica para ver cada hora.</div>
<script>
function ver(i){{document.querySelectorAll('.tab').forEach((t,j)=>t.classList.toggle('on',i===j));
document.querySelectorAll('.city').forEach((c,j)=>c.classList.toggle('on',i===j));
try{{localStorage.setItem('cc_ciudad',i)}}catch(e){{}}}}
try{{var g=localStorage.getItem('cc_ciudad');if(g!==null&&document.querySelectorAll('.tab')[g])ver(+g)}}catch(e){{}}
</script></body></html>"""
    with open(ARCHIVO_HTML, "w", encoding="utf-8") as f:
        f.write(doc)


# ----------------------- MAIN -----------------------
def main():
    if "--migrar" in sys.argv:
        i = sys.argv.index("--migrar")
        if i + 1 >= len(sys.argv):
            sys.exit("Uso: python clima_consenso.py --migrar CARPETA_DE_LA_VERSION_ANTERIOR")
        migrar(sys.argv[i + 1])
        return
    p = cargar_pesos()
    if "--verificar" in sys.argv or toca_verificar(p):
        verificar(p)
    resultados = {}
    for ciudad, cfg in CIUDADES.items():
        print(f"Descargando pronóstico {ciudad}...")
        try:
            filas = consenso(descargar(cfg["lat"], cfg["lon"]), p["ciudades"][ciudad])
            resultados[ciudad] = filas
            guardar_log(ciudad, filas)
            print(f"  OK: {len(filas)} horas, {filas[0]['n'] if filas else 0} modelos")
        except Exception as e:
            log_error(f"pronóstico {ciudad}: {e}")
    if not resultados:
        log_error("no se pudo descargar ningún pronóstico; se conserva el dashboard anterior")
        sys.exit(1)                      # falla visible en GitHub (llega un correo de aviso)
    os.makedirs(os.path.dirname(ARCHIVO_HTML), exist_ok=True)
    html(resultados, p)
    print(f"Dashboard: {ARCHIVO_HTML}")
    if "--no-open" not in sys.argv:
        webbrowser.open("file://" + ARCHIVO_HTML)


if __name__ == "__main__":
    main()
