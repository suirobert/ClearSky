# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
"""Navegador real (Chromium) para probar la página generada: sirve el sitio simulado y falsea todo lo que sale a internet."""
import base64, functools, http.server, json, math, os, threading, time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs
import pytest
import simulador

MOCK_LEAFLET = r"""
window.__L = {marcas: [], urls: 0, mapas: 0};
(function () {
  function o() { const x = {addTo() { return x; }, setView() { return x; }, setStyle() { return x; }, bindTooltip(t) { x._tt = t; return x; }, on() { return x; }, off() { return x; },
    remove() {}, invalidateSize() {}, setLatLng() { return x; }, setContent() { return x; }, openOn() { return x; }, removeLayer() { return x; }, setUrl() { window.__L.urls++; return x; }}; return x; }
  window.L = {map: (id) => { document.getElementById(id).style.background = '#2b3440'; window.__L.mapas++; return o(); }, tileLayer: () => o(), imageOverlay: () => o(), popup: () => o(),
    layerGroup: () => o(), circleMarker: (ll, op) => { const x = o(); window.__L.marcas.push({ll, op, x}); return x; }};
})();
"""
ALERTAS_VACIAS = {"truenos": {"n": 0, "personas": 0, "ok": False, "ts": None}, "inundado": {"n": 0, "personas": 0, "ok": False, "ts": None}, "puntos": [],
                  "minimo": {"personas": 2, "sin_codigo": 3}, "vigencia_min": {"truenos": 30, "inundado": 180}, "radio_km": {"truenos": 10, "inundado": 2}, "codigo_persona": True}


def hace(minutos):
    return int(time.time()) - minutos * 60


def alertas(truenos=None, inundado=None, puntos=()):
    """Respuesta de /alertas. truenos / inundado: (personas, hace_minutos) o None."""
    a = json.loads(json.dumps(ALERTAS_VACIAS))
    for clave, v in (("truenos", truenos), ("inundado", inundado)):
        if v: a[clave] = {"n": v[0], "personas": v[0], "ok": v[0] >= 2, "ts": hace(v[1])}
    a["puntos"] = [{"lat": la, "lon": lo, "personas": p, "n": p, "ts": hace(m)} for la, lo, p, m in puntos]
    return a


def hora_ec(minutos=0):
    return (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5) + timedelta(minutes=minutos)).strftime("%Y-%m-%dT%H:%M")


class _Silencioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


@pytest.fixture(scope="session")
def servidor(pagina_generada):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Silencioso, directory=str(pagina_generada)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="session")
def navegador():
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    ruta = os.environ.get("CC_CHROMIUM") or None
    b = p.chromium.launch(executable_path=ruta) if ruta else p.chromium.launch()
    yield b
    b.close(); p.stop()


@pytest.fixture
def abrir(servidor, navegador, cc):
    contextos = []

    def _abrir(*, lat=-2.17, lon=-79.9, ancho=1440, movil=False, tema="dark", hora_local=None, alertas_rep=None, ahora=None, reporte=None,
               lugares=None, reducir_movimiento=False, esperar=True, permitir_errores_red=False):
        """Abre la página con todo lo externo simulado. Devuelve un objeto con .pagina, .pedidos y .errores."""
        kw = dict(viewport={"width": ancho if not movil else 390, "height": 900 if not movil else 844}, geolocation={"latitude": lat, "longitude": lon},
                  permissions=["geolocation"], color_scheme=tema, timezone_id="America/Guayaquil", locale="es-EC",
                  reduced_motion="reduce" if reducir_movimiento else "no-preference")
        if movil: kw.update(device_scale_factor=2, is_mobile=True, has_touch=True)
        ctx = navegador.new_context(**kw); contextos.append(ctx)
        pedidos, errores = [], []
        ctx.on("request", lambda r: pedidos.append((r.method, r.url, r.post_data)))
        if hora_local:                                                       # "14:05" hora de Ecuador de hoy
            h, m = map(int, hora_local.split(":"))
            utc = (datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=h + 5, minutes=m)).replace(tzinfo=timezone.utc)
            ctx.clock.set_fixed_time(utc)                                   # con datetime (un número se interpreta como segundos)
        json_ok = lambda cuerpo, estado=200: dict(status=estado, content_type="application/json", headers={"access-control-allow-origin": "*"}, body=json.dumps(cuerpo))

        def modelos(route):
            q = parse_qs(urlparse(route.request.url).query)
            if "daily" in q:
                dias = [(date.today() - timedelta(days=d)).isoformat() for d in range(30, -1, -1)]
                return route.fulfill(**json_ok({"daily": {"time": dias, "temperature_2m_max": [31.0] * len(dias)}}))
            la, lo = float(q["latitude"][0]), float(q["longitude"][0])
            route.fulfill(**json_ok(simulador.respuesta_modelos(cc, la, lo, q["hourly"][0].split(","), simulador.horas(0, 7))))
        ctx.route("**/api.open-meteo.com/**", modelos)
        ctx.route("**/nominatim.openstreetmap.org/**", lambda r: r.fulfill(**json_ok({"address": {"suburb": "Barrio", "city": "Ciudad", "state": "Florida" if lat > 10 else ("Pichincha" if lat > -1 else "Guayas")}})))
        ctx.route("**/unpkg.com/**leaflet.js", lambda r: r.fulfill(status=200, content_type="application/javascript", body=MOCK_LEAFLET))
        ctx.route("**/unpkg.com/**leaflet.css", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        ctx.route("**/fb.test/resumen**", lambda r: r.fulfill(**json_ok({"total": 0})))
        ctx.route("**/fb.test/vapid", lambda r: r.fulfill(**json_ok({"publica": ""})))

        def manejar_alertas(route):
            a = alertas_rep(route.request) if callable(alertas_rep) else (alertas_rep if alertas_rep is not None else ALERTAS_VACIAS)
            route.fulfill(**json_ok(a)) if not isinstance(a, tuple) else route.fulfill(**json_ok(a[1], a[0]))
        ctx.route("**/fb.test/alertas**", manejar_alertas)

        def manejar_reporte(route):
            tipo = json.loads(route.request.post_data or "{}").get("tipo")
            estado, cuerpo = reporte(tipo) if reporte else (200, {"ok": True})
            route.fulfill(**json_ok(cuerpo, estado))
        ctx.route("**/fb.test/reporte", manejar_reporte)
        vacio = {"generado": hora_ec(-1), "lluvia": None, "movimiento": None, "rayos": None, "oficial": None, "nowcast": None, "errores": []}
        ctx.route("**/raw.githubusercontent.com/**", lambda r: r.fulfill(**json_ok(ahora or vacio)))
        if lugares: ctx.add_init_script(f"try {{ localStorage.setItem('cc_lugares', {json.dumps(json.dumps(lugares))}); }} catch (e) {{}}")
        pagina = ctx.new_page()
        pagina.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
        # los "Failed to load resource" del navegador no dicen cuál; se registran las respuestas con error (salvo las que la prueba provoca a propósito)
        pagina.on("console", lambda m: errores.append(f"console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
        pagina.on("response", lambda r: errores.append(f"HTTP {r.status}: {r.url}") if r.status >= 400 and not permitir_errores_red else None)
        pagina.goto(servidor + "/index.html")
        if esperar:
            pagina.wait_for_selector("#c24 .hc", timeout=20000)
            pagina.wait_for_load_state("networkidle")
            pagina.wait_for_timeout(300)
        return SimpleNamespace(pagina=pagina, pedidos=pedidos, errores=errores, contexto=ctx)

    yield _abrir
    for c in contextos: c.close()


def ahora_json(lluvia_km_oeste=42, u=30.0, estado_nowcast="pruebas", rayos=None, oficial=None):
    """ahora.json de prueba: una mancha de lluvia al oeste de Guayaquil que avanza hacia el este."""
    import numpy as np
    lat0, lon0, p, ny, nx = 2.5, -92.5, 0.1, 80, 175
    la = lat0 - (np.arange(ny) + .5) * p; lo = lon0 + (np.arange(nx) + .5) * p
    Y, X = np.meshgrid(la, lo, indexing="ij")
    clo = -79.9 - lluvia_km_oeste / (111.32 * math.cos(math.radians(2.17)))
    d2 = ((Y - -2.17) * 111.32) ** 2 + ((X - clo) * 111.32 * np.cos(np.radians(Y))) ** 2
    f = 6 * np.exp(-d2 / (2 * 9 ** 2)); f[f < 0.3] = 0
    q = np.clip(np.round(f * 2), 0, 255).astype("uint8")
    nty, ntx = 4, 9
    return {"generado": hora_ec(-1), "lluvia": {"t": hora_ec(-8), "lat0": lat0, "lon0": lon0, "paso": p, "ny": ny, "nx": nx, "escala": 0.5, "q": base64.b64encode(q.tobytes()).decode()},
            "movimiento": {"tile": 2.0, "lat0": 2.5, "lon0": -92.5, "ny": nty, "nx": ntx, "dt_min": 30, "u": [[u] * ntx] * nty, "v": [[0.0] * ntx] * nty, "skill": [[0.2] * ntx] * nty, "validos": nty * ntx},
            "nowcast": {"estado": estado_nowcast, "n": 12 if estado_nowcast == "pruebas" else 40, "ok": 5 if estado_nowcast == "pruebas" else 26, "precision": 42 if estado_nowcast == "pruebas" else 65,
                        "min_casos": 30, "min_precision": 50, "ventana_min": 20, "base": 4.2, "eventos": 22, "detectados": 9, "cobertura": 41},
            "rayos": rayos, "oficial": oficial, "errores": []}
