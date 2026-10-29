# ClearSky

**Pronóstico del tiempo para Ecuador que mide en público cuánto acierta.**

🌐 [**suirobert.github.io/ClearSky**](https://suirobert.github.io/ClearSky/)

Los pronósticos suelen fallar porque dependen de un solo modelo y nadie mide públicamente
cuánto aciertan. ClearSky combina 7 modelos meteorológicos internacionales, aprende cuál acierta más
en cada ciudad y ajusta sus porcentajes para que un 30% signifique de verdad 3 de cada 10.
Lo verifica todos los días, para la lluvia y para las tormentas eléctricas.

## Qué ofrece

- **Tu ubicación primero**: al abrir la página se calcula el pronóstico de tu punto exacto (con el nombre de tu barrio o ciudad), ajustado con lo aprendido en la ciudad verificada más parecida (costa o sierra). Las ciudades están en una lista aparte, en el botón de arriba.
- **Pronóstico por hora para 7 días**: lluvia, llovizna, tormenta eléctrica, cantidad esperada, temperatura, niebla, ráfagas e índice UV. Los días 1 a 3 están verificados; del 4 al 7 se marcan como _orientativos_.
- **Tormentas eléctricas verificadas**: se comparan cada día con los truenos que registran los aeropuertos y se calibran igual que la lluvia.
- **Ícono del cielo realista**: se decide por cuánto sol llega de verdad; las nubes altas y finas ya no muestran "nublado" cuando hace sol.
- **Mapa de lluvia**: probabilidad calibrada sobre Ecuador, hora por hora (3 días), con botón de reproducir. Es un pronóstico, no un radar.
- **Satélite**: imagen GOES-19 de NOAA para ver dónde se están formando las nubes.
- **¿Cómo está el clima donde estás?**: cualquiera puede reportar soleado, nublado, llovizna, lluvia, aguacero o tormenta. Se ve lo que reportan cerca en la última hora, y cada día se compara con lo que publicó la página.
- **Hecho para el celular**: gráfica y barra de horas que se arrastran con el dedo, días desplegables, lista de ciudades que se cierra deslizando hacia abajo, botones grandes.
- **Instalable como app** (sin tiendas): en Android, _Instalar ClearSky_ al pie de la página o en el menú del navegador; en iPhone, _Compartir → Agregar a inicio_. Funciona sin conexión con el último pronóstico.
- **Modo claro y oscuro**: sigue al dispositivo; el botón ◐ / ☀️ / 🌙 permite elegirlo a mano.
- **Transparente**: la sección _¿Qué tan confiable es este pronóstico?_ muestra cuánto ha acertado, el examen diario, los resultados reales de lo publicado y la comparación con los reportes de la gente.

## Ciudades

| Ciudad | Aeropuerto de verificación | Zona |
|---|---|---|
| Guayaquil | SEGU – José Joaquín de Olmedo | Costa |
| Quito | SEQM – Mariscal Sucre (Tababela) | Sierra |
| Manta | SEMT – Eloy Alfaro | Costa |
| Salinas | SESA – General Ulpiano Páez | Costa |
| Esmeraldas | SETN – Carlos Concha Torres | Costa |
| Cuenca | SECU – Mariscal Lamar | Sierra |

Si un aeropuerto no tiene suficientes reportes, la ciudad usa lo aprendido en la ciudad verificada
más cercana de su misma zona y la página lo indica. Para agregar una ciudad: añade una línea en
`CIUDADES` dentro de `clima_consenso.py`.

## Cómo funciona

- **Cada hora** GitHub Actions descarga 7 modelos (ECMWF, GFS, ICON, GEM, JMA, MeteoFrance, UKMO) desde Open-Meteo.
- **Consenso ponderado**: cada modelo vota con un peso según lo que ha acertado en esa ciudad. La cantidad de lluvia usa la mediana ponderada, así un modelo exagerado no arrastra el resultado. Algunos modelos solo llegan a 4-7 días, por eso los días lejanos usan menos modelos.
- **Verificación diaria**: compara lo que cada modelo pronosticó 24 horas antes con lo que reportaron los aeropuertos (METAR) en los últimos 45 días, con ±1 hora de tolerancia. Lluvia con `RA`/`DZ`, tormentas con `TS` (incluye `VCTS`).
- **Examen sin trampa**: aprende con los días anteriores y se examina con los últimos 10 días, que no usó para aprender. Prueba varias calibraciones (completa, reciente, mezcla, sin ajuste) y se queda con la que mejor acierta.
- **Tormentas**: si en una ciudad hubo menos de 4 horas con tormenta en el periodo, no se calibra y la página lo dice.
- **Cielo**: compara la radiación solar pronosticada con la que llegaría con cielo despejado. De noche usa solo las nubes bajas y medias.
- **Salvaguardas**: si faltan datos o hay menos de 3 modelos, no cambia nada y lo registra en `datos/errores.log`. Si falla el mapa, los íconos, las tormentas o los reportes, la página se publica igual sin esa parte.

### Cómo aprende

Cada día recalcula los pesos y la calibración con los **últimos 45 días**, una ventana que se
desliza: así se adapta a la temporada (invierno/verano) en lugar de arrastrar meses que ya no se
parecen. Las observaciones y lo publicado se guardan para siempre en `datos/`, de modo que el
historial crece y sirve para futuras mejoras (por ejemplo, comparar con la misma época del año
anterior). Todo el aprendizaje vive en el repositorio: no hay servidor propio ni base de datos que mantener.

> Niebla, ráfagas, índice UV y los días 4 a 7 **todavía no se verifican**; la página lo indica.

## Datos guardados

| Archivo | Contenido |
|---|---|
| `datos/pesos.json` | Lo aprendido: pesos de cada modelo, umbral, calibración de lluvia y de tormentas, y resultados por ciudad |
| `datos/observaciones/AAAA-MM.json` | Lluvia, llovizna y tormenta observadas por hora en los aeropuertos |
| `datos/historial/AAAA-MM-DD.csv` | Lo que mostró la página, para medir aciertos reales |
| `datos/reportes/AAAA-MM.json` | Reportes de usuarios: hora, tipo y zona redondeada a ~5 km |
| `datos/iconos/*.svg` | Íconos Meteocons descargados una sola vez |
| `datos/errores.log` | Avisos y errores. Solo existe si hubo algún aviso |

## Operación

- **Ejecutar a mano**: _Actions → Actualizar pronóstico → Run workflow_ o `gh workflow run actualizar.yml -f verificar=true`.
- **Si una ejecución falla**, GitHub envía un correo al dueño del repositorio.
- **Antes de editar en tu PC**, ejecuta siempre `git pull`: el bot sube datos cada hora. Si `git push` es rechazado, usa `git pull --rebase` y vuelve a hacer `git push`.
- **Probar en un PC**: `python clima_consenso.py` crea `dashboard.html`. No subas los cambios de `datos/` que genere esa prueba.

## Reportes de usuarios

Funcionan con un servicio gratuito en Cloudflare (carpeta `feedback/`). Mientras no se configure,
la sección no aparece.

```powershell
winget install --id OpenJS.NodeJS.LTS -e        # una sola vez; reabre PowerShell
cd feedback
npx wrangler login
npx wrangler d1 create clearsky                 # copia el database_id en wrangler.toml
npx wrangler d1 execute clearsky --remote --file schema.sql
npx wrangler secret put SAL                     # frase secreta larga cualquiera
$tok = -join ((48..57) + (97..122) | Get-Random -Count 36 | ForEach-Object { [char]$_ })
$tok | npx wrangler secret put TOKEN
npx wrangler deploy                             # muestra la dirección del servicio
cd ..
gh secret set FEEDBACK_TOKEN --body $tok
gh variable set FEEDBACK_URL --body "https://clearsky-feedback.TU-SUBDOMINIO.workers.dev"
```

El workflow necesita `CC_FEEDBACK_URL: ${{ vars.FEEDBACK_URL }}` y `CC_FEEDBACK_TOKEN: ${{ secrets.FEEDBACK_TOKEN }}` en su bloque `env`.

**Privacidad**: el servicio guarda la hora, el tipo de reporte y la ubicación redondeada a ~1 km.
La IP nunca se guarda; un código irreversible para evitar reportes repetidos se borra a las 48 horas.
En este repositorio público la ubicación se guarda aún más redondeada (~5 km). Límite: un reporte por
persona y zona cada 30 minutos. La ubicación para el pronóstico se usa solo en tu navegador.

**Uso**: por ahora los reportes sirven para **comprobar** el pronóstico en los barrios (aparece en
_¿Qué tan confiable es este pronóstico?_ cuando una ciudad junta 30 o más). Todavía no cambian los
porcentajes: para eso hacen falta muchos más y protección contra reportes falsos.

## Límites conocidos

- La verificación usa un punto por ciudad (el aeropuerto). En ciudades grandes puede llover en un barrio y no en otro.
- Quito se verifica en Tababela, que es más seco que el centro de la ciudad.
- El METAR es un reporte por hora: un aguacero corto entre dos reportes puede no quedar registrado.
- Las tormentas son poco frecuentes en algunas ciudades y temporadas; ahí la calibración tarda más.
- El mapa es un pronóstico de modelos, no un radar: Ecuador casi no tiene cobertura de radar meteorológico.
- **Para emergencias sigue siempre las alertas oficiales** de la Secretaría de Gestión de Riesgos y del INAMHI.

## Créditos y licencias

- Pronósticos: [Open-Meteo.com](https://open-meteo.com/), licencia CC BY 4.0. El plan gratuito es solo para uso no comercial.
- Observaciones METAR: [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/), Iowa State University.
- Satélite: GOES-19 GeoColor, CIRA/NOAA.
- Mapa: [Leaflet](https://leafletjs.com/) con teselas de © [OpenStreetMap](https://www.openstreetmap.org/copyright).
- Íconos animados: [Meteocons](https://github.com/basmilius/meteocons) de Bas Milius, licencia MIT.
- Nombre del lugar: BigDataCloud (geocodificación inversa gratuita del lado del cliente).
- Servicio de reportes: Cloudflare Workers y D1 (plan gratuito).
