# -*- coding: utf-8 -*-
"""
Parche ClearSky: línea de 24 h sin textos cortados + mensaje unificado de la próxima hora.
Uso (en la carpeta del repositorio):  python parche_visual.py
Crea clima_consenso.py.bak y lo restaura solo si algo falla.
"""
import os, re, shutil, sys, py_compile

ARCH = "clima_consenso.py"
NUEVO_TL = 'CORTOS = {"Parcialmente nublado": "Algo nublado", "Lluvia probable": "Lluvia", "Posible lluvia": "Lluvia"}\n\n\ndef html_linea_tiempo(prox24):\n    """Barra de 24 h. Las horas con lluvia se agrupan en un solo tramo y los textos\n       se acortan o se ocultan según el espacio, para que nunca queden cortados."""\n    n = len(prox24)\n    segs, etqs = [], []\n    for i, r in enumerate(prox24):\n        _, txt, col = condicion(r)\n        segs.append(f\'<div class="seg" style="background:{col}" \'\n                    f\'title="{r["hora"][11:16]} · {txt} · lluvia {r["prob"]}%"></div>\')\n        if i % 2 == 0:\n            etq = "Ahora" if i == 0 else r["hora"][11:13] + "h"\n            etqs.append(f\'<div class="tk" style="left:{i/n*100:.2f}%"><b>{etq}</b>\'\n                        f\'<span>{round(r["temp"]) if r["temp"] is not None else "-"}°</span></div>\')\n\n    familia = lambda r: "lluvia" if r["prob"] >= 20 else condicion(r)[1]\n    runs = []                                   # [familia, inicio, fin, prob_max]\n    for i, r in enumerate(prox24):\n        f = familia(r)\n        if runs and runs[-1][0] == f:\n            runs[-1][2] = i + 1\n            runs[-1][3] = max(runs[-1][3], r["prob"])\n        else:\n            runs.append([f, i, i + 1, r["prob"]])\n    unidos = []                                 # tramos cortos sin lluvia se unen al anterior\n    for run in runs:\n        if unidos and run[0] != "lluvia" and (run[2] - run[1] < 3 or unidos[-1][0] == run[0]) \\\n                and unidos[-1][0] != "lluvia":\n            unidos[-1][2] = run[2]\n        else:\n            unidos.append(run)\n\n    bloques = []\n    for f, i0, i1, pmax in unidos:\n        largo = i1 - i0\n        texto = ("Lluvia probable" if pmax >= 40 else "Posible lluvia") if f == "lluvia" else f\n        if largo >= 4:\n            pass\n        elif largo >= 2:\n            texto = CORTOS.get(texto, texto if len(texto) <= 8 else "")\n        else:\n            texto = ""\n        if texto:\n            bloques.append(f\'<div class="blq" style="left:{i0/n*100:.2f}%;width:{largo/n*100:.2f}%">{texto}</div>\')\n\n    ley = (\'<div class="ley" style="margin-top:2px">\'\n           \'<span><i style="background:#cbd5e1;height:8px"></i>Despejado</span>\'\n           \'<span><i style="background:#94a3b8;height:8px"></i>Algo nublado</span>\'\n           \'<span><i style="background:#64748b;height:8px"></i>Nublado</span>\'\n           \'<span><i style="background:#60a5fa;height:8px"></i>Llovizna</span>\'\n           \'<span><i style="background:#3b82f6;height:8px"></i>Posible lluvia</span>\'\n           \'<span><i style="background:#1d4ed8;height:8px"></i>Lluvia probable</span></div>\')\n    return (f\'<div class="tl"><div class="blqs">{"".join(bloques)}</div>\'\n            f\'<div class="bar">{"".join(segs)}</div><div class="tks">{"".join(etqs)}</div>{ley}</div>\')\n\n\n'

if not os.path.exists(ARCH):
    sys.exit("No encuentro clima_consenso.py. Ejecuta el parche dentro de la carpeta del repositorio.")
s = open(ARCH, encoding="utf-8").read()
if "CORTOS = {" in s:
    sys.exit("El parche ya estaba aplicado. No se cambió nada.")
a, b = s.find("def html_linea_tiempo("), s.find("def html_dias(")
if a < 0 or b < 0 or a > b:
    sys.exit("No encuentro la función de la línea de 24 h. No se cambió nada; envíame el archivo.")
patron = re.compile(r"^(?P<ind>[ \t]*)chips = \(f'<span class=\"chip\">Ahora: <b>\{cond_txt\}</b></span>'\n"
                    r"[ \t]*f'<span class=\"chip\">Lluvia en la próxima hora: <b>\{actual\[\"prob\"\]\}%</b></span>'\n",
                    re.M)
m = patron.search(s)
if not m:
    sys.exit("No encuentro los indicadores 'Ahora' y 'Lluvia en la próxima hora'. No se cambió nada; envíame el archivo.")

shutil.copy(ARCH, ARCH + ".bak")
s = s[:a] + NUEVO_TL + s[b:]
ind = m.group("ind")
reemplazo = (f"{ind}txt_prox = (f'{{cond_txt.lower()}} ({{actual[\"prob\"]}}%)' if actual['prob'] >= 20\n"
             f"{ind}            else f'{{cond_txt}}, sin lluvia')\n"
             f"{ind}chips = (f'<span class=\"chip\">Próxima hora: <b>{{txt_prox}}</b></span>'\n")
s = patron.sub(lambda _: reemplazo, s, count=1)
with open(ARCH, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
try:
    py_compile.compile(ARCH, doraise=True)
except Exception as e:
    shutil.copy(ARCH + ".bak", ARCH)
    sys.exit(f"Error al compilar ({e}). Se restauró el archivo original.")
print("Parche visual aplicado correctamente. Copia de seguridad: clima_consenso.py.bak")
