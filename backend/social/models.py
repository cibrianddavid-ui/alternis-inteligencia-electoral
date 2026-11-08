"""Modelos, constantes, excepciones y helpers del sistema de Social Listening."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Se incrementa porque cambia la semántica: de sentimiento genérico a postura
# dirigida hacia el actor/perfil analizado con calibración especializada y sub-batches.
ANALISIS_VERSION = "stance_v4"
COMPARISON_VERSION = "comp_v2"
LLM_BATCH_SIZE = 15
THEME_SAMPLE_SIZE = 120

ACTOR_POSTS = "apify/facebook-posts-scraper"
ACTOR_COMMENTS = "apify/facebook-comments-scraper"

MAX_POSTS_DEFAULT = 20
MAX_COMENTARIOS_DEFAULT = 100

TONOS_VALIDOS = {"positivo", "neutro", "negativo"}

# Política de actualización incremental. Se puede ajustar por .env.
POST_REFRESH_DAYS = int(os.environ.get("SOCIAL_POST_REFRESH_DAYS", "7"))
COMMENTS_REFRESH_MINUTES = int(os.environ.get("SOCIAL_COMMENTS_REFRESH_MINUTES", "60"))
UNKNOWN_DATE_REFRESH_HOURS = int(os.environ.get("SOCIAL_UNKNOWN_DATE_REFRESH_HOURS", "6"))
UNKNOWN_DATE_REFRESH_TOP_N = int(os.environ.get("SOCIAL_UNKNOWN_DATE_REFRESH_TOP_N", "3"))


# ---------------------------------------------------------------------------
# Validación y normalización de URLs de Facebook
# ---------------------------------------------------------------------------

def _limpiar_url_facebook(url: str) -> str:
    """Limpia parámetros de tracking conocidos en URLs de Facebook preservando parámetros esenciales."""
    if not url:
        return ""
    try:
        url_s = str(url).strip()
        parsed = urllib.parse.urlparse(url_s)
        if not parsed.netloc:
            return url_s.rstrip("/")
        tracking_params = {
            "mibextid", "rdid", "ref", "fbclid", "__cft__", "__tn__",
            "notif_t", "notif_id", "checkpoint_src", "extid", "sfnsn"
        }
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered_query = [
            (k, v) for k, v in query_pairs
            if k.lower() not in tracking_params and not k.startswith("__")
        ]
        new_query = urllib.parse.urlencode(filtered_query)
        path = parsed.path.rstrip("/")
        return urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            path,
            parsed.params,
            new_query,
            ""
        ))
    except Exception:
        return str(url).strip().rstrip("/")


def _validar_urls_facebook(post_urls: list[str]) -> list[str]:
    """Valida y deduplica URLs de posts de Facebook normalizando slashes finales y limpiando tracking."""
    if not post_urls:
        raise ValueError("La lista de URLs de publicaciones no puede estar vacía.")

    validas: list[str] = []
    vistos: set[str] = set()
    patrones_fb = (
        "facebook.com",
        "fb.com",
        "fb.watch",
        "m.facebook.com",
        "web.facebook.com",
    )

    for u in post_urls:
        u_str = str(u or "").strip()
        if not u_str:
            continue
        u_lower = u_str.lower()
        if not any(p in u_lower for p in patrones_fb):
            raise ValueError(f"La URL '{u_str[:60]}' no parece ser una URL válida de Facebook.")
        u_limpia = _limpiar_url_facebook(u_str)
        if u_limpia not in vistos:
            vistos.add(u_limpia)
            validas.append(u_limpia)

    if not validas:
        raise ValueError("No se proporcionaron URLs válidas de Facebook.")
    if len(validas) > 20:
        raise ValueError("El número máximo de publicaciones por solicitud es 20.")

    return validas


# ---------------------------------------------------------------------------
# Modelos Pydantic — compatibilidad con main.py / frontend
# ---------------------------------------------------------------------------


class SolicitudSocial(BaseModel):
    url_perfil: str = Field(
        min_length=10,
        max_length=512,
        description="URL pública del perfil o página de Facebook a analizar.",
    )
    max_posts: int = Field(
        default=MAX_POSTS_DEFAULT,
        ge=1,
        le=50,
        description="Número máximo de posts a extraer (1–50).",
    )
    max_comentarios: int = Field(
        default=MAX_COMENTARIOS_DEFAULT,
        ge=1,
        le=500,
        description="Máximo de comentarios por post incluidos en el análisis (1–500).",
    )
    forzar_refresco: bool = Field(
        default=False,
        description="Si es True, fuerza re-scraping de comentarios y no reutiliza la caché.",
    )


class SolicitudPostsSocial(BaseModel):
    post_urls: list[str] = Field(
        min_length=1,
        max_length=20,
        description="Lista de URLs de publicaciones de Facebook a analizar (1–20).",
    )
    max_comentarios: int = Field(
        default=MAX_COMENTARIOS_DEFAULT,
        ge=1,
        le=500,
        description="Máximo de comentarios por post incluidos en el análisis (1–500).",
    )
    forzar_refresco: bool = Field(
        default=False,
        description="Si es True, fuerza re-scraping de comentarios en Apify y omite la caché.",
    )

    @field_validator("post_urls")
    @classmethod
    def validar_urls(cls, v: list[str]) -> list[str]:
        return _validar_urls_facebook(v)


class ComentarioClasificado(BaseModel):
    texto: str
    tono: str  # positivo | neutro | negativo
    autor_nombre: Optional[str] = None
    autor_url: Optional[str] = None
    comentario_url: Optional[str] = None
    likes: int = 0


class ResultadoSocial(BaseModel):
    url_perfil: str
    posts_analizados: int
    total_comentarios: int
    sentimiento: Dict[str, Any]
    distribucion: Dict[str, Any]
    ngramas: List[Dict[str, Any]]
    resumen: str
    metodo_resumen: str
    comentarios_clasificados: List[ComentarioClasificado] = []
    # Se conserva este nombre por compatibilidad. Semánticamente representa
    # balance neto de postura = %positivo - %negativo, no una encuesta.
    aprobacion_pct: float = 0.0
    error: Optional[str] = None


class ErrorPost(BaseModel):
    post_url: str
    error: str


class ResultadoPostIndividual(BaseModel):
    alias: str
    post_url: str
    post_id: Optional[str] = ""
    perfil_nombre: Optional[str] = "Facebook"
    perfil_url: Optional[str] = ""
    texto_post: str = ""
    fecha_post: Optional[str] = ""
    post_likes: int = 0
    total_comentarios: int = 0
    comentarios_clasificados: int = 0
    comentarios_no_clasificados: int = 0
    cobertura_pct: float = 0.0
    distribucion: Dict[str, int] = Field(default_factory=dict)
    porcentajes: Dict[str, float] = Field(default_factory=dict)
    balance_postura: float = 0.0
    temas: List[Dict[str, Any]] = Field(default_factory=list)
    resumen: str = ""
    comentarios_representativos: List[ComentarioClasificado] = Field(default_factory=list)


class ComparacionPosts(BaseModel):
    total_posts: int
    total_comentarios: int
    diferencias_sentimiento: List[Dict[str, Any]] = Field(default_factory=list)
    temas_compartidos: List[Dict[str, Any]] = Field(default_factory=list)
    temas_distintivos: List[Dict[str, Any]] = Field(default_factory=list)
    todos_los_temas: List[Dict[str, Any]] = Field(default_factory=list)
    resumen_comparativo: str = ""


class ResultadoPostsManual(BaseModel):
    posts: List[ResultadoPostIndividual] = Field(default_factory=list)
    posts_con_error: List[ErrorPost] = Field(default_factory=list)
    comparacion: Optional[ComparacionPosts] = None


# ---------------------------------------------------------------------------
# Excepciones internas
# ---------------------------------------------------------------------------


class SocialListeningError(RuntimeError):
    """Error controlado del pipeline de Social Listening."""


class ApifyScrapeError(SocialListeningError):
    """Fallo real de adquisición en Apify."""


# ---------------------------------------------------------------------------
# Helpers de fechas
# ---------------------------------------------------------------------------


def _ahora_dt() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(valor: Any) -> Optional[datetime]:
    """Convierte timestamps comunes de Apify/SQLite a datetime UTC si es posible."""
    if valor in (None, ""):
        return None

    if isinstance(valor, datetime):
        dt = valor
    elif isinstance(valor, (int, float)):
        try:
            # Soportar segundos y milisegundos.
            ts = float(valor)
            if ts > 10_000_000_000:
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            return None
    else:
        s = str(valor).strip()
        if not s:
            return None
        if s.isdigit():
            return _parse_datetime(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            # Formatos frecuentes adicionales.
            formatos = (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d",
            )
            dt = None
            for fmt in formatos:
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _es_post_reciente(fecha_post: Any) -> bool:
    dt = _parse_datetime(fecha_post)
    if dt is None:
        return False
    return dt >= _ahora_dt() - timedelta(days=POST_REFRESH_DAYS)


def _esta_vencido(valor_fecha: Any, delta: timedelta) -> bool:
    dt = _parse_datetime(valor_fecha)
    if dt is None:
        return True
    return dt <= _ahora_dt() - delta
