# ClearSky

**Pronóstico del tiempo para Ecuador que mide en público cuánto acierta.**

🌐 [**suirobert.github.io/ClearSky**](https://suirobert.github.io/ClearSky/)

Los pronósticos suelen fallar porque dependen de un solo modelo y nadie mide públicamente
cuánto aciertan. ClearSky combina 7 modelos meteorológicos internacionales, aprende cuál acierta más
en cada ciudad y ajusta sus porcentajes para que un 30% signifique de verdad 3 de cada 10.
Además mira lo que está pasando **ahora** (satélite y reportes de la gente) y avisa del **calor**.

## Qué ofrece

- **Tu barrio primero**: pronóstico de tu punto exacto con el nombre del barrio (Urdesa, Alborada, La Carolina…). Las ciudades están en una lista aparte.
- **Lluvia, llovizna y tormentas eléctricas** con porcentajes verificados.
- **Calor**: sensación térmica (índice de calor), aviso desde 32°, consejos según el nivel y **onda de calor** cuando se esperan 3 días o más con máximas por encima de lo normal.
- **Humedad, viento, ráfagas e índice UV** (con la hora de mayor riesgo).
- **Lo que está pasando ahora**: si el satélite o varias personas cerca reportan lluvia, la página lo dice y corrige las próximas horas.
- **Próximas 24 horas** al estilo Dark Sky: tira de horas con ícono, probabilidad y una línea de temperatura. Toca una hora para ver su detalle.
- **7 días**, gráfica hora por hora (lluvia, cantidad, temperatura y sensación), **mapa** de lluvia (pronóstico y _lloviendo ahora_) y **satélite** GOES-19.
- **Reportar el clima**, **avisos en el teléfono**, **compartir** como imagen, modo claro y oscuro.
- **Instalar como app**: botón _📲 Instalar app_ (en Android instala directo; en iPhone muestra los 3 pasos de Safari).
- **Cómo funciona**: explicación sencilla dentro de la página.

## Ciudades

Guayaquil, Quito, Manta, Salinas, Esmeraldas, Cuenca, Machala, Portoviejo, Santo Domingo, Loja, Ambato,
Latacunga, Coca, Lago Agrio, Macas y Galápagos. Las que no tienen un aeropuerto con datos suficientes usan
lo aprendido en la ciudad verificada más cercana de su zona. Para agregar una: una línea en `CIUDADES`.

## Cómo funciona

1. **Datos**: cada hora, 7 modelos (ECMWF, GFS, ICON, GEM, JMA, MeteoFrance, UKMO) desde Open-Meteo, la lluvia satelital de NOAA (GOES-19) y los reportes de la gente.
2. **Consenso ponderado** según lo que acertó cada modelo en cada ciudad.
3. **Verificación diaria** de lo pronosticado 1 a 7 días antes contra los aeropuertos (METAR): lluvia, llovizna y tormentas.
4. **Examen sin trampa**: aprende con los días anteriores y se examina con los últimos 10; cada ajuste solo se acepta si acierta más.
5. **Ventana de 45 días** que se desliza: se adapta a la temporada. Lo aprendido queda en `datos/pesos.json`.
6. **Corrección inmediata** con satélite y reportes para las próximas 2-3 horas.
7. **Calor**: índice de calor de la NOAA (temperatura + humedad). Niveles: 27° precaución · 32° calor intenso · 41° peligroso · 54° extremo. Onda de calor: 3 días o más con máxima ≥ 2° sobre el promedio de los últimos 30 días.
8. **Pruebas antes de publicar**: si algo falla, se conserva la página anterior.

> Niebla, viento, humedad, UV, sensación térmica y la estimación satelital vienen de los modelos o del satélite y **todavía no se verifican**.

## Datos guardados

| Archivo | Contenido |
|---|---|
| `datos/pesos.json` | Lo aprendido por ciudad |
| `datos/observaciones/AAAA-MM.json` | Lluvia, llovizna y tormenta observadas en los aeropuertos |
| `datos/satelite/AAAA-MM.json` | Lluvia satelital (mm/h) en cada ciudad, hora por hora |
| `datos/historial/AAAA-MM-DD.csv` | Lo que mostró la página |
| `datos/alertas.json` | Cambios bruscos con texto listo para publicar |
| `datos/reportes/` y `datos/respaldos/` | Reportes de usuarios y respaldo semanal |

## Operación

- **Ejecutar a mano**: `gh workflow run actualizar.yml -f verificar=true`.
- **Antes de editar**: `git pull`; si `git push` es rechazado, `git pull --rebase` y otra vez `git push`.

## Servicio de reportes, avisos y vigilancia (Cloudflare)

Carpeta `feedback/` (Worker + D1, configurado desde el panel de Cloudflare). Secretos: `SAL`, `TOKEN`,
`VAPID_PUBLIC`, `VAPID_PRIVATE`, opcional `VAPID_SUB` y `GH_TOKEN`; variable `ORIGEN`. Cron `*/30 * * * *`.

**Privacidad**: reportes con hora, tipo y ubicación redondeada; la IP nunca se guarda.

## Límites conocidos

- La verificación usa un punto por ciudad (el aeropuerto).
- La lluvia satelital puede fallar con lloviznas y lluvias débiles. No es un radar.
- **Para emergencias sigue siempre las alertas oficiales** de la Secretaría de Gestión de Riesgos y del INAMHI.

## Créditos y licencias

- Pronósticos: [Open-Meteo.com](https://open-meteo.com/), CC BY 4.0 (uso no comercial en el plan gratuito).
- Observaciones METAR: [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/).
- Lluvia satelital: NOAA Enterprise Rain Rate. Imagen GOES-19 GeoColor: CIRA/NOAA vía NASA GIBS.
- Índice de calor: fórmula de la NOAA / National Weather Service.
- Mapa y barrios: [Leaflet](https://leafletjs.com/) y © [OpenStreetMap](https://www.openstreetmap.org/copyright).
- Íconos: [Meteocons](https://github.com/basmilius/meteocons) de Bas Milius, MIT.
