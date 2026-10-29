# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
"""Funciones puras de clima_consenso.py: calibración, métricas, observaciones, tablero de aciertos y reportes de la gente."""
import csv, json, os
from datetime import date, datetime, timedelta
import pytest
import simulador


def test_calibrar_prob_interpola_y_no_se_sale_de_la_tabla(cc):
    tabla = [[0, 2], [20, 10], [60, 50], [100, 90]]
    assert cc.calibrar_prob(40, []) == 40, "sin tabla no cambia nada"
    assert cc.calibrar_prob(-5, tabla) == 2 and cc.calibrar_prob(0, tabla) == 2
    assert cc.calibrar_prob(40, tabla) == pytest.approx(30)       # a mitad de camino entre (20,10) y (60,50)
    assert cc.calibrar_prob(100, tabla) == 90 and cc.calibrar_prob(150, tabla) == 90
    valores = [cc.calibrar_prob(p, tabla) for p in range(0, 101, 5)]
    assert valores == sorted(valores), "calibrar nunca invierte el orden: más probable sigue siendo más probable"


def test_brier(cc):
    assert cc.brier([100, 0, 100], [1, 0, 1]) == 0
    assert cc.brier([100, 100], [0, 0]) == 1
    assert cc.brier([50, 50], [1, 0]) == 0.25
    assert cc.brier([], []) is None


def test_metricas_tabla_de_contingencia(cc):
    pron = {"2026-01-01T10:00": 1, "2026-01-01T11:00": 1, "2026-01-01T12:00": 0, "2026-01-01T13:00": 0}
    obs = {"2026-01-01T10:00": 1, "2026-01-01T11:00": 0, "2026-01-01T12:00": 1, "2026-01-01T13:00": 0}
    m = cc.metricas(pron, obs, tol=0)
    assert (m["aciertos"], m["falsas"], m["perdidas"], m["neg_ok"]) == (1, 1, 1, 1)
    assert m["pod"] == 50 and m["far"] == 50 and m["exactitud"] == 50.0 and m["hss"] == 0.0
    perfecto = cc.metricas(obs, obs, tol=0); assert perfecto["hss"] == 1.0
    assert cc.metricas({"2026-01-01T10:00": 1}, {"2026-01-01T11:00": 1}, tol=1)["aciertos"] == 1, "tolerancia de ±1 h"


def test_las_observaciones_se_asignan_a_la_hora_siguiente(cc):
    # un reporte a las 14:20 cubre "hasta las 15:00": se guarda con la clave de las 15:00 (igual que los pronósticos por hora)
    obs = cc.parsear_obs("station,valid,tmpf,relh,wxcodes\nSEGU,2026-10-28 14:20,86.0,70,RA", {})
    assert obs == {"2026-10-28T15:00": (1, 1, 0)}
    assert cc.hora_clave(datetime(2026, 1, 1, 10, 0)) == "2026-01-01T10:00", "una hora exacta no se mueve"
    assert cc.hora_clave(datetime(2026, 1, 1, 10, 1)) == "2026-01-01T11:00"
    dos = cc.parsear_obs("station,valid,tmpf,relh,wxcodes\nX,2026-10-28 14:20,86.0,70,RA\nX,2026-10-28 15:00,84.2,75,TSRA", {})
    assert dos == {"2026-10-28T15:00": (1, 1, 1)}, "dos reportes en la misma hora se combinan (el máximo de cada tipo)"


def test_parsear_observaciones_detalle(cc):
    csv_ = "\n".join(["station,valid,tmpf,relh,wxcodes", "X,2026-10-28 15:00,84.2,75,TSRA", "X,2026-10-28 16:00,82.4,80,-DZ",
                      "X,2026-10-28 17:00,80.6,82,VCTS", "X,2026-10-28 18:00,78.8,85,M"])
    temps = {}; obs = cc.parsear_obs(csv_, temps)
    assert obs["2026-10-28T15:00"] == (1, 1, 1), "TSRA: lluvia + llovizna + tormenta"
    assert obs["2026-10-28T16:00"] == (0, 1, 0), "llovizna sola no es lluvia"
    assert obs["2026-10-28T17:00"] == (0, 0, 1), "VCTS: se oyen truenos cerca, sin lluvia"
    assert obs["2026-10-28T18:00"] == (0, 0, 0)
    assert temps["2026-10-28T15:00"][0] == pytest.approx(29.0, abs=0.1) and temps["2026-10-28T15:00"][1] == 75


def test_consenso_ponderado(cc):
    assert cc.prob_consenso({"a": 1.0, "b": 0.0, "c": 2.0}, {}, 0.5) == pytest.approx(66.666, abs=0.01)
    assert cc.prob_consenso({"a": 1.0, "b": 0.0}, {"a": 3.0, "b": 1.0}, 0.5) == 75
    assert cc.prob_consenso({}, {}, 0.5) == 0
    assert cc.mediana_ponderada({"a": 1, "b": 5, "c": 9}, {"a": 1, "b": 1, "c": 1}) == 5


# ---------------- tablero «Cómo nos fue»: acierto / falsa alarma / se nos escapó / dudoso ----------------
CASOS = {1: (70, True, "acierto"), 2: (70, False, "falsa"), 3: (10, False, "acierto"), 4: (10, True, "escapo"),
         5: (30, True, "duda"), 6: (45, True, "acierto"), 7: (5, False, "acierto")}


def test_tablero_de_aciertos_clasifica_bien_cada_dia(cc, tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "DATOS", str(tmp_path)); os.makedirs(tmp_path / "historial"); os.makedirs(tmp_path / "observaciones")
    obs = {}
    for d, (maximo, llovio, _) in CASOS.items():
        dia = date.today() - timedelta(days=d)
        with open(cc.archivo_log(dia), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(cc.CAMPOS_LOG)
            for h in range(24):
                hv = datetime.combine(dia, datetime.min.time()) + timedelta(hours=h)
                w.writerow([(hv - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M"), "Guayaquil", hv.strftime("%Y-%m-%dT%H:00"), "CONSENSO", 0, maximo if h == 15 else 5, 0])
                obs[hv.strftime("%Y-%m-%dT%H:00")] = [1 if (llovio and h == 16) else 0, 0, 0]
    for mes in {date.today().strftime("%Y-%m"), (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")}:
        json.dump({"Guayaquil": obs}, open(tmp_path / "observaciones" / f"{mes}.json", "w"))
    t = cc.tablero_aciertos({"ciudades": {"Guayaquil": {}}, "satelite": {"valido": False}})["ciudades"]["Guayaquil"]
    assert (t["a"], t["f"], t["e"], t["dudas"], t["n"]) == (4, 1, 1, 1, 6)
    for dia in t["dias"]:
        hace = (date.today() - date.fromisoformat(dia["d"])).days
        assert dia["c"] == CASOS[hace][2], f"hace {hace} días: dijimos {dia['max']}% y llovió={dia['llovio']}"


# ---------------- observaciones propias (rayos y satélite) ----------------
def test_observado_se_acumula_y_no_duplica(cc, tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "DATOS", str(tmp_path))
    hist = {"ciudades": ["Guayaquil", "Quito", "Salinas"], "lluvia": {}, "rayos": {}}
    base = datetime(2026, 10, 28, 10, 0)
    for i in range(18):
        t = (base + timedelta(minutes=10 * i)).strftime("%Y-%m-%dT%H:%M")
        hist["lluvia"][t] = [0.0, 2.5 if i in (7, 8) else 0.0, 0.0]
        hist["rayos"][t] = [30, [0, 0, 4 if i in (7, 8) else 0]]
    monkeypatch.setattr(cc, "http_get", lambda *a, **k: json.dumps(hist))
    assert cc.actualizar_observado() == 12 and cc.actualizar_observado() == 0, "la segunda vez no agrega nada"
    sal = cc.cargar_observado()["Salinas"]
    assert sal["2026-10-28T12:00"][2] == 8 and cc.obs_rayos(sal)["2026-10-28T12:00"] == 1, "8 rayos a menos de 25 km = hubo tormenta"
    assert cc.obs_rayos(sal)["2026-10-28T13:00"] == 0


# ---------------- reportes de la gente: los de truenos e inundación no cuentan como clima ----------------
def test_reportes_de_truenos_e_inundacion_se_archivan_pero_no_cuentan_como_lluvia(cc, tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "DATOS", str(tmp_path))
    ahora = int(datetime.now().timestamp())
    filas = [{"id": i + 1, "ts": ahora - 60 * i, "lat": -2.17, "lon": -79.9, "tipo": t}
             for i, t in enumerate(["lluvia", "truenos", "inundado", "soleado", "granizo"])]
    class R:
        def __init__(s, d): s.d = d
        def read(s): return s.d
        def __enter__(s): return s
        def __exit__(s, *a): pass
    pedidos = []
    def urlopen(req, timeout=0): pedidos.append(req.full_url); return R(json.dumps(filas if len(pedidos) == 1 else []).encode())
    monkeypatch.setattr(cc.urllib.request, "urlopen", urlopen)
    assert cc.descargar_reportes() == 5
    guardados = [r for r in cc.leer_reportes(1)]
    assert sorted(r[3] for r in guardados) == ["inundado", "lluvia", "soleado", "truenos"], "se archivan los 4 tipos válidos; 'granizo' se descarta"
    r = cc.verificar_reportes("Guayaquil", guardados)
    assert r["reportes"] == 2, "solo cuentan lluvia y soleado: truenos e inundación no son clima"
    assert ("truenos" in cc.TIPOS_EXTRA) and ("inundado" in cc.TIPOS_EXTRA) and "truenos" not in cc.TIPOS_REPORTE
