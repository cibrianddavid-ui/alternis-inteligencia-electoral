# Plataforma de Inteligencia Electoral — versión 3

Aplicación web con FastAPI y un frontend ligero para consultar resultados electorales, ver el perfil de cualquier
territorio, analizar la cobertura de prensa, redactar discursos con cifras verificadas y dar seguimiento a la campaña.

## Módulos

| Módulo | Qué hace | Quién lo usa |
|---|---|---|
| **Asistente electoral** | Preguntas en lenguaje natural; muestra cómo interpretó la consulta. El modelo interpreta, Python calcula. | Todos |
| **Perfil territorial** | Ficha de un estado, municipio, distrito o sección: ganadores, margen, participación, secciones más competidas y metas ligadas. Enlaces directos (`#territorio/municipio/MATEHUALA`). | Todos |
| **Resultados gráficos** | Compara partidos, elecciones y coaliciones; colores por partido. | Todos |
| **Semáforo de posicionamiento** | Cobertura de prensa de una persona o comparación de dos: tono, cobertura por semana, filtros y reporte en PDF. | Coordinación y Administración |
| **Discursos** | Formatos (mitin, debate, WhatsApp, boletín, guion), ajustes rápidos, datos de un territorio, **verificación de cifras**, historial persistente y exportación a Word, PDF y TXT. | Coordinación y Administración |
| **Campaña y tareas** | Metas con avance medible y territorio, tareas, tablero ejecutivo y alertas. | Ver: todos · Editar: Coordinación y Administración |
| **Administración** | Usuarios, roles y modo demostración. | Administración |

## Requisitos

- Python 3.9 o superior
- Una API key de Groq (`GROQ_API_KEY`) para el asistente y los discursos
- Una clave de SerpAPI (`SERPAPI_KEY`) para el semáforo
- Acceso de lectura a la hoja de Google con los resultados

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate          # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
python verificar_instalacion.py    # comprueba que no falte ningún archivo
```

Crea un archivo `.env` en la raíz con tus claves (no lo subas a Git):

```
GROQ_API_KEY=tu_clave
SERPAPI_KEY=tu_clave
```

## Ejecución y primer acceso

```bash
python run.py
```

Abre `http://127.0.0.1:8000`. La primera vez la plataforma pide **crear el primer administrador**; después esa
persona crea a los demás usuarios desde la pestaña *Administración*.

Para un servidor puedes evitar la pantalla de alta definiendo `ADMIN_USUARIO` y `ADMIN_CLAVE` en `.env`: se crea el
primer administrador al arrancar, solo si todavía no existe ningún usuario.

## Roles

- **Consulta**: ve resultados, perfiles, gráficas y la campaña; usa el asistente.
- **Coordinación**: además usa el semáforo, redacta discursos y edita la campaña.
- **Administración**: además gestiona usuarios y los datos de demostración.

Los permisos se aplican en el servidor; ocultar pestañas en pantalla es solo una comodidad.

## Variables de entorno

| Variable | Para qué sirve |
|---|---|
| `GROQ_API_KEY`, `GROQ_MODEL` | Modelo de lenguaje (asistente y discursos). |
| `SERPAPI_KEY` | Búsqueda de noticias. |
| `GOOGLE_SHEET_ID`, `GOOGLE_SHEET_GID` | Hoja de Google con los resultados. |
| `ADMIN_USUARIO`, `ADMIN_CLAVE` | Crean el primer administrador al arrancar (opcional). |
| `COOKIE_SECURE=1` | Marca la cookie de sesión como «solo HTTPS». **Actívala en producción.** |
| `ENABLE_DOCS=1` | Publica la documentación interactiva de la API en `/docs`. Desactivada por defecto. |

## Verificación de cifras en los discursos

Cuando eliges un territorio, el sistema calcula una ficha de datos (votos, porcentajes, margen, participación) y se
la entrega al modelo como única fuente de cifras. Después revisa cada cifra del borrador: las que coinciden con la
ficha o con lo que tú escribiste se marcan como respaldadas; las demás se resaltan para que las confirmes. Solo se
revisan cifras que parecen datos (porcentajes, decimales, cantidades de 100 o más); no se revisan años ni números
pequeños. La revisión no verifica hechos ni afirmaciones, solo cifras: **todo texto debe revisarlo una persona**.
Las exportaciones incluyen la nota de verificación, calculada en el servidor.

## Datos y su calidad

- La base se descarga de la hoja de Google al iniciar y se guarda en `data/electoral.db`.
- El partido «NA» (Nueva Alianza) se lee como texto y no como valor vacío.
- El perfil territorial avisa cuando la suma de las opciones no coincide con el total calculado de la base y marca
  los empates exactos. Vale la pena revisar esos casos contra la fuente.
- Porcentaje = votos / `TOTAL_VOTOS_CALCULADOS`; participación = votos emitidos / lista nominal.

## Datos que se guardan (carpeta `data/`)

| Archivo | Contenido |
|---|---|
| `electoral.db` | Resultados electorales (se regenera al iniciar). |
| `campania.db` | Áreas, personas, metas y tareas. **Contiene datos personales.** |
| `plataforma.db` | Usuarios (contraseñas con hash scrypt), sesiones e historial de discursos. |

Respalda `data/` (excepto `electoral.db`, que se regenera). En un servidor con disco efímero necesitas un volumen
persistente. No subas `data/` ni `.env` a Git.

## Seguridad: qué incluye y qué falta

Incluye: contraseñas con scrypt y sal; sesiones con token aleatorio guardado solo como huella; cookie `HttpOnly` y
`SameSite=Lax`; bloqueo temporal tras 8 intentos fallidos; verificación de origen en peticiones que modifican datos;
sesiones del asistente y conversaciones de discursos aisladas por persona; cabeceras básicas.

Pendiente antes de exponerla en internet: HTTPS (con `COOKIE_SECURE=1`), política de contenido (CSP), registro de
actividad (bitácora), respaldo automático y revisión legal del manejo de datos personales.

## Estructura

```
backend/   main.py (API) · auth.py, basedatos.py · territorio.py · redaccion.py, historial.py, informes.py
           asistente_electoral.py, resultados_graficos.py, posicionamiento.py · campania.py, demo.py
frontend/  index.html · assets/ app.js, auth.js, territorio.js, discursos.js, admin.js, tour.js, styles.css
data/      bases SQLite generadas localmente
```

## Limitaciones conocidas

- Cada instalación es una sola campaña; no hay separación entre varias campañas.
- Los datos electorales disponibles son de un solo año (2024); las funciones de tendencia esperan más años.
- En pantallas de celular no se muestra el bloque de usuario (cerrar sesión, contraseña y recorrido).
- El límite de intentos de acceso vive en memoria: con varios procesos de servidor no se comparte.

## Cartografía electoral

La pestaña **Cartografía electoral** usa `data/cartografia/secciones.geojson` (WGS84 / EPSG:4326), generado a partir de los archivos oficiales `SECCION.shp/.shx/.dbf/.prj`. El backend une cada polígono con los resultados mediante `SECCION` y expone `/api/cartografia/catalogo` y `/api/cartografia/mapa`. El visor permite representar ganador, margen entre primero y segundo, participación y porcentaje de voto de una fuerza política, y enlaza cada sección con su Perfil territorial.

## Corrección de versión 3 (8 de octubre de 2026)

El comparativo del Perfil territorial, las diferencias por elección, el histórico y la ficha para la IA ahora
usan cada partido u opción registrada por separado. El contrato del comparativo devuelve `partidos` y
`por_partido`, en lugar de `bloques` y `por_bloque`. El frontend incluido ya usa ese contrato.
Las coaliciones de la base se mantienen como opciones independientes: no se distribuyeron sus votos.

La configuración activa es `.env`, en la raíz del proyecto. En el archivo recibido la clave estaba en
`.env.example`; se trasladó a `.env` y se limpiaron las claves del ejemplo. Las variables no vacías del servidor tienen
precedencia sobre `.env`. Una variable GROQ_API_KEY vacía no impide recuperar la clave local. Reinicia el proceso después de modificar la configuración.
El análisis territorial distingue errores de autenticación, límite de uso y tiempo de espera de Groq.

Pruebas de regresión (con las dependencias instaladas):

```bash
python -m unittest discover -s tests -v
```

Consulta `REVISION_V3.md` para la evaluación completa y los pendientes. Esta entrega conserva las bases de datos
recibidas y las claves en `.env`; prepara una distribución sin credenciales ni bases privadas si vas a compartirla.
No incluye el entorno virtual de macOS: créalo de nuevo con los comandos de instalación.

### Si sigue apareciendo «Falta GROQ_API_KEY»

Detén el servidor y verifica que estés en la carpeta de esta versión. Ejecuta:

```bash
python configurar_claves.py
python run.py
```

El configurador muestra la carpeta y guarda la clave sin imprimirla. Si no la encuentra, la pide en la terminal
sin mostrarla mientras escribes o pegas. Conserva las demás variables de `.env`.
La carga recupera una clave con formato Groq de `.env.example` como compatibilidad con versiones anteriores,
pero solo cuando no existe una clave no vacía en el entorno o `.env`. No toma marcadores de ejemplo como claves.
