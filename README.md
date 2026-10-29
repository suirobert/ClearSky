# ClearSky

**Pronóstico del tiempo para Ecuador que mide en público cuánto acierta.**

🌐 [**suirobert.github.io/ClearSky**](https://suirobert.github.io/ClearSky/)

Los pronósticos suelen fallar porque dependen de un solo modelo y nadie mide públicamente
cuánto aciertan. ClearSky combina 7 modelos meteorológicos internacionales, aprende cuál acierta más
en cada ciudad y ajusta sus porcentajes para que un 30% signifique de verdad 3 de cada 10.
Además mira lo que está pasando **ahora**: el satélite y los reportes de la gente corrigen las próximas horas.

## Qué ofrece

- **Tu barrio primero**: pronóstico de tu punto exacto con el nombre del barrio (Urdesa, Alborada, La Carolina…). Las ciudades están en una lista aparte.
- **Lo que está pasando ahora**: si el satélite o varias personas cerca reportan lluvia, la página lo dice ("Está lloviendo cerca de ti") y corrige las próximas 2-3 horas.
- **Próximas 24 horas** al estilo Dark Sky: barra de condiciones y tira de horas con ícono, probabilidad y temperatura.
- **7 días verificados** a cada plazo; los que todavía no superan al promedio aparecen como _orientativos_.
- **Tormentas eléctricas verificadas** con los truenos que registran los aeropuertos.
- **Mapa** con dos modos: _Pronóstico_ (probabilidad hora por hora) y _Lloviendo ahora_ (estimación satelital de NOAA).
- **Satélite** GOES-19 interactivo.
- **Reportar el clima**, **avisos en el teléfono**, **compartir** como imagen, **app instalable**, modo claro y oscuro.
- **Se actualiza sola** mientras la página está abierta, sin recargar.

## Ciudades

| Ciudad | Verificación | Zona |
|---|---|---|
| Guayaquil | SEGU – José Joaquín de Olmedo | Costa |
| Quito | SEQM – Mariscal Sucre (Tababela) | Sierra |
| Manta | SEMT – Eloy Alfaro | Costa |
| Salinas | SESA – General Ulpiano Páez | Costa |
| Esmeraldas | SETN – Carlos Concha Torres | Costa |
| Cuenca | SECU – Mariscal Lamar | Sierra |
| Machala | SETR – Santa Rosa | Costa |
| Portoviejo | sin aeropuerto: usa lo aprendido en la costa | Costa |
| Santo Domingo | SESD | Costa |
| Loja | SETM – Catamayo | Sierra |
| Ambato | SEAM – Chachoán | Sierra |
| Latacunga | SELT – Cotopaxi | Sierra |
| Coca | SECO | Oriente |
| Lago Agrio | SENL | Oriente |
| Macas | SEMC | Oriente |
| Galápagos | SEGS – Baltra | Galápagos |

Si un aeropuerto no tiene suficientes reportes (muchos cierran de noche o no publican), la ciudad usa
lo aprendido en la ciudad verificada más cercana de su misma zona. Loja se verifica en Catamayo, que es
más bajo y seco que la ciudad. Para agregar una ciudad: una línea en `CIUDADES` dentro de `clima_consenso.py`.

## Cómo funciona

- **Cada hora** GitHub Actions descarga 7 modelos (ECMWF, GFS, ICON, GEM, JMA, MeteoFrance, UKMO) desde Open-Meteo y la lluvia satelital más reciente de NOAA.
- **Consenso ponderado** según lo que acertó cada modelo en cada ciudad.
- **Verificación diaria** de lo pronosticado 1 a 7 días antes contra los aeropuertos (METAR), con un **examen sin trampa** sobre días no usados para aprender. Ventana deslizante de 45 días para adaptarse a la temporada.
- **Corrección inmediata** (en la página y en los avisos): si la estimación satelital marca 1 mm/h o más en la zona, o al menos 3 reportes de la última hora y el 60% dicen que llueve, las próximas horas suben a 85% / 60% / 40%. Si al menos 3 reportes dicen que no llueve (y el satélite no ve lluvia), la hora actual baja a 25% como máximo.
- **Pruebas antes de publicar**: datos completos, valores en rango, JSON válido y JavaScript sin errores. Si algo falla, se conserva la página anterior y el workflow falla (GitHub avisa por correo).

> Niebla, ráfagas, índice UV y la estimación satelital **todavía no se verifican**. La estimación satelital
> se guarda hora por hora en `datos/satelite/` para poder verificarla contra los aeropuertos más adelante.

## Datos guardados

| Archivo | Contenido |
|---|---|
| `datos/pesos.json` | Lo aprendido por ciudad |
| `datos/observaciones/AAAA-MM.json` | Lluvia, llovizna y tormenta observadas en los aeropuertos |
| `datos/satelite/AAAA-MM.json` | Lluvia satelital (mm/h) en cada ciudad, hora por hora |
| `datos/historial/AAAA-MM-DD.csv` | Lo que mostró la página |
| `datos/alertas.json` | Cambios bruscos con texto listo para publicar |
| `datos/reportes/AAAA-MM.json` y `datos/respaldos/` | Reportes de usuarios (zona ~5 km) y respaldo semanal |

## Operación

- **Ejecutar a mano**: `gh workflow run actualizar.yml -f verificar=true`.
- **Antes de editar**: `git pull`; si `git push` es rechazado, `git pull --rebase` y otra vez `git push`.
- La lectura del satélite necesita `netCDF4`; en GitHub Actions se instala sola la primera vez de cada ejecución.

## Servicio de reportes, avisos y vigilancia (Cloudflare)

Carpeta `feedback/`, configurada desde el panel de Cloudflare:

1. **D1** `clearsky` → Console: ejecutar `schema.sql` (si ya existía la v11, solo las líneas nuevas: `ALTER TABLE sub ADD COLUMN admin INTEGER NOT NULL DEFAULT 0;` y `CREATE TABLE IF NOT EXISTS estado (k TEXT PRIMARY KEY, v TEXT);`).
2. **Worker** `clearsky-feedback`: pegar `worker.js`, binding D1 `DB`, Cron Trigger `*/30 * * * *`.
3. **Secretos**: `SAL`, `TOKEN`, `VAPID_PUBLIC`, `VAPID_PRIVATE`, opcional `VAPID_SUB`; variable `ORIGEN` = `https://suirobert.github.io`.
4. **Vigilancia** (opcional): secreto `GH_TOKEN` con un token de GitHub de grano fino, solo para este repositorio, con permiso _Actions: Read and write_. Si la página lleva más de 3 h sin actualizarse, el Worker relanza el workflow.
5. **Avisos de mantenimiento**: abre `https://suirobert.github.io/ClearSky/#admin` en tu teléfono, toca 🔔 _Avisarme_ y escribe el `TOKEN` cuando lo pida.

**Privacidad**: reportes con hora, tipo y ubicación redondeada (~1 km en el servicio, ~5 km en el repositorio); la IP nunca se guarda.

## Límites conocidos

- La verificación usa un punto por ciudad (el aeropuerto).
- La lluvia satelital se estima desde el espacio (infrarrojo calibrado con microondas): detecta bien los aguaceros, pero puede fallar con lloviznas y lluvias débiles. No es un radar.
- **Para emergencias sigue siempre las alertas oficiales** de la Secretaría de Gestión de Riesgos y del INAMHI.

## Créditos y licencias

- Pronósticos: [Open-Meteo.com](https://open-meteo.com/), CC BY 4.0 (uso no comercial en el plan gratuito).
- Observaciones METAR: [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/).
- Lluvia satelital: NOAA Enterprise Rain Rate (Open Data Dissemination Program, AWS). Imagen GOES-19 GeoColor: CIRA/NOAA vía NASA GIBS.
- Mapa y barrios: [Leaflet](https://leafletjs.com/) y © [OpenStreetMap](https://www.openstreetmap.org/copyright).
- Íconos: [Meteocons](https://github.com/basmilius/meteocons) de Bas Milius, MIT.
- Servicio de reportes y avisos: Cloudflare Workers y D1.
