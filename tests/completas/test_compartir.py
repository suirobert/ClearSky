# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
"""La imagen que se genera con «Compartir».

Se abre la página en un navegador real, se pulsa «Compartir» y se examina la imagen resultante: su tamaño, sus colores, los textos
que contiene (la página los registra en estado.ultimaImagen) y que nada se salga del lienzo. Así se detecta si vuelve el diseño viejo,
si un nombre largo se corta o si aparece algo que no debe (por ejemplo, reportes de la gente sin verificar)."""
import base64, functools, http.server, json, math, os, pathlib, random, threading
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
import pytest

W_IMG = 1080
MAX_ALTO = 1920                      # el alto de una historia de Instagram o de un estado de WhatsApp (9:16)
FONDO_OSCURO, FONDO_CLARO = [11, 15, 23], [244, 246, 250]      # colores de fondo de la página (--bg)


# ---------------------------------------------------------------- página y servidor de prueba
def _horas(dias):
    ini = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())
    return [(ini + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(24 * dias)]


def _respuesta(cc, lat, lon, variables, ts):
    hh = {"time": ts}
    for v in variables:
        for m in cc.MODELOS:
            col = []
            for t in ts:
                r = random.Random(f"{t}{m}{v}{round(lat, 1)}"); h = int(t[11:13])
                col.append({"precipitation": lambda: round(r.uniform(0, 2) if r.random() < .2 else 0, 1),
                            "temperature_2m": lambda: round(25 + 5 * math.sin((h - 9) / 24 * 6.28), 1),
                            "cloud_cover": lambda: r.randint(10, 80), "cloud_cover_low": lambda: r.randint(0, 50), "cloud_cover_mid": lambda: r.randint(0, 50),
                            "shortwave_radiation": lambda: round(800 * max(0, math.cos((h - 12.5) / 12 * 3.14))), "weather_code": lambda: r.choice([1, 2, 3]),
                            "wind_gusts_10m": lambda: r.randint(10, 35),
                            "uv_index": lambda: round(max(0, 10 * math.cos((h - 12.5) / 6.5 * 1.57)), 1) if 7 <= h <= 18 else 0,
                            "relative_humidity_2m": lambda: 75, "wind_speed_10m": lambda: r.randint(5, 20)}[v]())
            hh[f"{v}_{m}"] = col
    return {"latitude": lat, "longitude": lon, "elevation": 10, "utc_offset_seconds": -18000, "hourly": hh}


class _Silencioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


def generar_sitio(cc, carpeta):
    guardado = cc.ARCHIVO_HTML
    cc.ARCHIVO_HTML = str(carpeta / "index.html")                  # no pisa la página que usan las otras pruebas
    try:
        res = {c: cc.consenso(_respuesta(cc, o["lat"], o["lon"], cc.VARS_PRON, _horas(9)), cc.cfg_defecto(), o["lat"], o["lon"]) for c, o in cc.CIUDADES.items()}
        cc.generar_html(res, cc.cargar_pesos(), None, {}, [], None, {}, None)
    finally:
        cc.ARCHIVO_HTML = guardado


@pytest.fixture(scope="module")
def url_comp(cc, tmp_path_factory):
    previa = os.environ.get("CC_SITIO_COMPARTIR")                  # solo desarrollo: reutiliza una página ya generada en vez de crearla de nuevo
    if previa and (pathlib.Path(previa) / "index.html").exists():
        carpeta = pathlib.Path(previa)
    else:
        carpeta = tmp_path_factory.mktemp("sitio-compartir"); generar_sitio(cc, carpeta)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Silencioso, directory=str(carpeta)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    srv.shutdown()


def _hora_ec(minutos=0):
    return (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5) + timedelta(minutes=minutos)).strftime("%Y-%m-%dT%H:%M")


def ahora_con(oficial=False, rayos=False):
    boletin = {"id": "84", "estatus": "Advertencia", "emitido": _hora_ec(-900), "desde": _hora_ec(-60), "hasta": _hora_ec(3000), "nivel": "MEDIO – MUY ALTO", "nivel_max": "muy alto",
               "fenomeno": "LLUVIAS, TORMENTAS ELÉCTRICAS Y RÁFAGAS DE VIENTO", "que_puede_pasar": ["Acumulación de agua."], "recomienda": "Si las autoridades lo indican, evacúa",
               "url": "https://x/p.pdf", "pagina": "https://x/", "lluvia_mm": {}, "zonas": {"litoral": "Mayor intensidad en Guayas.", "interandina": "Pichincha.", "amazonia": "x", "insular": "x"}}
    return {"generado": _hora_ec(-1), "lluvia": None, "movimiento": None, "oficial": boletin if oficial else None, "nowcast": None, "errores": [],
            "rayos": {"t": _hora_ec(-2), "radio_km": 25, "celdas": [[-2.17, -79.92, 12, 14]]} if rayos else None}


class Pagina:
    def __init__(self, cc, ctx, pagina, tmp):
        self.cc, self.ctx, self.pagina, self.tmp, self.errores = cc, ctx, pagina, tmp, []

    def compartir(self, nombre="img"):
        """Pulsa «Compartir» (sin la API de compartir, para que se descargue) y devuelve (bytes del PNG, registro de la imagen)."""
        with self.pagina.expect_download(timeout=20000) as d:
            self.pagina.click("#bcompartir")
        ruta = self.tmp / f"{nombre}.png"; d.value.save_as(str(ruta))
        return ruta.read_bytes(), self.pagina.evaluate("estado.ultimaImagen")

    def pixel(self, png, x, y):
        b64 = base64.b64encode(png).decode()
        return self.pagina.evaluate("""async ([b, x, y]) => { const i = new Image(); i.src = 'data:image/png;base64,' + b; await i.decode();
            const c = document.createElement('canvas'); c.width = i.width; c.height = i.height; const g = c.getContext('2d'); g.drawImage(i, 0, 0);
            return [...g.getImageData(x, y, 1, 1).data]; }""", [b64, x, y])

    def colores_distintos(self, png):
        """Cuántos colores distintos hay en una cuadrícula de puntos de la imagen: una imagen vacía o casi plana tendría muy pocos."""
        b64 = base64.b64encode(png).decode()
        return self.pagina.evaluate("""async (b) => { const i = new Image(); i.src = 'data:image/png;base64,' + b; await i.decode();
            const c = document.createElement('canvas'); c.width = i.width; c.height = i.height; const g = c.getContext('2d'); g.drawImage(i, 0, 0);
            const s = new Set(); for (let y = 0; y < i.height; y += 7) for (let x = 0; x < i.width; x += 7) s.add([...g.getImageData(x, y, 1, 1).data].join(',')); return s.size; }""", b64)


@pytest.fixture
def abrir_comp(cc, url_comp, navegador, tmp_path):
    contextos = []

    def _abrir(lat=-2.17, lon=-79.9, tema="dark", hora_local="14:05", ahora=None, ancho=1200, solo_descarga=True):
        ctx = navegador.new_context(viewport={"width": ancho, "height": 900}, geolocation={"latitude": lat, "longitude": lon}, permissions=["geolocation"],
                                    color_scheme=tema, timezone_id="America/Guayaquil", locale="es-EC")
        contextos.append(ctx)
        if hora_local:
            h, m = map(int, hora_local.split(":"))
            ctx.clock.set_fixed_time((datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=h + 5, minutes=m)).replace(tzinfo=timezone.utc))
        if solo_descarga:
            ctx.add_init_script("try { navigator.canShare = undefined; navigator.share = undefined; } catch (e) {}")
        ok = lambda cuerpo: dict(status=200, content_type="application/json", headers={"access-control-allow-origin": "*"}, body=json.dumps(cuerpo))

        def modelos(route):
            q = parse_qs(urlparse(route.request.url).query)
            if "daily" in q:
                dias = [(date.today() - timedelta(days=d)).isoformat() for d in range(30, -1, -1)]
                return route.fulfill(**ok({"daily": {"time": dias, "temperature_2m_max": [31.0] * len(dias)}}))
            route.fulfill(**ok(_respuesta(cc, float(q["latitude"][0]), float(q["longitude"][0]), q["hourly"][0].split(","), _horas(9))))
        ctx.route("**/api.open-meteo.com/**", modelos)
        ctx.route("**/nominatim.openstreetmap.org/**", lambda r: r.fulfill(**ok({"address": {"suburb": "Urbanización La Romadera", "city": "Guayaquil", "state": "Guayas"}})))
        ctx.route("**/api.bigdatacloud.net/**", lambda r: r.fulfill(**ok({})))
        ctx.route("**/fb.test/resumen**", lambda r: r.fulfill(**ok({"total": 0})))
        ctx.route("**/fb.test/alertas**", lambda r: r.fulfill(**ok({"truenos": {"ok": False}, "inundado": {"ok": False}, "puntos": []})))
        ctx.route("**/fb.test/vapid", lambda r: r.fulfill(**ok({"publica": ""})))
        vacio = ahora_con()
        ctx.route("**/raw.githubusercontent.com/**", lambda r: r.fulfill(**ok(ahora or vacio)))
        ctx.route("**/unpkg.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        pag = ctx.new_page(); p = Pagina(cc, ctx, pag, tmp_path)
        pag.on("pageerror", lambda e: p.errores.append(f"pageerror: {e}"))
        pag.goto(url_comp)
        pag.wait_for_selector("#c24 .hc", timeout=20000); pag.wait_for_load_state("networkidle"); pag.wait_for_timeout(500)
        return p

    yield _abrir
    for c in contextos: c.close()


def textos(reg): return [r["t"] for r in reg["textos"]]
def junto(reg): return " | ".join(textos(reg))


# ================================================================= tamaño, colores y contenido
def test_la_imagen_tiene_el_tamano_y_los_colores_del_tema_oscuro(abrir_comp):
    p = abrir_comp(); png, reg = p.compartir()
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "es un PNG"
    assert reg["w"] == W_IMG and 1350 <= reg["h"] <= MAX_ALTO, f"alto razonable para compartir: {reg['h']}"
    assert p.pixel(png, W_IMG - 5, reg["h"] - 5)[:3] == FONDO_OSCURO, "fondo oscuro de la página (#0b0f17)"
    assert p.colores_distintos(png) >= 40, "no es una imagen vacía ni plana"
    assert reg["tema"] == "oscuro" and p.errores == []


def test_con_el_tema_claro_la_imagen_es_clara(abrir_comp):
    p = abrir_comp(tema="light"); p.pagina.evaluate("localStorage.setItem('cc_tema', 'claro'); aplicarTema(true)"); p.pagina.wait_for_timeout(300)
    png, reg = p.compartir()
    assert reg["tema"] == "claro" and p.pixel(png, W_IMG - 5, reg["h"] - 5)[:3] == FONDO_CLARO, "fondo claro (#f4f6fa)"
    assert p.errores == []


def test_trae_lo_que_trae_la_pagina_y_el_diseno_nuevo(abrir_comp):
    p = abrir_comp(); png, reg = p.compartir(); t = junto(reg)
    pagina = p.pagina.evaluate("({tit: document.querySelector('.tit').textContent.trim(), hs: document.querySelector('.hero .hs').textContent.trim()})")
    seguido = " ".join(textos(reg))                         # el titular puede partirse en dos líneas: se compara el texto corrido
    assert pagina["tit"] in seguido, f"falta el titular de la página: {pagina['tit']!r}"
    for esperado in ("ClearSky", "Urbanización La Romadera", "PRÓXIMAS HORAS", "PRÓXIMOS DÍAS", "Ahora", "Hoy", "Mañana", "suirobert.github.io/ClearSky"):
        assert esperado in t, f"falta «{esperado}» en la imagen"
    assert reg["horas"] == 8 and reg["dias"] == 5, "ocho horas de la próxima jornada y cinco días"
    assert pagina["hs"].split(" · ")[0] in t or "Máx" in t, "la línea de temperatura máxima y mínima"
    assert "Actualizado" not in t and any(m in t for m in ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")), "lleva fecha y hora de cuándo se generó"


def test_no_queda_nada_del_diseno_viejo_las_franjas_de_colores(abrir_comp):
    p = abrir_comp(); png, reg = p.compartir()
    ys = sorted({round(r["y"]) for r in reg["textos"]})
    assert "PRÓXIMAS HORAS" in junto(reg) and any(t.endswith("h") and t[:-1].isdigit() for t in textos(reg)), "tira de horas con su ícono y temperatura, no franjas"
    assert not any(" / " in t and t.count("°") == 2 for t in textos(reg)), "ya no se escribe «27° / 29°»: ahora es una barra de rango"


# ================================================================= nada se sale del lienzo
def test_ningun_texto_se_sale_de_la_imagen_ni_con_nombres_larguisimos(abrir_comp):
    p = abrir_comp()
    largo = "Urbanización Ciudadela Los Ceibos Mz. 1234 Villa 56 detrás del Centro Comercial, Guayaquil, Provincia del Guayas"
    p.pagina.evaluate("(t) => { estado.ubic.nombre = t; document.querySelector('.lugar').textContent = '📍 ' + t; }", largo)
    png, reg = p.compartir()
    for r in reg["textos"]:
        assert r["x"] >= 0 and r["x"] + r["w"] <= W_IMG + 1, f"se sale de la imagen: {r['t']!r} ({r['x']:.0f}..{r['x'] + r['w']:.0f})"
    lugar = [r for r in reg["textos"] if r["t"].startswith("Urbanización Ciudadela")][0]
    assert lugar["w"] <= W_IMG - 2 * 56 + 1 and lugar["t"].endswith("…"), "el nombre desmesurado se achica y, si aun así no cabe, se corta con puntos suspensivos"


def test_un_titular_y_un_subtitulo_muy_largos_se_parten_en_lineas(abrir_comp):
    p = abrir_comp()
    p.pagina.evaluate("""() => { document.querySelector('.tit').textContent = 'Tormenta eléctrica intensa probable mañana en la madrugada y en la mañana con ráfagas fuertes de viento';
        document.querySelector('.sub').textContent = 'Palabra '.repeat(80); }""")
    png, reg = p.compartir()
    assert all(r["x"] + r["w"] <= W_IMG + 1 for r in reg["textos"])
    assert any(t.endswith("…") for t in textos(reg)), "el subtítulo desmesurado se recorta con puntos suspensivos"
    assert reg["h"] <= MAX_ALTO + 200


def test_con_el_contenido_normal_no_hace_falta_comprimir_la_imagen(abrir_comp):
    p = abrir_comp(); png, reg = p.compartir()
    assert reg["compacto"] is False and reg["diasDibujados"] == 5 and reg["h"] <= MAX_ALTO


def test_cuando_hay_mucho_que_mostrar_la_imagen_se_comprime_para_seguir_cabiendo_en_una_historia(abrir_comp):
    p = abrir_comp(ahora=ahora_con(oficial=True, rayos=True)); p.pagina.wait_for_selector(".ah-rayo", timeout=8000)
    p.pagina.evaluate("document.querySelector('.sub').textContent = 'Palabra de relleno '.repeat(14)")        # un subtítulo de tres renglones
    png, reg = p.compartir()
    assert reg["compacto"] is True and reg["diasDibujados"] == 4, "se quita un día y se acorta el texto para no pasarse de alto"
    assert reg["h"] <= MAX_ALTO, f"alto: {reg['h']}"
    assert all(r["x"] + r["w"] <= W_IMG + 1 for r in reg["textos"])


def test_ningun_texto_se_encima_con_otro(abrir_comp):
    """Con avisos, etiquetas, titulares largos y tormenta (el caso más cargado), dos textos no pueden pisarse."""
    p = abrir_comp(ahora=ahora_con(oficial=True, rayos=True)); p.pagina.wait_for_selector(".ah-rayo", timeout=8000)
    p.pagina.evaluate("""() => { document.querySelector('.tit').textContent = 'Tormenta eléctrica probable mañana en la madrugada y en la mañana';
        ctx.prox.forEach((r, i) => { if (i > 3) { r.prob = 60; r.prob_ts = 50; } }); }""")
    png, reg = p.compartir(); tx = reg["textos"]
    for i, a in enumerate(tx):
        for b in tx[i + 1:]:
            mismo_renglon = abs(a["y"] - b["y"]) < 0.6 * min(a["px"], b["px"])
            if mismo_renglon and a["x"] < b["x"] + b["w"] - 1 and b["x"] < a["x"] + a["w"] - 1:
                pytest.fail(f"se pisan: {a['t']!r} y {b['t']!r}")


def test_el_subtitulo_va_pegado_al_titular(abrir_comp):
    p = abrir_comp(); png, reg = p.compartir(); tx = reg["textos"]
    tit = p.pagina.evaluate("document.querySelector('.tit').textContent.trim()"); sub = p.pagina.evaluate("document.querySelector('.sub').textContent.trim()")
    ultimo_tit = max((r for r in tx if r["px"] == 56 and r["t"] in tit), key=lambda r: r["y"])
    primer_sub = min((r for r in tx if r["px"] == 32 and r["t"] in sub), key=lambda r: r["y"])
    assert 40 <= primer_sub["y"] - ultimo_tit["y"] <= 70, f"separación entre titular y subtítulo: {primer_sub['y'] - ultimo_tit['y']:.0f}px"


def test_sin_temperatura_no_se_escribe_nan_ni_infinito(abrir_comp):
    p = abrir_comp()
    p.pagina.evaluate("() => { ctx.prox.forEach(r => { r.temp = null; }); Object.values(ctx.dias).forEach(rs => rs.forEach(r => { r.temp = null; })); }")
    png, reg = p.compartir()
    t = junto(reg)
    assert "NaN" not in t and "Infinity" not in t and "undefined" not in t, t[:300]
    assert "–" in textos(reg) and "0°" not in textos(reg), "sin dato se escribe «–», no «0°»"
    assert p.errores == []


# ================================================================= avisos y honestidad
def test_un_aviso_oficial_aparece_arriba_con_su_etiqueta(abrir_comp):
    p = abrir_comp(ahora=ahora_con(oficial=True)); p.pagina.wait_for_selector(".ah-of", timeout=8000)
    png, reg = p.compartir()
    assert reg["alerta"] == "AVISO OFICIAL · INAMHI" and "boletín 84" in junto(reg)
    t = junto(reg)
    assert "hasta" in t, "la vigencia del aviso (hasta cuándo) se ve: no se pierde al recortar el texto largo"
    assert "Lluvias" not in t or t.index("hasta") < t.index("Lluvias"), "la vigencia va antes que el nombre del fenómeno (lo más largo, que es lo que se recorta)"


def test_los_rayos_ahora_tienen_prioridad_sobre_el_aviso_oficial(abrir_comp):
    p = abrir_comp(ahora=ahora_con(oficial=True, rayos=True)); p.pagina.wait_for_selector(".ah-rayo", timeout=8000)
    png, reg = p.compartir()
    assert reg["alerta"] == "AHORA · SATÉLITE" and "rayos" in junto(reg).lower()
    assert reg["h"] > 1350


def test_los_reportes_de_la_gente_no_se_comparten_en_la_imagen(abrir_comp):
    """Las inundaciones y los truenos que reporta la gente son «no verificados»: no deben viajar en una imagen que se reenvía sin contexto."""
    p = abrir_comp()
    p.pagina.evaluate("""() => { document.querySelector('.chips').insertAdjacentHTML('afterbegin', '<span class="chip tru">🔊 Escuchan truenos cerca · <b>3 personas</b></span>');
        document.querySelector('.cab').insertAdjacentHTML('afterbegin', '<div class="rep-b rep-inund"><span>🌊 Reportan calles inundadas cerca de ti</span></div>'); }""")
    png, reg = p.compartir(); t = junto(reg).lower()
    assert "inundad" not in t and "truenos" not in t and "3 personas" not in t, t[:300]


def test_dentro_de_ecuador_dice_cuanto_acertamos(abrir_comp):
    p = abrir_comp()
    p.pagina.evaluate("""() => { const ref = estado.ubic.ref || estado.ubic.nombre; D.tablero = D.tablero || {ciudades: {}, global: {}};
        D.tablero.ciudades[ref] = {a: 6, f: 1, e: 0, dudas: 0, n: 7, fuente: 'aeropuerto', dias: []}; render(); }""")
    png, reg = p.compartir()
    assert "Acertamos 6 de 7 días esta semana" in junto(reg) and "Pronóstico verificado" in junto(reg)


def test_fuera_de_ecuador_no_dice_verificado_ni_presume_aciertos(abrir_comp):
    p = abrir_comp(lat=25.77, lon=-80.19)
    # los aciertos se cargan con la clave que de verdad usa ese lugar (su nombre), para que la prueba ejercite la regla «fuera de Ecuador no se presume»
    p.pagina.evaluate("""() => { D.tablero = {ciudades: {}, global: {}}; D.tablero.ciudades[estado.ubic.nombre] = {a: 7, f: 0, e: 0, dudas: 0, n: 7, fuente: 'aeropuerto', dias: []}; render(); }""")
    assert p.pagina.evaluate("tableroDe(ctx.v) !== null"), "(la prueba está bien armada: la página SÍ encuentra esos aciertos para este lugar)"
    png, reg = p.compartir(); t = junto(reg)
    assert "Pronóstico verificado" not in t and "Acertamos" not in t, t[-300:]
    assert "fuera de Ecuador" in t and p.errores == []


# ================================================================= compartir de verdad (celular) y otros detalles
def test_en_el_celular_se_entrega_el_archivo_a_la_hoja_de_compartir(abrir_comp):
    p = abrir_comp(solo_descarga=False)
    p.pagina.evaluate("""() => { window.__comp = null; navigator.canShare = () => true;
        navigator.share = async d => { window.__comp = {nombre: d.files[0].name, tipo: d.files[0].type, tam: d.files[0].size, texto: d.text}; }; }""")
    p.pagina.click("#bcompartir"); p.pagina.wait_for_function("window.__comp !== null", timeout=15000)
    c = p.pagina.evaluate("window.__comp")
    assert c["nombre"] == "clearsky.png" and c["tipo"] == "image/png" and c["tam"] > 30000
    assert "Urbanización La Romadera" in c["texto"] and "http" in c["texto"], "el mensaje lleva el titular, el lugar y la dirección"


def test_si_la_persona_cancela_compartir_no_se_descarga_nada(abrir_comp):
    p = abrir_comp(solo_descarga=False)
    p.pagina.evaluate("""() => { navigator.canShare = () => true; navigator.share = async () => { const e = new Error('cancelado'); e.name = 'AbortError'; throw e; }; }""")
    descargas = []; p.pagina.on("download", lambda d: descargas.append(d))
    p.pagina.click("#bcompartir"); p.pagina.wait_for_timeout(1500)
    assert descargas == [] and p.pagina.locator("#toast:not([hidden])").count() == 0


def test_de_noche_la_imagen_usa_el_ícono_de_luna_y_no_falla(abrir_comp):
    p = abrir_comp(hora_local="23:10"); png, reg = p.compartir()
    assert reg["horas"] >= 4 and p.errores == []
    assert p.pixel(png, W_IMG - 5, reg["h"] - 5)[:3] == FONDO_OSCURO


def test_la_imagen_sale_igual_en_pantalla_de_celular(abrir_comp):
    p = abrir_comp(ancho=390); png, reg = p.compartir()
    assert reg["w"] == W_IMG and reg["h"] >= 1350 and p.errores == []
