"""API de la plataforma de inteligencia electoral."""

from __future__ import annotations

import io
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from groq import Groq
from pydantic import BaseModel, Field, ValidationError, model_validator

from . import auth, basedatos, campania, demo, historial, redaccion
from .asistente_electoral import AsistenteElectoral, Config, preparar_base
from .discursos import datos_distrito, pdf, redactar, word
from .informes import (nombre_archivo, pdf_discurso, pdf_posicionamiento, txt_discurso,
                       word_discurso)
from .posicionamiento import buscar
from .resultados_graficos import ConsultaGraficos, analizar_resultados, construir_catalogo
from .territorio import Territorio, hechos

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
load_dotenv(ROOT / ".env")

SESION_CHAT_TTL = 3 * 3600   # segundos sin actividad
SESION_CHAT_MAX = 300


# ---------------------------------------------------------------------------
# Modelos de entrada
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    pregunta: str
    session_id: Optional[str] = None


class NoticiasRequest(BaseModel):
    nombre: str = Field(min_length=3, max_length=120)
    fecha_inicio: str
    fecha_fin: str
    paginas: int = Field(default=2, ge=1, le=5)


class ReporteNoticias(BaseModel):
    """Resultado de /api/posicionamiento/analizar tal como lo recibió el navegador."""
    nombre: str = Field(min_length=1, max_length=120)
    fecha_inicio: str = Field(max_length=10)
    fecha_fin: str = Field(max_length=10)
    noticias: list[dict[str, Any]] = Field(default_factory=list, max_length=150)
    asociaciones: list[dict[str, Any]] = Field(default_factory=list, max_length=60)
    analisis: dict[str, Any] = Field(default_factory=dict)
    semaforo: dict[str, Any] = Field(default_factory=dict)
    cobertura_semanal: dict[str, Any] = Field(default_factory=dict)
    grafica_semanal: Optional[str] = Field(default=None, max_length=4_000_000)


class ExportarPosicionamiento(BaseModel):
    reportes: list[ReporteNoticias] = Field(min_length=1, max_length=2)


class DiscursoRequest(BaseModel):
    peticion: str = Field(min_length=15, max_length=2000)
    municipio: str = Field(default="San Luis Potosí", min_length=2, max_length=120)
    anio: str = ""
    noticias: list[dict[str, Any]] = Field(default_factory=list)


class DescargarRequest(BaseModel):
    titulo: str = Field(max_length=150)
    discurso: str = Field(max_length=30000)
    evidencia: dict[str, Any]


class TerritorioSeleccion(BaseModel):
    nivel: str = Field(max_length=40)
    unidad: str = Field(min_length=1, max_length=120)
    anio: Optional[str] = Field(default=None, max_length=10)
    eleccion: Optional[str] = Field(default=None, max_length=40)


class DiscursoChatRequest(BaseModel):
    mensaje: Optional[str] = Field(default=None, max_length=4000)
    ajuste: Optional[str] = Field(default=None, max_length=40)
    discurso_id: Optional[int] = None
    # Si no se envía, se conserva el formato de la conversación (o "libre" en una nueva).
    formato: Optional[str] = Field(default=None, max_length=20)
    # Si el campo no se envía se conserva el territorio de la conversación; null lo quita.
    territorio: Optional[TerritorioSeleccion] = None


class RenombrarDiscurso(BaseModel):
    titulo: str = Field(min_length=1, max_length=150)


class ExportarDiscurso(BaseModel):
    texto: str = Field(min_length=1, max_length=30000)
    titulo: Optional[str] = Field(default=None, max_length=150)
    discurso_id: Optional[int] = None


class _MetaBase(BaseModel):
    territorio_tipo: Optional[str] = None
    territorio_valor: Optional[str] = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _territorio_completo(self):
        if bool(self.territorio_tipo) != bool(self.territorio_valor):
            raise ValueError("Indica el tipo y el valor del territorio, o ninguno.")
        if self.territorio_tipo and self.territorio_tipo not in campania.TERRITORIOS:
            raise ValueError("Tipo de territorio no válido.")
        return self


class MetaRequest(_MetaBase):
    titulo: str = Field(min_length=1, max_length=180)
    descripcion: str = Field(default="", max_length=2000)
    responsable_id: Optional[int] = None
    area_id: Optional[int] = None
    fecha_limite: Optional[str] = None
    indicador: str = Field(default="", max_length=180)
    objetivo: Optional[float] = Field(default=None, ge=0)
    avance: float = Field(default=0, ge=0)


class MetaCambios(BaseModel):
    titulo: Optional[str] = Field(default=None, min_length=1, max_length=180)
    descripcion: Optional[str] = Field(default=None, max_length=2000)
    fecha_limite: Optional[str] = None
    indicador: Optional[str] = Field(default=None, max_length=180)
    objetivo: Optional[float] = Field(default=None, ge=0)
    avance: Optional[float] = Field(default=None, ge=0)


class AreaRequest(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    descripcion: str = Field(default="", max_length=500)


class PersonaRequest(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    contacto: str = Field(default="", max_length=180)
    area_id: Optional[int] = None


class TareaRequest(BaseModel):
    titulo: str = Field(min_length=1, max_length=180)
    descripcion: str = Field(default="", max_length=2000)
    meta_id: Optional[int] = None
    persona_id: Optional[int] = None
    fecha_limite: Optional[str] = None
    prioridad: str = Field(default="media", pattern="^(baja|media|alta|critica)$")
    peso: int = Field(default=3, ge=1, le=8)
    evidencia: str = Field(default="", max_length=1000)


class EstadoTareaRequest(BaseModel):
    estado: str


# ---------------------------------------------------------------------------
# Estado de la aplicación
# ---------------------------------------------------------------------------

class EstadoAplicacion:
    def __init__(self) -> None:
        self.df = None
        self.mapa: dict[str, str] = {}
        self.territorio: Optional[Territorio] = None
        self.client: Optional[Groq] = None
        self.config: Optional[Config] = None
        # sesión de chat -> {usuario, asistente, ultimo}. Cada sesión pertenece a una persona.
        self.sesiones: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.error_inicio: Optional[str] = None
        self.error_asistente: Optional[str] = None

    def iniciar(self) -> None:
        self.config = Config(
            # "or": una variable vacía en .env (GROQ_MODEL=) se trata como ausente y usa el valor por defecto.
            sheet_id=os.getenv("GOOGLE_SHEET_ID") or Config.sheet_id,
            gid=os.getenv("GOOGLE_SHEET_GID") or Config.gid,
            ruta_bd=str(DATA / "electoral.db"),
            modelo=os.getenv("GROQ_MODEL") or Config.modelo,
        )
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if api_key:
            self.client = Groq(api_key=api_key)
        else:
            self.error_asistente = "Falta configurar GROQ_API_KEY en el archivo .env."
        try:
            self.df, self.mapa = preparar_base(self.config)
            self.territorio = Territorio(self.df, self.mapa)
        except Exception as exc:  # El detalle queda disponible para el administrador en /api/health.
            self.error_inicio = str(exc)

    def _purgar_sesiones(self) -> None:
        corte = time.time() - SESION_CHAT_TTL
        for sid in [s for s, e in self.sesiones.items() if e["ultimo"] < corte]:
            del self.sesiones[sid]
        exceso = len(self.sesiones) - SESION_CHAT_MAX
        if exceso > 0:
            for sid in sorted(self.sesiones, key=lambda s: self.sesiones[s]["ultimo"])[:exceso]:
                del self.sesiones[sid]

    def obtener_asistente(self, session_id: Optional[str], usuario_id: int) -> tuple[str, AsistenteElectoral]:
        if self.error_inicio or self.client is None or self.df is None or self.config is None:
            raise RuntimeError(self.error_inicio or self.error_asistente or "El asistente no esta disponible.")
        with self.lock:
            self._purgar_sesiones()
            entrada = self.sesiones.get(session_id) if session_id else None
            if entrada is None or entrada["usuario"] != usuario_id:  # nunca se comparten sesiones entre personas
                session_id = str(uuid.uuid4())
                entrada = {"usuario": usuario_id, "ultimo": time.time(), "asistente": AsistenteElectoral(
                    client=self.client, df=self.df, mapa=self.mapa, config=self.config)}
                self.sesiones[session_id] = entrada
            entrada["ultimo"] = time.time()
            return session_id, entrada["asistente"]

    def reiniciar_sesion(self, session_id: str, usuario_id: int) -> None:
        with self.lock:
            if self.sesiones.get(session_id, {}).get("usuario") == usuario_id:
                del self.sesiones[session_id]

    def modelo(self) -> str:
        return self.config.modelo if self.config else (os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b")


estado = EstadoAplicacion()


@asynccontextmanager
async def lifespan(_: FastAPI):
    basedatos.iniciar()
    auth.crear_admin_inicial_desde_entorno()
    campania.iniciar()
    estado.iniciar()
    yield


DOCS = os.getenv("ENABLE_DOCS", "0") == "1"
app = FastAPI(
    title="Plataforma de Inteligencia Electoral", version="1.0.0", lifespan=lifespan,
    docs_url="/docs" if DOCS else None, redoc_url=None, openapi_url="/openapi.json" if DOCS else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(auth.router_usuarios)


@app.exception_handler(Exception)
async def error_interno(request: Request, exc: Exception):
    """Todo error inesperado se registra completo en la consola y responde con un mensaje legible."""
    logging.getLogger("uvicorn.error").error("Error no controlado en %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": "Error interno del servidor. Revisa la consola donde corre la aplicación."}, status_code=500)


@app.middleware("http")
async def seguridad(request: Request, call_next):
    if not auth.origen_permitido(request):
        return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _exigir_datos() -> None:
    if estado.df is None:
        raise HTTPException(status_code=503, detail=estado.error_inicio or "Los datos electorales no estan disponibles.")


# ---------------------------------------------------------------------------
# Estado general
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health(usuario: Optional[dict[str, Any]] = Depends(auth.usuario_opcional)) -> dict[str, Any]:
    respuesta: dict[str, Any] = {
        "ok": estado.error_inicio is None and estado.df is not None,
        "filas": 0 if estado.df is None else len(estado.df),
        "asistente_disponible": estado.client is not None,
    }
    if usuario and usuario["rol"] == "admin":  # los detalles técnicos solo los ve el administrador
        respuesta.update(columnas=estado.mapa, error=estado.error_inicio, error_asistente=estado.error_asistente)
    return respuesta


# ---------------------------------------------------------------------------
# Asistente y gráficas (cualquier usuario con sesión)
# ---------------------------------------------------------------------------

@app.post("/api/chat")
def chat(solicitud: ChatRequest, usuario: dict[str, Any] = Depends(auth.usuario_actual)) -> dict[str, Any]:
    try:
        sid, asistente = estado.obtener_asistente(solicitud.session_id, usuario["id"])
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
        # Interpretación validada (filtros ya resueltos contra el catálogo). Es None
        # cuando el asistente pidió una aclaración o no pudo interpretar la consulta.
        "plan": resultado.get("plan"),
    }


@app.delete("/api/chat/{session_id}")
def limpiar_chat(session_id: str, usuario: dict[str, Any] = Depends(auth.usuario_actual)) -> dict[str, bool]:
    estado.reiniciar_sesion(session_id, usuario["id"])
    return {"ok": True}


@app.get("/api/graficos/catalogo")
def catalogo_graficos(_: dict[str, Any] = Depends(auth.usuario_actual)) -> dict[str, Any]:
    _exigir_datos()
    return construir_catalogo(estado.df, estado.mapa)


@app.post("/api/graficos/analizar")
def graficos(solicitud: ConsultaGraficos, _: dict[str, Any] = Depends(auth.usuario_actual)) -> dict[str, Any]:
    _exigir_datos()
    try:
        return analizar_resultados(estado.df, estado.mapa, solicitud)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="No fue posible construir la comparacion grafica.") from exc


# ---------------------------------------------------------------------------
# Perfil territorial
# ---------------------------------------------------------------------------

@app.get("/api/territorio/catalogo")
def catalogo_territorio(_: dict[str, Any] = Depends(auth.usuario_actual)) -> dict[str, Any]:
    _exigir_datos()
    if estado.territorio is None:
        raise HTTPException(status_code=503, detail="El perfil territorial no está disponible.")
    return estado.territorio.catalogo()


@app.get("/api/territorio/perfil")
def perfil_territorio(nivel: str, unidad: str, anio: Optional[str] = None,
                      _: dict[str, Any] = Depends(auth.usuario_actual)) -> dict[str, Any]:
    _exigir_datos()
    if estado.territorio is None:
        raise HTTPException(status_code=503, detail="El perfil territorial no está disponible.")
    try:
        return estado.territorio.perfil(nivel, unidad, anio)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Semáforo de posicionamiento (coordinador o superior: consume búsquedas de pago)
# ---------------------------------------------------------------------------

@app.post("/api/posicionamiento/analizar")
def posicionamiento(solicitud: NoticiasRequest, _: dict[str, Any] = Depends(auth.requiere("coordinador"))) -> dict[str, Any]:
    try:
        inicio = date.fromisoformat(solicitud.fecha_inicio)
        fin = date.fromisoformat(solicitud.fecha_fin)
        return buscar(solicitud.nombre.strip(), inicio, fin, solicitud.paginas,
                      client=estado.client, model_name=estado.modelo())
    except (ValueError, requests.exceptions.HTTPError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except requests.exceptions.RequestException as exc:
        raise HTTPException(status_code=502, detail="No se pudo conectar con el buscador de noticias.") from exc


@app.post("/api/posicionamiento/exportar")
def exportar_posicionamiento(solicitud: ExportarPosicionamiento, _: dict[str, Any] = Depends(auth.requiere("coordinador"))):
    reportes = [r.model_dump() for r in solicitud.reportes]
    try:
        contenido = pdf_posicionamiento(reportes)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Datos del reporte incompletos.") from exc
    return StreamingResponse(
        io.BytesIO(contenido), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo(reportes)}"'},
    )


# ---------------------------------------------------------------------------
# Discursos
# ---------------------------------------------------------------------------

@app.get("/api/discursos/opciones")
def opciones_discursos(_: dict[str, Any] = Depends(auth.requiere("coordinador"))) -> dict[str, Any]:
    return redaccion.opciones()


@app.get("/api/discursos/historial")
def listar_discursos(usuario: dict[str, Any] = Depends(auth.requiere("coordinador"))) -> list[dict[str, Any]]:
    return historial.listar(usuario["id"])


@app.get("/api/discursos/historial/{discurso_id}")
def obtener_discurso(discurso_id: int, usuario: dict[str, Any] = Depends(auth.requiere("coordinador"))) -> dict[str, Any]:
    conversacion = historial.obtener(usuario["id"], discurso_id)
    if conversacion is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return conversacion


@app.patch("/api/discursos/historial/{discurso_id}")
def renombrar_discurso(discurso_id: int, solicitud: RenombrarDiscurso,
                       usuario: dict[str, Any] = Depends(auth.requiere("coordinador"))) -> dict[str, bool]:
    if not historial.renombrar(usuario["id"], discurso_id, solicitud.titulo):
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return {"ok": True}


@app.delete("/api/discursos/historial/{discurso_id}")
def borrar_discurso(discurso_id: int, usuario: dict[str, Any] = Depends(auth.requiere("coordinador"))) -> dict[str, bool]:
    if not historial.eliminar(usuario["id"], discurso_id):
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return {"ok": True}


def _ficha(seleccion: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Ficha de datos verificables del territorio elegido (None si no se eligió ninguno)."""
    if not seleccion:
        return None
    _exigir_datos()
    if estado.territorio is None:
        raise HTTPException(status_code=503, detail="El perfil territorial no está disponible.")
    try:
        perfil = estado.territorio.perfil(seleccion["nivel"], seleccion["unidad"], seleccion.get("anio"))
        return hechos(perfil, seleccion.get("eleccion"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/discursos/chat")
def chat_discursos(solicitud: DiscursoChatRequest,
                   usuario: dict[str, Any] = Depends(auth.requiere("coordinador"))) -> dict[str, Any]:
    if estado.client is None:
        raise HTTPException(status_code=503, detail=estado.error_asistente or "Configura GROQ_API_KEY en .env.")
    if solicitud.formato is not None and solicitud.formato not in redaccion.FORMATOS:
        raise HTTPException(status_code=422, detail="Formato no válido.")
    texto = (solicitud.mensaje or "").strip()
    if solicitud.ajuste and solicitud.ajuste not in redaccion.AJUSTES:
        raise HTTPException(status_code=422, detail="Ajuste no válido.")
    if not texto and not solicitud.ajuste:
        raise HTTPException(status_code=422, detail="Escribe un mensaje o elige un ajuste rápido.")
    if texto and len(texto) < 3:
        raise HTTPException(status_code=422, detail="El mensaje es demasiado corto.")

    conversacion = None
    if solicitud.discurso_id:
        conversacion = historial.obtener(usuario["id"], solicitud.discurso_id)
        if conversacion is None:
            raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    previos = conversacion["mensajes"] if conversacion else []
    formato = solicitud.formato or (conversacion["formato"] if conversacion else "libre")

    seleccion = (solicitud.territorio.model_dump() if solicitud.territorio else None) \
        if "territorio" in solicitud.model_fields_set else (conversacion["territorio"] if conversacion else None)
    ficha = _ficha(seleccion)

    if solicitud.ajuste:
        ajuste = redaccion.AJUSTES[solicitud.ajuste]
        if ajuste.get("requiere_datos") and not ficha:
            raise HTTPException(status_code=422, detail="Elige un territorio para usar este ajuste.")
        borradores = [m for m in previos if m["role"] == "assistant"]
        if not borradores:
            raise HTTPException(status_code=422, detail="Primero genera un borrador para poder ajustarlo.")
        para_modelo = redaccion.instruccion_ajuste(solicitud.ajuste, borradores[-1].get("verificacion"))
        if texto:
            para_modelo += " " + texto
        para_guardar = f"[Ajuste] {ajuste['etiqueta']}" + (f": {texto}" if texto else "")
    else:
        para_modelo = para_guardar = texto

    mensajes = redaccion.construir_mensajes(
        [{"role": m["role"], "content": m["content"]} for m in previos], para_modelo, formato,
        ficha["lineas"] if ficha else None)
    try:
        resultado = estado.client.chat.completions.create(
            model=estado.modelo(), messages=mensajes, temperature=0.45 if ficha else 0.6, max_tokens=1800)
        borrador = (resultado.choices[0].message.content or "").strip()
        if not borrador:
            raise ValueError("La respuesta está vacía.")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No fue posible generar el discurso. Comprueba la clave y vuelve a intentar.") from exc

    escritos_por_la_persona = [m["content"] for m in previos if m["role"] == "user" and not m["content"].startswith("[Ajuste]")]
    if not solicitud.ajuste:
        escritos_por_la_persona.append(texto)
    elif texto:
        escritos_por_la_persona.append(texto)
    verificacion = redaccion.verificar_cifras(
        borrador, ficha["permitidos"] if ficha else [], escritos_por_la_persona, ficha["descripcion"] if ficha else None)

    # La conversación se crea o actualiza solo cuando el modelo respondió: no quedan registros huérfanos.
    if conversacion is None:
        titulo = historial.titulo_automatico(para_guardar, redaccion.FORMATOS[formato]["nombre"])
        discurso_id = historial.crear(usuario["id"], titulo, formato, seleccion)
    else:
        discurso_id, titulo = conversacion["id"], conversacion["titulo"]
        if formato != conversacion["formato"] or seleccion != conversacion["territorio"]:
            historial.configurar(usuario["id"], discurso_id, formato, seleccion)
    historial.agregar_mensaje(usuario["id"], discurso_id, "user", para_guardar)
    historial.agregar_mensaje(usuario["id"], discurso_id, "assistant", borrador, verificacion)
    return {"discurso_id": discurso_id, "titulo": titulo, "respuesta": borrador, "verificacion": verificacion,
            "formato": formato, "territorio": seleccion}


@app.post("/api/discursos/exportar/{formato}")
def exportar_discurso(formato: str, solicitud: ExportarDiscurso,
                      usuario: dict[str, Any] = Depends(auth.requiere("coordinador"))):
    if formato not in ("txt", "pdf", "docx"):
        raise HTTPException(status_code=422, detail="Formato inválido.")
    titulo, verificacion = solicitud.titulo or "Discurso", None
    if solicitud.discurso_id:
        conversacion = historial.obtener(usuario["id"], solicitud.discurso_id)
        if conversacion is None:
            raise HTTPException(status_code=404, detail="Conversación no encontrada.")
        titulo = solicitud.titulo or conversacion["titulo"]
        ficha = _ficha(conversacion["territorio"])
        # La verificación del documento se recalcula aquí sobre el texto exportado: no se confía en el navegador.
        verificacion = redaccion.verificar_cifras(
            solicitud.texto, ficha["permitidos"] if ficha else [],
            [m["content"] for m in conversacion["mensajes"] if m["role"] == "user" and not m["content"].startswith("[Ajuste]")],
            ficha["descripcion"] if ficha else None)
    if formato == "txt":
        contenido, tipo = txt_discurso(solicitud.texto), "text/plain; charset=utf-8"
    elif formato == "pdf":
        contenido, tipo = pdf_discurso(titulo, solicitud.texto, verificacion), "application/pdf"
    else:
        contenido = word_discurso(titulo, solicitud.texto, verificacion)
        tipo = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return StreamingResponse(io.BytesIO(contenido), media_type=tipo,
                             headers={"Content-Disposition": f'attachment; filename="discurso.{formato}"'})


@app.post("/api/discursos/generar")
def generar_discurso(solicitud: DiscursoRequest, _: dict[str, Any] = Depends(auth.requiere("coordinador"))) -> dict[str, Any]:
    _exigir_datos()
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
def descargar_discurso(formato: str, solicitud: DescargarRequest, _: dict[str, Any] = Depends(auth.requiere("coordinador"))):
    if formato not in {'pdf', 'docx'}:
        raise HTTPException(status_code=422, detail="Formato no válido.")
    try:
        contenido = (pdf if formato == 'pdf' else word)(solicitud.titulo, solicitud.discurso, solicitud.evidencia)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Datos del documento incompletos.") from exc
    tipo = 'application/pdf' if formato == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return StreamingResponse(io.BytesIO(contenido), media_type=tipo,
                             headers={'Content-Disposition': f'attachment; filename="discurso_distrito_local.{formato}"'})


# ---------------------------------------------------------------------------
# Campaña: cualquiera con sesión puede ver; coordinador o superior modifica
# ---------------------------------------------------------------------------

@app.get("/api/campania")
def datos_campania(_: dict[str, Any] = Depends(auth.usuario_actual)):
    return campania.listar()


@app.post("/api/campania/{tabla}")
def crear_campania(tabla: str, solicitud: dict[str, Any], _: dict[str, Any] = Depends(auth.requiere("coordinador"))):
    modelos = {"areas": AreaRequest, "metas": MetaRequest, "personas": PersonaRequest, "tareas": TareaRequest}
    if tabla not in modelos:
        raise HTTPException(status_code=404, detail="Sección inexistente.")
    try:
        datos = modelos[tabla](**solicitud).model_dump()
        if tabla in ("metas", "tareas") and datos.get("fecha_limite"):
            date.fromisoformat(datos["fecha_limite"])
        return campania.crear(tabla, datos)
    except (ValueError, ValidationError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/campania/metas/{meta_id}")
def actualizar_meta(meta_id: int, solicitud: MetaCambios, _: dict[str, Any] = Depends(auth.requiere("coordinador"))):
    cambios = solicitud.model_dump(exclude_unset=True)
    try:
        if cambios.get("fecha_limite"):
            date.fromisoformat(cambios["fecha_limite"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Fecha límite no válida.") from exc
    meta = campania.actualizar_meta(meta_id, cambios)
    if meta is None:
        raise HTTPException(status_code=404, detail="Meta no encontrada.")
    return meta


@app.patch("/api/campania/tareas/{tarea_id}")
def mover_tarea(tarea_id: int, solicitud: EstadoTareaRequest, _: dict[str, Any] = Depends(auth.requiere("coordinador"))):
    try:
        resultado = campania.mover(tarea_id, solicitud.estado)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if resultado is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    return resultado


@app.delete("/api/campania/{tabla}/{item_id}")
def borrar_campania(tabla: str, item_id: int, _: dict[str, Any] = Depends(auth.requiere("coordinador"))):
    if tabla not in ("areas", "metas", "personas", "tareas"):
        raise HTTPException(status_code=404, detail="Sección inexistente.")
    if not campania.eliminar(tabla, item_id):
        raise HTTPException(status_code=404, detail="Registro no encontrado.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Modo demostración (administrador)
# ---------------------------------------------------------------------------

@app.get("/api/demo")
def estado_demo(_: dict[str, Any] = Depends(auth.usuario_actual)) -> dict[str, bool]:
    return {"cargado": campania.hay_demo()}


@app.post("/api/demo")
def cargar_demo(_: dict[str, Any] = Depends(auth.requiere("admin"))) -> dict[str, Any]:
    try:
        return {"creado": demo.sembrar(estado.territorio)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/demo")
def quitar_demo(_: dict[str, Any] = Depends(auth.requiere("admin"))) -> dict[str, int]:
    return {"eliminados": campania.quitar_demo()}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")


@app.get("/{ruta:path}", include_in_schema=False)
def frontend(ruta: str):
    if ruta.startswith("api/"):
        raise HTTPException(status_code=404, detail="Ruta inexistente.")
    archivo = FRONTEND / ruta
    if ruta and archivo.is_file() and FRONTEND in archivo.resolve().parents:
        return FileResponse(archivo)
    return FileResponse(FRONTEND / "index.html")
