"""Scraping con Apify para Social Listening: posts y comentarios de Facebook."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from .models import (
    ACTOR_POSTS,
    ACTOR_COMMENTS,
    COMMENTS_REFRESH_MINUTES,
    UNKNOWN_DATE_REFRESH_HOURS,
    UNKNOWN_DATE_REFRESH_TOP_N,
    ApifyScrapeError,
    _limpiar_url_facebook,
    _parse_datetime,
    _es_post_reciente,
    _esta_vencido,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scraping con Apify
# ---------------------------------------------------------------------------


def _dataset_id(run: Any) -> Optional[str]:
    if run is None:
        return None
    if hasattr(run, "default_dataset_id") and run.default_dataset_id:
        return str(run.default_dataset_id)
    if isinstance(run, dict):
        return run.get("defaultDatasetId") or run.get("default_dataset_id")
    return getattr(run, "default_dataset_id", None)


def _normalizar_comentario(item: dict, post_url_hint: str = "") -> Optional[dict]:
    """Normaliza un comentario conservando IDs y metadatos útiles para deduplicar."""
    texto = item.get("text") or item.get("commentText") or item.get("message") or ""
    texto = str(texto).strip()
    if len(texto) <= 2:
        return None

    autor_data = item.get("author") or {}
    autor_nombre: Optional[str] = None
    autor_url: Optional[str] = None

    if isinstance(autor_data, str):
        autor_nombre = autor_data
    elif isinstance(autor_data, dict):
        autor_nombre = autor_data.get("name") or item.get("profileName")
        autor_url = autor_data.get("url") or item.get("profileUrl")
    else:
        autor_nombre = item.get("profileName")
        autor_url = item.get("profileUrl")

    raw_likes = item.get("likesCount")
    if raw_likes is None:
        raw_likes = item.get("likes", 0)
    try:
        likes = int(raw_likes or 0)
    except (ValueError, TypeError):
        likes = 0

    post_url = (
        item.get("postUrl")
        or item.get("inputUrl")
        or item.get("facebookUrl")
        or item.get("post_url")
        or item.get("topLevelUrl")
        or post_url_hint
        or ""
    )

    comentario_id = (
        item.get("id")
        or item.get("commentId")
        or item.get("comment_id")
        or item.get("feedbackId")
        or None
    )
    comentario_url = item.get("commentUrl") or item.get("comment_url")
    if not comentario_url:
        posible_url = item.get("url")
        if posible_url and "comment" in str(posible_url).lower():
            comentario_url = posible_url

    fecha_comentario = (
        item.get("date")
        or item.get("time")
        or item.get("timestamp")
        or item.get("createdAt")
        or item.get("created_time")
        or ""
    )

    return {
        "texto": texto,
        "autor_nombre": autor_nombre or "Usuario de Facebook",
        "autor_url": autor_url,
        "comentario_url": comentario_url,
        "likes": likes,
        "post_url": _limpiar_url_facebook(str(post_url or "")),
        "comentario_id": str(comentario_id) if comentario_id else None,
        "fecha_comentario": str(fecha_comentario or ""),
    }


def _obtener_posts(client: Any, url: str, max_posts: int) -> tuple[list[dict], list[dict]]:
    """Obtiene posts recientes y top comments embebidos.

    Un fallo real lanza ApifyScrapeError; una respuesta válida sin posts devuelve listas vacías.
    """
    try:
        run = client.actor(ACTOR_POSTS).call(
            run_input={
                "startUrls": [{"url": url}],
                "resultsLimit": max_posts,
            }
        )
        ds_id = _dataset_id(run)
        if not ds_id:
            raise ApifyScrapeError("Apify no devolvió datasetId para posts.")

        items = list(client.dataset(ds_id).iterate_items())
        posts_meta: list[dict] = []
        top_comentarios: list[dict] = []

        for item in items:
            post_url = item.get("url") or item.get("postUrl") or item.get("topLevelUrl")
            if post_url:
                raw_likes = item.get("likes")
                if raw_likes is None:
                    raw_likes = item.get("likesCount", 0)
                try:
                    post_likes = int(raw_likes or 0)
                except (ValueError, TypeError):
                    post_likes = 0

                raw_page = item.get("page") if isinstance(item.get("page"), dict) else {}
                raw_user = item.get("user") if isinstance(item.get("user"), dict) else {}
                raw_author = item.get("author") if isinstance(item.get("author"), dict) else {}

                perfil_nom = (
                    item.get("pageName")
                    or item.get("pageTitle")
                    or item.get("profileName")
                    or raw_page.get("name")
                    or raw_user.get("name")
                    or raw_author.get("name")
                    or item.get("authorName")
                    or ""
                )
                perfil_u = (
                    item.get("pageUrl")
                    or item.get("profileUrl")
                    or raw_page.get("url")
                    or raw_user.get("url")
                    or raw_author.get("url")
                    or ""
                )

                posts_meta.append(
                    {
                        "url": str(post_url),
                        "post_id": str(item.get("postId") or item.get("id") or ""),
                        "texto": str(
                            item.get("text")
                            or item.get("message")
                            or item.get("description")
                            or ""
                        ),
                        "fecha": str(
                            item.get("time")
                            or item.get("date")
                            or item.get("timestamp")
                            or ""
                        ),
                        "likes": post_likes,
                        "perfil_nombre": str(perfil_nom or ""),
                        "perfil_url": str(perfil_u or ""),
                    }
                )

            for top in item.get("topComments", []) or []:
                norm = _normalizar_comentario(top, post_url_hint=str(post_url or ""))
                if norm:
                    top_comentarios.append(norm)

        return posts_meta, top_comentarios
    except ApifyScrapeError:
        raise
    except Exception as exc:
        raise ApifyScrapeError(f"Error obteniendo posts de Apify: {exc}") from exc


def _obtener_comentarios(client: Any, post_url: str, max_por_post: int) -> list[dict]:
    """Obtiene comentarios de UN post.

    Importante: un error técnico lanza excepción. Solo una ejecución válida con dataset
    vacío devuelve ``[]``. Así nunca se marca un post como completado tras un fallo.
    """
    if not post_url:
        return []

    try:
        run = client.actor(ACTOR_COMMENTS).call(
            run_input={
                "startUrls": [{"url": post_url}],
                "resultsLimit": max(max_por_post, 1),
            }
        )
        ds_id = _dataset_id(run)
        if not ds_id:
            raise ApifyScrapeError(f"Apify no devolvió datasetId para comentarios de {post_url}")

        items = list(client.dataset(ds_id).iterate_items())
        comentarios: list[dict] = []
        for item in items:
            norm = _normalizar_comentario(item, post_url_hint=post_url)
            if norm:
                comentarios.append(norm)
        return comentarios
    except ApifyScrapeError:
        raise
    except Exception as exc:
        raise ApifyScrapeError(
            f"Error obteniendo comentarios del post {post_url}: {exc}"
        ) from exc


def _debe_refrescar_post(
    post_meta: dict,
    estado: Optional[dict],
    max_comentarios: int,
    forzar_refresco: bool,
    posicion: int,
) -> bool:
    if forzar_refresco:
        return True
    if not estado or not estado.get("comentarios_scrapeados"):
        return True

    # El usuario pide más comentarios de los que alguna vez se adquirieron.
    if int(estado.get("max_comentarios_scrapeados") or 0) < max_comentarios:
        return True

    ultima_actualizacion = estado.get("comentarios_actualizados_en")
    fecha_post = post_meta.get("fecha") or estado.get("fecha_post")

    # Los posts recientes pueden seguir recibiendo comentarios.
    if _es_post_reciente(fecha_post):
        return _esta_vencido(
            ultima_actualizacion,
            timedelta(minutes=COMMENTS_REFRESH_MINUTES),
        )

    # Cuando Apify no aporta fecha, refrescar solo un pequeño número de los posts
    # más recientes y con una frecuencia mucho menor.
    if not _parse_datetime(fecha_post) and posicion < UNKNOWN_DATE_REFRESH_TOP_N:
        return _esta_vencido(
            ultima_actualizacion,
            timedelta(hours=UNKNOWN_DATE_REFRESH_HOURS),
        )

    return False
