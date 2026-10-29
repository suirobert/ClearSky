# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
"""Red simulada para probar clima_consenso.py sin internet.

Reemplaza las descargas (Open-Meteo, aeropuertos METAR, NOAA, historial de la rama "ahora") por datos inventados pero
con una "verdad" conocida: llueve más por la tarde y, cuando llueve, los modelos suelen acertar. Es determinista: la misma
fecha produce los mismos datos."""
import csv, io, json, math, os, random
from datetime import date, datetime, timedelta, timezone

SIN_METAR = ("SESD", "SEAM", "SETR", "SETM")           # aeropuertos que "no publican" (como pasa en la realidad)


def verdad(t, lat=0, lon=0):
    """(¿llovió?, ¿hubo tormenta?) en la hora t, de forma determinista."""
    r = random.Random(f"{t}{round(lat)}{round(lon)}"); h = int(t[11:13])
    llovio = r.random() < 0.06 + (0.25 if 14 <= h <= 20 else 0)
    return llovio, bool(llovio and r.random() < 0.35)


def horas(pasado, futuro):
    ini = datetime.combine(date.today() - timedelta(days=pasado), datetime.min.time())
    return [(ini + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(24 * (pasado + futuro))]


def temp_real(t):
    return 25 + 6 * math.sin((int(t[11:13]) - 9) / 24 * 2 * math.pi)


def valor(t, modelo, lat, lon, var, plazo=1):
    llovio, tormenta = verdad(t, lat, lon)
    r = random.Random(f"{t}{modelo}{var}{lat}{plazo}"); h = int(t[11:13])
    acierta = r.random() < 0.8 - 0.05 * plazo
    if var == "precipitation":
        return round(r.uniform(.5, 6) if (llovio if acierta else r.random() < .15) else r.uniform(0, .3), 1)
    if var == "weather_code": return 95 if tormenta and acierta else (61 if llovio else r.choice([1, 2, 3]))
    if var == "temperature_2m": return round(temp_real(t) + 1.5 + r.uniform(-.8, .8), 1)       # los modelos dan 1,5° de más
    if var.startswith("cloud_cover"): return r.randint(0, 90)
    if var == "shortwave_radiation": return round(900 * max(0, math.cos((h - 12.5) / 12 * math.pi)) * .8)
    if var == "wind_gusts_10m": return r.randint(15, 48)
    if var == "uv_index": return round(max(0, 11 * math.cos((h - 12.5) / 6.5 * math.pi / 2)), 1) if 7 <= h <= 18 else 0
    if var == "relative_humidity_2m": return 72 if 10 <= h <= 17 else 88
    if var == "wind_speed_10m": return r.randint(5, 24)
    return 0


def respuesta_modelos(cc, lat, lon, variables, ts):
    hh = {"time": ts}
    for v in variables:
        base, plazo = v, 1
        if "_previous_day" in v: base, plazo = v.split("_previous_day")[0], int(v.split("_previous_day")[1])
        for m in cc.MODELOS: hh[f"{v}_{m}"] = [valor(t, m, lat, lon, base, plazo) for t in ts]
    return {"latitude": lat, "longitude": lon, "elevation": 10, "utc_offset_seconds": -18000, "hourly": hh}


def texto_metar(estacion):
    out = ["station,valid,tmpf,relh,wxcodes"]
    if estacion in SIN_METAR: return out[0]
    for t in horas(47, 1)[:-24]:
        llovio, tormenta = verdad(t); f = temp_real(t) * 9 / 5 + 32; h = int(t[11:13])
        out.append(f"X,{t.replace('T', ' ')},{f:.1f},{70 if 10 <= h <= 17 else 85},{('TSRA' if tormenta else 'RA') if llovio else 'M'}")
    return "\n".join(out)


# ---------------- satélite (NOAA): carpetas de S3 y un archivo NetCDF de "disco completo" ----------------
def _arbol(clave):
    partes = clave.split("/")
    arbol = {"": (["BLEND/", "G16/", "G18/", "G19/", "Himawari-9/"], [])}
    for i in range(1, len(partes) - 1): arbol["/".join(partes[:i]) + "/"] = (["/".join(partes[:i + 1]) + "/"], [])
    return arbol


def _xml_s3(arbol, prefijo):
    carpetas, archivos = arbol.get(prefijo, ([], []))
    return ('<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">' +
            "".join(f"<CommonPrefixes><Prefix>{c}</Prefix></CommonPrefixes>" for c in carpetas) +
            "".join(f"<Contents><Key>{a['key']}</Key><LastModified>{a['mod']}</LastModified><Size>{a['size']}</Size></Contents>" for a in archivos) +
            "</ListBucketResult>")


def hacer_netcdf(ruta):
    import netCDF4, numpy as np
    n = 900
    with netCDF4.Dataset(ruta, "w") as ds:
        ds.createDimension("y", n); ds.createDimension("x", n)
        yy, xx = np.meshgrid(np.linspace(81, -81, n), np.linspace(-81, 81, n), indexing="ij"); fuera = np.hypot(yy, xx) > 81
        la = np.where(fuera, np.nan, yy); lo = np.where(fuera, np.nan, (xx - 75.2 + 180) % 360 - 180)     # disco completo centrado en -75,2° (GOES-19)
        a = ds.createVariable("latitude", "f4", ("y", "x"), fill_value=-999.); a[:] = np.ma.masked_invalid(la)
        b = ds.createVariable("longitude", "f4", ("y", "x"), fill_value=-999.); b[:] = np.ma.masked_invalid(lo)
        v = ds.createVariable("RRQPE", "f4", ("y", "x"), fill_value=-999.)
        with np.errstate(invalid="ignore"): llu = np.where((np.abs(la + 0.2) < 0.5) & (np.abs(lo + 78.5) < 0.5), 6.0, np.nan)
        v[:] = np.ma.masked_invalid(llu)


class _Respuesta:
    def __init__(self, datos): self.f = io.BytesIO(datos)
    def read(self, n=-1): return self.f.read(n)
    def __enter__(self): return self
    def __exit__(self, *a): pass


def instalar(cc, carpeta_tmp, hist=None):
    """Sustituye la red de `cc` por datos simulados. Devuelve una función que deshace el cambio."""
    ahora_utc = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=25)
    clave = f"G19/RRQPE/INST/{ahora_utc:%Y/%m/%d/%H}/RRQPE-INST-GLB_v1r1_g19_s{ahora_utc:%Y%m%d%H%M}000_e.nc"
    arbol = _arbol(clave)
    arbol["/".join(clave.split("/")[:-1]) + "/"] = ([], [{"key": clave, "mod": f"{ahora_utc:%Y-%m-%dT%H:%M}:10.000Z", "size": 1234}])
    nc = os.path.join(carpeta_tmp, "goes.nc")
    try:
        hacer_netcdf(nc); hay_nc = True
    except Exception:                                    # sin netCDF4/numpy: la página se publica sin la capa de lluvia satelital
        hay_nc = False

    def http_get(url, params=None, *a, **k):
        if url == cc.URL_IEM: return texto_metar(dict(params)["station"] if isinstance(params, dict) else params[0][1])
        if url == cc.URL_PREVIOUS: return json.dumps(respuesta_modelos(cc, params["latitude"], params["longitude"], params["hourly"].split(","), horas(params["past_days"], 1)))
        if url == cc.URL_FORECAST:
            if "daily" in params:
                n = len(str(params["latitude"]).split(",")); dias = [(date.today() - timedelta(days=d)).isoformat() for d in range(30, -1, -1)]
                return json.dumps([{"daily": {"time": dias, "temperature_2m_max": [32.5] * len(dias)}} for _ in range(n)])
            ts = horas(0, params["forecast_days"]); v = params["hourly"].split(",")
            if isinstance(params["latitude"], str) and "," in params["latitude"]:
                return json.dumps([respuesta_modelos(cc, float(a), float(b), v, ts) for a, b in zip(params["latitude"].split(","), params["longitude"].split(","))])
            return json.dumps(respuesta_modelos(cc, params["latitude"], params["longitude"], v, ts))
        if url == cc.URL_ERR + "/":
            if not hay_nc: raise RuntimeError("sin NetCDF en esta prueba")
            return _xml_s3(arbol, params["prefix"])
        if url == cc.URL_HIST:
            if hist is None: raise RuntimeError("404: todavía no hay hist.json")
            return json.dumps(hist)
        raise RuntimeError("red no simulada: " + str(url))

    def urlopen(req, timeout=0):
        u = req.full_url if hasattr(req, "full_url") else req
        if "/export" in u: return _Respuesta(b"[]")
        if u.startswith(cc.URL_ERR) and hay_nc: return _Respuesta(open(nc, "rb").read())
        raise RuntimeError("sin red: " + u)

    guardado = (cc.http_get, cc.urllib.request.urlopen, cc.time.sleep)
    cc.http_get, cc.urllib.request.urlopen, cc.time.sleep = http_get, urlopen, lambda s: None
    def restaurar():
        cc.http_get, cc.urllib.request.urlopen, cc.time.sleep = guardado
    return restaurar


def sembrar_historial(cc, dias=8):
    """Pronósticos publicados en los últimos días (base del tablero «Cómo nos fue»)."""
    for d in range(1, dias + 1):
        dia = date.today() - timedelta(days=d); ruta = cc.archivo_log(dia); os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(cc.CAMPOS_LOG)
            for c in cc.CIUDADES:
                for h in range(24):
                    hv = datetime.combine(dia, datetime.min.time()) + timedelta(hours=h); k = hv.strftime("%Y-%m-%dT%H:00")
                    llovio, _ = verdad(k); r = random.Random(f"p{c}{k}")
                    prob = (55 + r.randint(-10, 30)) if llovio else r.randint(0, 25)
                    w.writerow([(hv - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M"), c, k, "CONSENSO", 0, prob, prob])
