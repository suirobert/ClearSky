# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
"""Lo más básico: que todo compile y que el esquema de la base de datos sea el que el servicio espera."""
import py_compile, shutil, sqlite3, subprocess, tempfile, pathlib
import pytest


def _node():
    n = shutil.which("node")
    if not n: pytest.skip("node no está instalado")
    return n


@pytest.mark.parametrize("archivo", ["clima_consenso.py", "ahora.py"])
def test_python_compila(raiz, archivo):
    ruta = raiz / archivo
    if not ruta.exists(): pytest.skip(f"{archivo} no está en este repositorio")
    py_compile.compile(str(ruta), doraise=True)


def test_javascript_de_la_pagina_es_valido(raiz, cc):
    texto = (raiz / "clima_consenso.py").read_text(encoding="utf-8")
    js = texto[texto.index('JS = r"""') + 9: texto.index('"""\n\n\n# ----------------------- MAIN')]
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "pagina.js"; f.write_text(js, encoding="utf-8")
        r = subprocess.run([_node(), "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-600:]


def test_worker_de_cloudflare_es_valido(raiz):
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "worker.mjs"; shutil.copy(raiz / "feedback" / "worker.js", f)
        r = subprocess.run([_node(), "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-600:]


def test_esquema_de_la_base_de_datos(raiz):
    db = sqlite3.connect(":memory:"); db.executescript((raiz / "feedback" / "schema.sql").read_text(encoding="utf-8"))
    columnas = lambda t: {c[1] for c in db.execute(f"PRAGMA table_info({t})")}
    assert {"id", "ts", "lat", "lon", "tipo", "h"} <= columnas("rep"), "falta la columna h (código de persona)"
    assert {"endpoint", "p256dh", "auth", "lat", "lon", "nombre", "creado", "aviso", "admin", "pref"} <= columnas("sub")
    assert {"h", "ts"} <= columnas("lim") and {"k", "v"} <= columnas("estado")
    db.executescript((raiz / "feedback" / "schema.sql").read_text(encoding="utf-8"))        # se puede volver a ejecutar sin errores


def test_los_archivos_de_codigo_llevan_el_aviso_de_derechos(raiz):
    for archivo in ["clima_consenso.py", "ahora.py", "feedback/worker.js"]:
        ruta = raiz / archivo
        if ruta.exists():
            assert "Required Notice: Copyright" in ruta.read_text(encoding="utf-8")[:400], f"{archivo}: falta el aviso de derechos"
