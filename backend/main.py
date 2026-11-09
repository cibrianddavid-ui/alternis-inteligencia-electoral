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
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from groq import Groq, AuthenticationError, RateLimitError, APITimeoutError
from pydantic import BaseModel, Field, ValidationError, model_validator

from .configuracion import ROOT, cargar_entorno

cargar_entorno()

from . import analisis_territorial, auth, basedatos, bloques, campania, demo, historial, limpieza, redaccion, social_listening
from .asistente_electoral import AsistenteElectoral, Config, preparar_base
from .cartografia import Cartografia
from .discursos import datos_distrito, pdf, redactar, word
from .informes import (nombre_archivo, pdf_discurso, pdf_posicionamiento, txt_discurso,
                       word_discurso)
from .posicionamiento import buscar
from .resultados_graficos import ConsultaGraficos, analizar_resultados, construir_catalogo
from .territorio import Territorio, hechos

FRONTEND = ROOT / "frontend"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

log = logging.getLogger("uvicorn.error")

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
    etapa: str = Field(default="campania", pattern="^(precampania|campania)$")
    mes: int = Field(default=1, ge=1, le=3)


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
    area_id: Optional[int] = None
    etapa: str = Field(default="campania", pattern="^(precampania|campania)$")
    mes: int = Field(default=1, ge=1, le=3)


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
        self.calidad: Optional[dict[str, Any]] = None
        self.cartografia: Optional[Cartografia] = None
        self.client: Optional[Groq] = None
        self.config: Optional[Config] = None
        # sesión de chat -> {usuario, asistente, ultimo}. Cada sesión pertenece a una persona.
        self.sesiones: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.error_inicio: Optional[str] = None
        self.error_asistente: Optional[str] = None

    def iniciar(self) -> None:
        cargar_entorno()
        self.error_asistente = None
        self.error_inicio = None
        self.client = None
        self.config = Config(
            # "or": una variable vacía en .env (GROQ_MODEL=) se trata como ausente y usa el valor por defecto.
            sheet_id=os.getenv("GOOGLE_SHEET_ID") or Config.sheet_id,
            gid=os.getenv("GOOGLE_SHEET_GID") or Config.gid,
            sheet_2018_id=os.getenv("GOOGLE_SHEET_2018_ID") or Config.sheet_2018_id,
            sheet_2018_gid=os.getenv("GOOGLE_SHEET_2018_GID") or Config.sheet_2018_gid,
            sheet_2012_id=os.getenv("GOOGLE_SHEET_2012_ID") or Config.sheet_2012_id,
            sheet_2012_gid=os.getenv("GOOGLE_SHEET_2012_GID") or Config.sheet_2012_gid,
            ruta_bd=str(DATA / "electoral.db"),
            modelo=os.getenv("GROQ_MODEL") or Config.modelo,
        )
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if api_key:
            self.client = Groq(api_key=api_key, timeout=45.0, max_retries=1)
        else:
            log.warning("Groq no configurado. Carpeta del proyecto en ejecución: %s", ROOT)
            self.error_asistente = ("El servidor no encontró una clave de Groq. En la carpeta de esta instalación "
                                    "ejecuta python configurar_claves.py y después reinicia el servidor.")
        try:
            self.df, self.mapa = preparar_base(self.config)
            self.territorio = Territorio(self.df, self.mapa)
            self.calidad = limpieza.auditar(self.df, self.mapa)
            self.cartografia = Cartografia(self.territorio, DATA / "cartografia" / "secciones.geojson")
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
    allow_origin_regex=r"^https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
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
    else:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
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


class AnalisisTerritorialRequest(BaseModel):
    nivel: str = Field(max_length=40)
    unidad: str = Field(max_length=120)
    anio: Optional[str] = Field(default=None, max_length=10)
    regenerar: bool = False


@app.get("/api/territorio/comparativo")
def comparativo_territorio(nivel: str, unidad: str, anio: Optional[str] = None,
                           _: dict[str, Any] = Depends(auth.usuario_actual)) -> dict[str, Any]:
    """Voto por partido u opción registrada en cada elección, diferencias entre elecciones y cambios de ganador por sección. Sin IA."""
    _exigir_datos()
    if estado.territorio is None:
        raise HTTPException(status_code=503, detail="El perfil territorial no está disponible.")
    try:
        return analisis_territorial.comparativo(estado.territorio, nivel, unidad, anio)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/territorio/analisis")
def analisis_territorio(solicitud: AnalisisTerritorialRequest,
                        _: dict[str, Any] = Depends(auth.usuario_actual)) -> dict[str, Any]:
    """Análisis escrito del territorio. El modelo redacta; las cifras salen de pandas y se verifican."""
    _exigir_datos()
    if estado.territorio is None:
        raise HTTPException(status_code=503, detail="El perfil territorial no está disponible.")
    if estado.client is None:
        raise HTTPException(status_code=503, detail=estado.error_asistente or "El modelo de lenguaje no está configurado.")
    try:
        return analisis_territorial.analizar(estado.territorio, estado.client, estado.modelo(), solicitud.nivel,
                                             solicitud.unidad, solicitud.anio, solicitud.regenerar)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthenticationError as exc:
        raise HTTPException(status_code=502, detail="Groq rechazó la clave configurada. Revisa GROQ_API_KEY en .env y reinicia el servidor.") from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail="Groq alcanzó su límite de uso. Espera unos momentos y vuelve a intentar.") from exc
    except APITimeoutError as exc:
        raise HTTPException(status_code=504, detail="Groq tardó demasiado en responder. Vuelve a intentar.") from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Falló el análisis territorial")
        raise HTTPException(status_code=502, detail="No fue posible redactar el análisis. Intenta de nuevo.") from exc


@app.get("/api/calidad")
def calidad_datos(_: dict[str, Any] = Depends(auth.requiere("admin"))) -> dict[str, Any]:
    """Hallazgos de calidad de la base, con qué hacer en la fuente."""
    _exigir_datos()
    return {**(estado.calidad or {}), "bloques": bloques.definicion()}


# ---------------------------------------------------------------------------
# Cartografía electoral
# ---------------------------------------------------------------------------

@app.get("/api/cartografia/catalogo")
def catalogo_cartografia(_: dict[str, Any] = Depends(auth.usuario_actual)) -> dict[str, Any]:
    _exigir_datos()
    if estado.cartografia is None:
        raise HTTPException(status_code=503, detail="La cartografía electoral no está disponible.")
    return estado.cartografia.catalogo()


@app.get("/api/cartografia/mapa")
def mapa_cartografia(eleccion: str, anio: Optional[str] = None, nivel: Optional[str] = None,
                     unidad: Optional[str] = None, variable: str = "ganador",
                     _: dict[str, Any] = Depends(auth.usuario_actual)) -> dict[str, Any]:
    _exigir_datos()
    if estado.cartografia is None:
        raise HTTPException(status_code=503, detail="La cartografía electoral no está disponible.")
    try:
        return estado.cartografia.mapa(anio=anio, eleccion=eleccion, nivel=nivel, unidad=unidad,
                                       variable=variable)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Semáforo de posicionamiento (coordinador o superior: consume búsquedas de pago)
# ---------------------------------------------------------------------------

@app.get("/api/cartografia/casillas")
def casillas_cartografia(_: dict[str, Any] = Depends(auth.usuario_actual)):
    from .casillas_v4 import cargar_casillas
    return cargar_casillas(DATA / "casillas_2024.csv")


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


class TareaOrganizacion(BaseModel):
    area_id: Optional[int] = None
    etapa: Optional[str] = Field(default=None, pattern="^(precampania|campania)$")
    mes: Optional[int] = Field(default=None, ge=1, le=3)


@app.patch("/api/campania/tareas/{tarea_id}/organizacion")
def organizar_tarea(tarea_id: int, solicitud: TareaOrganizacion, _: dict[str, Any] = Depends(auth.requiere("coordinador"))):
    try:
        resultado = campania.actualizar_tarea(tarea_id, solicitud.model_dump(exclude_unset=True))
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if resultado is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return resultado


@app.delete("/api/campania/{tabla}/{item_id}")
def borrar_campania(tabla: str, item_id: int, _: dict[str, Any] = Depends(auth.requiere("coordinador"))):
    if tabla not in ("areas", "metas", "personas", "tareas"):
        raise HTTPException(status_code=404, detail="Sección inexistente.")
    if not campania.eliminar(tabla, item_id):
        raise HTTPException(status_code=404, detail="Registro no encontrado.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Social Listening — análisis de redes sociales (Facebook / Apify)
# ---------------------------------------------------------------------------

@app.post("/api/social/analizar")
async def social_analizar(
    solicitud: social_listening.SolicitudSocial,
    usuario_sesion: dict[str, Any] = Depends(auth.requiere("coordinador")),
) -> social_listening.ResultadoSocial:
    """Ejecuta el pipeline de Social Listening para un perfil público de Facebook.

    Prioriza al 100% el análisis con el modelo de lenguaje (LLM en social_listening.py).
    Si el proceso del LLM falla de forma crítica, se invoca explícitamente el módulo
    aislado de contingencia (social_fallback.py).
    """
    import asyncio  # noqa: PLC0415
    loop = asyncio.get_event_loop()

    client = estado.client
    if client is None:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if api_key:
            client = Groq(api_key=api_key)
    modelo = estado.modelo() if hasattr(estado, "modelo") else (os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b")
    username = str(usuario_sesion.get("usuario") or "anonimo")

    try:
        resultado = await loop.run_in_executor(
            None,
            lambda: social_listening.analizar_perfil(
                solicitud,
                groq_client=client,
                groq_model=modelo,
                usuario=username,
            ),
        )
        return resultado
    except social_listening.CuotaScrapingExcedidaError as exc_cuota:
        raise HTTPException(
            status_code=429,
            detail=str(exc_cuota),
        )
    except Exception as exc_llm:
        log.warning(
            "Fallo en el pipeline LLM de Social Listening (%s). Activando módulo de respaldo independiente (social_fallback)...",
            exc_llm,
        )
        from . import social_fallback  # noqa: PLC0415
        return await loop.run_in_executor(
            None,
            lambda: social_fallback.analizar_perfil_fallback(solicitud, error_origen=str(exc_llm)),
        )


@app.post("/api/social/posts/analizar")
@app.post("/api/social/posts")
async def social_analizar_posts(
    solicitud: social_listening.SolicitudPostsSocial,
    usuario_sesion: dict[str, Any] = Depends(auth.requiere("coordinador")),
) -> social_listening.ResultadoPostsManual:
    """Ejecuta el pipeline de Social Listening para una lista manual de publicaciones de Facebook.

    Procesa cada publicación de forma individual y, cuando se proporcionan dos o más
    publicaciones, genera una comparación estadística y temática.
    """
    import asyncio  # noqa: PLC0415
    loop = asyncio.get_event_loop()

    client = estado.client
    if client is None:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if api_key:
            client = Groq(api_key=api_key)
    modelo = estado.modelo() if hasattr(estado, "modelo") else (os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b")
    username = str(usuario_sesion.get("usuario") or "anonimo")

    try:
        return await loop.run_in_executor(
            None,
            lambda: social_listening.analizar_posts_manual(
                solicitud,
                groq_client=client,
                groq_model=modelo,
                usuario=username,
            ),
        )
    except social_listening.CuotaScrapingExcedidaError as exc_cuota:
        raise HTTPException(
            status_code=429,
            detail=str(exc_cuota),
        )


class ExportarSocialPDF(BaseModel):
    tipo: str = Field(..., description="Tipo de reporte: 'perfil', 'posts', 'comparativa', 'historico'")
    data: dict[str, Any] = Field(..., description="Datos del resultado del análisis a exportar")


@app.post("/api/social/exportar/pdf")
@app.post("/api/social/exportar")
def social_exportar_pdf(
    solicitud: ExportarSocialPDF,
    _: dict[str, Any] = Depends(auth.requiere("coordinador")),
):
    """Exporta los análisis de Social Listening en formato PDF estructurado."""
    try:
        contenido, nombre = social_listening.generar_pdf_social(solicitud.tipo, solicitud.data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("Error generando PDF de social listening (%s): %s", solicitud.tipo, exc, exc_info=True)
        raise HTTPException(status_code=422, detail=f"No se pudo generar el reporte en PDF: {exc}") from exc

    return StreamingResponse(
        io.BytesIO(contenido),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# ---------------------------------------------------------------------------
# Histórico de postura por perfil (Exclusivamente BD Local, 0 llamadas a Apify)
# ---------------------------------------------------------------------------

@app.get("/api/social/historico/perfiles")
def social_historico_perfiles(
    _: dict[str, Any] = Depends(auth.requiere("coordinador")),
) -> list[dict[str, Any]]:
    """Devuelve la lista de perfiles que ya cuentan con publicaciones almacenadas en SQLite."""
    perfiles = social_listening.obtener_perfiles_en_bd()
    return [p.model_dump() for p in perfiles]


@app.get("/api/social/historico")
@app.post("/api/social/historico")
async def social_historico(
    solicitud: Optional[social_listening.SolicitudHistorico] = None,
    url_perfil: Optional[str] = Query(None, description="URL pública del perfil de Facebook a consultar en la BD"),
    agrupacion: str = Query("mes", description="Nivel de agrupación: 'semana' o 'mes'"),
    _: dict[str, Any] = Depends(auth.requiere("coordinador")),
) -> dict[str, Any]:
    """Genera la serie temporal histórica agrupada por semana o mes sin llamadas a Apify."""
    target_url = (solicitud.url_perfil if solicitud else url_perfil) or ""
    target_agrupacion = (solicitud.agrupacion if solicitud else agrupacion) or "mes"

    if not target_url.strip():
        raise HTTPException(
            status_code=400,
            detail="Debe proporcionar 'url_perfil' ya sea en el cuerpo JSON o como parámetro de consulta.",
        )

    if target_agrupacion not in ("semana", "mes"):
        raise HTTPException(
            status_code=400,
            detail="El parámetro 'agrupacion' debe ser exactamente 'semana' o 'mes'.",
        )

    import asyncio  # noqa: PLC0415
    loop = asyncio.get_event_loop()

    client = estado.client
    if client is None:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if api_key:
            client = Groq(api_key=api_key)
    modelo = estado.modelo() if hasattr(estado, "modelo") else (os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b")

    try:
        resultado = await loop.run_in_executor(
            None,
            lambda: social_listening.analizar_historico_perfil(
                url_perfil=target_url,
                agrupacion=target_agrupacion,
                groq_client=client,
                groq_model=modelo,
            ),
        )
        return resultado.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("Error en análisis histórico de perfil (%s): %s", target_url, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error en análisis histórico: {exc}") from exc


# ---------------------------------------------------------------------------
# Control y Auditoría de Consumo de Apify por Usuario
# ---------------------------------------------------------------------------

@app.get("/api/social/consumo")
def social_consumo(
    periodo: str = Query("mes", description="Periodo de auditoría: 'hoy', 'semana', 'mes', 'todo'"),
    vista: str = Query("mi", description="'mi' (consumo propio) o 'global' (solo admin)"),
    usuario_sesion: dict[str, Any] = Depends(auth.requiere("coordinador")),
) -> dict[str, Any]:
    """Devuelve el contador de posts y comentarios scrapeados de Apify por usuario."""
    username = str(usuario_sesion.get("usuario") or "anonimo")
    es_admin = usuario_sesion.get("rol") == "admin"
    ver_global = es_admin and (vista == "global")
    return social_listening.obtener_resumen_consumo(
        usuario=username,
        periodo=periodo,
        ver_global=ver_global,
    )




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
