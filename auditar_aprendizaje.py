# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
# Licencia: PolyForm Noncommercial 1.0.0 (ver el archivo LICENSE). Uso no comercial.
"""
¿ClearSky está aprendiendo de verdad?  (auditoría con lo que ya se publicó)
===========================================================================
«Aprender» puede querer decir dos cosas distintas:
  1. Que los números guardados (pesos, umbral, tablas) cambien. Eso siempre pasa: no prueba nada.
  2. Que los pronósticos que se PUBLICAN acierten más. Esto es lo único que importa, y solo se puede comprobar con datos que el
     sistema no usó para aprender: los pronósticos que ya mostró la página, comparados con lo que después observaron los aeropuertos.

Este script hace esa segunda comprobación, sin internet y sin cambiar nada:
  A) Con datos/historial (lo que se publicó) y datos/observaciones (lo que pasó), compara por ciudad y por anticipación:
       · el pronóstico publicado (calibrado) contra el MISMO pronóstico sin calibrar  -> ¿la calibración sirve de verdad?
       · el pronóstico publicado contra decir siempre el promedio de lluvia          -> ¿le gana a no hacer nada?
     con un intervalo de confianza (se remuestrean DÍAS enteros, no horas sueltas, porque las horas de un mismo día no son
     independientes) y un veredicto honesto: «mejor», «peor» o «no se distingue del azar todavía».
  B) Con el historial de git de datos/pesos.json, muestra cómo ha evolucionado lo aprendido día a día y lo que midió el
     «examen sin trampa» de cada verificación.

Uso (desde la carpeta del proyecto, con el repositorio actualizado: git pull):
    python auditar_aprendizaje.py                       # todas las ciudades con datos
    python auditar_aprendizaje.py --ciudad Guayaquil    # una sola
    python auditar_aprendizaje.py --anticipaciones 1,6,24 --remuestras 3000
    python auditar_aprendizaje.py --sin-historial       # omite la parte B (no usa git)
"""
import argparse, csv, glob, json, os, random, subprocess, sys
from datetime import datetime, timedelta

ANTICIPACIONES = (1, 6, 24)       # horas de antelación con que se hizo el pronóstico que se evalúa
MIN_DIAS_VEREDICTO = 14           # menos días que esto: no se concluye nada
MIN_HORAS = 72                    # menos horas comparables que esto: no se calcula
MIN_HORAS_LLUVIA = 10             # menos horas CON LLUVIA que esto: la comparación depende de 1 o 2 horas y no significa nada
NIVEL = 0.90                      # nivel del intervalo de confianza
SEMILLA = 20261029                # el remuestreo es determinista: el mismo dato da siempre el mismo intervalo


# ====================================================================================================================
#  A) Lo que se publicó contra lo que pasó
# ====================================================================================================================
def leer_observaciones(datos, ciudad):
    """hora -> 1/0 (¿llovió en el aeropuerto?). La clave 15:00 cubre de 14:00 a 15:00, igual que los pronósticos."""
    out = {}
    for ruta in sorted(glob.glob(os.path.join(datos, "observaciones", "*.json"))):
        try:
            with open(ruta, encoding="utf-8") as f:
                for hora, v in (json.load(f).get(ciudad) or {}).items():
                    out[hora] = int(v[0])
        except (OSError, ValueError, TypeError, IndexError):
            continue
    return out


def leer_publicados(datos, ciudad, anticipacion):
    """hora -> (prob. calibrada, prob. sin calibrar o None): el ÚLTIMO pronóstico hecho con al menos `anticipacion` horas de antelación.
       Así, anticipacion=1 es el pronóstico más reciente y anticipacion=24 el que se hizo el día anterior."""
    elegido = {}
    for ruta in sorted(glob.glob(os.path.join(datos, "historial", "*.csv"))):
        with open(ruta, encoding="utf-8") as f:
            for fila in csv.DictReader(f):
                if fila.get("ciudad") != ciudad or fila.get("modelo") != "CONSENSO" or not fila.get("prob_lluvia"):
                    continue
                try:
                    ej = datetime.strptime(fila["ejecucion"], "%Y-%m-%d %H:%M")
                    hv = datetime.strptime(fila["hora_valida"], "%Y-%m-%dT%H:00")
                    cal = float(fila["prob_lluvia"])
                except (KeyError, ValueError):
                    continue
                try:
                    bruta = float(fila["prob_bruta"]) if fila.get("prob_bruta") not in (None, "") else None
                except ValueError:
                    bruta = None
                if (hv - ej).total_seconds() / 3600 < anticipacion:
                    continue
                k = fila["hora_valida"]
                if k not in elegido or ej > elegido[k][0]:
                    elegido[k] = (ej, cal, bruta)
    return {k: (v[1], v[2]) for k, v in elegido.items()}


def emparejar(publicados, obs):
    """[(hora, observado, calibrada 0-1, sin calibrar 0-1 o None)] solo donde hay pronóstico y observación."""
    return [(k, obs[k], c / 100, None if b is None else b / 100) for k, (c, b) in sorted(publicados.items()) if k in obs]


def por_dia(pares):
    """Suma por día: [horas, horas con lluvia, suma de errores² calibrada, suma de errores² sin calibrar, horas con sin calibrar]."""
    dias = {}
    for k, o, c, b in pares:
        d = dias.setdefault(k[:10], [0, 0, 0.0, 0.0, 0])
        d[0] += 1
        d[1] += o
        d[2] += (c - o) ** 2
        if b is not None:
            d[3] += (b - o) ** 2
            d[4] += 1
    return dias


def puntajes(dias, claves):
    """Brier (error cuadrático medio: 0 = perfecto, menos es mejor) de cada alternativa y cuánto mejora ClearSky.
       `claves` puede repetir días (así funciona el remuestreo)."""
    n = sum(dias[d][0] for d in claves)
    if not n:
        return None
    llovio = sum(dias[d][1] for d in claves)
    p = llovio / n
    b_cal, b_cli = sum(dias[d][2] for d in claves) / n, p * (1 - p)
    con_bruta = sum(dias[d][4] for d in claves)
    b_bru = sum(dias[d][3] for d in claves) / con_bruta if con_bruta == n else None
    return {"n": n, "llovio": llovio, "base": p, "b_cal": b_cal, "b_cli": b_cli, "b_bru": b_bru,
            "vs_clima": 1 - b_cal / b_cli if b_cli > 0 else None, "vs_bruta": 1 - b_cal / b_bru if b_bru else None}


def percentil(valores, q):
    v = sorted(valores)
    return v[min(len(v) - 1, max(0, int(q * len(v))))]


def intervalos(dias, remuestras, nivel=NIVEL):
    """Intervalo de confianza de la mejora, remuestreando DÍAS completos con reemplazo. Devuelve (vs_clima, vs_bruta), cada uno (bajo, alto) o None."""
    claves = sorted(dias)
    rng = random.Random(SEMILLA)
    a, b = [], []
    for _ in range(remuestras):
        s = puntajes(dias, [rng.choice(claves) for _ in claves])
        if s and s["vs_clima"] is not None:
            a.append(s["vs_clima"])
        if s and s["vs_bruta"] is not None:
            b.append(s["vs_bruta"])
    cola = (1 - nivel) / 2
    ic = lambda v: (percentil(v, cola), percentil(v, 1 - cola)) if len(v) >= max(20, remuestras // 2) else None
    return ic(a), ic(b)


def veredicto(ic, n_dias, llovio=None):
    if n_dias < MIN_DIAS_VEREDICTO:
        return f"pocos días ({n_dias} de {MIN_DIAS_VEREDICTO}): todavía no se puede concluir"
    if llovio is not None and llovio < MIN_HORAS_LLUVIA:
        return f"pocas horas con lluvia ({llovio} de {MIN_HORAS_LLUVIA} mínimas): todavía no se puede concluir"
    if ic is None:
        return "sin datos suficientes"
    if ic[0] > 0:
        return "MEJOR, con 90% de confianza"
    if ic[1] < 0:
        return "PEOR, con 90% de confianza"
    return "no se distingue del azar todavía"


def tendencia(dias):
    """Mejora frente al promedio en la primera y en la segunda mitad de los días. Solo orienta: con pocos días es puro ruido."""
    claves = sorted(dias)
    if len(claves) < 2 * 7:
        return None
    m = len(claves) // 2
    a, b = puntajes(dias, claves[:m]), puntajes(dias, claves[m:])
    return (a["vs_clima"], b["vs_clima"]) if a and b and a["vs_clima"] is not None and b["vs_clima"] is not None else None


def anuncios(pares, umbral=0.40):
    """De las horas en que se publicó 40% o más, ¿cuántas veces llovió?  (n, llovió)"""
    sel = [o for _, o, c, _ in pares if c >= umbral]
    return len(sel), sum(sel)


def primer_pronostico(datos):
    """(primera, última) ejecución guardada en datos/historial, o None. Sirve para explicar cuánta historia hay."""
    primera = ultima = None
    for ruta in sorted(glob.glob(os.path.join(datos, "historial", "*.csv"))):
        with open(ruta, encoding="utf-8") as f:
            for fila in csv.DictReader(f):
                try:
                    ej = datetime.strptime(fila["ejecucion"], "%Y-%m-%d %H:%M")
                except (KeyError, ValueError):
                    continue
                primera = ej if primera is None or ej < primera else primera
                ultima = ej if ultima is None or ej > ultima else ultima
    return (primera, ultima) if primera else None


def cobertura(datos, ciudad):
    """Cuántas horas hay publicadas, observadas y en común para una ciudad, y de qué fechas."""
    pub, obs = leer_publicados(datos, ciudad, 0), leer_observaciones(datos, ciudad)
    comunes = [k for k in pub if k in obs]
    return {"pub": len(pub), "obs": len(obs), "comunes": len(comunes),
            "pub_desde": min(pub) if pub else None, "pub_hasta": max(pub) if pub else None,
            "obs_desde": min(obs) if obs else None, "obs_hasta": max(obs) if obs else None}


def motivo_sin_datos(c):
    """Una frase que dice POR QUÉ no hay horas comparables (en vez de un «0» sin explicación)."""
    if c["obs"] == 0:
        return "no hay observaciones de aeropuerto en datos/observaciones (su aeropuerto no publica o todavía no se descargó)"
    if c["pub"] == 0:
        return "no hay pronósticos publicados de esta ciudad en datos/historial"
    if c["comunes"] == 0:
        return (f"hay {c['pub']} h publicadas ({c['pub_desde'][:13]} a {c['pub_hasta'][:13]}) y {c['obs']} h observadas "
                f"({c['obs_desde'][:13]} a {c['obs_hasta'][:13]}), pero ninguna hora coincide")
    return f"solo {c['comunes']} horas en común"


def evaluar(datos, ciudad, anticipacion, remuestras):
    pub, obs = leer_publicados(datos, ciudad, anticipacion), leer_observaciones(datos, ciudad)
    pares = emparejar(pub, obs)
    con_bruta = [p for p in pares if p[3] is not None]
    if len(con_bruta) >= MIN_HORAS:
        pares = con_bruta                      # misma muestra para comparar calibrado y sin calibrar
    if len(pares) < MIN_HORAS:
        return {"ciudad": ciudad, "anticipacion": anticipacion, "horas": len(pares), "insuficiente": True}
    dias = por_dia(pares)
    total = puntajes(dias, sorted(dias))
    ic_cli, ic_bru = intervalos(dias, remuestras)
    return {"ciudad": ciudad, "anticipacion": anticipacion, "horas": len(pares), "dias": len(dias), "total": total, "ic_clima": ic_cli, "ic_bruta": ic_bru,
            "ver_clima": veredicto(ic_cli, len(dias), total["llovio"]), "ver_bruta": veredicto(ic_bru, len(dias), total["llovio"]) if total["b_bru"] is not None else None,
            "tendencia": tendencia(dias), "anuncios": anuncios(pares)}


def pct(x):
    return "—" if x is None else f"{x * 100:+.0f}%"


def intervalo_txt(ic):
    return "" if ic is None else f" [90%: {pct(ic[0])} a {pct(ic[1])}]"


def imprimir_evaluacion(r):
    cab = f"{r['ciudad']} · pronósticos hechos con ≥ {r['anticipacion']} h de antelación"
    if r.get("insuficiente"):
        print(f"\n{cab}\n   Todavía no hay datos suficientes ({r['horas']} horas comparables; hacen falta {MIN_HORAS}).")
        return
    t = r["total"]
    print(f"\n{cab}\n   {r['dias']} días · {r['horas']} horas comparables · {t['llovio']} con lluvia ({t['base'] * 100:.0f}%)")
    print(f"   Brier (menos es mejor):   ClearSky {t['b_cal']:.3f}" + (f" · sin calibrar {t['b_bru']:.3f}" if t["b_bru"] is not None else "") + f" · decir siempre el promedio {t['b_cli']:.3f}")
    print(f"   ¿Le gana a decir siempre el promedio?  {pct(t['vs_clima'])}{intervalo_txt(r['ic_clima'])} -> {r['ver_clima']}")
    if r["ver_bruta"]:
        print(f"   ¿La calibración sirve de verdad?       {pct(t['vs_bruta'])}{intervalo_txt(r['ic_bruta'])} -> {r['ver_bruta']}")
    n, ll = r["anuncios"]
    if n >= 5:
        print(f"   Cuando publicamos 40% o más: llovió {ll} de {n} veces ({ll / n * 100:.0f}%)")
    if r["tendencia"]:
        print(f"   Tendencia (solo orienta): primera mitad {pct(r['tendencia'][0])} · segunda mitad {pct(r['tendencia'][1])}")


# ====================================================================================================================
#  B) Cómo ha cambiado lo aprendido (historial de git de datos/pesos.json)
# ====================================================================================================================
def versiones_pesos(raiz, maximo):
    """JSON de datos/pesos.json en cada commit que lo cambió, del más nuevo al más viejo. None si no hay git."""
    try:
        r = subprocess.run(["git", "-C", raiz, "log", f"-{maximo}", "--format=%H", "--", "datos/pesos.json"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode:
        return None
    out = []
    for h in r.stdout.split():
        try:
            s = subprocess.run(["git", "-C", raiz, "show", f"{h}:datos/pesos.json"], capture_output=True, text=True, timeout=60)
            if not s.returncode:
                out.append(json.loads(s.stdout))
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    return out


def historial_ciudad(versiones, ciudad):
    """Una fila por día (la última verificación de ese día), en orden cronológico."""
    por_fecha = {}
    for p in versiones:
        c = (p.get("ciudades") or {}).get(ciudad) or {}
        act = p.get("actualizado")
        if not act or not c.get("verificado"):
            continue
        ex, real = c.get("examen") or {}, c.get("real") or {}
        fila = {"actualizado": act, "fecha": act[:10], "pesos": c.get("pesos") or {}, "umbral": c.get("umbral"), "modo": c.get("modo_calibracion"),
                "ex_clima": ex.get("mejora_vs_clima_pct"), "ex_crudo": ex.get("mejora_vs_crudo_pct"),
                "real_clima": real.get("mejora_vs_clima_pct"), "real_horas": real.get("horas"), "real_lluvia": real.get("horas_lluvia")}
        if fila["fecha"] not in por_fecha or act > por_fecha[fila["fecha"]]["actualizado"]:
            por_fecha[fila["fecha"]] = fila
    return [por_fecha[f] for f in sorted(por_fecha)]


def cambio_pesos(a, b):
    """Cuánto cambiaron los pesos de los modelos entre dos verificaciones (suma de diferencias absolutas)."""
    return sum(abs((b.get(m) or 0) - (a.get(m) or 0)) for m in set(a) | set(b))


def imprimir_historial(ciudad, filas):
    print(f"\n{ciudad} · lo aprendido día a día (de datos/pesos.json en git)")
    if len(filas) < 2:
        print("   Todavía hay menos de 2 verificaciones guardadas en git: no hay evolución que mostrar.")
        return
    print("   fecha        examen vs promedio  examen vs sin aprender  real vs promedio (horas · con lluvia)   calibración   cambio de pesos")
    previo, hay_pocos = None, False
    for f in filas[-14:]:
        cambio = "" if previo is None else f"{cambio_pesos(previo['pesos'], f['pesos']):.2f}"
        pocos = f["real_horas"] is not None and (f["real_horas"] < MIN_HORAS or (f["real_lluvia"] is not None and f["real_lluvia"] < MIN_HORAS_LLUVIA))
        llu = "" if f["real_lluvia"] is None else f" · {f['real_lluvia']} con lluvia"
        real = "—" if f["real_clima"] is None else f"{f['real_clima']:+d}% ({f['real_horas']} h{llu})" + ("*" if pocos else "")
        hay_pocos = hay_pocos or (pocos and f["real_clima"] is not None)
        fmt = lambda x: "—" if x is None else f"{x:+d}%"
        print(f"   {f['fecha']}   {fmt(f['ex_clima']):>16}   {fmt(f['ex_crudo']):>20}   {real:>34}   {str(f['modo'] or '—'):<12}  {cambio:>8}")
        previo = f
    if hay_pocos:
        print(f"   * Muy pocos datos (menos de {MIN_HORAS} horas o de {MIN_HORAS_LLUVIA} horas con lluvia): ese porcentaje depende de 1 o 2 horas y NO significa que el pronóstico sea malo ni bueno.")
    cambios = sum(1 for a, b in zip(filas, filas[1:]) if cambio_pesos(a["pesos"], b["pesos"]) > 1e-9)
    print(f"   Los pesos cambiaron en {cambios} de {len(filas) - 1} verificaciones. (Que cambien NO prueba que mejoren: mira las columnas «real».)")


NOTAS = """
Cómo leer esto
  · «Brier» es el error cuadrático medio de las probabilidades: 0 es perfecto; decir siempre «llueve X% de las veces» da p·(1-p).
    «Mejora» = cuánto baja el error frente a esa alternativa (+30% = el error es 30% menor).
  · El intervalo (90%) dice cuánta incertidumbre hay. Si incluye el 0, la diferencia puede ser suerte. Con menos de 14 días no se concluye.
  · Los pronósticos evaluados son los que de verdad se publicaron: ninguna de esas horas se usó para aprender. Ese es el dato limpio.
  · El «examen sin trampa» de pesos.json (columna «examen») es útil pero algo optimista: el sistema prueba varias combinaciones y presenta
    la que mejor le fue en esos mismos 10 días. Por eso la columna «real» manda cuando discrepan.
  · «Decir siempre el promedio» usa la frecuencia de lluvia de la propia muestra (conoce la respuesta de antemano). Con cientos de horas la ventaja
    es pequeña; con pocas horas, o pocas con lluvia, es enorme: un pronóstico PERFECTO puede salir en negativo si en la muestra llovió 1 o 2 horas.
    Por eso se exige un mínimo de horas y de horas con lluvia, y los porcentajes con pocos datos van con asterisco.
  · Esto mide la lluvia en el aeropuerto de cada ciudad (un solo punto) y solo compara calibrado contra sin calibrar. Para medir el efecto de
    TODO lo aprendido (pesos incluidos) en datos reales habría que guardar también el pronóstico «sin aprender»; hoy eso solo está en el examen.
  · Poca lluvia = poca evidencia: en épocas secas es normal que el veredicto sea «no se distingue».
"""


def ciudades_con_datos(datos):
    out = set()
    for ruta in glob.glob(os.path.join(datos, "observaciones", "*.json")):
        try:
            with open(ruta, encoding="utf-8") as f:
                out |= set(json.load(f))
        except (OSError, ValueError):
            continue
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="¿ClearSky está aprendiendo de verdad? Compara lo publicado con lo que pasó.")
    ap.add_argument("--datos", default="datos", help="carpeta de datos (por defecto ./datos)")
    ap.add_argument("--ciudad", action="append", help="una ciudad (se puede repetir); por defecto, todas las que tienen datos")
    ap.add_argument("--anticipaciones", default=",".join(map(str, ANTICIPACIONES)), help="horas de antelación a evaluar, separadas por comas")
    ap.add_argument("--remuestras", type=int, default=2000, help="remuestras del intervalo de confianza")
    ap.add_argument("--sin-historial", action="store_true", help="no leer el historial de git de pesos.json")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.datos):
        sys.exit(f"No encuentro la carpeta de datos: {a.datos}. Ejecuta esto desde la carpeta del proyecto.")
    ciudades = a.ciudad or ciudades_con_datos(a.datos)
    if not ciudades:
        sys.exit("No hay observaciones en datos/observaciones: todavía no hay nada que comparar.")
    try:
        antic = [int(x) for x in a.anticipaciones.split(",") if x.strip()]
    except ValueError:
        sys.exit("--anticipaciones debe ser una lista de números, por ejemplo 1,6,24")
    print("AUDITORÍA DEL APRENDIZAJE DE CLEARSKY\n" + "=" * 37)
    print("A) Lo que se publicó contra lo que pasó (datos que el sistema no usó para aprender)")
    historia = primer_pronostico(a.datos)
    if historia:
        desde, hasta = historia
        dias = (hasta - desde).total_seconds() / 86400
        print(f"   El historial de pronósticos publicados empieza el {desde:%Y-%m-%d %H:%M} (guarda {dias:.1f} días de historia).")
        if max(antic) >= 24:
            print(f"   Un pronóstico con ≥24 h de antelación solo se puede comparar con horas posteriores al {desde + timedelta(hours=24):%Y-%m-%d %H:%M}.")
    evals = {c: {n: evaluar(a.datos, c, n, a.remuestras) for n in antic} for c in ciudades}
    cob = {c: cobertura(a.datos, c) for c in ciudades}
    print(f"\n   Horas comparables por ciudad (hacen falta {MIN_HORAS}):")
    print("   " + f"{'ciudad':<15}" + "".join(f"{'≥' + str(n) + ' h':>9}" for n in antic) + f"{'publicadas':>13}{'observadas':>12}")
    for c in ciudades:
        print("   " + f"{c:<15}" + "".join(f"{evals[c][n]['horas']:>9}" for n in antic) + f"{cob[c]['pub']:>13}{cob[c]['obs']:>12}")
    sin_datos = [c for c in ciudades if all(evals[c][n].get("insuficiente") for n in antic)]
    por_motivo = {}
    for c in sin_datos:
        m = motivo_sin_datos(cob[c])
        m = "tienen solo unas pocas horas en común todavía (mira la tabla de arriba)" if m.startswith("solo ") else m      # las cifras ya están en la tabla
        por_motivo.setdefault(m, []).append(c)
    for motivo, cs in por_motivo.items():
        print(f"\n   Todavía sin datos suficientes en {', '.join(cs)}:\n      {motivo}.")
    detalladas = [(c, n) for c in ciudades for n in antic if not evals[c][n].get("insuficiente")]
    if not detalladas:
        print(f"\n   Todavía ninguna ciudad llega a {MIN_HORAS} horas comparables con ninguna antelación: no hay veredicto que dar. Vuelve a ejecutarlo en unos días.")
    for c, n in detalladas:
        imprimir_evaluacion(evals[c][n])
    if not a.sin_historial:
        raiz = os.path.dirname(os.path.abspath(a.datos))
        versiones = versiones_pesos(raiz, 400)
        print("\n" + "=" * 37 + "\nB) Cómo ha cambiado lo aprendido")
        if versiones is None:
            print("   No pude leer el historial de git (¿no es un repositorio o falta git?). Usa --sin-historial para omitir esta parte.")
        else:
            sin_evolucion = []
            for c in ciudades:
                filas = historial_ciudad(versiones, c)
                if len(filas) >= 2:
                    imprimir_historial(c, filas)
                else:
                    sin_evolucion.append(c)
            if sin_evolucion:
                print(f"\nSin evolución que mostrar todavía (menos de 2 verificaciones guardadas en git): {', '.join(sin_evolucion)}.")
    print(NOTAS)


if __name__ == "__main__":
    main()
