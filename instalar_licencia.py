# -*- coding: utf-8 -*-
"""Descarga el texto OFICIAL de la licencia PolyForm Noncommercial 1.0.0 y lo guarda como LICENSE.
La licencia tiene que copiarse idéntica: por eso no se escribe a mano, se baja del sitio oficial y se comprueba.
Uso (en la carpeta del proyecto):  python instalar_licencia.py"""
import sys, urllib.request

URLS = ["https://raw.githubusercontent.com/polyformproject/polyform-licenses/1.0.0/PolyForm-Noncommercial-1.0.0.md",
        "https://polyformproject.org/wp-content/uploads/2019/07/Polyform-Noncommercial-1.0.0.txt"]
TITULO = "PolyForm Noncommercial License 1.0.0"
SECCIONES = ["Acceptance", "Copyright License", "Distribution License", "Notices", "Changes and New Works License", "Patent License",
             "Noncommercial Purposes", "Personal Uses", "Noncommercial Organizations", "Fair Use", "No Other Rights", "Patent Defense",
             "Violations", "No Liability", "Definitions"]


def comprobar(datos):
    """Devuelve la lista de problemas (vacía si el texto parece la licencia completa)."""
    t = datos.decode("utf-8", errors="replace")
    problemas = []
    if TITULO.lower() not in t.lower():
        problemas.append("no aparece el título de la licencia")
    for s in SECCIONES:
        if s.lower() not in t.lower():
            problemas.append(f"falta la sección «{s}»")
    if "Yoyodyne" not in t:
        problemas.append("falta el ejemplo de «Required Notice»")
    n = t.count("\n") + 1
    if not 4000 <= len(datos) <= 5200:
        problemas.append(f"el tamaño es raro: {len(datos)} bytes (el oficial pesa unos 4,5 KB)")
    if not 60 <= n <= 110:
        problemas.append(f"el número de líneas es raro: {n}")
    if "<html" in t.lower():
        problemas.append("parece una página web, no el texto de la licencia")
    return problemas


def descargar():
    ultimo = None
    for u in URLS:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "ClearSky-licencia"})
            with urllib.request.urlopen(req, timeout=30) as r:
                datos = r.read()
            p = comprobar(datos)
            if not p:
                return u, datos
            ultimo = f"{u}: " + "; ".join(p)
        except Exception as e:
            ultimo = f"{u}: {e}"
    raise RuntimeError(ultimo)


if __name__ == "__main__":
    try:
        url, datos = descargar()
    except Exception as e:
        sys.exit("No se pudo obtener un texto válido de la licencia.\n  " + str(e) +
                 "\nDescárgala a mano de https://polyformproject.org/licenses/noncommercial/1.0.0 y guárdala como LICENSE sin cambiarle nada.")
    with open("LICENSE", "wb") as f:
        f.write(datos)
    lineas = datos.count(b"\n") + 1
    print(f"OK: LICENSE guardado ({len(datos)} bytes, {lineas} líneas) desde {url}")
    print("Revisa que NOTICE tenga tu aviso «Required Notice:» y haz commit de LICENSE y NOTICE.")
