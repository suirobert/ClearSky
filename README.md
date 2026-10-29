# ClearSky

**Pronóstico del tiempo para Ecuador que mide en público cuánto acierta.**

🌐 [**suirobert.github.io/ClearSky**](https://suirobert.github.io/ClearSky/)

En Ecuador es común decir "si la app dice que va a llover, no llueve". ClearSky parte de esa desconfianza:
combina 7 modelos meteorológicos, aprende cuál acierta más en cada ciudad, ajusta sus porcentajes para que un 30%
signifique de verdad 3 de cada 10, **y publica cada día lo que dijo contra lo que pasó, incluidos los fallos**.

> **Licencia: código disponible, no "open source".** El código se puede leer, estudiar, copiar y modificar para
> **uso no comercial** ([PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)).
> **No se puede usar para ganar dinero** (venderlo, ofrecerlo como servicio de pago, publicarlo con anuncios, etc.)
> sin un permiso aparte. Más abajo, en [Licencia](#licencia).

## Qué ofrece

- **Tu barrio primero**: pronóstico de tu punto exacto con el nombre del barrio. Las ciudades van en una lista aparte.
- **Mis lugares**: guarda hasta 5 sitios (casa, trabajo…) y míralos con un toque. Se guardan solo en tu dispositivo.
- **Lluvia, llovizna, tormentas eléctricas, calor** (sensación térmica, onda de calor), viento, humedad e índice UV. Las etiquetas de la cabecera tienen color propio para distinguirlas.
- **"Cómo nos fue esta semana"**: un punto por día (verde acertamos, rojo falló) y, al abrirla, el detalle día por día.
- **Lo que está pasando ahora**: lluvia observada por satélite, rayos de los últimos 20 minutos y reportes de la gente.
- **Lluvia en camino**: "llegaría en ~40 min, viene del oeste". **Solo se presenta como verificada si de verdad acierta** (ver abajo).
- **Aviso oficial del INAMHI** cuando hay un boletín vigente que afecta tu región (o el lugar que estás viendo).
- **Hora por hora** con selector de **24 h · 3 días · 7 días** y de variable (lluvia, temperatura, viento, UV).
- **Mapa** que **se reproduce solo**: _Pronóstico_ (próximas horas) y _Lloviendo ahora_ (la última hora, en bucle). Se pausa al tocarlo y respeta "reducir movimiento".
- **Estado de los datos**: de dónde viene cada dato y qué tan fresco está (pronóstico, satélite, rayos, boletín, verificación).
- **Reportar el clima** (soleado, despejado, nublado, llovizna, lluvia, aguacero, tormenta) y **¿Llovió donde estás?** al abrir un aviso de lluvia.
- **Avisos al teléfono a tu medida**: eliges entre lluvia, tormentas eléctricas y avisos oficiales.
- Aviso de **fuera de Ecuador** (por ejemplo, VPN): sin datos de Ecuador y con el pronóstico marcado como _no verificado_.
- Compartir, modo claro/oscuro, instalable como app, se actualiza sola.

## Cómo funciona (y cómo sabemos que acierta)

1. **Datos** cada hora: 7 modelos (ECMWF, GFS, ICON, GEM, JMA, MeteoFrance, UKMO) desde Open-Meteo.
2. **Consenso ponderado** según lo que acertó cada modelo en cada ciudad.
3. **Verificación diaria** de lo pronosticado a 1-7 días contra los aeropuertos (METAR), con **examen sin trampa**: se aprende con los días anteriores y se examina con los últimos 10, que no se usaron para aprender. Solo se acepta un ajuste si acierta más. Ventana deslizante de 45 días.
4. **Temperatura y humedad** también se comparan cada hora con el aeropuerto y se corrigen si el examen lo aprueba.
5. **Rayos (GOES-19 GLM)**: cada 10 minutos se cuentan las descargas a menos de 25 km de cada ciudad. Con 16 días acumulados, las **tormentas se verifican con rayos**, y esa calibración se usa **solo si en el examen supera a decir siempre el promedio**.
6. **Ciudades sin aeropuerto utilizable** (Machala, Santo Domingo, Loja, Ambato, Portoviejo): se verifican con la **lluvia que estima el satélite** con 21 días acumulados, **pero solo si antes el satélite demostró coincidir con los aeropuertos** (HSS ≥ 0,25 y detecta al menos 35% de sus lluvias). La página las marca como verificación _aproximada_.
7. **Lluvia en camino**: con tres imágenes del satélite (ahora, hace 30 y hace 60 min) se mide hacia dónde se mueve la lluvia por cuadros de 2°. Un cuadro solo se publica si con el movimiento de hace 30 min se adivinó la imagen de ahora mejor que suponiendo que la lluvia no se mueve. `ahora.json` guarda en `mov_diag` por qué se aceptó o descartó cada cuadro.
8. **Verificación de la lluvia en camino**: cada predicción de "llegaría en X min" se guarda y 40 minutos después se compara con la lluvia que de verdad apareció en esa ciudad (acierto si hubo lluvia de 1 mm/h o más dentro de ±20 minutos de la hora prevista). La función tiene tres estados:
   - **En pruebas** (menos de 30 casos comprobados): se muestra con esa etiqueta, no cambia el titular y **no envía avisos al teléfono**.
   - **Verificada** (30 casos o más y al menos 50% de aciertos): se muestra como "En camino", con cuántas veces acertó, y puede avisar.
   - **Oculta** (30 casos o más y menos de 50%): no se muestra.

   La página muestra también el azar de referencia (cuántas veces empieza a llover en una ventana igual sin avisar nada) y qué parte de las lluvias que llegaron se anticipó. Cada predicción comprobada se archiva para siempre en `datos/nowcast/`.
9. **Tablero de aciertos**: "dijimos lluvia" = máximo del día de 40% o más; "dijimos que no llovería" = menos de 20%; entre medio, dudoso.
10. **Pruebas antes de publicar**: si algo falla, se conserva la página anterior.

> Lo que todavía **no** se verifica: niebla, viento, índice UV y humedad en ciudades sin aeropuerto.
> La lluvia en camino se comprueba contra **la lluvia que estima el propio satélite**, no contra un pluviómetro, y la página lo dice.

## Archivos de datos

| Archivo | Contenido |
|---|---|
| `datos/pesos.json` | Lo aprendido por ciudad (lluvia, tormentas con aeropuerto y con rayos, temperatura, plazos, franjas) |
| `datos/observaciones/AAAA-MM.json` | Lluvia, llovizna y tormenta observadas en los aeropuertos |
| `datos/observado/AAAA-MM.json` | Observaciones propias por hora y ciudad: `[lluvia máx. mm/h del satélite, muestras, rayos a <25 km, franjas de 10 min]` |
| `datos/nowcast/AAAA-MM.json` | Cada predicción de "llegaría en X min" ya comprobada: `[ciudad, hora, minutos, mm/h, 1 = llovió / 0 = no]` |
| `datos/historial/AAAA-MM-DD.csv` | Lo que mostró la página (base del tablero de aciertos) |
| `datos/alertas.json` | Cambios bruscos con texto listo para publicar |
| `datos/reportes/` y `datos/respaldos/` | Reportes de usuarios y respaldo semanal |

## Los dos workflows

- **`actualizar.yml`** (cada hora): pronóstico, verificación, página. La verificación completa tarda unos 13-15 minutos. Guarda lo observado en `datos/observado/` y las predicciones comprobadas en `datos/nowcast/`.
- **`ahora.yml`** (cada 10 minutos, `ahora.py`): publica en la rama `ahora` los archivos `ahora.json` (lluvia, imágenes de la última hora, movimiento, rayos, boletín del INAMHI y estado de la verificación; lo lee la página), `hist.json` (72 h de lluvia y rayos por ciudad) y `nowcast.json` (predicciones pendientes y comprobadas). **El servicio de Cloudflare lo lanza con un cron `*/10`**, porque el cron propio de GitHub no es puntual. Si falla, la página sigue funcionando con el pronóstico.
- Ambos usan `ubuntu-24.04` fijo, `actions/checkout@v5` y `actions/setup-python@v6`.

## Servicio de reportes y avisos (Cloudflare)

Carpeta `feedback/`: Worker + D1, configurados desde el panel de Cloudflare.

- **Base de datos**: `schema.sql`. Si la tabla `sub` ya existía, ejecuta una sola vez `ALTER TABLE sub ADD COLUMN pref INTEGER NOT NULL DEFAULT 7;`
- **Secretos**: `SAL`, `TOKEN`, `VAPID_PUBLIC`, `VAPID_PRIVATE`, `GH_TOKEN` (token de GitHub de grano fino, solo este repositorio, permiso _Actions: Read and write_) y opcional `VAPID_SUB`; variable `ORIGEN`.
- **Crons**: `*/30 * * * *` (avisos y vigilancia) y `*/10 * * * *` (lanza `ahora.yml`).
- **Comprobar**: en D1, `SELECT * FROM estado WHERE k = 'dispatch-ahora';` debe decir `204 ok` con una hora reciente.
- **Preferencias** (`pref`): 1 = lluvia (incluye "llegaría en X min", solo cuando está verificada), 2 = tormentas eléctricas, 4 = avisos oficiales.
- Límites: tormenta, máximo uno cada 2 h; lluvia, uno cada 6 h; oficial, una vez por boletín.
- Los avisos de lluvia abren la página con la pregunta **¿Llovió donde estás?**, y la respuesta se guarda como reporte.
- Los **avisos siguen tu ubicación actual**: los lugares guardados sirven para ver su pronóstico, no para recibir avisos de cada uno.

**Privacidad**: la IP nunca se guarda. Los reportes guardan hora, tipo y ubicación redondeada. Los lugares guardados solo viven en tu navegador.

## Licencia

ClearSky se publica bajo la licencia **PolyForm Noncommercial 1.0.0** (archivo `LICENSE`, aviso de derechos en `NOTICE`).

- ✅ **Permitido**: leer el código, estudiarlo, usarlo para uso personal, educativo o de investigación, modificarlo y compartirlo **sin fines comerciales** (incluye a organizaciones sin fines de lucro, instituciones educativas y de gobierno), siempre conservando `LICENSE` y `NOTICE`.
- ❌ **No permitido sin permiso aparte**: cualquier uso comercial. Por ejemplo, vender el código o una app basada en él, ofrecerlo como servicio de pago, o publicarlo con anuncios o suscripciones.
- 📩 **Uso comercial**: abre un _issue_ en este repositorio para pedir un permiso.

Es una licencia de **código disponible**, no de _código abierto_ en el sentido de la Open Source Initiative, porque limita el uso comercial.
Se dice así a propósito: presentarla como "open source" sería engañoso.

Notas:
- La licencia protege el **código y los textos**. Las ideas generales (combinar varios modelos, verificarlos contra observaciones) no se pueden proteger y cualquiera puede construir algo parecido desde cero.
- Los datos que genera ClearSky (`datos/`) se publican bajo los mismos términos.
- Los datos de terceros conservan sus propias licencias (ver Créditos). Open-Meteo, en su plan gratuito, tampoco permite uso comercial.
- Este texto es informativo y **no es asesoría legal**.

Para instalar o reinstalar la licencia: `python instalar_licencia.py` (descarga el texto oficial y lo comprueba; no se escribe a mano porque debe copiarse idéntico).

## Límites conocidos

Ninguno está "resuelto": son límites de lo que se puede medir. Esto es lo que se hace para que no engañen:

- **Un punto por ciudad.** La verificación compara con un solo punto (el aeropuerto) y en una ciudad grande puede llover en un barrio y no en otro. Las ciudades sin aeropuerto se verifican con el satélite, de forma _aproximada_, y los reportes de la gente permiten comparar por barrio, pero todavía no corrigen los porcentajes.
- **La lluvia satelital es una estimación.** Detecta bien los aguaceros y puede fallar con lloviznas. No es un radar. Antes de usarla para verificar se mide cuánto coincide con los aeropuertos, y solo se usa si coincide lo suficiente.
- **"Lluvia en camino" extiende en línea recta** el movimiento observado: sirve para la próxima hora y no anticipa lluvias que nacen o se disipan. Se verifica contra la propia estimación del satélite y tarda semanas en reunir casos, porque solo se predice donde el movimiento superó la validación.
- **Los rayos son detecciones satelitales** y pueden tardar unos minutos. Los datos con más de 15-25 minutos no se muestran como "ahora".
- **Para emergencias sigue siempre las alertas oficiales** de la Secretaría de Gestión de Riesgos y del INAMHI (911).

## Créditos

- Pronósticos: [Open-Meteo.com](https://open-meteo.com/), CC BY 4.0 (uso no comercial en el plan gratuito).
- Observaciones METAR: [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/).
- Lluvia satelital: NOAA Enterprise Rain Rate. Rayos: GOES-19 GLM (NOAA). Imagen GOES-19 GeoColor: CIRA/NOAA vía NASA GIBS.
- Boletines: [INAMHI](https://servicios.inamhi.gob.ec/alertas/).
- Índice de calor: fórmula de la NOAA / National Weather Service.
- Mapa y barrios: [Leaflet](https://leafletjs.com/) y © [OpenStreetMap](https://www.openstreetmap.org/copyright).
- Íconos: [Meteocons](https://github.com/basmilius/meteocons) de Bas Milius, MIT.
- Licencia: [PolyForm Project](https://polyformproject.org/licenses/noncommercial/1.0.0).
