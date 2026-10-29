# ClearSky

**Pronóstico del tiempo para Ecuador que mide en público cuánto acierta.**

🌐 [**suirobert.github.io/ClearSky**](https://suirobert.github.io/ClearSky/)

En Ecuador es común decir "si la app dice que va a llover, no llueve". ClearSky parte de esa desconfianza:
combina 7 modelos meteorológicos, aprende cuál acierta más en cada ciudad, ajusta sus porcentajes para que un 30%
signifique de verdad 3 de cada 10, **y publica cada día lo que dijo contra lo que pasó, incluidos los fallos**.

## Qué ofrece

- **Tu barrio primero**: pronóstico de tu punto exacto con el nombre del barrio. Las ciudades van en una lista aparte.
- **Lluvia, llovizna, tormentas eléctricas, calor** (sensación térmica, onda de calor), viento, humedad e índice UV.
- **Pestaña "Aciertos"**: día por día, lo que dijimos y lo que pasó (acertamos / falsa alarma / se nos escapó), por ciudad.
- **Lo que está pasando ahora**: lluvia observada por satélite, rayos de los últimos 20 minutos y reportes de la gente.
- **Lluvia en camino**: "llegaría en ~40 min, viene del oeste", a partir del movimiento de la lluvia que mide el satélite.
- **Aviso oficial del INAMHI** cuando hay un boletín vigente que afecta tu región.
- **Hora por hora** con selector de **24 h · 3 días · 7 días** y de variable (lluvia, temperatura, viento, UV). Los días lejanos que todavía no superan al promedio salen rayados como _orientativos_.
- **Avisos al teléfono a tu medida**: eliges entre lluvia, tormentas eléctricas y avisos oficiales.
- Mapa, satélite, reportar el clima, compartir, modo claro/oscuro, instalable como app, se actualiza sola.

## Cómo funciona (y cómo sabemos que acierta)

1. **Datos** cada hora: 7 modelos (ECMWF, GFS, ICON, GEM, JMA, MeteoFrance, UKMO) desde Open-Meteo.
2. **Consenso ponderado** según lo que acertó cada modelo en cada ciudad.
3. **Verificación diaria** de lo pronosticado a 1-7 días contra los aeropuertos (METAR), con **examen sin trampa**: se aprende con los días anteriores y se examina con los últimos 10, que no se usaron para aprender. Solo se acepta un ajuste si acierta más. Ventana deslizante de 45 días.
3. **Temperatura y humedad** también se comparan cada hora con el aeropuerto y se corrigen si el examen lo aprueba.
4. **Rayos (GOES-19 GLM)**: cada 10 minutos se cuentan las descargas a menos de 25 km de cada ciudad. Cuando hay 16 días acumulados, las **tormentas se verifican con rayos** (no solo con lo que reporta un aeropuerto) y esa calibración se usa **solo si en el examen supera a decir siempre el promedio**.
5. **Ciudades sin aeropuerto utilizable** (Machala, Santo Domingo, Loja, Ambato, Portoviejo): se verifican con la **lluvia que estima el satélite** cuando hay 21 días acumulados, **pero solo si antes el satélite demostró coincidir con los aeropuertos** (HSS ≥ 0,25 y detecta al menos 35% de las lluvias). La página las marca como verificación _aproximada_.
6. **Lluvia en camino**: con tres imágenes del satélite (ahora, hace 30 y hace 60 min) se mide hacia dónde se mueve la lluvia por cuadros de 2°. **Un cuadro solo se publica si con el movimiento de hace 30 min se adivinó la imagen de ahora mejor que suponiendo que la lluvia no se mueve.** Se extiende ese movimiento hasta 90 minutos.
7. **Tablero de aciertos**: "dijimos lluvia" = máximo del día de 40% o más; "dijimos que no llovería" = menos de 20%; entre medio, dudoso.
8. **Pruebas antes de publicar**: si algo falla, se conserva la página anterior.

> Lo que todavía **no** se verifica: niebla, viento, índice UV, humedad en ciudades sin aeropuerto y la lluvia en camino
> (se valida el movimiento, pero no se guarda cuántas veces acertó el "llegaría en X min"; pendiente).

## Archivos de datos

| Archivo | Contenido |
|---|---|
| `datos/pesos.json` | Lo aprendido por ciudad (lluvia, tormentas con aeropuerto y con rayos, temperatura, plazos, franjas) |
| `datos/observaciones/AAAA-MM.json` | Lluvia, llovizna y tormenta observadas en los aeropuertos |
| `datos/observado/AAAA-MM.json` | Observaciones propias por hora y ciudad: `[lluvia máx. mm/h del satélite, muestras, rayos a <25 km, franjas de 10 min]` |
| `datos/historial/AAAA-MM-DD.csv` | Lo que mostró la página (base del tablero de aciertos) |
| `datos/alertas.json` | Cambios bruscos con texto listo para publicar |
| `datos/reportes/` y `datos/respaldos/` | Reportes de usuarios y respaldo semanal |

## Los dos workflows

- **`actualizar.yml`** (cada hora): pronóstico, verificación, página. Guarda lo observado en `datos/observado/`.
- **`ahora.yml`** (cada 10 minutos, `ahora.py`): publica en la rama `ahora` los archivos `ahora.json` (lluvia, movimiento, rayos y boletín del INAMHI, que lee la página) y `hist.json` (72 h de lluvia y rayos por ciudad). Si falla, la página sigue funcionando con el pronóstico.

## Servicio de reportes y avisos (Cloudflare)

Carpeta `feedback/`: Worker + D1, configurados desde el panel de Cloudflare.

- **Base de datos**: `schema.sql`. Si la tabla `sub` ya existía, ejecuta una sola vez `ALTER TABLE sub ADD COLUMN pref INTEGER NOT NULL DEFAULT 7;`
- **Secretos**: `SAL`, `TOKEN`, `VAPID_PUBLIC`, `VAPID_PRIVATE`, opcional `VAPID_SUB` y `GH_TOKEN`; variable `ORIGEN`. Cron `*/30 * * * *`.
- **Preferencias** (`pref`): 1 = lluvia (incluye "llegaría en X min"), 2 = tormentas eléctricas, 4 = avisos oficiales. Cada persona las elige en la página.
- Límites: tormenta, máximo uno cada 2 h; lluvia, uno cada 6 h; oficial, una vez por boletín.

**Privacidad**: la IP nunca se guarda. Los reportes guardan hora, tipo y ubicación redondeada.

## Límites conocidos

- La verificación usa un punto por ciudad (el aeropuerto).
- La lluvia satelital es una estimación: detecta bien los aguaceros, pero puede fallar con lloviznas. No es un radar.
- "Lluvia en camino" extiende en línea recta el movimiento observado: sirve para la próxima hora y no puede anticipar lluvias que nacen o se disipan.
- Los rayos son detecciones satelitales y pueden tardar unos minutos.
- **Para emergencias sigue siempre las alertas oficiales** de la Secretaría de Gestión de Riesgos y del INAMHI (911).

## Créditos y licencias

- Pronósticos: [Open-Meteo.com](https://open-meteo.com/), CC BY 4.0 (uso no comercial en el plan gratuito).
- Observaciones METAR: [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/).
- Lluvia satelital: NOAA Enterprise Rain Rate. Rayos: GOES-19 GLM (NOAA). Imagen GOES-19 GeoColor: CIRA/NOAA vía NASA GIBS.
- Boletines: [INAMHI](https://servicios.inamhi.gob.ec/alertas/).
- Índice de calor: fórmula de la NOAA / National Weather Service.
- Mapa y barrios: [Leaflet](https://leafletjs.com/) y © [OpenStreetMap](https://www.openstreetmap.org/copyright).
- Íconos: [Meteocons](https://github.com/basmilius/meteocons) de Bas Milius, MIT.
