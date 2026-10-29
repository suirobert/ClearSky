# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
"""La ubicación sigue a la persona.

Un GPS falso permite mover a la persona de un barrio a otro y ver qué pide la página: con qué opciones consulta la
posición, cuándo vuelve a calcular el pronóstico y el nombre del lugar, y qué muestra mientras tanto.
Los nombres «Barrio A» y «Barrio B» son inventados (los devuelve un OpenStreetMap simulado según la latitud)."""
import functools, http.server, json, math, random, threading, time
from datetime import date, datetime, timedelta
from urllib.parse import urlparse, parse_qs
import pytest

A = (-2.100, -79.920)            # "Barrio A"
B = (-2.160, -79.900)            # "Barrio B": a ~7 km de A
LIMITE_LAT = -2.13               # al norte de esta latitud OpenStreetMap devuelve "Barrio A"; al sur, "Barrio B"
SESENTA_MIN = 3600 * 1000


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


@pytest.fixture(scope="module")
def sitio_ubic(cc, tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("sitio-ubicacion")
    guardado = cc.ARCHIVO_HTML
    cc.ARCHIVO_HTML = str(carpeta / "index.html")          # no pisa la página que usan las otras pruebas
    try:
        resultados = {c: cc.consenso(_respuesta(cc, o["lat"], o["lon"], cc.VARS_PRON, _horas(9)), cc.cfg_defecto(), o["lat"], o["lon"]) for c, o in cc.CIUDADES.items()}
        cc.generar_html(resultados, cc.cargar_pesos(), None, {}, [], None, {}, None)
    finally:
        cc.ARCHIVO_HTML = guardado
    return carpeta


@pytest.fixture(scope="module")
def url_ubic(sitio_ubic):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Silencioso, directory=str(sitio_ubic)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    srv.shutdown()


# ---------------------------------------------------------------- GPS falso
GPS_JS = """(() => {
  const E = window.__geo = {modo: '%(modo)s', lat: %(lat)s, lon: %(lon)s, prec: %(prec)s, codigo: 2, demora: %(demora)s, permiso: '%(permiso)s', llamadas: []};
  navigator.geolocation.getCurrentPosition = function (exito, error, opc) {
    E.llamadas.push({maximumAge: opc && opc.maximumAge, enableHighAccuracy: opc && opc.enableHighAccuracy, lat: E.lat, lon: E.lon});
    setTimeout(() => { if (E.modo === 'ok') exito({coords: {latitude: E.lat, longitude: E.lon, accuracy: E.prec}, timestamp: Date.now()}); else error({code: E.codigo, message: 'sin señal'}); }, E.demora);
  };
  const q = navigator.permissions && navigator.permissions.query ? navigator.permissions.query.bind(navigator.permissions) : null;
  if (navigator.permissions) navigator.permissions.query = d => d && d.name === 'geolocation' ? Promise.resolve({state: E.permiso}) : (q ? q(d) : Promise.reject());
})();"""


class Sesion:
    """Una persona con un teléfono en un lugar: todo lo que sale a internet está simulado y se cuenta."""
    def __init__(self, cc, ctx, pagina):
        self.cc, self.ctx, self.pagina = cc, ctx, pagina
        self.pedidos, self.errores, self.reportes = [], [], []
        self.retener, self.retenidas = False, []              # OpenStreetMap "tarda": se retiene la respuesta del nombre del barrio

    def liberar(self):
        """Contesta lo que estaba retenido (el nombre del barrio llega)."""
        self.retener = False
        while self.retenidas: self.atender_osm(self.retenidas.pop())

    # --- lecturas
    def nombre(self): return self.pagina.inner_text("#lugarnom")
    def lugar(self): return self.pagina.inner_text(".lugar") if self.pagina.query_selector(".lugar") else ""
    def gps(self): return self.pagina.evaluate("window.__geo.llamadas")
    def mover(self, lat, lon, prec=20):
        if getattr(self, "nativo", False): self.ctx.set_geolocation({"latitude": lat, "longitude": lon, "accuracy": prec})
        else: self.pagina.evaluate("([a, b, c]) => { __geo.lat = a; __geo.lon = b; __geo.prec = c; }", [lat, lon, prec])
    def n(self, que): return len([p for p in self.pedidos if que in p])

    def esperar_nombre(self, texto, ms=12000):
        self.pagina.wait_for_function("t => document.querySelector('#lugarnom').textContent.includes(t)", arg=texto, timeout=ms)

    def se_queda(self, texto, ms=3500):
        """True si el nombre sigue siendo `texto` después de esperar `ms` (es decir, NO cambió)."""
        self.pagina.wait_for_timeout(ms)
        return texto in self.nombre()

    def volver_a_la_pestana(self):
        self.pagina.evaluate("document.dispatchEvent(new Event('visibilitychange'))")


def _cache(cc, lat, lon, nombre, hace_ms):
    om = _respuesta(cc, round(lat, 2), round(lon, 2), cc.VARS_PRON, _horas(9))
    return json.dumps({"lat": round(lat, 2), "lon": round(lon, 2), "om": om, "nombre": nombre, "t": int(time.time() * 1000) - hace_ms})


@pytest.fixture
def persona(cc, url_ubic, navegador):
    contextos = []

    def _crear(donde=A, cache=None, permiso="granted", prec=20, ancho=390, gps_falla=False, demora_gps=0, gps_nativo=False):
        """cache = (lat, lon, nombre, hace_ms): la última ubicación que la página guardó en este teléfono."""
        extra = {"geolocation": {"latitude": donde[0], "longitude": donde[1], "accuracy": prec}} if gps_nativo else {}      # el GPS de Chromium, sin el simulado
        ctx = navegador.new_context(viewport={"width": ancho, "height": 844}, is_mobile=True, has_touch=True, timezone_id="America/Guayaquil", locale="es-EC",
                                    permissions=["geolocation"] if permiso == "granted" else [], **extra)
        contextos.append(ctx)
        if not gps_nativo:
            ctx.add_init_script(GPS_JS % {"lat": donde[0], "lon": donde[1], "prec": prec, "permiso": permiso, "modo": "error" if gps_falla else "ok", "demora": demora_gps})
        if cache:
            ctx.add_init_script("try { localStorage.setItem('cc_ubic_cache', %s); localStorage.setItem('cc_ubic', '1'); } catch (e) {}" % json.dumps(_cache(cc, *cache)))
        json_ok = lambda cuerpo: dict(status=200, content_type="application/json", headers={"access-control-allow-origin": "*"}, body=json.dumps(cuerpo))
        pagina = ctx.new_page(); s = Sesion(cc, ctx, pagina); s.nativo = gps_nativo
        ctx.on("request", lambda r: s.pedidos.append(r.url))

        def modelos(route):
            q = parse_qs(urlparse(route.request.url).query)
            if "daily" in q:
                dias = [(date.today() - timedelta(days=d)).isoformat() for d in range(30, -1, -1)]
                return route.fulfill(**json_ok({"daily": {"time": dias, "temperature_2m_max": [31.0] * len(dias)}}))
            route.fulfill(**json_ok(_respuesta(cc, float(q["latitude"][0]), float(q["longitude"][0]), q["hourly"][0].split(","), _horas(9))))

        def atender_osm(route):
            q = parse_qs(urlparse(route.request.url).query); lat = float(q["lat"][0]); zoom = q.get("zoom", ["17"])[0]
            if zoom == "5": return route.fulfill(**json_ok({"address": {"state": "Guayas"}}))
            route.fulfill(**json_ok({"address": {"suburb": "Barrio A" if lat > LIMITE_LAT else "Barrio B", "city": "Guayaquil", "state": "Guayas"}}))

        def osm(route):
            if s.retener and "zoom=5" not in route.request.url: s.retenidas.append(route); return     # la respuesta se libera cuando la prueba lo decide
            atender_osm(route)
        s.atender_osm = atender_osm

        ctx.route("**/api.open-meteo.com/**", modelos)
        ctx.route("**/nominatim.openstreetmap.org/**", osm)
        ctx.route("**/api.bigdatacloud.net/**", lambda r: r.fulfill(**json_ok({})))
        ctx.route("**/fb.test/resumen**", lambda r: r.fulfill(**json_ok({"total": 0})))
        ctx.route("**/fb.test/alertas**", lambda r: r.fulfill(**json_ok({"truenos": {"ok": False}, "inundado": {"ok": False}, "puntos": []})))
        ctx.route("**/fb.test/vapid", lambda r: r.fulfill(**json_ok({"publica": ""})))
        ctx.route("**/raw.githubusercontent.com/**", lambda r: r.fulfill(**json_ok({"generado": "2000-01-01T00:00", "lluvia": None, "rayos": None, "oficial": None, "nowcast": None, "errores": []})))
        ctx.route("**/unpkg.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))

        def reporte(route):
            s.reportes.append(json.loads(route.request.post_data or "{}")); route.fulfill(**json_ok({"ok": True}))
        ctx.route("**/fb.test/reporte", reporte)
        pagina.on("pageerror", lambda e: s.errores.append(f"pageerror: {e}"))
        pagina.goto(url_ubic)
        pagina.wait_for_selector("#lugarnom")
        return s

    yield _crear
    for c in contextos: c.close()


# ================================================================= LO QUE PASABA: se quedaba en la ubicación guardada
def test_al_recargar_con_una_ubicacion_guardada_vieja_se_usa_la_de_ahora(persona):
    """Reproduce el error: la página abre con lo guardado (Barrio A, de hace 1 hora) y debe corregirse a donde está la persona (Barrio B)."""
    s = persona(donde=B, cache=(*A, "Barrio A, Guayaquil", SESENTA_MIN))
    s.esperar_nombre("Barrio B")
    assert "Barrio B" in s.nombre()
    assert s.pagina.evaluate("Math.abs(estado.ubic.lat - (%s)) < 0.02" % B[0]), "el pronóstico es del punto nuevo, no del guardado"
    assert s.errores == []


def test_la_posicion_se_pide_fresca_y_no_se_reutiliza_la_de_la_cache(persona):
    s = persona(donde=B, cache=(*A, "Barrio A, Guayaquil", SESENTA_MIN))
    s.esperar_nombre("Barrio B")
    llamadas = s.gps()
    assert llamadas, "la página tiene que preguntarle al GPS: antes devolvía la posición guardada sin preguntar"
    assert all(l["maximumAge"] is not None and l["maximumAge"] <= 60000 for l in llamadas), f"no se aceptan posiciones de más de un minuto: {llamadas}"
    assert all(l["enableHighAccuracy"] for l in llamadas)


def test_sin_ubicacion_guardada_funciona_como_siempre(persona):
    s = persona(donde=A)
    s.esperar_nombre("Barrio A")
    assert s.errores == []


# ================================================================= seguir a la persona mientras la página está abierta
def test_si_te_mueves_y_vuelves_a_la_pagina_el_lugar_se_actualiza(persona):
    s = persona(donde=A)
    s.esperar_nombre("Barrio A")
    s.mover(*B)
    s.volver_a_la_pestana()
    s.esperar_nombre("Barrio B")
    assert s.pagina.evaluate("Math.abs(estado.ubic.lat - (%s)) < 0.02" % B[0])
    assert s.errores == []


def test_el_ruido_normal_del_gps_no_recalcula_nada(persona):
    s = persona(donde=A)
    s.esperar_nombre("Barrio A"); s.pagina.wait_for_timeout(800)
    modelos, osm = s.n("open-meteo.com/v1/forecast"), s.n("nominatim")
    s.mover(A[0] - 0.0004, A[1] + 0.0003, prec=15)                 # ~55 m
    s.volver_a_la_pestana(); s.pagina.wait_for_timeout(1500)
    assert s.gps()[-1]["lat"] == pytest.approx(A[0] - 0.0004), "sí leyó el GPS"
    assert (s.n("open-meteo.com/v1/forecast"), s.n("nominatim")) == (modelos, osm), "55 m no justifican volver a descargar nada"
    assert "Barrio A" in s.nombre()


def test_un_gps_impreciso_no_provoca_saltos_pero_uno_preciso_si(persona):
    s = persona(donde=A)
    s.esperar_nombre("Barrio A")
    s.mover(*B, prec=9000)                                          # el teléfono dice "estoy en B, con 9 km de error"
    s.volver_a_la_pestana()
    assert s.se_queda("Barrio A", 2500), "con 9 km de incertidumbre no se puede saber si te moviste"
    s.mover(*B, prec=20); s.pagina.evaluate("estado.ultMov = 0"); s.volver_a_la_pestana()
    s.esperar_nombre("Barrio B")


def test_mientras_se_actualiza_dice_actualizando_y_nunca_mezcla_nombre_viejo_con_coordenadas_nuevas(persona):
    s = persona(donde=A)
    s.esperar_nombre("Barrio A")
    s.retener = True                                                # OpenStreetMap todavía no contesta cómo se llama el nuevo lugar
    s.mover(*B); s.pagina.evaluate("estado.ultMov = 0")
    s.volver_a_la_pestana()
    s.pagina.wait_for_function("document.querySelector('#lugarnom').textContent.includes('Actualizando')", timeout=6000)
    assert "Actualizando tu ubicación" in s.lugar(), "también en la cabecera del pronóstico"
    s.pagina.wait_for_timeout(700)
    assert "Barrio" not in s.nombre() and "Barrio" not in s.lugar(), "ni el nombre viejo ni uno a medias, por mucho que tarde"
    s.liberar()
    s.esperar_nombre("Barrio B")
    assert "Actualizando" not in s.nombre() and "Actualizando" not in s.lugar()
    u = s.pagina.evaluate("({n: estado.ubic.nombre, lat: estado.ubic.lat})")
    assert u["n"].startswith("Barrio B") and abs(u["lat"] - B[0]) < 0.02, f"nombre y coordenadas son del mismo lugar: {u}"


def test_con_una_ubicacion_guardada_vieja_dice_actualizando_mientras_llega_el_gps(persona):
    s = persona(donde=B, cache=(*A, "Barrio A, Guayaquil", SESENTA_MIN), demora_gps=1500)
    s.pagina.wait_for_function("document.querySelector('#lugarnom').textContent.includes('Actualizando')", timeout=3000)
    assert "Actualizando tu ubicación" in s.lugar(), "no se muestra el barrio viejo como si fuera el actual"
    s.esperar_nombre("Barrio B")
    assert "Actualizando" not in s.nombre() and "Actualizando" not in s.lugar()


def test_si_el_gps_no_responde_se_queda_lo_guardado_sin_quedar_en_actualizando(persona):
    s = persona(donde=B, cache=(*A, "Barrio A, Guayaquil", SESENTA_MIN), gps_falla=True, demora_gps=800)
    s.pagina.wait_for_function("document.querySelector('#lugarnom').textContent.includes('Actualizando')", timeout=3000)
    s.esperar_nombre("Barrio A", 6000)                              # el GPS falla: vuelve a mostrar lo guardado
    assert "Actualizando" not in s.nombre() and "Actualizando" not in s.lugar()
    assert s.errores == []


def test_si_falla_la_descarga_a_mitad_de_la_actualizacion_tampoco_se_queda_en_actualizando(persona):
    s = persona(donde=A)
    s.esperar_nombre("Barrio A")
    s.ctx.route("**/api.open-meteo.com/**", lambda r: r.fulfill(status=500, content_type="application/json", body="{}"))
    s.mover(*B); s.pagina.evaluate("estado.ultMov = 0"); s.volver_a_la_pestana()
    s.pagina.wait_for_timeout(2500)
    assert "Actualizando" not in s.nombre() and "Actualizando" not in s.lugar()
    assert "Barrio A" in s.nombre(), "con lo anterior, que sigue siendo coherente"


def test_con_el_gps_real_del_navegador_tambien_te_sigue(persona):
    """Sin el GPS simulado: usa la API de geolocalización de verdad (Chromium), para no depender de mi simulación."""
    s = persona(donde=A, gps_nativo=True)
    s.esperar_nombre("Barrio A")
    s.mover(*B)
    s.pagina.evaluate("estado.ultMov = 0"); s.volver_a_la_pestana()
    s.esperar_nombre("Barrio B", 15000)
    assert s.pagina.evaluate("Math.abs(estado.ubic.plat - (%s)) < 0.001" % B[0]), "guardó la posición exacta, no la redondeada"
    assert s.errores == []


def test_con_el_gps_real_y_una_ubicacion_guardada_vieja_se_corrige(persona):
    s = persona(donde=B, cache=(*A, "Barrio A, Guayaquil", SESENTA_MIN), gps_nativo=True)
    s.esperar_nombre("Barrio B", 15000)
    assert s.errores == []


# ================================================================= botón y selector
def test_boton_actualizar_mi_ubicacion_en_el_selector(persona):
    s = persona(donde=A)
    s.esperar_nombre("Barrio A")
    s.mover(*B)                                                     # se movió sin que la página lo notara
    s.pagina.click("#lugarbtn"); s.pagina.wait_for_selector("#lact")
    assert "Actualizar mi ubicación" in s.pagina.inner_text("#lact")
    s.pagina.click("#lact")
    s.esperar_nombre("Barrio B")
    assert s.gps()[-1]["maximumAge"] == 0, "el botón exige una lectura nueva, sin usar posiciones guardadas"


def test_si_el_gps_falla_al_pulsar_el_boton_lo_dice(persona):
    s = persona(donde=A)
    s.esperar_nombre("Barrio A")
    s.pagina.evaluate("__geo.modo = 'error'")
    s.pagina.click("#lugarbtn"); s.pagina.wait_for_selector("#lact"); s.pagina.click("#lact")
    s.pagina.wait_for_selector("#toast:not([hidden])", timeout=8000)
    assert "No pudimos obtener tu ubicación" in s.pagina.inner_text("#toast")
    assert "Barrio A" in s.nombre(), "y se queda con el lugar anterior"


def test_elegir_mi_ubicacion_en_el_selector_revisa_si_te_moviste(persona):
    s = persona(donde=A)
    s.esperar_nombre("Barrio A")
    s.pagina.evaluate("estado.vista = 0; render()")                 # está viendo una ciudad
    s.mover(*B)
    s.pagina.click("#lugarbtn"); s.pagina.wait_for_selector("#sheet .lit")
    s.pagina.click("#sheet .lit[data-v=ubic]")
    s.esperar_nombre("Barrio B")


def test_pronostico_de_una_ciudad_elegida_no_se_mueve_con_la_persona(persona):
    s = persona(donde=A)
    s.esperar_nombre("Barrio A")
    s.pagina.evaluate("estado.vista = 2; render()")                 # Manta, por ejemplo
    ciudad = s.nombre()
    s.mover(*B); s.pagina.evaluate("estado.ultMov = 0"); s.volver_a_la_pestana(); s.pagina.wait_for_timeout(2500)
    assert s.nombre() == ciudad, "estaba mirando otra ciudad: no se la cambiamos"
    assert "Barrio B" in s.pagina.evaluate("estado.ubic.nombre"), "pero 'Mi ubicación' sí quedó actualizada por detrás"


# ================================================================= el resto de la página usa la posición de ahora
def test_el_reporte_del_clima_sale_de_donde_estas_ahora(persona):
    """Antes el reporte salía con la posición guardada, por ejemplo la de ayer: los reportes de la gente quedaban en el barrio equivocado."""
    s = persona(donde=B, cache=(*A, "Barrio A, Guayaquil", SESENTA_MIN))
    s.esperar_nombre("Barrio B")
    s.pagina.click("#breporte"); s.pagina.wait_for_selector(".fbb button")
    s.pagina.locator(".fbb button[data-t=lluvia]").click(); s.pagina.wait_for_timeout(800)
    assert s.reportes, "se envió el reporte"
    assert s.reportes[-1]["lat"] == pytest.approx(round(B[0], 2), abs=0.011), s.reportes[-1]
    assert s.reportes[-1]["lat"] != pytest.approx(round(A[0], 2), abs=0.011), "no es la posición de la ubicación guardada"


def test_la_actualizacion_periodica_de_20_minutos_vuelve_a_leer_el_gps(persona):
    s = persona(donde=A)
    s.esperar_nombre("Barrio A")
    s.mover(*B)
    s.pagina.evaluate("estado.ubic.t = 0")                          # pasaron más de 20 minutos
    s.pagina.evaluate("refrescarDatos()")
    s.esperar_nombre("Barrio B")
    assert s.gps()[-1]["maximumAge"] == 0


def test_permiso_denegado_con_una_ubicacion_vieja_lo_avisa(persona):
    s = persona(donde=A, cache=(*A, "Barrio A, Guayaquil", SESENTA_MIN), permiso="denied")
    s.pagina.wait_for_selector("#toast:not([hidden])", timeout=8000)
    assert "Sin permiso de ubicación" in s.pagina.inner_text("#toast")
    assert "Barrio A" in s.nombre() and "Actualizando" not in s.nombre()
    assert s.errores == []


def test_una_ubicacion_guardada_muy_reciente_no_muestra_actualizando(persona):
    s = persona(donde=A, cache=(*A, "Barrio A, Guayaquil", 2 * 60 * 1000))
    assert "Actualizando" not in s.nombre() and "Actualizando" not in s.lugar()
    s.esperar_nombre("Barrio A")
