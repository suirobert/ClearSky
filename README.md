# Clima Consenso

Pronóstico de lluvia para Ecuador que combina 7 modelos meteorológicos internacionales,
da más peso a los que mejor aciertan en cada ciudad y se calibra a diario con lo que
realmente llovió en los aeropuertos.

## Cómo funciona
- **Cada hora** GitHub Actions ejecuta `clima_consenso.py`, genera la página y la publica en GitHub Pages.
- **Una vez al día** compara los pronósticos con lo observado, se examina con días que no usó para aprender
  y solo acepta los ajustes que mejoran el resultado.
- Todo lo aprendido queda en `datos/`:

| Archivo | Contenido |
|---|---|
| `datos/pesos.json` | Pesos de cada modelo, umbral y calibración por ciudad |
| `datos/observaciones/AAAA-MM.json` | Lluvia observada por hora (memoria permanente) |
| `datos/historial/AAAA-MM-DD.csv` | Lo que mostró la página, para medir aciertos reales |
| `datos/errores.log` | Avisos y errores (últimas 500 líneas) |

## Operación
- **Ejecutar a mano:** pestaña *Actions* → *Actualizar pronóstico* → *Run workflow*
  (o `gh workflow run actualizar.yml -f verificar=true`).
- **Si una ejecución falla**, GitHub envía un correo al dueño del repositorio.
- **Probar en un PC:** `python clima_consenso.py` (crea `dashboard.html`; no subas esos cambios de `datos/`).

Fuentes: [Open-Meteo](https://open-meteo.com) (modelos) · [IEM / Iowa State](https://mesonet.agron.iastate.edu) (METAR).
