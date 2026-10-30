# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
"""La página en un navegador real: diseño, pestañas, reportes, avisos de la gente, mapa y lo que ya existía."""
import json
import pytest
from conftest import alertas, ahora_json, hace, hora_ec

PC = dict(ancho=1440)
VARIOS = [dict(), dict(movil=True), dict(tema="light")]


# ======================= lo básico =======================
@pytest.mark.parametrize("config", VARIOS, ids=["pc-oscuro", "celular", "pc-claro"])
def test_la_pagina_carga_sin_errores_de_javascript(abrir, config):
    p = abrir(**config)
    assert p.pagina.inner_text(".tit"), "hay titular"
    assert p.pagina.locator("#c24 .hc").count() >= 24, "la tira de 24 horas"
    assert p.errores == []


def test_pestanas_se_llaman_graficas_mapa_satelite(abrir):
    p = abrir()
    assert p.pagina.eval_on_selector_all(".tab", "ts => ts.map(t => t.textContent.trim())") == ["Gráficas", "Mapa", "Satélite"]


def test_el_titulo_repetido_ya_no_esta_y_solo_aparece_al_elegir_un_dia(abrir):
    p = abrir(); pg = p.pagina
    assert not pg.locator("#gtit").is_visible(), "sin día elegido no hay título: los botones 24 h / 3 días / 7 días ya lo dicen"
    assert "hora por hora" not in pg.inner_text("#panel-graf").lower()
    pg.locator(".drow").nth(1).click()
    pg.wait_for_selector("#gtit", state="visible")
    assert "hora por hora" in pg.inner_text("#gtit").lower() and pg.locator("#todo").is_visible()
    pg.click("#todo")
    assert not pg.locator("#gtit").is_visible() and pg.locator("#rsel").is_visible()


def test_selector_de_rango_24h_3dias_7dias(abrir):
    p = abrir(hora_local="14:05"); pg = p.pagina       # reloj fijo: con poco día por delante el tramo de hoy es angosto (ver test_horas_del_dia.py)
    for rango, minimo in (("24", 24), ("72", 72), ("168", 168)):
        pg.click(f"#rsel .rb[data-r='{rango}']")
        assert pg.eval_on_selector("#rsel .rb.on", "e => e.dataset.r") == rango
    assert pg.locator("#graf svg text:text-is('Hoy')").count() == 1 and pg.locator("#graf svg text").count() > 20, "en 7 días hay cabecera por día"
    assert p.errores == []


def test_desplegables_siguen_abiertos_cuando_la_pagina_se_actualiza(abrir):
    p = abrir(); pg = p.pagina
    pg.evaluate("document.querySelector('#aciertos').open = true; document.querySelector('#como').open = true")
    pg.evaluate("render()"); pg.wait_for_timeout(200)
    assert pg.evaluate("['aciertos', 'como'].map(i => document.getElementById(i).open)") == [True, True]


def test_diseno_en_pc_y_celular(abrir):
    pg = abrir().pagina
    m = pg.evaluate("""() => { const r = s => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return {x: b.left, y: b.top + scrollY, r: b.right, b: b.bottom + scrollY}; };
        const chips = [...document.querySelectorAll('.chips .chip')].map(c => c.getBoundingClientRect().right);
        return {col1: r('.col1'), chips: r('.chips'), maxChip: Math.max(...chips), acc: r('.acciones'), dias: r('#dias'), tabs: r('.cmulti')}; }""")
    assert m["maxChip"] <= m["col1"]["r"] + 1, "las etiquetas no se salen de su columna"
    assert m["acc"]["y"] >= m["dias"]["b"] - 2, "los botones van al final de la columna izquierda"
    assert m["tabs"]["y"] < m["chips"]["b"], "las pestañas empiezan junto a la cabecera: sin hueco"
    mv = abrir(movil=True).pagina
    assert mv.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "en celular no hay desborde horizontal"


def test_etiquetas_con_color_propio(abrir):
    pg = abrir(hora_local="14:05").pagina                  # reloj fijo: de noche no hay etiqueta de UV
    colores = pg.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.chips .chip')].map(c => [c.className.replace('chip', '').trim() || 'base', getComputedStyle(c).backgroundColor]))""")
    assert "hum" in colores and any(k.startswith("uv") for k in colores), colores
    assert len(set(colores.values())) >= 4, "humedad, UV, sensación y tormenta se distinguen entre sí"


# ======================= reportar el clima =======================
def _abrir_reporte(pg):
    pg.click("#breporte"); pg.wait_for_selector("#sheet .fbb button")


def test_botones_de_reporte_de_dia_y_de_noche(abrir):
    dia = abrir(hora_local="14:05"); _abrir_reporte(dia.pagina)
    nombres = lambda pg: pg.eval_on_selector_all(".fbb button", "bs => bs.map(b => b.textContent.trim())")
    n = nombres(dia.pagina)
    assert len(n) == 9 and {"Soleado", "Despejado", "Escucho truenos", "Calle inundada", "Aguacero"} <= {x.lstrip("☀️🔆☁️🌦️🌧️☔⛈️🔊🌊🌙 ") for x in n}, n
    assert dia.pagina.locator(".fbb").count() == 2 and dia.pagina.locator(".fbx button").count() == 2, "dos grupos: clima y «¿Algo más?»"
    noche = abrir(hora_local="23:00"); _abrir_reporte(noche.pagina)
    n2 = nombres(noche.pagina)
    assert len(n2) == 8 and not any("Soleado" in x for x in n2), "de noche no hay «Soleado»"
    assert any("🌙" in x and "Despejado" in x for x in n2), "y «Despejado» lleva la luna"
    assert "☔" in " ".join(n) and n.count([x for x in n if "Calle inundada" in x][0]) == 1
    assert "🌊" not in [x for x in n if "Aguacero" in x][0], "Aguacero ya no usa la ola: es de «Calle inundada»"


def test_cada_categoria_tiene_su_propio_tiempo_de_espera(abrir):
    enviados = []
    p = abrir(reporte=lambda t: (enviados.append(t), (200, {"ok": True}))[1]); pg = p.pagina; _abrir_reporte(pg)
    clic = lambda texto: (pg.locator(f".fbb button:has-text('{texto}')").click(), pg.wait_for_timeout(250))
    clic("Lluvia"); assert enviados == ["lluvia"] and "Registramos" in pg.inner_text("#fbres")
    clic("Lluvia"); assert enviados == ["lluvia"], "otro de clima: espera 30 min"; assert "30 minutos" in pg.inner_text("#fbres")
    clic("Escucho truenos"); assert enviados == ["lluvia", "truenos"], "truenos no se bloquea por haber reportado clima"
    assert "2 personas" in pg.inner_text("#fbres") or "otra persona" in pg.inner_text("#fbres")
    clic("Escucho truenos"); assert enviados == ["lluvia", "truenos"] and "10 minutos" in pg.inner_text("#fbres")
    clic("Calle inundada"); assert enviados == ["lluvia", "truenos", "inundado"]
    assert "911" in pg.inner_text("#fbres") and "otra persona" in pg.inner_text("#fbres"), "dice que hace falta confirmación y menciona el 911"
    guardado = pg.evaluate("Object.keys(localStorage).filter(k => k.startsWith('cc_fb_ts')).sort()")
    assert guardado == ["cc_fb_ts", "cc_fb_ts_inundado", "cc_fb_ts_truenos"]
    assert p.errores == []


def test_con_el_servicio_sin_actualizar_los_reportes_nuevos_no_se_guardan_como_otra_cosa(abrir):
    enviados = []
    def viejo(t):
        enviados.append(t)
        return (400, {"error": "datos"}) if t in ("despejado", "truenos", "inundado") else (200, {"ok": True})
    p = abrir(reporte=viejo, permitir_errores_red=True); pg = p.pagina; _abrir_reporte(pg)
    pg.locator(".fbb button:has-text('Despejado')").click(); pg.wait_for_timeout(300)
    assert enviados == ["despejado", "soleado"], "«Despejado» se reenvía como «Soleado»: sigue contando como cielo sin lluvia"
    pg.locator(".fbb button:has-text('Escucho truenos')").click(); pg.wait_for_timeout(300)
    assert enviados == ["despejado", "soleado", "truenos"], "los truenos NO se reenvían como otro tipo"
    assert "todavía no está disponible" in pg.inner_text("#fbres")
    pg.locator(".fbb button:has-text('Calle inundada')").click(); pg.wait_for_timeout(300)
    assert enviados[-1] == "inundado" and "todavía no está disponible" in pg.inner_text("#fbres")
    assert pg.evaluate("localStorage.getItem('cc_fb_ts_truenos')") is None, "un reporte rechazado no activa la espera"


def test_reportar_fuera_de_ecuador_no_envia_nada(abrir):
    p = abrir(lat=25.77, lon=-80.19); pg = p.pagina; _abrir_reporte(pg)
    pg.locator(".fbb button:has-text('Calle inundada')").click(); pg.wait_for_timeout(300)
    assert "fuera de Ecuador" in pg.inner_text("#fbres")
    assert not [x for x in p.pedidos if x[0] == "POST" and "/reporte" in x[1]], "no se envió nada"


# ======================= inundación y truenos reportados por la gente =======================
def test_banner_de_inundacion_solo_con_dos_o_mas_personas(abrir):
    p = abrir(alertas_rep=alertas(inundado=(3, 12))); pg = p.pagina
    b = pg.locator(".rep-inund"); assert b.count() == 1
    t = b.inner_text()
    for texto in ("reportan calles inundadas", "3 personas", "reportado por usuarios", "no está verificado", "no cruces", "911"):
        assert texto in t.lower(), texto             # el CSS escribe la etiqueta en mayúsculas
    assert p.errores == []
    for caso, a in (("una sola persona", alertas(inundado=(1, 5))), ("ninguna", alertas()), ("ya venció (4 h)", alertas(inundado=(3, 240)))):
        assert abrir(alertas_rep=a).pagina.locator(".rep-inund").count() == 0, caso


def test_chip_de_truenos(abrir):
    t = abrir(alertas_rep=alertas(truenos=(3, 5))).pagina
    assert "Escuchan truenos cerca" in t.inner_text(".chips") and "3 personas" in t.inner_text(".chip.tru")
    assert abrir(alertas_rep=alertas(truenos=(1, 5))).pagina.locator(".chip.tru").count() == 0, "un solo reporte no basta"
    assert abrir(alertas_rep=alertas(truenos=(3, 45))).pagina.locator(".chip.tru").count() == 0, "a los 30 min ya no se muestra"


def test_en_celular_el_chip_de_truenos_es_el_primero_y_el_banner_no_aprieta_el_texto(abrir):
    pg = abrir(movil=True, alertas_rep=alertas(inundado=(3, 12), truenos=(3, 5))).pagina
    assert "tru" in pg.eval_on_selector(".chips .chip:first-child", "e => e.className"), "la fila de etiquetas se desplaza: un dato de seguridad va primero"
    caja = pg.evaluate("""() => { const b = document.querySelector('.rep-inund'), t = b.querySelector('span:last-child');
        return {banner: b.getBoundingClientRect().width, texto: t.getBoundingClientRect().width, etiqueta: b.querySelector('.etq').getBoundingClientRect().top < t.getBoundingClientRect().top}; }""")
    assert caja["texto"] >= caja["banner"] * 0.8, f"el texto ocupa todo el ancho del banner: {caja}"
    assert caja["etiqueta"], "la etiqueta va arriba del texto, no al lado"


def test_si_el_satelite_tambien_ve_rayos_el_aviso_menciona_a_la_gente(abrir):
    rayos = {"t": hora_ec(-2), "radio_km": 25, "celdas": [[-2.17, -79.92, 12, 14]]}
    pg = abrir(alertas_rep=alertas(truenos=(3, 5)), ahora=ahora_json(rayos=rayos)).pagina
    pg.wait_for_selector(".ah-rayo", timeout=8000)
    t = pg.inner_text(".ah-rayo")
    assert "Tormenta eléctrica activa" in t and "3 personas también escuchan truenos" in t


def test_el_servicio_de_alertas_caido_no_rompe_la_pagina(abrir):
    p = abrir(alertas_rep=lambda r: (500, {"error": "interno"}), permitir_errores_red=True)
    assert p.pagina.locator(".rep-inund").count() == 0 and p.pagina.locator(".chip.tru").count() == 0 and p.errores == []
    assert p.pagina.inner_text(".tit")


def test_marcas_de_inundacion_en_el_mapa(abrir):
    puntos = [(-2.17, -79.90, 3, 12), (-2.20, -79.95, 2, 40)]
    pg = abrir(alertas_rep=alertas(inundado=(3, 12), puntos=puntos)).pagina
    pg.click("#tab-mapa"); pg.wait_for_function("window.__L && window.__L.mapas > 0")
    marcas = pg.evaluate("window.__L.marcas.filter(m => m.op.fillColor === '#0ea5e9').map(m => m.x._tt)")
    assert len(marcas) == 2 and all("no verificado" in m and "reportan inundación" in m for m in marcas), marcas
    assert "no verificados" in pg.inner_text(".rep-ley")
    sin = abrir(alertas_rep=alertas()).pagina
    sin.click("#tab-mapa"); sin.wait_for_function("window.__L && window.__L.mapas > 0")
    assert sin.evaluate("window.__L.marcas.filter(m => m.op.fillColor === '#0ea5e9').length") == 0 and sin.locator(".rep-ley").count() == 0


def test_fuera_de_ecuador_no_se_muestran_ni_se_piden_los_reportes_de_la_gente(abrir):
    p = abrir(lat=25.77, lon=-80.19, alertas_rep=alertas(inundado=(5, 5), truenos=(5, 5)))
    pg = p.pagina
    assert pg.locator(".aviso-top.fuera").count() == 1, "avisa que está fuera de Ecuador"
    assert pg.locator(".rep-inund").count() == 0 and pg.locator(".chip.tru").count() == 0
    assert not [x for x in p.pedidos if "/alertas" in x[1]], "ni siquiera consulta el servicio"
    assert pg.inner_text("#aciertos summary").endswith("solo en Ecuador")


def test_la_inundacion_aparece_en_la_vista_de_un_lugar_guardado(abrir):
    lugar = [{"id": "-2.17,-79.90", "nombre": "Casa, Guayaquil", "lat": -2.17, "lon": -79.9}]
    # se consulta por el punto que se está viendo: el servicio recibe esas coordenadas
    p = abrir(lat=-0.18, lon=-78.47, lugares=lugar, alertas_rep=lambda r: alertas(inundado=(2, 5)) if "lat=-2.17" in r.url else alertas())
    pg = p.pagina
    assert pg.locator(".rep-inund").count() == 0, "en Quito no hay reportes"
    pg.click("#lugarbtn"); pg.locator("#sheet .lrow .lit").first.click()
    pg.wait_for_selector(".rep-inund", timeout=8000)
    assert "Casa" in pg.inner_text(".rep-inund")


# ======================= el mapa =======================
def test_el_mapa_del_pronostico_se_reproduce_solo_y_se_pausa_al_tocar(abrir):
    pg = abrir().pagina
    pg.click("#tab-mapa"); pg.wait_for_function("window.__L && window.__L.urls >= 1")
    pg.wait_for_function("window.__L && window.__L.urls >= 4", timeout=8000)
    assert pg.inner_text("#mplay").strip() == "❚❚", "muestra «pausa» mientras corre"
    pg.evaluate("document.querySelector('#mslider').dispatchEvent(new Event('input'))")
    antes = pg.evaluate("window.__L.urls"); pg.wait_for_timeout(1800)
    assert pg.inner_text("#mplay").strip() == "▶" and pg.evaluate("window.__L.urls") == antes, "tocar la barra pausa y no se reinicia sola"
    pg.click("#mplay"); pg.wait_for_function(f"window.__L && window.__L.urls > {antes}", timeout=5000)


def test_con_reducir_movimiento_el_mapa_no_se_reproduce_solo(abrir):
    pg = abrir(reducir_movimiento=True).pagina
    pg.click("#tab-mapa"); pg.wait_for_function("window.__L && window.__L.urls >= 1"); pg.wait_for_timeout(2200)
    assert pg.evaluate("window.__L.urls") == 1 and pg.inner_text("#mplay").strip() == "▶"


# ======================= lluvia en camino =======================
@pytest.mark.parametrize("estado, etiqueta, cambia_titular", [("pruebas", "En pruebas", False), ("verificado", "En camino", True), ("oculto", None, False)])
def test_lluvia_en_camino_segun_su_verificacion(abrir, estado, etiqueta, cambia_titular):
    pg = abrir(ahora=ahora_json(estado_nowcast=estado)).pagina
    pg.wait_for_function("document.querySelector('.ah-nc') || " + ("false" if etiqueta is None else "false"), timeout=6000) if etiqueta else pg.wait_for_timeout(1500)
    ban = pg.locator(".ah-nc")
    if etiqueta is None:
        assert ban.count() == 0, "oculta: no aparece nada"
        return
    assert ban.locator(".etq").inner_text().strip().lower() == etiqueta.lower()
    assert pg.inner_text(".tit").startswith("Lluvia llegando") == cambia_titular, "solo cambia el titular cuando la función demostró acertar"
    if estado == "pruebas": assert "no sabemos cuántas veces acierta" in ban.inner_text()


# ======================= lo que ya existía =======================
def test_tarjeta_de_aciertos_con_puntos_por_dia(abrir):
    pg = abrir().pagina
    hoy = pg.evaluate("new Date().toISOString().slice(0, 10)")
    dias = [{"d": f"2026-10-{20 + i}", "max": m, "llovio": ll, "h": ll, "c": c} for i, (m, ll, c) in enumerate(
        [(66, 1, "acierto"), (72, 0, "falsa"), (10, 0, "acierto"), (10, 1, "escapo"), (30, 1, "duda"), (80, 1, "acierto"), (75, 1, "acierto")])]
    pg.evaluate("(d) => { D.tablero.ciudades['Guayaquil'] = {dias: d, a: 4, f: 1, e: 1, dudas: 1, n: 6, fuente: 'aeropuerto'}; render(); }", dias)
    assert pg.eval_on_selector_all("#aciertos .pt", "ps => ps.map(p => p.className.replace('pt ', ''))") == ["ok", "mal", "ok", "mal", "neu", "ok", "ok"]
    assert "4 de 6" in pg.inner_text("#aciertos summary")
    pg.click("#aciertos summary"); assert pg.locator("#aciertos .acf").count() == 7
    assert {"Acertamos", "Falsa alarma", "Se nos escapó", "Dudoso"} <= set(pg.eval_on_selector_all("#aciertos .acv", "es => es.map(e => e.textContent)"))


def test_mis_lugares_guarda_muestra_y_quita(abrir):
    lugar = [{"id": "-0.18,-78.47", "nombre": "La Carolina, Quito", "lat": -0.18, "lon": -78.47}]
    pg = abrir(lugares=lugar).pagina
    pg.click("#lugarbtn"); pg.wait_for_selector("#sheet .lrow")
    assert "mis lugares" in pg.inner_text("#sheet").lower() and "La Carolina" in pg.inner_text("#sheet .lrow")
    pg.locator("#sheet .lrow .lit").first.click(); pg.wait_for_function("document.querySelector('#lugarnom').textContent.startsWith('⭐')", timeout=8000)
    assert "La Carolina" in pg.inner_text("#lugarnom")
    pg.click("#lugarbtn"); pg.click("#sheet .lq"); pg.wait_for_timeout(300)
    assert pg.evaluate("JSON.parse(localStorage.getItem('cc_lugares') || '[]').length") == 0


def test_tarjeta_estado_de_los_datos(abrir):
    pg = abrir(ahora=ahora_json()).pagina
    pg.evaluate("document.querySelector('#estado').open = true")
    assert pg.locator("#estado .est").count() == 6
    assert "satélite" in pg.inner_text("#estado") and "pluviómetros" in pg.inner_text("#estado")


def test_pie_con_fuentes_oficiales_y_licencia(abrir):
    t = abrir().pagina.inner_text("#pie")
    for texto in ("INAMHI", "Alertas Ecuador", "911", "Código disponible", "licencia PolyForm Noncommercial"):
        assert texto in t, texto
    assert "Código abierto" not in t, "la licencia no es open source: no se dice"
