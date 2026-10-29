# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 Robert Alejandro Figueroa Pazmiño (https://github.com/suirobert/ClearSky)
"""Configuración común de las pruebas. Las variables de entorno se fijan ANTES de importar clima_consenso
(el script las lee al cargarse): todo se escribe en una carpeta temporal, nunca en tu repositorio."""
import os, sys, tempfile, pathlib, importlib

RAIZ = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="clearsky-pruebas-"))
os.environ["CC_DATOS"] = str(TMP / "datos")
os.environ["CC_HTML"] = str(TMP / "sitio" / "index.html")
os.environ["CC_FEEDBACK_URL"] = "https://fb.test"
os.environ["CC_FEEDBACK_TOKEN"] = "token-de-prueba"
sys.path.insert(0, str(RAIZ)); sys.path.insert(0, str(RAIZ / "tests"))

import pytest
import simulador


@pytest.fixture(scope="session")
def cc():
    return importlib.import_module("clima_consenso")


@pytest.fixture(scope="session")
def raiz():
    return RAIZ


@pytest.fixture(scope="session")
def pagina_generada(cc):
    """Corre el script completo (verificación incluida) con la red simulada y devuelve la carpeta del sitio generado.
    Es lo más lento de las pruebas (~1-2 min): se hace una sola vez.
    Para repetir pruebas del navegador sin esperar, CC_PAGINA_YA_GENERADA=<carpeta del sitio> reutiliza una página ya generada."""
    previa = os.environ.get("CC_PAGINA_YA_GENERADA")
    if previa and (pathlib.Path(previa) / "index.html").exists():
        return pathlib.Path(previa)
    restaurar = simulador.instalar(cc, str(TMP))
    guardado = sys.argv
    try:
        simulador.sembrar_historial(cc)
        sys.argv = ["clima_consenso.py", "--no-open", "--verificar"]
        cc.main()
    finally:
        sys.argv = guardado
        restaurar()
    return pathlib.Path(cc.ARCHIVO_HTML).parent
