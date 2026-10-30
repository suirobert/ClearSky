# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
"""La página a distintas horas del día.

Lo que la página muestra cambia con la hora (hay sol o no, queda mucho o poco del día de hoy). Una prueba que da por hecho «es de día» pasa
cuando se escribe y falla meses después, cuando el CI corre de noche: eso fue lo que pasó con la ejecución de las 18:04. Estas pruebas fijan
el reloj del navegador a varias horas (de madrugada, a mediodía, al atardecer, de noche) y comprueban las reglas que dependen de ella."""
import pytest
from test_compartir import abrir_comp, url_comp          # el navegador, la página y el reloj fijo (solo las utilidades)

HORAS = ["04:17", "12:00", "17:55", "18:04", "21:00", "23:30"]       # 04:17 es la hora del CI semanal; 18:04, la de la ejecución que falló
CON_SOL = {"06:10", "12:00", "17:55"}                                # la página muestra la etiqueta de UV de 06:00 a 17:59 (antes de las 6 y desde las 18 ya no hay sol)
MIN_HORAS_PC, MIN_HORAS_CELULAR = 3, 11                              # horas que deben quedar de hoy para que el gráfico de 7 días rotule «Hoy»

JS_TEXTOS = """() => { const svg = document.querySelector('#graf svg'), r0 = svg.getBoundingClientRect();
    return [...svg.querySelectorAll('text')].map(e => { const b = e.getBoundingClientRect(); return {t: e.textContent, l: b.left - r0.left, r: b.right - r0.left, a: b.top - r0.top, b: b.bottom - r0.top}; })
      .concat([{t: '__svg__', l: 0, r: r0.width, a: 0, b: r0.height}]); }"""
JS_HORAS_HOY = """() => { const f = ctx.prox.slice(0, 168), i = f.findIndex((r, k) => k > 0 && r.hora.slice(11, 13) === '00'); return i < 0 ? f.length : i; }"""


def abrir_siete_dias(abrir_comp, hora, ancho):
    p = abrir_comp(hora_local=hora, ancho=ancho)
    p.pagina.click("#rsel .rb[data-r='168']"); p.pagina.wait_for_timeout(250)
    return p


MARGEN_V = 2          # px: la caja de un texto incluye el espacio entre renglones; la tinta ocupa menos (el «km/h» del eje y el «40» de arriba se rozan en caja, no en tinta)


def textos_que_se_pisan(textos):
    """Pares de textos del gráfico cuyas cajas se cruzan (0,5 px de tolerancia a lo ancho y 2 px a lo alto)."""
    t = [x for x in textos if x["t"] != "__svg__"]
    return [(a["t"], b["t"]) for i, a in enumerate(t) for b in t[i + 1:]
            if a["l"] < b["r"] - 0.5 and b["l"] < a["r"] - 0.5 and a["a"] + MARGEN_V < b["b"] - MARGEN_V and b["a"] + MARGEN_V < a["b"] - MARGEN_V]


def fuera_del_grafico(textos):
    lienzo = [x for x in textos if x["t"] == "__svg__"][0]
    return [x["t"] for x in textos if x["t"] != "__svg__" and (x["l"] < -0.5 or x["r"] > lienzo["r"] + 0.5)]


@pytest.mark.parametrize("hora", HORAS)
def test_a_cualquier_hora_el_grafico_de_7_dias_rotula_hoy_si_cabe_y_nunca_pisa_textos_en_pc(abrir_comp, hora):
    p = abrir_siete_dias(abrir_comp, hora, 1440); pg = p.pagina
    textos, quedan = pg.evaluate(JS_TEXTOS), pg.evaluate(JS_HORAS_HOY)
    nombres = [x["t"] for x in textos]
    assert textos_que_se_pisan(textos) == [], f"a las {hora}: {textos_que_se_pisan(textos)}"
    assert fuera_del_grafico(textos) == [], f"a las {hora} hay textos fuera del gráfico"
    if quedan >= MIN_HORAS_PC:
        assert "Hoy" in nombres, f"a las {hora} quedan {quedan} h de hoy y el tramo de hoy no tiene nombre (así falló el CI a las 18:04)"
    assert sum(1 for t in nombres if t in ("Mañana", "Mañ")) == 1, "el día de mañana siempre se rotula"
    assert p.errores == []


@pytest.mark.parametrize("hora", HORAS)
def test_a_cualquier_hora_el_grafico_de_7_dias_funciona_en_celular(abrir_comp, hora):
    p = abrir_siete_dias(abrir_comp, hora, 390); pg = p.pagina
    textos, quedan = pg.evaluate(JS_TEXTOS), pg.evaluate(JS_HORAS_HOY)
    assert textos_que_se_pisan(textos) == [], f"a las {hora}: {textos_que_se_pisan(textos)}"
    assert fuera_del_grafico(textos) == []
    if quedan >= MIN_HORAS_CELULAR:
        assert "Hoy" in [x["t"] for x in textos], f"a las {hora} quedan {quedan} h de hoy y el tramo de hoy no tiene nombre (en el celular el tramo es más angosto)"
    assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "sin desborde horizontal"
    assert p.errores == []


def test_la_raya_que_separa_hoy_de_manana_no_pasa_por_detras_del_nombre_de_hoy(abrir_comp):
    """Si el tramo de hoy es angosto, «Hoy» sobresale de su columna; la raya vertical que la cierra no debe cruzar el texto."""
    p = abrir_siete_dias(abrir_comp, "12:00", 390); pg = p.pagina
    r = pg.evaluate("""() => { const svg = document.querySelector('#graf svg'), r0 = svg.getBoundingClientRect();
        const hoy = [...svg.querySelectorAll('text')].find(e => e.textContent === 'Hoy'); if (!hoy) return null; const h = hoy.getBoundingClientRect();
        const cruzan = [...svg.querySelectorAll('line')].map(e => e.getBoundingClientRect()).filter(b => b.width < 2 && b.left > h.left + 1 && b.left < h.right - 1 && b.top < h.bottom - 2 && b.bottom > h.top + 2);
        return {hoy: [h.left - r0.left, h.right - r0.left], cruzan: cruzan.length}; }""")
    assert r is not None, "a las 12:00 en el celular «Hoy» debe estar"
    assert r["cruzan"] == 0, f"una raya vertical pasa por detrás de «Hoy» ({r['hoy']})"


@pytest.mark.parametrize("variable", ["lluvia", "temp", "viento", "uv"])
def test_las_cuatro_graficas_de_7_dias_tampoco_pisan_textos_con_poco_dia_por_delante(abrir_comp, variable):
    p = abrir_siete_dias(abrir_comp, "18:04", 390); pg = p.pagina
    pg.click(f"#gsel .gb[data-v={variable}]"); pg.wait_for_timeout(250)
    assert textos_que_se_pisan(pg.evaluate(JS_TEXTOS)) == [] and p.errores == []


@pytest.mark.parametrize("hora", sorted(CON_SOL) + ["04:17", "18:04", "21:00", "23:30"])
def test_la_etiqueta_de_uv_aparece_solo_con_sol(abrir_comp, hora):
    """Con la hora fija, la etiqueta de UV es predecible: de día está y de noche no. (La prueba anterior daba por hecho que siempre estaba.)"""
    p = abrir_comp(hora_local=hora); pg = p.pagina
    hay = pg.evaluate("!!document.querySelector('.chips .chip.uv')")
    assert hay == (hora in CON_SOL), f"a las {hora}: etiqueta de UV {'presente' if hay else 'ausente'}"
    assert p.errores == []


@pytest.mark.parametrize("hora", HORAS)
def test_a_cualquier_hora_la_pagina_principal_se_ve_completa(abrir_comp, hora):
    p = abrir_comp(hora_local=hora); pg = p.pagina
    assert pg.inner_text(".tit").strip() and pg.locator("#c24 .hc").count() >= 12, "titular y tira de próximas horas"
    assert pg.locator("#dias .drow").count() >= 4, "lista de días"
    assert pg.evaluate("document.querySelector('.chips').getBoundingClientRect().right <= document.querySelector('.col1').getBoundingClientRect().right + 1"), \
        "las etiquetas no se salen de su columna aunque cambie su cantidad (de noche hay una menos)"
    assert p.errores == []
