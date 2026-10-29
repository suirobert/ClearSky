# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
"""auditar_aprendizaje.py con datos sintéticos de verdad conocida: ¿detecta cuando la calibración sí ayuda, cuando no, y cuando faltan datos?"""
import csv, importlib, json, math, os, random, subprocess, sys
from datetime import date, datetime, timedelta
import pytest


@pytest.fixture(scope="module")
def aud(raiz):
    sys.path.insert(0, str(raiz))
    return importlib.import_module("auditar_aprendizaje")


def sembrar(carpeta, dias=30, modo="ayuda", ciudad="Guayaquil", semilla=1, anticipaciones=(1, 6, 24), inicio=date(2026, 9, 1)):
    """Escribe datos/historial y datos/observaciones como los deja ClearSky.
       La probabilidad VERDADERA de lluvia sigue un patrón diurno y la lluvia se sortea con ella.
       sin calibrar = una versión exagerada de la verdad (se pasa de segura).
       modo «ayuda»: el publicado es la verdad (bien calibrado) · «igual»: el publicado es idéntico al sin calibrar · «ruido»: el publicado es peor que el sin calibrar."""
    rng = random.Random(semilla)
    (carpeta / "historial").mkdir(parents=True, exist_ok=True); (carpeta / "observaciones").mkdir(parents=True, exist_ok=True)
    obs = {}
    for d in range(dias):
        dia = inicio + timedelta(days=d); filas = []
        for h in range(24):
            hv = datetime.combine(dia, datetime.min.time()) + timedelta(hours=h)
            verdadera = 0.05 + 0.5 * max(0.0, math.sin((h - 9) / 24 * 2 * math.pi)) ** 2
            llovio = int(rng.random() < verdadera)
            obs[hv.strftime("%Y-%m-%dT%H:00")] = [llovio, llovio, 0]
            bruta = min(0.98, max(0.02, verdadera + (0.35 if verdadera > 0.2 else -0.03)))
            cal = {"ayuda": verdadera, "igual": bruta, "ruido": min(0.98, max(0.02, 1 - bruta))}[modo]
            for ant in anticipaciones:
                ej = hv - timedelta(hours=ant)
                filas.append([ej.strftime("%Y-%m-%d %H:%M"), ciudad, hv.strftime("%Y-%m-%dT%H:00"), "CONSENSO", 0, round(cal * 100, 1), round(bruta * 100, 1)])
        with open(carpeta / "historial" / f"{dia:%Y-%m-%d}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["ejecucion", "ciudad", "hora_valida", "modelo", "lluvia_mm", "prob_lluvia", "prob_bruta"]); w.writerows(filas)
    por_mes = {}
    for k, v in obs.items():
        por_mes.setdefault(k[:7], {})[k] = v
    for mes, d in por_mes.items():
        with open(carpeta / "observaciones" / f"{mes}.json", "w", encoding="utf-8") as f:
            json.dump({ciudad: d}, f)
    return carpeta


# ---------------------------------------------------------------- piezas
def test_brier_y_mejora_con_un_ejemplo_calculado_a_mano(aud):
    dias = aud.por_dia([("2026-09-01T10:00", 1, 0.8, 0.5), ("2026-09-01T11:00", 0, 0.2, 0.5)])
    s = aud.puntajes(dias, ["2026-09-01"])
    assert s["n"] == 2 and s["llovio"] == 1 and s["base"] == 0.5
    assert s["b_cal"] == pytest.approx(0.04) and s["b_bru"] == pytest.approx(0.25) and s["b_cli"] == pytest.approx(0.25)
    assert s["vs_clima"] == pytest.approx(0.84) and s["vs_bruta"] == pytest.approx(0.84)
    peor = aud.puntajes(aud.por_dia([("2026-09-01T10:00", 1, 0.2, 0.8), ("2026-09-01T11:00", 0, 0.8, 0.2)]), ["2026-09-01"])
    assert peor["vs_clima"] < 0 and peor["vs_bruta"] < 0, "un pronóstico peor da mejora NEGATIVA"


def test_el_pronostico_elegido_es_el_ultimo_hecho_con_al_menos_esa_antelacion(aud, tmp_path):
    (tmp_path / "historial").mkdir()
    with open(tmp_path / "historial" / "2026-09-01.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["ejecucion", "ciudad", "hora_valida", "modelo", "lluvia_mm", "prob_lluvia", "prob_bruta"])
        for ant, p in ((3, 30), (8, 50), (20, 70)):               # la misma hora (12:00), pronosticada 3, 8 y 20 horas antes
            w.writerow([(datetime(2026, 9, 1, 12) - timedelta(hours=ant)).strftime("%Y-%m-%d %H:%M"), "Guayaquil", "2026-09-01T12:00", "CONSENSO", 0, p, p + 5])
        w.writerow(["2026-09-01 09:00", "Quito", "2026-09-01T12:00", "CONSENSO", 0, 99, 99])      # otra ciudad: no cuenta
        w.writerow(["2026-09-01 09:00", "Guayaquil", "2026-09-01T12:00", "OTRO", 0, 99, 99])      # otro modelo: no cuenta
    assert aud.leer_publicados(str(tmp_path), "Guayaquil", 1)["2026-09-01T12:00"] == (30.0, 35.0), "con ≥1 h: el más reciente (hecho 3 h antes)"
    assert aud.leer_publicados(str(tmp_path), "Guayaquil", 6)["2026-09-01T12:00"] == (50.0, 55.0), "con ≥6 h: el hecho 8 h antes, no el de 3 h"
    assert aud.leer_publicados(str(tmp_path), "Guayaquil", 24) == {}, "nada se hizo con 24 h de antelación"


def test_una_hora_sin_prob_bruta_no_rompe_la_lectura(aud, tmp_path):
    (tmp_path / "historial").mkdir()
    with open(tmp_path / "historial" / "2026-09-01.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["ejecucion", "ciudad", "hora_valida", "modelo", "lluvia_mm", "prob_lluvia", "prob_bruta"])
        w.writerow(["2026-09-01 08:00", "Guayaquil", "2026-09-01T12:00", "CONSENSO", 0, 40, ""])
        w.writerow(["2026-09-01 08:00", "Guayaquil", "2026-09-01T13:00", "CONSENSO", 0, "mala", 10])
    assert aud.leer_publicados(str(tmp_path), "Guayaquil", 1) == {"2026-09-01T12:00": (40.0, None)}


# ---------------------------------------------------------------- veredictos con verdad conocida
def test_si_la_calibracion_de_verdad_ayuda_lo_detecta_con_confianza(aud, tmp_path):
    sembrar(tmp_path, dias=30, modo="ayuda")
    r = aud.evaluar(str(tmp_path), "Guayaquil", 6, 600)
    assert r["dias"] == 30 and r["horas"] == 720
    assert r["total"]["vs_bruta"] > 0.1 and r["ic_bruta"][0] > 0 and r["ver_bruta"].startswith("MEJOR"), (r["total"], r["ic_bruta"])
    assert r["total"]["vs_clima"] > 0 and r["ic_clima"][0] > 0 and r["ver_clima"].startswith("MEJOR")


def test_si_publicado_y_sin_calibrar_son_iguales_dice_que_no_se_distingue(aud, tmp_path):
    sembrar(tmp_path, dias=30, modo="igual")
    r = aud.evaluar(str(tmp_path), "Guayaquil", 6, 600)
    assert r["total"]["vs_bruta"] == pytest.approx(0) and r["ver_bruta"] == "no se distingue del azar todavía", "no inventa mejoras donde no las hay"


def test_si_el_publicado_es_peor_lo_dice(aud, tmp_path):
    sembrar(tmp_path, dias=30, modo="ruido")
    r = aud.evaluar(str(tmp_path), "Guayaquil", 6, 600)
    assert r["total"]["vs_bruta"] < 0 and r["ic_bruta"][1] < 0 and r["ver_bruta"].startswith("PEOR"), (r["total"], r["ic_bruta"])


def test_con_pocos_dias_no_concluye_aunque_los_numeros_se_vean_bien(aud, tmp_path):
    sembrar(tmp_path, dias=5, modo="ayuda")
    r = aud.evaluar(str(tmp_path), "Guayaquil", 6, 400)
    assert r["total"]["vs_bruta"] > 0 and r["ver_bruta"].startswith("pocos días (5 de 14)"), r["ver_bruta"]


def test_sin_datos_suficientes_no_calcula(aud, tmp_path):
    sembrar(tmp_path, dias=2, modo="ayuda")
    r = aud.evaluar(str(tmp_path), "Guayaquil", 6, 200)
    assert r["insuficiente"] and r["horas"] < 72
    assert aud.evaluar(str(tmp_path), "Quito", 6, 200)["insuficiente"], "una ciudad sin datos"


def test_el_intervalo_es_determinista_y_contiene_la_estimacion(aud, tmp_path):
    sembrar(tmp_path, dias=30, modo="ayuda")
    a = aud.evaluar(str(tmp_path), "Guayaquil", 6, 500); b = aud.evaluar(str(tmp_path), "Guayaquil", 6, 500)
    assert a["ic_bruta"] == b["ic_bruta"] and a["ic_clima"] == b["ic_clima"], "el mismo dato da siempre el mismo intervalo"
    lo, hi = a["ic_clima"]; assert lo <= a["total"]["vs_clima"] <= hi
    ancho = lambda r: r["ic_clima"][1] - r["ic_clima"][0]
    sembrar(tmp_path / "poco", dias=15, modo="ayuda", semilla=2)
    assert ancho(aud.evaluar(str(tmp_path / "poco"), "Guayaquil", 6, 500)) > ancho(a), "con menos días hay más incertidumbre"


def test_el_intervalo_remuestrea_dias_no_horas(aud):
    """Si las horas de un día están todas «pegadas» (un día entero bueno, otro entero malo), el intervalo tiene que ser ancho."""
    dias = {f"2026-09-{d:02d}": [24, 6, 0.5 if d % 2 else 6.0, 0.0, 24] for d in range(1, 21)}       # días alternos muy buenos y muy malos
    ic_clima, _ = aud.intervalos(dias, 400)
    suelto = {f"2026-09-{d:02d}": [24, 6, 3.25, 0.0, 24] for d in range(1, 21)}                       # mismo promedio, pero parejo todos los días
    ic_parejo, _ = aud.intervalos(suelto, 400)
    assert ic_clima[1] - ic_clima[0] > 5 * (ic_parejo[1] - ic_parejo[0]), (ic_clima, ic_parejo)


def test_anuncios_y_tendencia(aud):
    pares = [("2026-09-01T10:00", 1, 0.6, None), ("2026-09-01T11:00", 0, 0.5, None), ("2026-09-01T12:00", 1, 0.45, None), ("2026-09-01T13:00", 0, 0.1, None)]
    assert aud.anuncios(pares) == (3, 2)
    assert aud.tendencia(aud.por_dia(pares)) is None, "con un solo día no hay tendencia"


# ---------------------------------------------------------------- historial en git
def _git(carpeta, *args):
    subprocess.run(["git", "-C", str(carpeta), *args], check=True, capture_output=True, text=True)


def _pesos(actualizado, pesos, ex_clima=12, ex_crudo=8, real=5):
    return {"actualizado": actualizado, "version": 11, "ciudades": {"Guayaquil": {"verificado": True, "umbral": 1.0, "pesos": pesos, "modo_calibracion": "reciente",
            "examen": {"mejora_vs_clima_pct": ex_clima, "mejora_vs_crudo_pct": ex_crudo}, "real": {"mejora_vs_clima_pct": real, "horas": 200}},
            "Quito": {"verificado": False}}}


def test_el_historial_de_git_muestra_la_evolucion_y_los_cambios_de_pesos(aud, tmp_path):
    _git(tmp_path, "init", "-q"); _git(tmp_path, "config", "user.email", "a@b.c"); _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "datos").mkdir()
    versiones = [("2026-10-01T04:00:00", {"a": 1.0, "b": 1.0}, 10, 6, 3), ("2026-10-02T04:00:00", {"a": 1.2, "b": 0.8}, 14, 9, 6),
                 ("2026-10-02T09:00:00", {"a": 1.2, "b": 0.8}, 15, 9, 6), ("2026-10-03T04:00:00", {"a": 1.2, "b": 0.8}, 13, 8, 9)]
    for act, pesos, ex, cr, real in versiones:
        (tmp_path / "datos" / "pesos.json").write_text(json.dumps(_pesos(act, pesos, ex, cr, real)), encoding="utf-8")
        _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-q", "-m", act)
    filas = aud.historial_ciudad(aud.versiones_pesos(str(tmp_path), 50), "Guayaquil")
    assert [f["fecha"] for f in filas] == ["2026-10-01", "2026-10-02", "2026-10-03"], "una fila por día, en orden"
    assert filas[1]["ex_clima"] == 15, "de un mismo día se queda la última verificación"
    assert [f["real_clima"] for f in filas] == [3, 6, 9]
    assert aud.cambio_pesos(filas[0]["pesos"], filas[1]["pesos"]) == pytest.approx(0.4) and aud.cambio_pesos(filas[1]["pesos"], filas[2]["pesos"]) == 0
    assert aud.historial_ciudad(aud.versiones_pesos(str(tmp_path), 50), "Quito") == [], "una ciudad sin verificar no aparece"


def test_fuera_de_un_repositorio_git_lo_avisa_en_vez_de_fallar(aud, tmp_path):
    assert aud.versiones_pesos(str(tmp_path), 10) is None


# ---------------------------------------------------------------- el script completo
def test_el_script_completo_imprime_el_informe(aud, tmp_path, capsys):
    carpeta = tmp_path / "datos"; sembrar(carpeta, dias=30, modo="ayuda")
    aud.main(["--datos", str(carpeta), "--anticipaciones", "6", "--remuestras", "300", "--sin-historial"])
    salida = capsys.readouterr().out
    for esperado in ("AUDITORÍA DEL APRENDIZAJE", "Guayaquil", "≥ 6 h", "¿La calibración sirve de verdad?", "MEJOR", "Cómo leer esto", "optimista"):
        assert esperado in salida, esperado
    assert "B) Cómo ha cambiado" not in salida


def test_el_script_sin_carpeta_de_datos_da_un_mensaje_claro(aud, tmp_path):
    with pytest.raises(SystemExit) as e:
        aud.main(["--datos", str(tmp_path / "no_existe")])
    assert "carpeta de datos" in str(e.value)


# ================================================================= lo que mostró la ejecución real: pocos datos y ceros sin explicación
def _escribir_historial(carpeta, ciudad, inicio_ejec, horas_validas, prob=30.0):
    (carpeta / "historial").mkdir(parents=True, exist_ok=True)
    with open(carpeta / "historial" / "2026-10-28.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if f.tell() == 0:
            w.writerow(["ejecucion", "ciudad", "hora_valida", "modelo", "lluvia_mm", "prob_lluvia", "prob_bruta"])
        for hv in horas_validas:
            w.writerow([inicio_ejec.strftime("%Y-%m-%d %H:%M"), ciudad, hv.strftime("%Y-%m-%dT%H:00"), "CONSENSO", 0, prob, prob + 5])


def _escribir_obs(carpeta, ciudad, horas, lluvia=()):
    (carpeta / "observaciones").mkdir(parents=True, exist_ok=True)
    ruta = carpeta / "observaciones" / "2026-10.json"
    actual = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    actual.setdefault(ciudad, {}).update({h.strftime("%Y-%m-%dT%H:00"): [1 if h in lluvia else 0, 0, 0] for h in horas})
    ruta.write_text(json.dumps(actual), encoding="utf-8")


def _escenario_real(tmp_path):
    """Lo que había el 29 de oct: historial desde el 28 a las 20:00; Guayaquil con 23 h en común y 1 hora de lluvia; Ambato sin observaciones;
       Coca con observaciones de otro día (no coinciden)."""
    ej = datetime(2026, 10, 28, 20, 0)
    horas = [datetime(2026, 10, 28, 21) + timedelta(hours=i) for i in range(23)]
    _escribir_historial(tmp_path, "Guayaquil", ej, horas); _escribir_obs(tmp_path, "Guayaquil", horas, lluvia={horas[10]})
    _escribir_historial(tmp_path, "Guayaquil", datetime(2026, 10, 29, 2, 0), horas[10:])            # otra ejecución, más tarde: «primera» y «última» difieren
    _escribir_historial(tmp_path, "Ambato", ej, horas)                                                  # pronósticos, pero ningún aeropuerto
    _escribir_historial(tmp_path, "Coca", ej, horas); _escribir_obs(tmp_path, "Coca", [datetime(2026, 9, 1) + timedelta(hours=i) for i in range(48)])
    return tmp_path


def test_la_salida_resume_en_una_tabla_y_no_repite_tres_bloques_por_ciudad(aud, tmp_path, capsys):
    carpeta = _escenario_real(tmp_path)
    aud.main(["--datos", str(carpeta), "--sin-historial", "--remuestras", "100"])
    salida = capsys.readouterr().out
    assert "Horas comparables por ciudad (hacen falta 72)" in salida and "≥24 h" in salida
    assert salida.count("Todavía no hay datos suficientes") == 0, "ya no se repite una frase por ciudad y antelación"
    fila = [l for l in salida.splitlines() if l.strip().startswith("Guayaquil")][0].split()
    assert fila[1] == "23" and fila[-2:] == ["23", "23"], fila               # 23 h comparables con ≥1 h; 23 publicadas y 23 observadas
    assert fila[3] == "0", f"con ≥24 h de antelación no hay nada comparable: {fila}"
    assert "Todavía ninguna ciudad llega a 72 horas" in salida, "y dice claramente que no hay veredicto todavía"


def test_explica_por_que_cada_ciudad_no_tiene_datos(aud, tmp_path, capsys):
    carpeta = _escenario_real(tmp_path)
    aud.main(["--datos", str(carpeta), "--sin-historial", "--remuestras", "100", "--ciudad", "Ambato", "--ciudad", "Coca"])
    salida = capsys.readouterr().out
    assert "Todavía sin datos suficientes en" in salida
    assert "Ambato" in salida and "no hay observaciones de aeropuerto" in salida
    assert "Coca" in salida and "ninguna hora coincide" in salida, "hay pronósticos y observaciones, pero de fechas distintas"
    assert aud.motivo_sin_datos({"pub": 0, "obs": 5, "comunes": 0}) == "no hay pronósticos publicados de esta ciudad en datos/historial"
    assert aud.motivo_sin_datos({"pub": 9, "obs": 9, "comunes": 20}) == "solo 20 horas en común"


def test_explica_por_que_no_hay_pronosticos_con_24_h_de_antelacion(aud, tmp_path, capsys):
    carpeta = _escenario_real(tmp_path)
    aud.main(["--datos", str(carpeta), "--sin-historial", "--remuestras", "100", "--ciudad", "Guayaquil"])
    salida = capsys.readouterr().out
    assert "El historial de pronósticos publicados empieza el 2026-10-28 20:00" in salida
    assert "(guarda 0.2 días de historia)" in salida, "la duración va de la primera a la última ejecución (6 h = 0,25 días)"
    assert "solo se puede comparar con horas posteriores al 2026-10-29 20:00" in salida
    pri = aud.primer_pronostico(str(carpeta)); assert pri[0] == datetime(2026, 10, 28, 20, 0)
    assert aud.primer_pronostico(str(carpeta / "no_hay")) is None


def test_con_pocas_horas_con_lluvia_no_concluye_aunque_haya_muchas_horas_y_dias(aud, tmp_path):
    """El caso peligroso: 30 días de datos pero casi sin lluvia. Un pronóstico con probabilidades razonables saldría «peor» que el promedio."""
    carpeta = tmp_path
    (carpeta / "historial").mkdir(); (carpeta / "observaciones").mkdir()
    rng = random.Random(3); obs = {}
    for d in range(30):
        dia = date(2026, 9, 1) + timedelta(days=d); filas = []
        for h in range(24):
            hv = datetime.combine(dia, datetime.min.time()) + timedelta(hours=h)
            llovio = int(d == 4 and h == 15)                                    # una sola hora de lluvia en un mes
            obs[hv.strftime("%Y-%m-%dT%H:00")] = [llovio, 0, 0]
            filas.append([(hv - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M"), "Guayaquil", hv.strftime("%Y-%m-%dT%H:00"), "CONSENSO", 0, 25.0, 30.0])
        with open(carpeta / "historial" / f"{dia:%Y-%m-%d}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["ejecucion", "ciudad", "hora_valida", "modelo", "lluvia_mm", "prob_lluvia", "prob_bruta"]); w.writerows(filas)
    (carpeta / "observaciones" / "2026-09.json").write_text(json.dumps({"Guayaquil": obs}), encoding="utf-8")
    r = aud.evaluar(str(carpeta), "Guayaquil", 6, 200)
    assert r["dias"] == 30 and r["total"]["llovio"] == 1
    assert r["ver_clima"].startswith("pocas horas con lluvia (1 de 10"), r["ver_clima"]
    assert not r["ver_clima"].startswith("PEOR") and not r["ver_clima"].startswith("MEJOR")


def test_el_historial_marca_con_asterisco_los_porcentajes_con_pocos_datos(aud, capsys):
    f = lambda fecha, real, horas, lluvia: {"actualizado": fecha + "T04:00:00", "fecha": fecha, "pesos": {"a": 1.0}, "umbral": 1.0, "modo": "reciente", "ex_clima": 29, "ex_crudo": 11,
                                           "real_clima": real, "real_horas": horas, "real_lluvia": lluvia}
    aud.imprimir_historial("Guayaquil", [f("2026-10-28", -51, 16, 1), f("2026-10-29", -147, 23, 1)])
    salida = capsys.readouterr().out
    assert "-147% (23 h · 1 con lluvia)*" in salida and "-51% (16 h · 1 con lluvia)*" in salida
    assert "Muy pocos datos" in salida and "NO significa que el pronóstico sea malo ni bueno" in salida
    aud.imprimir_historial("Quito", [f("2026-10-01", 20, 300, 50), f("2026-10-02", 22, 320, 55)])
    limpio = capsys.readouterr().out
    assert "*" not in limpio.replace("* ", "") and "Muy pocos datos" not in limpio, "con datos suficientes no hay advertencia"


def test_un_pronostico_perfecto_con_pocas_horas_puede_salir_negativo_y_por_eso_se_exige_un_minimo(aud):
    """La razón de ser del mínimo de horas con lluvia, comprobada: pronóstico PERFECTAMENTE calibrado, 23 horas, 1 hora de lluvia."""
    rng = random.Random(11); malos = 0; n = 300
    for _ in range(n):
        horas = [rng.randrange(24) for _ in range(23)]
        pv = [0.04 + 0.55 * max(0.0, math.sin((h - 9) / 24 * 2 * math.pi)) ** 2 for h in horas]
        while True:
            o = [int(rng.random() < q) for q in pv]
            if sum(o) == 1: break
        dias = aud.por_dia([(f"2026-09-01T{h:02d}:00", oo, q, None) for h, oo, q in zip(range(23), o, pv)])
        s = aud.puntajes(dias, ["2026-09-01"])
        malos += s["vs_clima"] < 0
    assert malos / n > 0.8, "con 1 hora de lluvia, un pronóstico perfecto sale peor que el promedio casi siempre: la métrica no sirve ahí"


def test_la_salida_agrupa_las_ciudades_con_el_mismo_problema_en_vez_de_repetir_parrafos(aud, tmp_path, capsys):
    carpeta = _escenario_real(tmp_path)
    ej = datetime(2026, 10, 28, 20, 0); horas = [datetime(2026, 10, 28, 21) + timedelta(hours=i) for i in range(20)]
    _escribir_historial(carpeta, "Quito", ej, horas); _escribir_obs(carpeta, "Quito", horas)
    aud.main(["--datos", str(carpeta), "--sin-historial", "--remuestras", "50", "--ciudad", "Guayaquil", "--ciudad", "Quito", "--ciudad", "Ambato"])
    salida = capsys.readouterr().out
    assert salida.count("tienen solo unas pocas horas en común") == 1 and "Guayaquil, Quito" in salida, "Guayaquil y Quito van en un solo párrafo"
    assert salida.count("Todavía sin datos suficientes en") == 2, "uno para «pocas horas» y otro para Ambato"


def test_en_el_historial_las_ciudades_sin_evolucion_van_en_una_sola_linea(aud, tmp_path, capsys):
    _git(tmp_path, "init", "-q"); _git(tmp_path, "config", "user.email", "a@b.c"); _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "datos").mkdir()
    carpeta = _escenario_real(tmp_path / "datos")
    for act, real in (("2026-10-28T04:00:00", 5), ("2026-10-29T04:00:00", 7)):
        (tmp_path / "datos" / "pesos.json").write_text(json.dumps(_pesos(act, {"a": 1.0}, 10, 8, real)), encoding="utf-8")
        _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-q", "-m", act)
    aud.main(["--datos", str(tmp_path / "datos"), "--remuestras", "50", "--ciudad", "Guayaquil", "--ciudad", "Ambato", "--ciudad", "Coca"])
    salida = capsys.readouterr().out
    assert "Guayaquil · lo aprendido día a día" in salida, "la que sí tiene 2 verificaciones se muestra"
    assert "Sin evolución que mostrar todavía" in salida and "Ambato, Coca" in salida
    assert salida.count("menos de 2 verificaciones") == 1, "una sola línea para todas las demás"
