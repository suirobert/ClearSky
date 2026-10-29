# -*- coding: utf-8 -*-
"""
ClearSky · capa AHORA y OFICIAL (v16)
-------------------------------------
Se ejecuta cada 10 minutos (workflow ahora.yml) y genera dos archivos:

  ahora.json  (lo lee la página y el servicio de avisos)
    - lluvia:     lluvia que el satélite estima en este momento (NOAA Enterprise Rain Rate, GOES-19)
    - movimiento: hacia dónde se mueve la lluvia (por cuadros de 2°), medido con 3 imágenes (t, t-30, t-60 min).
                  Solo se incluyen los cuadros donde la extrapolación demostró ser mejor que "no se mueve".
    - rayos:      descargas eléctricas de los últimos 20-30 min (GOES-19 GLM, NOAA)
    - oficial:    último boletín meteorológico del INAMHI

  hist.json  (lo usa el script horario para VERIFICAR con observaciones propias)
    - lluvia por ciudad cada 10 min (máximo en ~5 km) y rayos por ciudad en cada franja de 10 min completa.
    Se conservan 72 h; el script horario los pasa a datos/observado/ para siempre.

Cada parte falla por separado: si una fuente no responde, el motivo queda en "errores" y las demás siguen.
Uso:  python ahora.py [carpeta_de_salida_o_ruta_ahora.json]
"""
import io, json, math, os, re, sys, base64, tempfile, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

REPO = os.environ.get("GITHUB_REPOSITORY") or "suirobert/ClearSky"
REPO_URL = f"https://github.com/{REPO}"
URL_HIST = f"https://raw.githubusercontent.com/{REPO}/ahora/hist.json"
OFFSET_S = -5 * 3600                       # Ecuador continental y Galápagos: UTC-5
URL_ERR = "https://noaa-enterprise-rainrate-pds.s3.amazonaws.com"
URL_GOES19 = "https://noaa-goes19.s3.amazonaws.com"
URL_PDF_INAMHI = "https://www.inamhi.gob.ec/pronostico/advertencia.pdf"
URL_PAG_INAMHI = "https://servicios.inamhi.gob.ec/alertas/"

# --- lluvia satelital ---
AHORA_CAJA = (-5.5, 2.5, -92.5, -75.0)      # sur, norte, oeste, este
AHORA_PASO = 0.1                            # cuadrícula que se publica (~11 km)
PASO_FINO = 0.05                            # cuadrícula para medir el movimiento (~5,5 km)
AHORA_MAX_H = 3
SAT_PREFERIDOS = ["G19", "GOES-19", "BLEND", "G16", "GOES-16", "G18", "GOES-18"]   # carpetas reales: G19, BLEND, G16, G18

# --- movimiento de la lluvia ---
MOV_DT_MIN = 30                             # separación entre imágenes
MOV_TOL_MIN = 7                             # tolerancia al buscar la imagen de hace 30 / 60 min
MOV_TILE_DEG = 2.0                          # tamaño de cada cuadro de movimiento
MOV_MARGEN = 12                             # celdas extra alrededor del cuadro (la lluvia viene de fuera)
MOV_MAX_DESP = 8                            # desplazamiento máximo buscado (celdas de 0,05° en 30 min = hasta ~90 km/h)
MOV_MIN_CELDAS = 20                         # celdas con >= 1 mm/h necesarias en el cuadro
MOV_MIN_MEJORA = 0.15                       # el movimiento debe reducir el error al menos 15% frente a "no se mueve"
MOV_MIN_CSI_GANANCIA = 0.05                 # la extrapolación debe superar a la persistencia por esta diferencia (CSI)

# --- rayos ---
RAYOS_CAJA = (-8.0, 5.0, -96.0, -72.0)     # algo más amplia: tormentas en el mar y en la frontera
RAYOS_VENTANA_MIN = 30
RAYOS_RECIENTE_MIN = 20
RAYOS_MAX_EDAD_MIN = 15                    # si el archivo más nuevo es más viejo, no se muestra como "ahora"
RAYOS_RADIO_KM = 25
RAYOS_CELDA = 0.05                         # ~5,5 km
RAYOS_MAX_CELDAS = 1500
RAYOS_ARCHIVOS_FRANJA = 27                 # de 30 archivos (20 s) por franja de 10 min: mínimo para darla por completa

HIST_HORAS = 72

CIUDADES = {
    "Guayaquil": (-2.1894, -79.8891), "Quito": (-0.1807, -78.4678), "Manta": (-0.9677, -80.7089),
    "Salinas": (-2.2147, -80.9588), "Esmeraldas": (0.9592, -79.6540), "Cuenca": (-2.9001, -79.0059),
    "Machala": (-3.2581, -79.9554), "Portoviejo": (-1.0546, -80.4545), "Santo Domingo": (-0.2530, -79.1716),
    "Loja": (-3.9931, -79.2042), "Ambato": (-1.2491, -78.6168), "Latacunga": (-0.9352, -78.6155),
    "Coca": (-0.4664, -76.9871), "Lago Agrio": (0.0849, -76.8828), "Macas": (-2.3087, -78.1171),
    "Galápagos": (-0.7432, -90.3136),
}
ORDEN = list(CIUDADES)


# =====================================================================================================
#  Utilidades
# =====================================================================================================
def ahora_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def a_local(utc):
    return utc + timedelta(seconds=OFFSET_S)


def iso_local(utc):
    return a_local(utc).strftime("%Y-%m-%dT%H:%M")


def http_get(url, params=None, binario=False, intentos=3, timeout=60):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"ClearSky (+{REPO_URL})"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                datos = r.read()
            return datos if binario else datos.decode("utf-8", errors="replace")
        except Exception as e:
            ultimo = e
            time.sleep(1.5 * (i + 1))
    raise ultimo


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


def s3_listar(base, prefijo, delimitador=True):
    """Lista un nivel de un bucket público de NOAA (sin cuenta). Devuelve (subcarpetas, archivos)."""
    import xml.etree.ElementTree as ET
    carpetas, archivos, token = [], [], None
    for _ in range(30):
        params = {"list-type": "2", "prefix": prefijo}
        if delimitador:
            params["delimiter"] = "/"
        if token:
            params["continuation-token"] = token
        raiz = ET.fromstring(http_get(base + "/", params, intentos=2))
        token = None
        for e in raiz:
            tag = e.tag.split("}")[-1]
            hijos = {h.tag.split("}")[-1]: h.text for h in e}
            if tag == "CommonPrefixes":
                carpetas.append(hijos.get("Prefix"))
            elif tag == "Contents":
                archivos.append({"key": hijos.get("Key"), "mod": hijos.get("LastModified") or "", "size": int(hijos.get("Size") or 0)})
            elif tag == "NextContinuationToken":
                token = e.text
        if not token:
            break
    return carpetas, archivos


# =====================================================================================================
#  1. LLUVIA ESTIMADA POR SATÉLITE (NOAA Enterprise Rain Rate) + MOVIMIENTO
# =====================================================================================================
def _inicio_err(key):
    m = re.search(r"_s(\d{12})", key)
    return datetime.strptime(m.group(1), "%Y%m%d%H%M") if m else None


def err_recientes():
    """Archivos de lluvia instantánea más recientes (de la carpeta de la hora actual y la anterior), del más nuevo al más viejo."""
    carpetas, _ = s3_listar(URL_ERR, "")
    nombre = lambda c: c.rstrip("/").rsplit("/", 1)[-1]
    raices = [c for p in SAT_PREFERIDOS for c in carpetas if nombre(c).upper().startswith(p.upper())]
    if not raices:
        raise RuntimeError("no hay carpeta de GOES/BLEND en NOAA; carpetas: " + ", ".join(nombre(c) for c in carpetas))
    errores = []
    for raiz in raices[:3]:
        pref, recorrido = raiz, [nombre(raiz)]
        try:
            for _ in range(8):
                carpetas, archivos = s3_listar(URL_ERR, pref)
                nc = [a for a in archivos if a["key"].endswith(".nc")]
                inst = [a for a in nc if "INST" in a["key"].upper()]
                lista = inst or nc
                if lista:
                    # la hora anterior (los cuadros de hace 30 y 60 min pueden estar ahí)
                    m = re.search(r"(\d{4})/(\d{2})/(\d{2})/(\d{2})/?$", pref)
                    if m:
                        t = datetime(*map(int, m.groups())) - timedelta(hours=1)
                        previa = pref[:m.start()] + f"{t:%Y/%m/%d/%H}/"
                        try:
                            _, a2 = s3_listar(URL_ERR, previa)
                            n2 = [a for a in a2 if a["key"].endswith(".nc")]
                            lista = lista + ([a for a in n2 if "INST" in a["key"].upper()] or n2)
                        except Exception:
                            pass
                    return sorted(lista, key=lambda a: (a["mod"], a["key"]), reverse=True)
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


def leer_lluvia_nc(ruta, pasos=(AHORA_PASO,)):
    """Lluvia instantánea (mm/h) promediada en una o varias cuadrículas sobre Ecuador (una por cada paso, en grados).
       Acepta latitud/longitud 1D o 2D (disco completo) y longitudes 0-360. Devuelve (lista_de_rejillas, puntos)."""
    nc4, np = importar_netcdf()
    s0, n0, w0, e0 = AHORA_CAJA
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
                raise RuntimeError("el archivo no cubre Ecuador")
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
    salida = []
    for paso in pasos:
        ny, nx = round((n0 - s0) / paso), round((e0 - w0) / paso)
        gy = np.clip(((n0 - LA) / paso).astype(int), 0, ny - 1)
        gx = np.clip(((LO - w0) / paso).astype(int), 0, nx - 1)
        suma, cuenta = np.zeros(ny * nx), np.zeros(ny * nx)
        np.add.at(suma, gy * nx + gx, datos)
        np.add.at(cuenta, gy * nx + gx, 1)
        salida.append(np.where(cuenta > 0, suma / np.maximum(cuenta, 1), 0).reshape(ny, nx))
    return salida, float(LA.size)


# ---------------------- movimiento ----------------------
def _fuente(np, F, y0, y1, x0, x1, dy, dx):
    """Recorte [y0:y1, x0:x1] de F desplazado (dy, dx): devuelve F[y-dy, x-dx] (ceros fuera del mapa)."""
    ny, nx = F.shape
    out = np.zeros((y1 - y0, x1 - x0), F.dtype)
    ys0, ys1, xs0, xs1 = y0 - dy, y1 - dy, x0 - dx, x1 - dx
    cy0, cy1, cx0, cx1 = max(ys0, 0), min(ys1, ny), max(xs0, 0), min(xs1, nx)
    if cy0 < cy1 and cx0 < cx1:
        out[cy0 - ys0:cy1 - ys0, cx0 - xs0:cx1 - xs0] = F[cy0:cy1, cx0:cx1]
    return out


def _parabola(a, b, c):
    """Mínimo sub-celda de una parábola por tres puntos (a, b, c) en -1, 0, +1."""
    d = a - 2 * b + c
    return 0.0 if d <= 1e-12 else max(-0.5, min(0.5, 0.5 * (a - c) / d))


def desplazamientos(A, B, paso, nty, ntx, tile_cells):
    """Para cada cuadro, cuánto se movió la lluvia entre B (antes) y A (ahora): (dy, dx) en celdas, positivo = al sur / al este.
       Devuelve una lista de listas con dicts {dy, dx, mejora, celdas} o None si no hay lluvia suficiente o no hay mejora."""
    import numpy as np
    g = lambda F: np.log1p(np.clip(F, 0, 30))
    GA, GB = g(A), g(B)
    ny, nx = A.shape
    desp = np.arange(-MOV_MAX_DESP, MOV_MAX_DESP + 1)
    res = [[None] * ntx for _ in range(nty)]
    for ty in range(nty):
        for tx in range(ntx):
            y0, y1 = ty * tile_cells, min((ty + 1) * tile_cells, ny)
            x0, x1 = tx * tile_cells, min((tx + 1) * tile_cells, nx)
            celdas = int((A[y0:y1, x0:x1] >= 1.0).sum())
            if celdas < MOV_MIN_CELDAS:
                continue
            wy0, wy1, wx0, wx1 = max(y0 - MOV_MARGEN, 0), min(y1 + MOV_MARGEN, ny), max(x0 - MOV_MARGEN, 0), min(x1 + MOV_MARGEN, nx)
            ventana = GA[wy0:wy1, wx0:wx1]
            mse = np.zeros((len(desp), len(desp)))
            for i, dy in enumerate(desp):
                for j, dx in enumerate(desp):
                    mse[i, j] = float(((ventana - _fuente(np, GB, wy0, wy1, wx0, wx1, int(dy), int(dx))) ** 2).mean())
            i0, j0 = MOV_MAX_DESP, MOV_MAX_DESP
            base = mse[i0, j0]
            if base <= 1e-9:
                continue
            i, j = np.unravel_index(int(np.argmin(mse)), mse.shape)
            if i in (0, len(desp) - 1) or j in (0, len(desp) - 1):
                continue                                                 # en el borde de la búsqueda: no es confiable
            mejora = 1 - mse[i, j] / base
            if mejora < MOV_MIN_MEJORA:
                continue
            dy = float(desp[i]) + _parabola(mse[i - 1, j], mse[i, j], mse[i + 1, j])
            dx = float(desp[j]) + _parabola(mse[i, j - 1], mse[i, j], mse[i, j + 1])
            res[ty][tx] = {"dy": dy, "dx": dx, "mejora": float(mejora), "celdas": celdas}
    return res


def _csi(np, pred, real, umbral=1.0):
    p, r = pred >= umbral, real >= umbral
    a, b, c = int((p & r).sum()), int((p & ~r).sum()), int((~p & r).sum())
    return a / (a + b + c) if (a + b + c) else None


def movimiento_lluvia(A, B, C, paso=PASO_FINO, dt=MOV_DT_MIN):
    """Movimiento de la lluvia por cuadros de 2° (km/h hacia el este y hacia el norte) a partir de tres imágenes:
       A (ahora), B (hace 30 min), C (hace 60 min). Un cuadro solo se publica si, con el movimiento medido entre C y B,
       se predijo A mejor que suponiendo que la lluvia no se mueve (validación con datos reales de los últimos 30 min)."""
    import numpy as np
    s0, n0, w0, e0 = AHORA_CAJA
    tile_cells = int(round(MOV_TILE_DEG / paso))
    ny, nx = A.shape
    nty, ntx = -(-ny // tile_cells), -(-nx // tile_cells)
    ahora = desplazamientos(A, B, paso, nty, ntx, tile_cells)          # movimiento B -> A (para predecir)
    previo = desplazamientos(B, C, paso, nty, ntx, tile_cells)         # movimiento C -> B (para validar)
    u = [[None] * ntx for _ in range(nty)]
    v = [[None] * ntx for _ in range(nty)]
    q = [[None] * ntx for _ in range(nty)]
    validos = 0
    for ty in range(nty):
        for tx in range(ntx):
            m1, m0 = ahora[ty][tx], previo[ty][tx]
            if not m1 or not m0:
                continue
            y0, y1 = ty * tile_cells, min((ty + 1) * tile_cells, ny)
            x0, x1 = tx * tile_cells, min((tx + 1) * tile_cells, nx)
            # validación: con el movimiento de hace 30 min, ¿se adivina la imagen de ahora mejor que sin movimiento?
            pred = _fuente(np, B, y0, y1, x0, x1, int(round(m0["dy"])), int(round(m0["dx"])))
            real = A[y0:y1, x0:x1]
            csi_adv, csi_per = _csi(np, pred, real), _csi(np, B[y0:y1, x0:x1], real)
            if csi_adv is None or csi_per is None or csi_adv < csi_per + MOV_MIN_CSI_GANANCIA:
                continue
            latc = n0 - (y0 + y1) / 2 * paso
            km_x = m1["dx"] * paso * 111.32 * math.cos(math.radians(latc))
            km_y = -m1["dy"] * paso * 111.32
            ve, vn = km_x * 60 / dt, km_y * 60 / dt
            if math.hypot(ve, vn) > 90:
                continue
            u[ty][tx], v[ty][tx] = round(ve, 1), round(vn, 1)
            q[ty][tx] = round(csi_adv - csi_per, 2)
            validos += 1
    if not validos:
        return None
    return {"tile": MOV_TILE_DEG, "lat0": n0, "lon0": w0, "ny": nty, "nx": ntx, "dt_min": dt,
            "u": u, "v": v, "skill": q, "validos": validos}


def lluvia_y_movimiento():
    """Lluvia actual (payload para la página), movimiento (o None) y lluvia máxima en cada ciudad (~5 km)."""
    import numpy as np
    lista = err_recientes()
    t_ult = _inicio_err(lista[0]["key"])
    utc = t_ult or datetime.strptime(lista[0]["mod"][:16], "%Y-%m-%dT%H:%M")
    edad_h = (ahora_utc() - utc).total_seconds() / 3600
    if edad_h > AHORA_MAX_H:
        raise RuntimeError(f"la imagen más reciente tiene {edad_h:.1f} h ({lista[0]['key']})")
    if lista[0]["size"] > 600 * 2 ** 20:
        raise RuntimeError(f"archivo demasiado grande ({lista[0]['size'] // 2 ** 20} MB)")

    def cercano(objetivo_min):
        mejor = None
        for a in lista[1:]:
            t = _inicio_err(a["key"])
            if t is None:
                continue
            d = abs((utc - t).total_seconds() / 60 - objetivo_min)
            if d <= MOV_TOL_MIN and (mejor is None or d < mejor[0]):
                mejor = (d, a)
        return mejor[1] if mejor else None

    previos = {"B": cercano(MOV_DT_MIN), "C": cercano(2 * MOV_DT_MIN)}

    def bajar(a):
        if a is None:
            return None
        try:
            fd, ruta = tempfile.mkstemp(suffix=".nc")
            os.close(fd)
            with open(ruta, "wb") as f:
                f.write(http_get(f"{URL_ERR}/{urllib.parse.quote(a['key'])}", binario=True, timeout=180))
            return ruta
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=3) as ex:                    # se descarga en paralelo; la lectura NetCDF, una por una
        r_a, r_b, r_c = ex.map(bajar, [lista[0], previos["B"], previos["C"]])
    rutas = [r for r in (r_a, r_b, r_c) if r]
    try:
        if not r_a:
            raise RuntimeError(f"no se pudo descargar {lista[0]['key']}")
        try:
            (g10, g05), _ = leer_lluvia_nc(r_a, (AHORA_PASO, PASO_FINO))
        except Exception as e:
            raise RuntimeError(f"{e} (archivo {lista[0]['key']})")
        mov, motivo = None, None
        if r_b and r_c:
            try:
                (B,), _ = leer_lluvia_nc(r_b, (PASO_FINO,))
                (C,), _ = leer_lluvia_nc(r_c, (PASO_FINO,))
                mov = movimiento_lluvia(g05, B, C)
                if mov is None:
                    motivo = "sin lluvia suficiente o sin mejora frente a 'no se mueve'"
            except Exception as e:
                motivo = f"no se pudo medir: {e}"
        else:
            motivo = "faltan las imágenes de hace 30 y 60 min"
    finally:
        for r in rutas:
            try:
                os.remove(r)
            except Exception:
                pass
    q = np.clip(np.round(g10 * 2), 0, 255).astype("uint8")           # pasos de 0,5 mm/h
    s0, n0, w0, e0 = AHORA_CAJA
    lluvia = {"t": iso_local(utc), "lat0": n0, "lon0": w0, "paso": AHORA_PASO, "ny": int(q.shape[0]), "nx": int(q.shape[1]),
              "escala": 0.5, "q": base64.b64encode(q.tobytes()).decode("ascii"), "fuente": lista[0]["key"].rsplit("/", 1)[-1]}
    ciudades = {}
    for c, (la, lo) in CIUDADES.items():                              # máximo en 3x3 celdas de 0,05° (~±5,5 km)
        iy, ix = int((n0 - la) / PASO_FINO), int((lo - w0) / PASO_FINO)
        sub = g05[max(iy - 1, 0):iy + 2, max(ix - 1, 0):ix + 2]
        ciudades[c] = round(float(sub.max()), 1) if sub.size else None
    return lluvia, mov, motivo, ciudades, utc


# =====================================================================================================
#  2. RAYOS (GOES-19 GLM, archivos de 20 s)
# =====================================================================================================
def _inicio_archivo(key):
    m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})(\d)", key)
    if not m:
        return None
    a, d, h, mi, s, _ = m.groups()
    return datetime.strptime(a + d, "%Y%j") + timedelta(hours=int(h), minutes=int(mi), seconds=int(s))


def _leer_flashes(datos, nc4, np):
    """Latitud, longitud y calidad de las descargas ('flashes') de un archivo GLM-L2-LCFA."""
    try:
        ds = nc4.Dataset("glm.nc", mode="r", memory=datos)
    except Exception:
        fd, ruta = tempfile.mkstemp(suffix=".nc")
        os.close(fd)
        with open(ruta, "wb") as f:
            f.write(datos)
        try:
            ds = nc4.Dataset(ruta)
            return _extraer(ds, np)
        finally:
            os.remove(ruta)
    return _extraer(ds, np)


def _extraer(ds, np):
    try:
        nombres = list(ds.variables)
        vlat = "flash_lat" if "flash_lat" in ds.variables else next((n for n in nombres if "flash" in n and "lat" in n), None)
        vlon = "flash_lon" if "flash_lon" in ds.variables else next((n for n in nombres if "flash" in n and "lon" in n), None)
        if not vlat or not vlon:
            raise RuntimeError("no hay flash_lat/flash_lon; variables: " + ", ".join(nombres)[:300])
        a32 = lambda v: np.ma.filled(np.ma.asarray(ds.variables[v][:], dtype=np.float32), np.nan)
        lat, lon = a32(vlat), a32(vlon)
        if "flash_quality_flag" in ds.variables:                    # 0 = buena calidad
            q = np.ma.filled(np.ma.asarray(ds.variables["flash_quality_flag"][:]), 0)
            bueno = (q == 0)
            lat, lon = lat[bueno], lon[bueno]
        return lat, lon
    finally:
        ds.close()


def rayos_ahora():
    nc4, np = importar_netcdf()
    ahora = ahora_utc()
    archivos = []
    for h in (ahora - timedelta(hours=1), ahora):
        pref = f"GLM-L2-LCFA/{h:%Y}/{h.timetuple().tm_yday:03d}/{h:%H}/"
        _, objs = s3_listar(URL_GOES19, pref, delimitador=False)
        archivos += objs
    cand = []
    for a in archivos:
        t = _inicio_archivo(a["key"])
        if t and ahora - timedelta(minutes=RAYOS_VENTANA_MIN) <= t <= ahora:
            cand.append((t, a["key"]))
    if not cand:
        raise RuntimeError(f"sin archivos GLM recientes en {URL_GOES19} (listados: {len(archivos)})")
    cand.sort()
    edad_min = (ahora - cand[-1][0]).total_seconds() / 60
    if edad_min > RAYOS_MAX_EDAD_MIN:
        raise RuntimeError(f"el archivo GLM más reciente tiene {edad_min:.0f} min ({cand[-1][1].rsplit('/', 1)[-1]})")

    def bajar(par):                      # las descargas van en paralelo; la lectura NetCDF NO (la librería no es segura con hilos)
        t, key = par
        try:
            return t, http_get(f"{URL_GOES19}/{key}", binario=True, intentos=2, timeout=40), None
        except Exception as e:
            return t, None, str(e)

    with ThreadPoolExecutor(max_workers=8) as ex:
        descargados = list(ex.map(bajar, cand))
    res = []
    for t, datos, err in descargados:
        if err is None:
            try:
                lat, lon = _leer_flashes(datos, nc4, np)
                res.append((t, lat, lon, None))
                continue
            except Exception as e:
                err = str(e)
        res.append((t, None, None, err))
    fallos = [e for _, _, _, e in res if e]
    if len(fallos) > len(res) // 2:
        raise RuntimeError(f"no se pudieron leer {len(fallos)} de {len(res)} archivos GLM; ejemplo: {fallos[0]}")
    s0, n0, w0, e0 = RAYOS_CAJA
    L, G, E, T = [], [], [], []
    for t, lat, lon, e in res:
        if lat is None or not len(lat):
            continue
        L.append(lat); G.append(lon)
        E.append(np.full(len(lat), (ahora - t).total_seconds() / 60.0, dtype=np.float32))
        T.append(np.full(len(lat), (t - datetime(1970, 1, 1)).total_seconds() / 600.0, dtype=np.float64))   # franja de 10 min
    if L:
        lat, lon, edad, tt = np.concatenate(L), np.concatenate(G), np.concatenate(E), np.concatenate(T)
        m = np.isfinite(lat) & np.isfinite(lon) & (lat >= s0) & (lat <= n0) & (lon >= w0) & (lon <= e0)
        lat, lon, edad, tt = lat[m], lon[m], edad[m], tt[m]
    else:
        lat = lon = edad = np.zeros(0, dtype=np.float32)
        tt = np.zeros(0, dtype=np.float64)
    rec = edad <= RAYOS_RECIENTE_MIN
    celdas = []
    if len(lat):
        iy = np.round(lat / RAYOS_CELDA).astype(np.int64)
        ix = np.round(lon / RAYOS_CELDA).astype(np.int64)
        clave = iy * 100000 + ix
        for k in np.unique(clave):
            sel = clave == k
            celdas.append([round(float(iy[sel][0]) * RAYOS_CELDA, 2), round(float(ix[sel][0]) * RAYOS_CELDA, 2),
                           int(rec[sel].sum()), int(sel.sum())])
        celdas.sort(key=lambda c: -c[3])
        celdas = celdas[:RAYOS_MAX_CELDAS]
    # cercanía de cada ciudad (una vez) y conteos por franja de 10 min completa
    cerca_de = {}
    ciudades = {}
    for nombre, (cla, clo) in CIUDADES.items():
        if not len(lat):
            cerca_de[nombre] = np.zeros(0, dtype=bool)
            continue
        dy = (lat - cla) * 111.0
        dx = (lon - clo) * 111.0 * math.cos(math.radians(cla))
        cerca_de[nombre] = (dx * dx + dy * dy) <= RAYOS_RADIO_KM ** 2
        n = int((cerca_de[nombre] & rec).sum())
        if n:
            ciudades[nombre] = n
    ultimo = cand[-1][0]
    por_franja = {}
    for t, _ in cand:
        k = int((t - datetime(1970, 1, 1)).total_seconds() // 600)
        por_franja[k] = por_franja.get(k, 0) + 1
    franjas = {}
    for k, nf in por_franja.items():
        fin = datetime(1970, 1, 1) + timedelta(minutes=10 * (k + 1))
        if nf < RAYOS_ARCHIVOS_FRANJA or ultimo < fin - timedelta(seconds=25):
            continue                                             # incompleta o todavía abierta
        cuenta = {}
        for nombre in CIUDADES:
            msk = cerca_de[nombre]
            n = int((msk & (np.floor(tt) == k)).sum()) if len(msk) else 0
            if n:
                cuenta[nombre] = n
        franjas[iso_local(datetime(1970, 1, 1) + timedelta(minutes=10 * k))] = {"f": nf, "c": cuenta}
    return {"t": iso_local(ultimo), "ventana_min": RAYOS_VENTANA_MIN, "reciente_min": RAYOS_RECIENTE_MIN,
            "radio_km": RAYOS_RADIO_KM, "archivos": len(cand), "total20": int(rec.sum()), "total30": int(len(lat)),
            "ciudades": ciudades, "celdas": celdas, "fuente": "GOES-19 GLM (NOAA)", "_franjas": franjas}


# =====================================================================================================
#  3. BOLETÍN DEL INAMHI
# =====================================================================================================
MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
         "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
VINETAS = "•●·*▪■◦\u2022\u25cf\u00b7\uf0b7\uf0a7\ufffd"          # viñetas que puede dejar la extracción de un PDF
REGIONES = {"litoral": "litoral", "interandina": "interandina", "amazonica": "amazonia", "amazonia": "amazonia", "insular": "insular"}


def pdf_a_texto(datos):
    try:
        from pypdf import PdfReader
    except ImportError:
        if not os.environ.get("GITHUB_ACTIONS"):
            raise
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pypdf"], check=True, timeout=300)
        from pypdf import PdfReader
    lector = PdfReader(io.BytesIO(datos))
    return "\n".join((p.extract_text() or "") for p in lector.pages)


def _sin_acentos(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _mes(nombre):
    return MESES.get(_sin_acentos(nombre).lower())


def parsear_boletin(texto):
    """Extrae del texto del boletín: número, estatus, emisión, vigencia, nivel, fenómeno, descripción,
       qué puede pasar, zonas afectadas por región, lluvia esperada y recomendaciones."""
    t = re.sub(r"\s+", " ", texto or "").strip()
    muestra = t[:240]
    def buscar(patron, flags=re.I):
        return re.search(patron, t, flags)
    m = buscar(r"Bolet[ií]n Meteorol[oó]gico\s*(?:Nro\.?|N\.?\s*[º°o]\.?|No\.?|N[º°])\s*(\d+)")
    if not m:
        raise ValueError("no se reconoce el boletín del INAMHI; texto: " + muestra)
    numero = m.group(1)
    m = buscar(r"Estatus:\s*([A-ZÁÉÍÓÚÑ]+)")
    estatus = m.group(1) if m else ""
    ahora_anio = ahora_utc().year
    emision = None
    m = buscar(r"emisi[oó]n:\s*(?:[a-záéíóúñ]+,\s*)?(\d{1,2}) de ([a-záéíóú]+) de (\d{4}),?\s*(\d{1,2}):(\d{2})")
    if m and _mes(m.group(2)):
        emision = datetime(int(m.group(3)), _mes(m.group(2)), int(m.group(1)), int(m.group(4)), int(m.group(5)))
    m = buscar(r"Vigencia:\s*desde\s*(?:las\s*)?(\d{1,2})\s*[Hh:]\s*(\d{2})\s*(?:horas\s*)?del?\s*(\d{1,2}) de ([a-záéíóú]+)"
               r"(?: de (\d{4}))?\s*hasta\s*(?:las\s*)?(\d{1,2})\s*[Hh:]\s*(\d{2})\s*(?:horas\s*)?del?\s*(\d{1,2}) de ([a-záéíóú]+)(?: de (\d{4}))?")
    if not m or not _mes(m.group(4)) or not _mes(m.group(9)):
        raise ValueError("no se pudo leer la vigencia del boletín; texto: " + muestra)
    h1, mi1, d1, mes1, a1, h2, mi2, d2, mes2, a2 = m.groups()
    anio2 = int(a2 or a1 or (emision.year if emision else ahora_anio))
    anio1 = int(a1 or anio2)
    if not a1 and _mes(mes1) > _mes(mes2):
        anio1 = anio2 - 1                                        # vigencia que cruza de diciembre a enero
    desde = datetime(anio1, _mes(mes1), int(d1), int(h1), int(mi1))
    hasta = datetime(anio2, _mes(mes2), int(d2), int(h2), int(mi2))
    m = buscar(r"NIVEL (?:DE )?AMENAZA:\s*(.+?)\s+FEN[ÓO]MENO")
    nivel = m.group(1).strip() if m else ""
    fen = buscar(r"FEN[ÓO]MENO:\s*(.+?)(?:\s+Zona\s+Mayor|\s+Fig\.|\s+DESCRIPCI[ÓO]N|$)")
    fenomeno = fen.group(1).strip()[:160] if fen else ""
    desc = buscar(r"DESCRIPCI[ÓO]N\s+(.+?)\s+Estimaci[oó]n de Precipitaci[oó]n")
    descripcion = desc.group(1).strip() if desc else ""
    qpp = buscar(r"QU[ÉE] PUEDE PASAR\s*\?\s*(.+?)\s+ZONAS AFECTADAS")
    que = [x.strip(VINETAS + " -") for x in re.split("[" + re.escape(VINETAS) + "]", qpp.group(1)) if x.strip(VINETAS + " -")] if qpp else []
    if len(que) == 1 and len(que[0]) > 220:                      # sin viñetas: se separa por frases
        que = [x.strip() + "." for x in re.split(r"(?<=[a-záéíóú])\.\s+(?=[A-ZÁÉÍÓÚ])", que[0].rstrip(".")) if x.strip()]
    zonas = {}
    zb = buscar(r"(?-i:ZONAS AFECTADAS)\s*(.+?)(?:\s+Secretar[ií]a Nacional|\s+(?-i:ATENCI[ÓO]N A:)|$)")
    if zb:
        bloque = zb.group(1)
        marcas = list(re.finditer(r"Regi[oó]n\s+(Litoral|Interandina|Amaz[oó]nica|Insular)\s*:", bloque, re.I))
        for i, r in enumerate(marcas):                       # no depende de las viñetas: corta entre "Región X:" y la siguiente
            fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(bloque)
            zonas[REGIONES[_sin_acentos(r.group(1)).lower()]] = bloque[r.end():fin].strip(VINETAS + " -–")
    lluvia_mm = {}
    pb = buscar(r"Estimaci[oó]n de Precipitaci[oó]n.+?REGI[ÓO]N\s+Nivel Medio\s+Nivel Alto\s+Muy Alto\s+(.+?)\s+(?:- )?Entre los principales")
    if pb:
        rango = r"(\d+\s*[–-]\s*\d+)"
        for r in re.finditer(r"(Litoral|Interandina|Amazon[ií]a)\s+" + rango + r"(?:\s+" + rango + r")?(?:\s+" + rango + r")?", pb.group(1), re.I):
            reg = REGIONES[_sin_acentos(r.group(1)).lower()]
            niveles = ["Medio", "Alto", "Muy alto"]
            lluvia_mm[reg] = [[niveles[i], re.sub(r"\s*[–-]\s*", "–", g)] for i, g in enumerate(r.groups()[1:]) if g]
    rec = buscar(r"Secretar[ií]a Nacional de Gesti[oó]n de Riesgos recomienda:\s*(.+?)(?:\s+(?-i:ATENCI[ÓO]N A:)|$)")
    nv = nivel.upper()
    nivel_max = "muy alto" if "MUY ALTO" in nv else "alto" if "ALTO" in nv else "medio" if "MEDIO" in nv else "bajo"
    fu = _sin_acentos(fenomeno).upper()
    fenomenos = [n for n, k in (("lluvia", "LLUVIA"), ("tormenta", "TORMENTA"), ("rafagas", "RAFAGA"), ("granizo", "GRANIZO")) if k in fu]
    return {"id": numero, "estatus": estatus.capitalize(), "emitido": emision.strftime("%Y-%m-%dT%H:%M") if emision else None,
            "desde": desde.strftime("%Y-%m-%dT%H:%M"), "hasta": hasta.strftime("%Y-%m-%dT%H:%M"),
            "nivel": nivel, "nivel_max": nivel_max, "fenomeno": fenomeno, "fenomenos": fenomenos,
            "descripcion": descripcion, "que_puede_pasar": que, "zonas": zonas, "lluvia_mm": lluvia_mm,
            "recomienda": rec.group(1).strip() if rec else "", "url": URL_PDF_INAMHI, "pagina": URL_PAG_INAMHI,
            "obtenido": iso_local(ahora_utc())}


def aviso_inamhi():
    datos = http_get(URL_PDF_INAMHI, binario=True, timeout=60)
    if not datos.startswith(b"%PDF"):
        raise RuntimeError("la dirección del boletín no devolvió un PDF")
    return parsear_boletin(pdf_a_texto(datos))


# =====================================================================================================
#  4. HISTORIAL PARA VERIFICAR (hist.json)
# =====================================================================================================
def cargar_hist():
    """Historial anterior: del archivo local que deja el workflow (git show origin/ahora:hist.json), o de la dirección pública."""
    try:
        ruta = os.environ.get("CC_HIST_PREVIO")
        texto = open(ruta, encoding="utf-8").read() if ruta and os.path.exists(ruta) else http_get(URL_HIST, intentos=2, timeout=30)
        h = json.loads(texto)
        if isinstance(h, dict) and h.get("ciudades") == ORDEN:
            return h
    except Exception:
        pass
    return {"v": 1, "ciudades": ORDEN, "lluvia": {}, "rayos": {}}


def actualizar_hist(hist, utc_frame, lluvia_ciudades, franjas):
    """Agrega la lluvia de este cuadro y las franjas de rayos completas; conserva 72 h."""
    if lluvia_ciudades:
        k = iso_local(utc_frame.replace(minute=utc_frame.minute // 10 * 10, second=0, microsecond=0))
        hist["lluvia"][k] = [lluvia_ciudades.get(c) for c in ORDEN]
    for k, f in (franjas or {}).items():
        hist["rayos"][k] = [f["f"], [f["c"].get(c, 0) for c in ORDEN]]
    limite = iso_local(ahora_utc() - timedelta(hours=HIST_HORAS))
    for sec in ("lluvia", "rayos"):
        hist[sec] = {k: v for k, v in hist[sec].items() if k >= limite}
    return hist


# =====================================================================================================
def main():
    destino = sys.argv[1] if len(sys.argv) > 1 else "ahora.json"
    carpeta = destino if os.path.isdir(destino) else os.path.dirname(os.path.abspath(destino))
    ruta_ahora = os.path.join(destino, "ahora.json") if os.path.isdir(destino) else destino
    ruta_hist = os.path.join(carpeta, "hist.json")
    salida = {"generado": iso_local(ahora_utc()), "lluvia": None, "movimiento": None, "rayos": None, "oficial": None, "errores": []}
    utc_frame, lluvia_c, franjas = None, None, None
    t0 = time.time()
    try:
        salida["lluvia"], salida["movimiento"], motivo, lluvia_c, utc_frame = lluvia_y_movimiento()
        print(f"  lluvia: OK ({time.time() - t0:.0f} s)")
        if salida["movimiento"]:
            print(f"  movimiento: {salida['movimiento']['validos']} cuadros validados")
        elif motivo:
            print(f"  movimiento: no disponible ({motivo})")
    except Exception as e:
        salida["errores"].append(f"lluvia: {e}")
        print(f"  lluvia: ERROR {e}")
    t0 = time.time()
    try:
        salida["rayos"] = rayos_ahora()
        franjas = salida["rayos"].pop("_franjas", None)
        print(f"  rayos: OK ({time.time() - t0:.0f} s)")
    except Exception as e:
        salida["errores"].append(f"rayos: {e}")
        print(f"  rayos: ERROR {e}")
    t0 = time.time()
    try:
        salida["oficial"] = aviso_inamhi()
        print(f"  oficial: OK ({time.time() - t0:.0f} s)")
    except Exception as e:
        salida["errores"].append(f"oficial: {e}")
        print(f"  oficial: ERROR {e}")
    try:
        hist = actualizar_hist(cargar_hist(), utc_frame or ahora_utc(), lluvia_c, franjas)
        tmp = ruta_hist + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(hist, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, ruta_hist)
        print(f"  historial: {len(hist['lluvia'])} muestras de lluvia, {len(hist['rayos'])} franjas de rayos")
    except Exception as e:
        salida["errores"].append(f"historial: {e}")
        print(f"  historial: ERROR {e}")
    tmp = ruta_ahora + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(salida, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, ruta_ahora)
    print(f"Escrito {ruta_ahora} ({os.path.getsize(ruta_ahora) // 1024} KB)")
    if not any(salida[k] for k in ("lluvia", "rayos", "oficial")):
        sys.exit(1)


if __name__ == "__main__":
    main()
