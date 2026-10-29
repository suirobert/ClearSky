# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
"""Corre clima_consenso.py de punta a punta con la red simulada y revisa lo que publica."""
import json, os, re
import pytest


def _datos(pagina_generada):
    html = (pagina_generada / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<script id="datos" type="application/json">(.*?)</script>', html, re.S)
    return html, json.loads(m.group(1).replace("<\\/", "</"))


def test_se_publican_las_16_ciudades_con_valores_razonables(pagina_generada, cc):
    _, d = _datos(pagina_generada)
    assert [c["nombre"] for c in d["ciudades"]] == list(cc.CIUDADES), "ninguna ciudad se pierde"
    for c in d["ciudades"]:
        assert len(c["filas"]) >= 48, c["nombre"]
        for r in c["filas"][:48]:
            assert 0 <= r["prob"] <= 100 and 0 <= r["prob_dz"] <= 100 and 0 <= (r["prob_ts"] or 0) <= 100
            assert r["temp"] is None or -15 <= r["temp"] <= 50
            assert r["hum"] is None or 0 <= r["hum"] <= 100
    assert d["feedback_url"] == "https://fb.test" and d["verificacion"]


def test_archivos_de_la_app_instalable_y_de_los_avisos(pagina_generada):
    for a in ["manifest.webmanifest", "sw.js", "icon-192.png", "icon-512.png", "apple-touch-icon.png", "push.json"]:
        assert (pagina_generada / a).exists(), a
    push = json.loads((pagina_generada / "push.json").read_text(encoding="utf-8"))
    assert len(push["ciudades"]) == 16 and push["modelos"], "el servicio de avisos necesita estos datos"
    assert all({"nombre", "lat", "lon", "zona", "cfg"} <= set(c) for c in push["ciudades"])


def test_lo_aprendido_se_guarda_y_la_temperatura_se_corrige(cc):
    p = json.load(open(cc.ARCHIVO_PESOS, encoding="utf-8"))
    assert p["version"] == cc.VERSION
    gye = p["ciudades"]["Guayaquil"]
    assert gye["verificado"] and gye["tabla_lluvia"] is not None and gye["examen"], "Guayaquil quedó verificada con su examen sin trampa"
    t = gye["temp"]
    assert t["verificado"] and t["activa"], "los modelos simulados dan 1,5° de más: la corrección debe activarse"
    assert all(1.0 <= v <= 2.0 for v in t["sesgo"].values()), f"debe aprender ~1,5°: {t['sesgo']}"
    assert t["mae"] < t["mae_crudo"], "corregida acierta más que sin corregir"
    for c in ["Machala", "Santo Domingo", "Loja", "Ambato"]:
        assert not p["ciudades"][c].get("verificado"), f"{c}: su aeropuerto no publica; no puede estar verificada"


def test_las_ciudades_sin_aeropuerto_usan_lo_aprendido_en_la_mas_cercana(pagina_generada):
    _, d = _datos(pagina_generada)
    c = {x["nombre"]: x for x in d["ciudades"]}
    assert not c["Machala"]["propia"] and c["Machala"]["ref"], "Machala usa una ciudad de referencia"
    assert c["Machala"]["cfg"]["tabla_lluvia"], "y hereda su calibración"
    assert c["Guayaquil"]["propia"] and c["Guayaquil"]["ref"] == "Guayaquil"


def test_el_tablero_de_aciertos_se_publica(pagina_generada):
    _, d = _datos(pagina_generada)
    t = d["tablero"]
    assert t and t["ciudades"] and t["global"]["n"] > 0
    g = t["ciudades"]["Guayaquil"]
    assert g["n"] >= 4 and g["a"] + g["f"] + g["e"] == g["n"] and len(g["dias"]) >= 4


def test_la_pagina_trae_lo_nuevo(pagina_generada):
    html, _ = _datos(pagina_generada)
    for texto in ["Gráficas", "Calle inundada", "Escucho truenos", "Lo que reporta la gente", "cargarAlertasRep", "Reportado por usuarios"]:
        assert texto in html, texto
    assert "Hora por hora · " not in html, "el título repetido ya no existe"


def test_validar_pagina_rechaza_paginas_rotas(pagina_generada, cc):
    html, d = _datos(pagina_generada)
    cc.validar_pagina(html, d)                                                       # la buena pasa
    with pytest.raises(RuntimeError, match="marca"): cc.validar_pagina(html.replace("<style>", "<style>__CSS__"), d)
    with pytest.raises(RuntimeError, match="JSON"): cc.validar_pagina(html.replace('type="application/json">{', 'type="application/json">{{'), d)
    corto = json.loads(json.dumps(d)); corto["ciudades"][0]["filas"] = corto["ciudades"][0]["filas"][:10]
    with pytest.raises(RuntimeError, match="solo 10 horas"): cc.validar_pagina(html, corto)
    raro = json.loads(json.dumps(d)); raro["ciudades"][1]["filas"][3]["prob"] = 140
    with pytest.raises(RuntimeError, match="fuera de rango"): cc.validar_pagina(html, raro)


def test_no_hay_errores_inesperados_en_el_registro(cc):
    esperados = ["el aeropuerto solo reportó", "observaciones propias", "íconos Meteocons", "archivo de la lluvia en camino"]
    ruta = cc.ARCHIVO_ERR
    lineas = open(ruta, encoding="utf-8").read().splitlines() if os.path.exists(ruta) else []
    raros = [l for l in lineas if not any(e in l for e in esperados)]
    assert not raros, "errores que no son los de siempre:\n" + "\n".join(raros[:8])
