# Plataforma de Inteligencia Electoral

Aplicación web local con FastAPI y un frontend ligero. Incluye el Asistente electoral y un módulo interactivo de resultados gráficos; deja preparados los módulos de cartografía electoral y semáforo de posicionamiento.

## Requisitos

- Python 3.9 o superior
- Una API key de Groq
- Acceso de lectura a la hoja de Google configurada

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

En Windows, activa el entorno con:

```powershell
.venv\Scripts\activate
```

Edita `.env` y coloca tu clave en `GROQ_API_KEY`.

## Ejecución

```bash
python run.py
```

Abre `http://127.0.0.1:8000` en el navegador. La documentación de la API está disponible en `http://127.0.0.1:8000/docs`.

## Estructura

- `backend/main.py`: API, sesiones y entrega del frontend.
- `backend/asistente_electoral.py`: interpretación, consultas y métricas.
- `backend/resultados_graficos.py`: filtros, agregaciones, porcentajes, variaciones y coaliciones.
- `frontend/`: interfaz de la aplicación.
- `data/`: base SQLite generada localmente.

La base se actualiza desde Google Sheets al iniciar la aplicación. El módulo gráfico permite comparar años, tipos de elección y fuerzas políticas, además de descargar la gráfica y sus datos. No publiques el archivo `.env` ni tu API key.

## Noticias y fichas territoriales

En `requirements.txt` se escriben **nombres de paquetes**, no los comandos `pip install` ni `python -m`.
Tras `pip install -r requirements.txt`, instala una sola vez el modelo de español:

```bash
python -m spacy download es_core_news_sm
```

Si ya habías instalado los paquetes antes de esta corrección, ejecuta
`python -m pip install -r requirements.txt` de nuevo para añadir
`lxml_html_clean`. Comprueba que el intérprete activo sea el de `.venv`.

Edita `.env` y agrega `SERPAPI_KEY=tu_clave_real`. No compartas la clave ni subas `.env` a GitHub.
`backend/posicionamiento.py` contiene la consulta de noticias y las expresiones. El filtro del buscador no garantiza la fecha editorial original; comprueba las fuentes antes de usar los resultados.

## Discursos y campaña

En **Discursos y posicionamientos**, escribe una petición libre para generar un discurso con la clave `GROQ_API_KEY` existente. Puedes pedir ajustes en el mismo chat. El último borrador se descarga como TXT o PDF y se puede copiar o compartir con la función de compartir del dispositivo. La conversación se mantiene mientras la página siga abierta.

En **Campaña y tareas**, crea metas y personas, y luego asigna cada tarea a una meta y una persona. Mueve tareas arrastrándolas entre **Por hacer**, **En proceso** y **Finalizada**, o usa el selector de estado, útil también en el celular. Metas, personas y tareas se guardan en `data/campania.db` y persisten entre reinicios. Conserva ese archivo al actualizar el proyecto; en un servidor con disco efímero necesitarás un volumen persistente para conservarlas.
