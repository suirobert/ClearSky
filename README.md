# ClearSky

**Pronóstico del tiempo para Ecuador que mide en público cuánto acierta.**

🌐 [**suirobert.github.io/ClearSky**](https://suirobert.github.io/ClearSky/)

Los pronósticos suelen fallar porque dependen de un solo modelo y nadie mide públicamente
cuánto aciertan. ClearSky combina 7 modelos meteorológicos internacionales, aprende cuál acierta más
en cada ciudad y ajusta sus porcentajes para que un 30% signifique de verdad 3 de cada 10.
Lo verifica todos los días: lluvia, tormentas eléctricas y pronósticos de hasta 7 días.

## Qué ofrece

- **Tu barrio primero**: al abrir la página se calcula el pronóstico de tu punto exacto y se muestra el nombre del barrio o ciudadela (Urdesa, Alborada, La Carolina…) gracias a OpenStreetMap. Las ciudades están en una lista aparte.
- **Diseño compacto** al estilo Dark Sky: lo esencial arriba, barra de 24 horas y 7 días. En la PC se reparte en dos columnas (pronóstico a la izquierda; gráfica, mapa y satélite a la derecha).
- **7 días verificados**: cada plazo (1 a 7 días de anticipación) se mide por separado. Los días que todavía no superan al promedio aparecen como _orientativos_.
- **Porcentajes por franja horaria**: madrugada, mañana, tarde y noche se calibran por separado cuando eso acierta más.
- **Tormentas eléctricas verificadas** con los truenos que registran los aeropuertos.
- **Alertas de cambio brusco**: si la lluvia de las próximas horas sube de golpe, la página lo avisa y queda un texto listo para publicar en redes (`datos/alertas.json`).
- **Avisos en el teléfono**: con el botón 🔔 _Avisarme_, ClearSky te manda una notificación cuando es probable que llueva donde estás en las próximas 2 horas (máximo una cada 6 horas). En iPhone funciona con la app instalada en la pantalla de inicio.
- **Mapa de lluvia** continuo y suave sobre Ecuador, hora por hora; al tocar un punto muestra su porcentaje. Es un pronóstico, no un radar.
- **Satélite** GOES-19 de NOAA.
- **Reportar el clima** (soleado, nublado, llovizna, lluvia, aguacero, tormenta) desde un botón; se ve lo que reporta la gente cerca.
- **Compartir**: genera una imagen del pronóstico para WhatsApp o redes.
- **App instalable**, modo claro y oscuro, uso sin conexión, apertura inmediata con el último pronóstico guardado y compatible con lectores de pantalla y teclado.

## Ciudades

| Ciudad | Aeropuerto de verificación | Zona |
|---|---|---|
| Guayaquil | SEGU – José Joaquín de Olmedo | Costa |
| Quito | SEQM – Mariscal Sucre (Tababela) | Sierra |
| Manta | SEMT – Eloy Alfaro | Costa |
| Salinas | SESA – General Ulpiano Páez | Costa |
| Esmeraldas | SETN – Carlos Concha Torres | Costa |
| Cuenca | SECU – Mariscal Lamar | Sierra |

## Cómo funciona

- **Cada hora** GitHub Actions descarga 7 modelos (ECMWF, GFS, ICON, GEM, JMA, MeteoFrance, UKMO) desde Open-Meteo.
- **Consenso ponderado**: cada modelo vota con un peso según lo que ha acertado en esa ciudad.
- **Verificación diaria**: compara lo que cada modelo pronosticó 1 a 7 días antes con lo que reportaron los aeropuertos (METAR) en los últimos 45 días, con ±1 hora de tolerancia.
- **Examen sin trampa**: aprende con los días anteriores y se examina con los últimos 10 días, que no usó para aprender. Cada ajuste (pesos, calibración, franjas horarias, plazos) solo se acepta si acierta más en ese examen.
- **Ventana deslizante**: siempre usa los últimos 45 días, así se adapta a la temporada. Las observaciones se guardan para siempre en `datos/`.

> Niebla, ráfagas e índice UV vienen directamente de los modelos y **todavía no se verifican**.

## Datos guardados

| Archivo | Contenido |
|---|---|
| `datos/pesos.json` | Lo aprendido: pesos, umbral, calibraciones (general, por franja, por plazo, tormentas) y resultados |
| `datos/observaciones/AAAA-MM.json` | Lluvia, llovizna y tormenta observadas por hora |
| `datos/historial/AAAA-MM-DD.csv` | Lo que mostró la página, para medir aciertos reales y detectar cambios bruscos |
| `datos/alertas.json` | Alertas de cambio brusco con texto listo para publicar |
| `datos/reportes/AAAA-MM.json` | Reportes de usuarios (zona redondeada a ~5 km) |
| `datos/respaldos/reportes-AAAA-Sxx.json.gz` | Respaldo semanal completo de los reportes (últimas 8 semanas) |
| `datos/iconos/*.svg` | Íconos Meteocons |

Junto a la página publicada se generan `push.json` (configuración para los avisos), `manifest.webmanifest`, `sw.js` e íconos.

## Operación

- **Ejecutar a mano**: `gh workflow run actualizar.yml -f verificar=true`.
- **Antes de editar en tu PC**: `git pull`. Si `git push` es rechazado: `git pull --rebase` y otra vez `git push`.
- **Probar en un PC**: `python clima_consenso.py` crea `dashboard.html`. No subas los cambios de `datos/` de esa prueba.

## Servicio de reportes y avisos (Cloudflare)

Carpeta `feedback/`. Se configura desde el panel de Cloudflare, sin instalar nada:

1. **Base de datos** (D1 → `clearsky` → Console): ejecutar `schema.sql`.
2. **Worker** `clearsky-feedback`: pegar `worker.js` y _Deploy_. Binding D1 con nombre `DB`.
3. **Variables**: `SAL` y `TOKEN` (secretos), `ORIGEN` = `https://suirobert.github.io`.
4. **Avisos**: generar las claves una sola vez con
   `Invoke-RestMethod https://clearsky-feedback.<tu-subdominio>.workers.dev/vapid-nuevo -Headers @{Authorization="Bearer <TOKEN>"}`
   y guardar `VAPID_PUBLIC`, `VAPID_PRIVATE` (el JSON completo) y opcionalmente `VAPID_SUB` (`mailto:tu-correo`) como secretos.
5. **Tarea programada** (Settings → Triggers → Cron): `*/30 * * * *`.
6. **GitHub**: secreto `FEEDBACK_TOKEN` y variable `FEEDBACK_URL`.

Límites del plan gratuito: cada revisión atiende hasta 15 zonas y 25 avisos.

**Privacidad**: los reportes guardan hora, tipo y ubicación redondeada a ~1 km; la IP nunca se guarda.
Para los avisos se guarda la suscripción del navegador y la zona redondeada a ~1 km; se borran al desactivarlos.
El nombre del barrio se consulta a OpenStreetMap con coordenadas redondeadas a ~100 m y se guarda solo en tu navegador.

## Límites conocidos

- La verificación usa un punto por ciudad (el aeropuerto). En ciudades grandes puede llover en un barrio y no en otro.
- Quito se verifica en Tababela, que es más seco que el centro de la ciudad.
- El mapa es un pronóstico de modelos, no un radar: Ecuador casi no tiene cobertura de radar meteorológico.
- **Para emergencias sigue siempre las alertas oficiales** de la Secretaría de Gestión de Riesgos y del INAMHI.

## Créditos y licencias

- Pronósticos: [Open-Meteo.com](https://open-meteo.com/), CC BY 4.0 (plan gratuito solo para uso no comercial).
- Observaciones METAR: [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/).
- Satélite: GOES-19 GeoColor, CIRA/NOAA.
- Mapa y barrios: [Leaflet](https://leafletjs.com/) y © [OpenStreetMap](https://www.openstreetmap.org/copyright) (Nominatim).
- Íconos: [Meteocons](https://github.com/basmilius/meteocons) de Bas Milius, MIT.
- Servicio de reportes y avisos: Cloudflare Workers y D1 (plan gratuito).
