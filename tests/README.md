# Pruebas de ClearSky

Estas pruebas comprueban que un cambio no rompa lo que ya funciona. **No usan internet ni tocan tus datos**: todo lo
externo (Open-Meteo, aeropuertos, NOAA, GitHub, Cloudflare) está simulado y los archivos se escriben en una carpeta temporal.

## Qué cubren

| Carpeta | Qué prueba | Tarda |
|---|---|---|
| `rapidas/` | Que todo compile (Python, JavaScript de la página, servicio de Cloudflare), el esquema de la base de datos, y las funciones puras: calibración, métricas, lectura de aeropuertos, tablero de aciertos, reportes de la gente | ~1 s |
| `worker/` (`*.test.mjs`) | El servicio de Cloudflare con una base SQLite en memoria: reportes de clima, **truenos e inundación** (límites por categoría, personas distintas, radios, vigencia, privacidad), avisos al teléfono, cron de 10 min, cifrado | ~1 s |
| `completas/` | El script **entero** con datos simulados (verificación, página, tablero) y la **página en un navegador real**: diseño, pestañas, reportes, banner de inundación, mapa, lluvia en camino, lugares guardados | ~2-3 min |

`simulador.py` inventa datos con una «verdad» conocida (llueve más por la tarde; los modelos dan 1,5° de más) para poder
comprobar, por ejemplo, que la corrección de temperatura aprende ese 1,5°.

## Cómo correrlas en tu PC (PowerShell, desde la carpeta del proyecto)

```powershell
pip install -r tests/requirements.txt
python -m playwright install chromium        # una sola vez: el navegador de las pruebas

python -m pytest tests/rapidas               # segundos
node --test                                  # servicio de Cloudflare (necesita Node 22 o más reciente)
python -m pytest tests/completas             # 2-3 minutos
```

Para repetir las pruebas del navegador sin esperar a que se genere la página cada vez:

```powershell
$env:CC_PAGINA_YA_GENERADA = "C:\ruta\a\la\carpeta\sitio"   # una carpeta con index.html ya generado
```

## En GitHub

`.github/workflows/pruebas.yml` las corre solas cuando cambia el código (no cuando el bot guarda datos) y una vez por semana
(para detectar cambios en las librerías o en GitHub que rompan algo sin que nadie haya tocado el código). Si algo falla,
GitHub te avisa por correo y el commit aparece con una ✗ roja.

## Cómo se sabe que las pruebas sirven

Una prueba que nunca falla no protege nada. Por eso, al escribirlas, se **rompió el código a propósito** de varias maneras
(por ejemplo, «un solo reporte basta para mostrar una inundación» o «la inundación dura 30 minutos en vez de 3 horas») y se
comprobó que alguna prueba fallara cada vez. Si agregas una función nueva, haz lo mismo: cambia una condición y verifica que
la prueba lo note.

## Qué NO cubren

- No hablan con los servicios reales: si NOAA cambia el formato de sus archivos, estas pruebas no lo ven (lo vería `ahora.yml`
  en su ejecución normal, que registra el motivo en `ahora.json`).
- Las pruebas de `ahora.py` se limitan a comprobar que compile; su lectura de NetCDF y del boletín del INAMHI no están
  cubiertas todavía.
- El navegador de las pruebas es Chromium. No se prueba Safari ni Firefox.
