"""API local para la plataforma de inteligencia electoral."""

from __future__ import annotations

import os
import threading
import uuid
import io
import requests
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from groq import Groq
from pydantic import BaseModel, Field
from pydantic import ValidationError

from .asistente_electoral import AsistenteElectoral, Config, preparar_base
from .resultados_graficos import (
    ConsultaGraficos,
    analizar_resultados,
    construir_catalogo,
)
from .posicionamiento import buscar
from .discursos import datos_distrito, redactar, word, pdf
from . import campania
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from xml.sax.saxutils import escape
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
load_dotenv(ROOT / ".env")


class ChatRequest(BaseModel):
    pregunta: str
    session_id: Optional[str] = None


class NoticiasRequest(BaseModel):
    nombre: str = Field(min_length=3, max_length=120)
    fecha_inicio: str
    fecha_fin: str
    paginas: int = Field(default=2, ge=1, le=5)


class DiscursoRequest(BaseModel):
    peticion: str = Field(min_length=15, max_length=2000)
    municipio: str = Field(default="San Luis Potosí", min_length=2, max_length=120)
    anio: str = ""
    noticias: list[dict[str, Any]] = Field(default_factory=list)


class DescargarRequest(BaseModel):
    titulo: str = Field(max_length=150)
    discurso: str = Field(max_length=30000)
    evidencia: dict[str, Any]


class DiscursoChatRequest(BaseModel):
    mensaje: str = Field(min_length=3, max_length=4000)
    historial: list[dict[str, str]] = Field(default_factory=list)


class ExportarDiscurso(BaseModel):
    texto: str = Field(min_length=1, max_length=30000)


class MetaRequest(BaseModel):
    titulo: str = Field(min_length=1, max_length=180)
    descripcion: str = Field(default="", max_length=2000)


class PersonaRequest(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    contacto: str = Field(default="", max_length=180)


class TareaRequest(BaseModel):
    titulo: str = Field(min_length=1, max_length=180)
    descripcion: str = Field(default="", max_length=2000)
    meta_id: Optional[int] = None
    persona_id: Optional[int] = None
    fecha_limite: Optional[str] = None


class EstadoTareaRequest(BaseModel):
    estado: str


class EstadoAplicacion:
    def __init__(self) -> None:
        self.df = None
        self.mapa: dict[str, str] = {}
        self.client: Optional[Groq] = None
        self.config: Optional[Config] = None
        self.sesiones: dict[str, AsistenteElectoral] = {}
        self.lock = threading.Lock()
        self.error_inicio: Optional[str] = None
        self.error_asistente: Optional[str] = None

    def iniciar(self) -> None:
        self.config = Config(
            sheet_id=os.getenv("GOOGLE_SHEET_ID", Config.sheet_id),
            gid=os.getenv("GOOGLE_SHEET_GID", Config.gid),
            ruta_bd=str(DATA / "electoral.db"),
            modelo=os.getenv("GROQ_MODEL", Config.modelo),
        )
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if api_key:
            self.client = Groq(api_key=api_key)
        else:
            self.error_asistente = "Falta configurar GROQ_API_KEY en el archivo .env."
        try:
            self.df, self.mapa = preparar_base(self.config)
        except Exception as exc:  # El detalle queda disponible en /api/health.
            self.error_inicio = str(exc)
            return


    def obtener_asistente(self,session_id: Optional[str],) -> tuple[str, AsistenteElectoral]:
        if self.error_inicio or self.client is None or self.df is None or self.config is None:
            raise RuntimeError(
                self.error_inicio or self.error_asistente or "El asistente no esta disponible."
            )
        sid = session_id or str(uuid.uuid4())
        with self.lock:
            if sid not in self.sesiones:
                self.sesiones[sid] = AsistenteElectoral(
                    client=self.client,
                    df=self.df,
                    mapa=self.mapa,
                    config=self.config,
                )
            return sid, self.sesiones[sid]

    def reiniciar_sesion(self, session_id: str) -> None:
        with self.lock:
            self.sesiones.pop(session_id, None)


estado = EstadoAplicacion()


@asynccontextmanager
async def lifespan(_: FastAPI):
    campania.iniciar()
    estado.iniciar()
    yield


app = FastAPI(
    title="Plataforma de Inteligencia Electoral",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": estado.error_inicio is None and estado.df is not None,
        "filas": 0 if estado.df is None else len(estado.df),
        "columnas": estado.mapa,
        "error": estado.error_inicio,
        "asistente_disponible": estado.client is not None,
        "error_asistente": estado.error_asistente,
    }


@app.post("/api/chat")
def chat(solicitud: ChatRequest) -> dict[str, Any]:
    try:
        sid, asistente = estado.obtener_asistente(solicitud.session_id)
        resultado = asistente.preguntar(solicitud.pregunta, mostrar_diagnostico=False)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="No fue posible procesar la consulta.") from exc

    datos = resultado.get("datos")
    if datos is not None:
        datos = datos.head(250).where(datos.notna(), None).to_dict(orient="records")

    return {
        "session_id": sid,
        "respuesta": resultado.get("respuesta", "No se obtuvo una respuesta."),
        "datos": datos or [],
        "requiere_aclaracion": resultado.get("requiere_aclaracion", False),
    }


@app.delete("/api/chat/{session_id}")
def limpiar_chat(session_id: str) -> dict[str, bool]:
    estado.reiniciar_sesion(session_id)
    return {"ok": True}


@app.get("/api/graficos/catalogo")
def catalogo_graficos() -> dict[str, Any]:
    if estado.df is None:
        raise HTTPException(
            status_code=503,
            detail=estado.error_inicio or "Los datos electorales no estan disponibles.",
        )
    return construir_catalogo(estado.df, estado.mapa)


@app.post("/api/graficos/analizar")
def graficos(solicitud: ConsultaGraficos) -> dict[str, Any]:
    if estado.df is None:
        raise HTTPException(
            status_code=503,
            detail=estado.error_inicio or "Los datos electorales no estan disponibles.",
        )
    try:
        return analizar_resultados(estado.df, estado.mapa, solicitud)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No fue posible construir la comparacion grafica.",
        ) from exc


@app.post("/api/posicionamiento/analizar")
def posicionamiento(solicitud: NoticiasRequest) -> dict[str, Any]:
    from datetime import date
    try:
        inicio = date.fromisoformat(solicitud.fecha_inicio)
        fin = date.fromisoformat(solicitud.fecha_fin)
        return buscar(solicitud.nombre.strip(), inicio, fin, solicitud.paginas)
    except (ValueError, requests.exceptions.HTTPError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except requests.exceptions.RequestException as exc:
        raise HTTPException(status_code=502, detail="No se pudo conectar con el buscador de noticias.") from exc


@app.post("/api/discursos/generar")
def generar_discurso(solicitud: DiscursoRequest) -> dict[str, Any]:
    if estado.df is None:
        raise HTTPException(status_code=503, detail=estado.error_inicio or "Datos no disponibles.")
    try:
        evidencia = datos_distrito(estado.df, estado.mapa, solicitud.municipio, solicitud.anio)
        noticias = [
            {'titulo': str(n.get('titulo', ''))[:200], 'fuente': str(n.get('fuente', ''))[:100],
             'url': str(n.get('url', ''))[:500]}
            for n in solicitud.noticias[:8]
        ]
        discurso = redactar(solicitud.peticion, evidencia, noticias)
        if not discurso:
            raise RuntimeError("El modelo no produjo un discurso.")
        return {'titulo': f"Ficha informativa del distrito local {evidencia['distrito']}",
                'discurso': discurso, 'evidencia': evidencia, 'noticias_consultadas': noticias}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="No se pudo generar la ficha informativa.") from exc


@app.post("/api/discursos/descargar/{formato}")
def descargar_discurso(formato: str, solicitud: DescargarRequest):
    if formato not in {'pdf', 'docx'}:
        raise HTTPException(status_code=422, detail="Formato no válido.")
    try:
        contenido = (pdf if formato == 'pdf' else word)(solicitud.titulo, solicitud.discurso, solicitud.evidencia)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Datos del documento incompletos.") from exc
    tipo = 'application/pdf' if formato == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return StreamingResponse(io.BytesIO(contenido), media_type=tipo,
        headers={'Content-Disposition': f'attachment; filename="discurso_distrito_local.{formato}"'})


@app.post("/api/discursos/chat")
def chat_discursos(solicitud: DiscursoChatRequest) -> dict[str, str]:
    if estado.client is None:
        raise HTTPException(status_code=503, detail=estado.error_asistente or "Configura GROQ_API_KEY en .env.")
    mensajes = [{"role": "system", "content": (
        "Eres un redactor en español para intervenciones públicas de un candidato. "
        "Cuando pidan un discurso, entrega directamente un borrador breve, natural y listo para leer. "
        "Si solicitan ajustes, reescribe el discurso completo considerando la conversación. "
        "No inventes nombres, cargos, logros, cifras, promesas concretas ni hechos no proporcionados; "
        "usa marcadores [nombre] o [dato por verificar] cuando haga falta. "
        "Evita dirigirte a segmentos demográficos específicos o personalizar mensajes para cambiar "
        "sus preferencias políticas. Pregunta solo cuando no puedas producir un borrador útil."
    )}]
    for item in solicitud.historial[-12:]:
        role, content = item.get("role"), item.get("content", "")
        if role in ("user", "assistant") and isinstance(content, str):
            mensajes.append({"role": role, "content": content[:5000]})
    mensajes.append({"role": "user", "content": solicitud.mensaje.strip()})
    try:
        resultado = estado.client.chat.completions.create(
            model=estado.config.modelo if estado.config else os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            messages=mensajes, temperature=0.6, max_tokens=1800)
        texto = resultado.choices[0].message.content or ""
        if not texto.strip():
            raise ValueError("La respuesta está vacía.")
        return {"respuesta": texto.strip()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No fue posible generar el discurso. Comprueba la clave y vuelve a intentar.") from exc


@app.post("/api/discursos/exportar/{formato}")
def exportar_discurso(formato: str, solicitud: ExportarDiscurso):
    if formato not in ("txt", "pdf"):
        raise HTTPException(status_code=422, detail="Formato inválido.")
    if formato == "txt":
        contenido = solicitud.texto.encode("utf-8-sig")
        tipo = "text/plain; charset=utf-8"
    else:
        buffer = io.BytesIO()
        fuentes = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                   "/Library/Fonts/Arial Unicode.ttf")
        fuente = next((path for path in fuentes if Path(path).exists()), None)
        if fuente and "DiscursoUnicode" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("DiscursoUnicode", fuente))
        styles = getSampleStyleSheet()
        base = "DiscursoUnicode" if fuente else "Helvetica"
        estilo = ParagraphStyle("TextoDiscurso", parent=styles["Normal"], fontName=base,
                               fontSize=11, leading=17, spaceAfter=12)
        titulo = ParagraphStyle("TituloDiscurso", parent=styles["Title"], fontName=base)
        story = [Paragraph("Discurso", titulo), Spacer(1, 18)]
        story.extend(Paragraph(escape(parrafo).replace("\n", "<br/>").replace("\t", "&nbsp;&nbsp;"), estilo)
                     for parrafo in solicitud.texto.split("\n\n") if parrafo.strip())
        SimpleDocTemplate(buffer, pagesize=letter, leftMargin=52, rightMargin=52,
                          topMargin=55, bottomMargin=55).build(story)
        contenido, tipo = buffer.getvalue(), "application/pdf"
    return StreamingResponse(io.BytesIO(contenido), media_type=tipo,
                             headers={"Content-Disposition": f'attachment; filename="discurso.{formato}"'})


@app.get("/api/campania")
def datos_campania():
    return campania.listar()


@app.post("/api/campania/{tabla}")
def crear_campania(tabla: str, solicitud: dict[str, Any]):
    modelos = {"metas": MetaRequest, "personas": PersonaRequest, "tareas": TareaRequest}
    if tabla not in modelos:
        raise HTTPException(status_code=404, detail="Sección inexistente.")
    try:
        datos = modelos[tabla](**solicitud).model_dump()
        if tabla == "tareas" and datos["fecha_limite"]:
            from datetime import date
            date.fromisoformat(datos["fecha_limite"])
        return campania.crear(tabla, datos)
    except (ValueError, ValidationError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/campania/tareas/{tarea_id}")
def mover_tarea(tarea_id: int, solicitud: EstadoTareaRequest):
    try:
        resultado = campania.mover(tarea_id, solicitud.estado)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if resultado is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    return resultado


@app.delete("/api/campania/{tabla}/{item_id}")
def borrar_campania(tabla: str, item_id: int):
    if tabla not in ("metas", "personas", "tareas"):
        raise HTTPException(status_code=404, detail="Sección inexistente.")
    if not campania.eliminar(tabla, item_id):
        raise HTTPException(status_code=404, detail="Registro no encontrado.")
    return {"ok": True}


app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")


@app.get("/{ruta:path}", include_in_schema=False)
def frontend(ruta: str):
    archivo = FRONTEND / ruta
    if ruta and archivo.is_file() and FRONTEND in archivo.resolve().parents:
        return FileResponse(archivo)
    return FileResponse(FRONTEND / "index.html")
