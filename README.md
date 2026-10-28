# ClearSky

**Pronóstico de lluvia para Ecuador que se verifica todos los días con lo que realmente llovió.**

🌐 **[suirobert.github.io/ClearSky](https://suirobert.github.io/ClearSky/)**

Los pronósticos de lluvia suelen fallar porque dependen de un solo modelo y nadie mide públicamente
cuánto aciertan. ClearSky combina 7 modelos meteorológicos internacionales, aprende cuál acierta más
en cada ciudad y ajusta sus porcentajes para que un 30% signifique de verdad 3 de cada 10.

## Qué ofrece

- **Pronóstico por hora para 3 días**: probabilidad de lluvia y de llovizna, cantidad esperada, temperatura y nubosidad.
- **Mi ubicación**: el navegador calcula el pronóstico de tu punto exacto con lo aprendido en la ciudad verificada más parecida (costa o sierra).
- **Interactivo**: gráfica con cursor (mouse o dedo), toca un día para ver su detalle, oculta o muestra cada serie.
- **Transparente**: la sección *¿Qué tan confiable es este pronóstico?* muestra cuánto ha acertado, el examen diario y los resultados reales de lo publicado.
- **Reportes de usuarios** *(opcional)*: botón *¿Está lloviendo donde estás?* para comprobar el pronóstico por barrios.
- **Funciona en celular y computadora**, sin instalar nada.

## Ciudades

| Ciudad | Aeropuerto de verificación | Zona |
|---|---|---|
| Guayaquil | SEGU – José Joaquín de Olmedo | Costa |
| Quito | SEQM – Mariscal Sucre (Tababela) | Sierra |
| Manta | SEMT – Eloy Alfaro | Costa |
| Salinas | SESA – General Ulpiano Páez | Costa |
| Esmeraldas | SETN – Carlos Concha Torres | Costa |
| Cuenca | SECU – Mariscal Lamar | Sierra |

Si un aeropuerto no tiene suficientes reportes (muchos cierran de noche), la ciudad usa lo aprendido
en la ciudad verificada más cercana de su misma zona y la página lo indica.
Para agregar una ciudad: añade una línea en `CIUDADES` dentro de `clima_consenso.py`.

## Cómo funciona

1. **Cada hora** GitHub Actions descarga 7 modelos (ECMWF, GFS, ICON, GEM, JMA, MeteoFrance, UKMO) desde Open-Meteo.
2. **Consenso ponderado**: cada modelo vota con un peso según lo que ha acertado en esa ciudad. La cantidad de lluvia usa la mediana ponderada, así un modelo exagerado no arrastra el resultado.
3. **Verificación diaria**: compara lo que cada modelo pronosticó con lo que reportaron los aeropuertos (METAR) en los últimos 45 días, con ±1 hora de tolerancia.
4. **Examen sin trampa**: aprende con los días anteriores y se examina con los últimos 10 días, que no usó para aprender. Prueba varias calibraciones (completa, reciente, mezcla, sin ajuste) y se queda con la que mejor acierta. Así se adapta a los cambios de temporada.
5. **Salvaguardas**: si faltan datos del aeropuerto o hay menos de 3 modelos disponibles, no cambia nada y lo registra en `datos/errores.log`.
6. **Seguimiento real**: compara lo que mostró la página con lo que pasó de verdad.

## Datos guardados

| Archivo | Contenido |
|---|---|
| `datos/pesos.json` | Pesos de cada modelo, umbral, calibración y resultados por ciudad |
| `datos/observaciones/AAAA-MM.json` | Lluvia observada por hora (memoria permanente) |
| `datos/historial/AAAA-MM-DD.csv` | Lo que mostró la página, para medir aciertos reales |
| `datos/errores.log` | Avisos y errores (últimas 500 líneas) |

## Operación

- **Ejecutar a mano**: *Actions → Actualizar pronóstico → Run workflow* o `gh workflow run actualizar.yml -f verificar=true`.
- **Si una ejecución falla**, GitHub envía un correo al dueño del repositorio.
- **Antes de editar en tu PC**, ejecuta siempre `git pull`: el bot sube datos cada hora.
- **Probar en un PC**: `python clima_consenso.py` crea `dashboard.html`. No subas los cambios de `datos/` que genere esa prueba.

## Reportes de usuarios (opcional)

El botón *¿Está lloviendo donde estás?* usa un servicio gratuito en Cloudflare (carpeta `feedback/`).
Mientras no se configure, el botón no aparece.

```powershell
winget install --id OpenJS.NodeJS.LTS -e        # una sola vez; reabre PowerShell
cd feedback
npx wrangler login
npx wrangler d1 create clearsky                 # copia el database_id en wrangler.toml
npx wrangler d1 execute clearsky --remote --file schema.sql
npx wrangler secret put SAL                     # escribe una frase secreta larga cualquiera
npx wrangler deploy                             # muestra la dirección del servicio
cd ..
gh variable set FEEDBACK_URL --body "https://clearsky-feedback.TU-SUBDOMINIO.workers.dev"
gh workflow run actualizar.yml
```

**Privacidad**: solo se guarda la hora, el tipo de reporte y la ubicación redondeada a ~1 km.
La IP nunca se guarda; un código irreversible para evitar reportes repetidos se borra a las 48 horas.
Límites: un reporte por persona y zona cada 30 minutos.

## Límites conocidos

- La verificación usa un punto por ciudad (el aeropuerto). En ciudades grandes puede llover en un barrio y no en otro.
- Quito se verifica en Tababela, que es más seco que el centro de la ciudad.
- El METAR es un reporte por hora: un aguacero corto entre dos reportes puede no quedar registrado.
- **Para emergencias sigue siempre las alertas oficiales** de la Secretaría de Gestión de Riesgos y del INAMHI.

## Créditos y licencias de datos

- Pronósticos: [Open-Meteo.com](https://open-meteo.com/), licencia CC BY 4.0. El plan gratuito es solo para uso no comercial.
- Observaciones METAR: [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/), Iowa State University.
- Servicio de reportes: Cloudflare Workers y D1 (plan gratuito).
