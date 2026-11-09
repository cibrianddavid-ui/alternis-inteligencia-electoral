"""Persistencia SQLite para Social Listening: posts, comentarios, caché."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
import os
import re
from collections.abc import Iterable
from typing import Any, Optional

from .. import basedatos
from .models import (
    ANALISIS_VERSION,
    COMPARISON_VERSION,
    TONOS_VALIDOS,
    ResultadoSocial,
    ResultadoPostsManual,
    _limpiar_url_facebook,
    _parse_datetime,
    _es_post_reciente,
    _esta_vencido,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Esquema / migraciones no destructivas
# ---------------------------------------------------------------------------


def _columnas_tabla(conn: Any, tabla: str) -> set[str]:
    filas = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
    columnas: set[str] = set()
    for f in filas:
        try:
            columnas.add(str(f["name"]))
        except (TypeError, KeyError):
            columnas.add(str(f[1]))
    return columnas


def _agregar_columna_si_falta(conn: Any, tabla: str, columna: str, definicion: str) -> None:
    if columna not in _columnas_tabla(conn, tabla):
        log.info("Migración social: agregando %s.%s", tabla, columna)
        conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")


def asegurar_esquema_social() -> None:
    """Crea tablas si faltan y añade columnas nuevas sin ocultar errores reales."""
    with basedatos.transaccion() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_posts (
                post_url TEXT PRIMARY KEY,
                url_perfil TEXT NOT NULL,
                comentarios_scrapeados INTEGER NOT NULL DEFAULT 0,
                creado TEXT,
                post_id TEXT,
                texto_post TEXT,
                fecha_post TEXT,
                post_likes INTEGER NOT NULL DEFAULT 0,
                actualizado TEXT,
                comentarios_actualizados_en TEXT,
                max_comentarios_scrapeados INTEGER NOT NULL DEFAULT 0,
                perfil_nombre TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_comentarios (
                id TEXT PRIMARY KEY,
                url_perfil TEXT NOT NULL,
                post_url TEXT,
                texto TEXT NOT NULL,
                autor_nombre TEXT,
                autor_url TEXT,
                comentario_url TEXT,
                likes INTEGER NOT NULL DEFAULT 0,
                tono TEXT,
                creado TEXT,
                analizado INTEGER NOT NULL DEFAULT 0,
                analisis_version TEXT,
                analizado_en TEXT,
                fecha_comentario TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_cache (
                url_perfil TEXT PRIMARY KEY,
                datos_json TEXT NOT NULL,
                actualizado TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_apify_consumo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                usuario TEXT NOT NULL DEFAULT 'anonimo',
                tipo TEXT NOT NULL,
                actor TEXT NOT NULL,
                url_objetivo TEXT NOT NULL,
                posts_scrapeados INTEGER NOT NULL DEFAULT 0,
                comentarios_scrapeados INTEGER NOT NULL DEFAULT 0,
                run_id TEXT,
                detalles_json TEXT
            )
            """
        )

        # Compatibilidad con instalaciones existentes.
        for columna, definicion in (
            ("post_id", "TEXT"),
            ("texto_post", "TEXT"),
            ("fecha_post", "TEXT"),
            ("post_likes", "INTEGER NOT NULL DEFAULT 0"),
            ("actualizado", "TEXT"),
            ("comentarios_actualizados_en", "TEXT"),
            ("max_comentarios_scrapeados", "INTEGER NOT NULL DEFAULT 0"),
            ("perfil_nombre", "TEXT"),
            ("canonical_url", "TEXT"),
            ("comments_count", "INTEGER NOT NULL DEFAULT 0"),
        ):
            _agregar_columna_si_falta(conn, "social_posts", columna, definicion)

        for columna, definicion in (
            ("analizado", "INTEGER NOT NULL DEFAULT 0"),
            ("analisis_version", "TEXT"),
            ("analizado_en", "TEXT"),
            ("fecha_comentario", "TEXT"),
        ):
            _agregar_columna_si_falta(conn, "social_comentarios", columna, definicion)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_posts_perfil ON social_posts(url_perfil)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_posts_post_id ON social_posts(post_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_posts_canonical ON social_posts(canonical_url)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_comentarios_perfil ON social_comentarios(url_perfil)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_comentarios_post ON social_comentarios(post_url)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_comentarios_analisis ON social_comentarios(analizado, analisis_version)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_apify_user_time ON social_apify_consumo(usuario, timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_apify_timestamp ON social_apify_consumo(timestamp)"
        )
        _agregar_columna_si_falta(conn, "social_apify_consumo", "usuario", "TEXT NOT NULL DEFAULT 'anonimo'")
        conn.execute(
            "UPDATE social_posts SET canonical_url = post_url WHERE canonical_url IS NULL OR canonical_url = ''"
        )


# ---------------------------------------------------------------------------
# Deduplicación
# ---------------------------------------------------------------------------


def _texto_normalizado_para_hash(texto: str) -> str:
    return " ".join((texto or "").strip().lower().split())


def _clave_comentario(c: dict) -> str:
    """Clave estable: ID Facebook > URL > hash(post+autor+fecha+texto completo)."""
    if c.get("comentario_id"):
        return "id::" + str(c["comentario_id"])
    if c.get("comentario_url"):
        return "url::" + str(c["comentario_url"])

    autor = c.get("autor_url") or c.get("autor_nombre") or ""
    key_str = "|".join(
        [
            str(c.get("post_url") or ""),
            str(autor),
            str(c.get("fecha_comentario") or ""),
            _texto_normalizado_para_hash(str(c.get("texto") or "")),
        ]
    )
    return "hash::" + hashlib.sha256(key_str.encode("utf-8")).hexdigest()


def _deduplicar_comentarios(comentarios: Iterable[dict]) -> list[dict]:
    vistos: set[str] = set()
    unicos: list[dict] = []
    for c in comentarios:
        clave = _clave_comentario(c)
        if clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(c)
    return unicos


# ---------------------------------------------------------------------------
# Persistencia — posts
# ---------------------------------------------------------------------------


def _inferir_nombre_perfil(perfil_url: str, perfil_nombre: str = "") -> str:
    """Extrae un nombre legible de actor político o página de Facebook a partir de su URL o perfil."""
    p_nom = (perfil_nombre or "").strip()
    if p_nom and p_nom.lower() not in ("facebook", "unknown", "perfil"):
        return p_nom
    if not perfil_url:
        return "Facebook"

    url_limpia = perfil_url.split("?")[0].rstrip("/")
    partes = [p for p in url_limpia.split("/") if p and p not in ("https:", "http:", "www.facebook.com", "facebook.com", "fb.com", "m.facebook.com")]
    if not partes:
        return "Facebook"

    segmento = partes[-1]
    if segmento.isdigit() or segmento.lower() in ("posts", "reel", "reels", "watch", "videos", "photos"):
        if len(partes) >= 2 and partes[0].lower() not in ("reel", "reels", "watch"):
            segmento = partes[0]
        else:
            return "Facebook"

    limpio = re.sub(r"([a-z])([A-Z])", r"\1 \2", segmento)
    limpio = limpio.replace(".", " ").replace("-", " ").replace("_", " ").strip()
    return limpio if limpio else "Facebook"


def db_guardar_posts(url_perfil: str, posts_meta: list[dict]) -> set[str]:
    """Guarda/actualiza posts y devuelve URLs cuyo texto cambió.

    Si cambia ``texto_post`` se invalida después el análisis de sus comentarios porque
    la postura depende del contexto del post.
    """
    if not posts_meta:
        return set()

    ahora_str = basedatos.ahora()
    contexto_cambiado: set[str] = set()

    with basedatos.transaccion() as conn:
        for p in posts_meta:
            post_url = str(p.get("url") or "").strip().rstrip("/")
            if not post_url:
                continue

            existente = conn.execute(
                "SELECT texto_post FROM social_posts WHERE post_url = ? OR post_url = ?",
                (post_url, post_url + "/"),
            ).fetchone()
            texto_nuevo = str(p.get("texto") or "")
            texto_anterior = ""
            if existente is not None:
                try:
                    texto_anterior = str(existente["texto_post"] or "")
                except (TypeError, KeyError):
                    texto_anterior = str(existente[0] or "")

            if texto_anterior and texto_nuevo and texto_anterior != texto_nuevo:
                contexto_cambiado.add(post_url)

            p_perfil_url = str(p.get("perfil_url") or url_perfil or "").strip()
            p_perfil_nombre = _inferir_nombre_perfil(p_perfil_url, str(p.get("perfil_nombre") or ""))
            canonical_url = str(p.get("canonical_url") or post_url).strip().rstrip("/")
            p_comments_count = int(p.get("comments_count") or p.get("comments") or 0)

            conn.execute(
                """
                INSERT OR IGNORE INTO social_posts
                (post_url, url_perfil, perfil_nombre, comentarios_scrapeados, creado,
                 max_comentarios_scrapeados, canonical_url, comments_count)
                VALUES (?, ?, ?, 0, ?, 0, ?, ?)
                """,
                (post_url, p_perfil_url, p_perfil_nombre, ahora_str, canonical_url, p_comments_count),
            )
            conn.execute(
                """
                UPDATE social_posts
                SET url_perfil = CASE WHEN ? != '' THEN ? ELSE url_perfil END,
                    perfil_nombre = CASE WHEN ? != '' THEN ? ELSE perfil_nombre END,
                    post_id = CASE WHEN ? != '' THEN ? ELSE post_id END,
                    texto_post = CASE WHEN ? != '' THEN ? ELSE texto_post END,
                    fecha_post = CASE WHEN ? != '' THEN ? ELSE fecha_post END,
                    canonical_url = CASE WHEN ? != '' THEN ? ELSE canonical_url END,
                    comments_count = CASE WHEN ? > 0 THEN ? ELSE comments_count END,
                    post_likes = ?,
                    actualizado = ?
                WHERE post_url = ? OR post_url = ?
                """,
                (
                    p_perfil_url,
                    p_perfil_url,
                    p_perfil_nombre,
                    p_perfil_nombre,
                    str(p.get("post_id") or ""),
                    str(p.get("post_id") or ""),
                    texto_nuevo,
                    texto_nuevo,
                    str(p.get("fecha") or ""),
                    str(p.get("fecha") or ""),
                    canonical_url,
                    canonical_url,
                    p_comments_count,
                    p_comments_count,
                    int(p.get("likes") or 0),
                    ahora_str,
                    post_url,
                    post_url + "/",
                ),
            )

    return contexto_cambiado


def db_invalidar_analisis_posts(post_urls: Iterable[str]) -> None:
    urls = [u for u in post_urls if u]
    if not urls:
        return
    with basedatos.transaccion() as conn:
        for url in urls:
            u_clean = url.strip().rstrip("/")
            conn.execute(
                """
                UPDATE social_comentarios
                SET analizado = 0,
                    analisis_version = NULL,
                    analizado_en = NULL,
                    tono = NULL
                WHERE post_url = ? OR post_url = ?
                """,
                (u_clean, u_clean + "/"),
            )


def db_obtener_estado_posts(url_perfil: str, post_urls: list[str]) -> dict[str, dict]:
    if not post_urls:
        return {}
    urls_expandidas = set()
    for u in post_urls:
        u_clean = str(u or "").strip().rstrip("/")
        if u_clean:
            urls_expandidas.add(u_clean)
            urls_expandidas.add(u_clean + "/")
            u_limpia = _limpiar_url_facebook(u_clean)
            if u_limpia:
                urls_expandidas.add(u_limpia)
                urls_expandidas.add(u_limpia + "/")
    url_list = list(urls_expandidas)
    if not url_list:
        return {}
    placeholders = ",".join("?" for _ in url_list)
    with basedatos.conectar() as conn:
        if url_perfil and url_perfil.strip():
            filas = conn.execute(
                f"""
                SELECT post_url, canonical_url, comentarios_scrapeados, comentarios_actualizados_en,
                       max_comentarios_scrapeados, fecha_post, texto_post,
                       COALESCE(comments_count, 0) AS comments_count,
                       (SELECT COUNT(*) FROM social_comentarios WHERE post_url = social_posts.post_url OR post_url = social_posts.canonical_url) AS total_comentarios_en_bd
                FROM social_posts
                WHERE url_perfil = ? AND (post_url IN ({placeholders}) OR canonical_url IN ({placeholders}))
                """,
                [url_perfil.strip(), *url_list, *url_list],
            ).fetchall()
        else:
            filas = conn.execute(
                f"""
                SELECT post_url, canonical_url, comentarios_scrapeados, comentarios_actualizados_en,
                       max_comentarios_scrapeados, fecha_post, texto_post,
                       COALESCE(comments_count, 0) AS comments_count,
                       (SELECT COUNT(*) FROM social_comentarios WHERE post_url = social_posts.post_url OR post_url = social_posts.canonical_url) AS total_comentarios_en_bd
                FROM social_posts
                WHERE post_url IN ({placeholders}) OR canonical_url IN ({placeholders})
                """,
                [*url_list, *url_list],
            ).fetchall()

    estados: dict[str, dict] = {}
    for f in filas:
        p_url_val = str(f["post_url"])
        datos_estado = {
            "comentarios_scrapeados": int(f["comentarios_scrapeados"] or 0),
            "comentarios_actualizados_en": f["comentarios_actualizados_en"],
            "max_comentarios_scrapeados": int(f["max_comentarios_scrapeados"] or 0),
            "fecha_post": f["fecha_post"],
            "texto_post": f["texto_post"] or "",
            "comments_count": int(f["comments_count"] or 0),
            "total_comentarios_en_bd": int(f["total_comentarios_en_bd"] or 0),
        }
        estados[p_url_val] = datos_estado
        estados[p_url_val.rstrip("/")] = datos_estado
        estados[p_url_val.rstrip("/") + "/"] = datos_estado
        if f["canonical_url"]:
            c_val = str(f["canonical_url"])
            estados[c_val] = datos_estado
            estados[c_val.rstrip("/")] = datos_estado
            estados[c_val.rstrip("/") + "/"] = datos_estado
    return estados


def db_marcar_post_refrescado(post_url: str, max_comentarios: int) -> None:
    """Marca refresh correcto. Debe llamarse SOLO tras una respuesta válida de Apify."""
    u_sin = post_url.strip().rstrip("/")
    u_con = u_sin + "/"
    u_limpia = _limpiar_url_facebook(u_sin)
    candidatos = list(dict.fromkeys([u_sin, u_con, u_limpia, u_limpia + "/"]))
    placeholders = ",".join("?" for _ in candidatos)
    ahora_str = basedatos.ahora()
    with basedatos.transaccion() as conn:
        conn.execute(
            f"""
            UPDATE social_posts
            SET comentarios_scrapeados = 1,
                comentarios_actualizados_en = ?,
                max_comentarios_scrapeados = MAX(
                    COALESCE(max_comentarios_scrapeados, 0), ?
                ),
                actualizado = ?
            WHERE post_url IN ({placeholders}) OR canonical_url IN ({placeholders})
            """,
            [ahora_str, int(max_comentarios), ahora_str, *candidatos, *candidatos],
        )


def db_obtener_posts_meta(url_perfil: str, limite: int) -> list[dict]:
    """Posts almacenados en BD para el perfil dado, tolerando variaciones de URL y slashes."""
    u_sin = url_perfil.strip().rstrip("/")
    u_con = u_sin + "/"
    u_limpia = _limpiar_url_facebook(u_sin)
    candidatos = list(dict.fromkeys([u_sin, u_con, u_limpia, u_limpia + "/"]))
    placeholders = ",".join("?" for _ in candidatos)
    with basedatos.conectar() as conn:
        filas = conn.execute(
            f"""
            SELECT post_url, post_id, texto_post, fecha_post, post_likes, perfil_nombre, url_perfil,
                   actualizado, creado, COALESCE(comments_count, 0) AS comments_count
            FROM social_posts
            WHERE url_perfil IN ({placeholders})
            ORDER BY COALESCE(fecha_post, '') DESC, rowid DESC
            LIMIT ?
            """,
            [*candidatos, limite],
        ).fetchall()

    return [
        {
            "url": f["post_url"],
            "post_id": f["post_id"] or "",
            "texto": f["texto_post"] or "",
            "fecha": f["fecha_post"] or "",
            "likes": int(f["post_likes"] or 0),
            "perfil_nombre": _inferir_nombre_perfil(f["url_perfil"] or url_perfil, f["perfil_nombre"] or "Facebook"),
            "perfil_url": f["url_perfil"] or url_perfil,
            "url_perfil": f["url_perfil"] or url_perfil,
            "actualizado": f["actualizado"] or "",
            "creado": f["creado"] or "",
            "comments_count": int(f["comments_count"] or 0),
        }
        for f in filas
    ]


def db_obtener_post_meta_por_url(post_url: str) -> Optional[dict]:
    """Carga metadatos de un post específico desde la BD tolerando slashes finales, tracking params y alias canónicos."""
    if not post_url:
        return None
    u_raw = post_url.strip()
    u_sin = u_raw.rstrip("/")
    u_con = u_sin + "/"
    u_limpia = _limpiar_url_facebook(u_raw)
    u_limpia_sin = u_limpia.rstrip("/")
    u_limpia_con = u_limpia_sin + "/"
    candidatos = list(dict.fromkeys([u_raw, u_sin, u_con, u_limpia, u_limpia_sin, u_limpia_con]))

    try:
        with basedatos.conectar() as conn:
            placeholders = ",".join("?" for _ in candidatos)
            f = conn.execute(
                f"""
                SELECT post_url, url_perfil, perfil_nombre, post_id, texto_post,
                       fecha_post, post_likes, comentarios_scrapeados,
                       comentarios_actualizados_en, max_comentarios_scrapeados,
                       canonical_url
                FROM social_posts
                WHERE post_url IN ({placeholders}) OR canonical_url IN ({placeholders})
                ORDER BY CASE WHEN texto_post IS NOT NULL AND length(texto_post) > 0 THEN 0 ELSE 1 END,
                         CASE WHEN canonical_url IS NOT NULL AND canonical_url != '' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                candidatos * 2,
            ).fetchone()

            if f is None:
                return None

            canon = f["canonical_url"] or f["post_url"]
            if canon and canon != f["post_url"]:
                f_canon = conn.execute(
                    """
                    SELECT post_url, url_perfil, perfil_nombre, post_id, texto_post,
                           fecha_post, post_likes, comentarios_scrapeados,
                           comentarios_actualizados_en, max_comentarios_scrapeados,
                           canonical_url
                    FROM social_posts
                    WHERE post_url = ? OR post_url = ?
                    ORDER BY CASE WHEN texto_post IS NOT NULL AND length(texto_post) > 0 THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (canon.rstrip("/"), canon.rstrip("/") + "/"),
                ).fetchone()
                if f_canon is not None and (f_canon["texto_post"] or f_canon["post_likes"]):
                    f = f_canon

            p_nom = _inferir_nombre_perfil(f["url_perfil"] or "", f["perfil_nombre"] or "Facebook")
            return {
                "url": canon or f["post_url"],
                "canonical_url": canon or f["post_url"],
                "requested_url": u_raw,
                "url_perfil": f["url_perfil"] or "",
                "perfil_url": f["url_perfil"] or "",
                "perfil_nombre": p_nom,
                "post_id": f["post_id"] or "",
                "texto": f["texto_post"] or "",
                "fecha": f["fecha_post"] or "",
                "likes": int(f["post_likes"] or 0),
                "comentarios_scrapeados": int(f["comentarios_scrapeados"] or 0),
                "comentarios_actualizados_en": f["comentarios_actualizados_en"],
                "max_comentarios_scrapeados": int(f["max_comentarios_scrapeados"] or 0),
            }
    except Exception as exc:
        log.warning("Error consultando metadatos de post %s en BD: %s", post_url, exc)
        return None


# ---------------------------------------------------------------------------
# Persistencia — comentarios
# ---------------------------------------------------------------------------


def db_guardar_comentarios(
    url_perfil: str,
    comentarios: list[dict],
    post_url_defecto: str = "",
) -> dict[str, int]:
    """Persiste comentarios antes del LLM.

    Si cambia el texto de un comentario existente, invalida su análisis anterior.
    Devuelve estadísticas de inserción/actualización.
    """
    if not comentarios:
        return {"insertados": 0, "actualizados": 0}

    ahora_str = basedatos.ahora()
    insertados = 0
    actualizados = 0

    with basedatos.transaccion() as conn:
        for c in comentarios:
            cid = _clave_comentario(c)
            post_url = str(c.get("post_url") or post_url_defecto or "").strip().rstrip("/")
            texto = str(c.get("texto") or "").strip()
            if not texto:
                continue

            existente = conn.execute(
                "SELECT texto FROM social_comentarios WHERE id = ?",
                (cid,),
            ).fetchone()

            if existente is None:
                conn.execute(
                    """
                    INSERT INTO social_comentarios
                    (id, url_perfil, post_url, texto, autor_nombre, autor_url,
                     comentario_url, likes, tono, creado, analizado,
                     analisis_version, analizado_en, fecha_comentario)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, NULL, NULL, ?)
                    """,
                    (
                        cid,
                        url_perfil,
                        post_url,
                        texto,
                        c.get("autor_nombre"),
                        c.get("autor_url"),
                        c.get("comentario_url"),
                        int(c.get("likes") or 0),
                        ahora_str,
                        str(c.get("fecha_comentario") or ""),
                    ),
                )
                insertados += 1
                continue

            texto_anterior = str(existente["texto"] or "")
            texto_cambio = texto_anterior != texto

            conn.execute(
                """
                UPDATE social_comentarios
                SET url_perfil = ?,
                    post_url = CASE WHEN ? != '' THEN ? ELSE post_url END,
                    texto = ?,
                    autor_nombre = COALESCE(?, autor_nombre),
                    autor_url = COALESCE(?, autor_url),
                    comentario_url = COALESCE(?, comentario_url),
                    likes = ?,
                    fecha_comentario = CASE WHEN ? != '' THEN ? ELSE fecha_comentario END,
                    tono = CASE WHEN ? THEN NULL ELSE tono END,
                    analizado = CASE WHEN ? THEN 0 ELSE analizado END,
                    analisis_version = CASE WHEN ? THEN NULL ELSE analisis_version END,
                    analizado_en = CASE WHEN ? THEN NULL ELSE analizado_en END
                WHERE id = ?
                """,
                (
                    url_perfil,
                    post_url,
                    post_url,
                    texto,
                    c.get("autor_nombre"),
                    c.get("autor_url"),
                    c.get("comentario_url"),
                    int(c.get("likes") or 0),
                    str(c.get("fecha_comentario") or ""),
                    str(c.get("fecha_comentario") or ""),
                    int(texto_cambio),
                    int(texto_cambio),
                    int(texto_cambio),
                    int(texto_cambio),
                    cid,
                ),
            )
            actualizados += 1

    return {"insertados": insertados, "actualizados": actualizados}


def db_actualizar_tono(comentario_id: str, tono: str) -> None:
    """Actualiza tono y marca analizado en BD para un comentario."""
    if tono not in TONOS_VALIDOS:
        return
    with basedatos.transaccion() as conn:
        conn.execute(
            """
            UPDATE social_comentarios
            SET tono = ?, analizado = 1, analisis_version = ?, analizado_en = ?
            WHERE id = ?
            """,
            (tono, ANALISIS_VERSION, basedatos.ahora(), comentario_id),
        )


def db_actualizar_tonos_batch(resultados: Iterable[tuple[str, str]]) -> None:
    """Actualiza tono de múltiples comentarios en una sola transacción."""
    datos = [(cid, tono) for cid, tono in resultados if cid and tono in TONOS_VALIDOS]
    if not datos:
        return

    ahora_str = basedatos.ahora()
    with basedatos.transaccion() as conn:
        conn.executemany(
            """
            UPDATE social_comentarios
            SET tono = ?, analizado = 1, analisis_version = ?, analizado_en = ?
            WHERE id = ?
            """,
            [(tono, ANALISIS_VERSION, ahora_str, cid) for cid, tono in datos],
        )


def db_obtener_comentarios(
    url_perfil: Optional[str] = None,
    post_urls: Optional[list[str]] = None,
    limite_por_post: Optional[int] = None,
) -> list[dict]:
    """Carga comentarios de BD, con límite POR POST y sin sesgar por likes.

    El límite se aplica por ``rowid DESC`` para favorecer comentarios adquiridos más
    recientemente. Los likes solo se usan después para seleccionar comentarios destacados.
    """
    condiciones: list[str] = []
    params: list[Any] = []

    if url_perfil:
        condiciones.append("url_perfil = ?")
        params.append(url_perfil)

    if post_urls:
        urls_expandidas = set()
        for u in post_urls:
            u_clean = str(u or "").strip().rstrip("/")
            if u_clean:
                urls_expandidas.add(u_clean)
                urls_expandidas.add(u_clean + "/")
                u_limpia = _limpiar_url_facebook(u_clean)
                if u_limpia:
                    urls_expandidas.add(u_limpia)
                    urls_expandidas.add(u_limpia + "/")

        try:
            with basedatos.conectar() as conn:
                placeholders = ",".join("?" for _ in urls_expandidas)
                filas_alias = conn.execute(
                    f"""
                    SELECT post_url, canonical_url, post_id
                    FROM social_posts
                    WHERE post_url IN ({placeholders})
                       OR canonical_url IN ({placeholders})
                    """,
                    list(urls_expandidas) * 2,
                ).fetchall()

                post_ids = set()
                for f in filas_alias:
                    if f["canonical_url"]:
                        c_clean = str(f["canonical_url"]).strip().rstrip("/")
                        urls_expandidas.add(c_clean)
                        urls_expandidas.add(c_clean + "/")
                    if f["post_url"]:
                        p_clean = str(f["post_url"]).strip().rstrip("/")
                        urls_expandidas.add(p_clean)
                        urls_expandidas.add(p_clean + "/")
                    if f["post_id"]:
                        post_ids.add(str(f["post_id"]))

                if post_ids:
                    id_placeholders = ",".join("?" for _ in post_ids)
                    filas_por_id = conn.execute(
                        f"""
                        SELECT post_url, canonical_url
                        FROM social_posts
                        WHERE post_id IN ({id_placeholders})
                        """,
                        list(post_ids),
                    ).fetchall()
                    for f in filas_por_id:
                        if f["canonical_url"]:
                            c_clean = str(f["canonical_url"]).strip().rstrip("/")
                            urls_expandidas.add(c_clean)
                            urls_expandidas.add(c_clean + "/")
                        if f["post_url"]:
                            p_clean = str(f["post_url"]).strip().rstrip("/")
                            urls_expandidas.add(p_clean)
                            urls_expandidas.add(p_clean + "/")
        except Exception as exc:
            log.warning("No se pudieron expandir alias de URLs para comentarios: %s", exc)

        url_list = list(urls_expandidas)
        if url_list:
            placeholders = ",".join("?" for _ in url_list)
            condiciones.append(f"post_url IN ({placeholders})")
            params.extend(url_list)

    where = " AND ".join(condiciones) if condiciones else "1=1"

    with basedatos.conectar() as conn:
        if limite_por_post is not None:
            filas = conn.execute(
                f"""
                WITH ranked AS (
                    SELECT id, texto, autor_nombre, autor_url, comentario_url,
                           likes, tono, post_url, analizado, analisis_version,
                           fecha_comentario, rowid AS rid,
                           ROW_NUMBER() OVER (
                               PARTITION BY post_url ORDER BY rowid DESC
                           ) AS rn
                    FROM social_comentarios
                    WHERE {where}
                )
                SELECT id, texto, autor_nombre, autor_url, comentario_url,
                       likes, tono, post_url, analizado, analisis_version,
                       fecha_comentario, rid
                FROM ranked
                WHERE rn <= ?
                ORDER BY post_url, rid DESC
                """,
                [*params, int(limite_por_post)],
            ).fetchall()
        else:
            filas = conn.execute(
                f"""
                SELECT id, texto, autor_nombre, autor_url, comentario_url,
                       likes, tono, post_url, analizado, analisis_version,
                       fecha_comentario, rowid AS rid
                FROM social_comentarios
                WHERE {where}
                ORDER BY post_url, rowid DESC
                """,
                params,
            ).fetchall()

    return [
        {
            "id": f["id"],
            "texto": f["texto"],
            "autor_nombre": f["autor_nombre"],
            "autor_url": f["autor_url"],
            "comentario_url": f["comentario_url"],
            "likes": int(f["likes"] or 0),
            "tono": f["tono"],
            "post_url": f["post_url"] or "",
            "analizado": int(f["analizado"] or 0),
            "analisis_version": f["analisis_version"],
            "fecha_comentario": f["fecha_comentario"],
            "rid": int(f["rid"] or 0),
        }
        for f in filas
    ]


# ---------------------------------------------------------------------------
# Caché ligada a la huella real del dataset
# ---------------------------------------------------------------------------


def _fingerprint_dataset(comentarios: list[dict], post_texto_map: dict[str, str]) -> str:
    """Huella determinista de los datos que afectan el resultado mostrado."""
    piezas: list[str] = []
    for post_url in sorted(post_texto_map):
        texto_hash = hashlib.sha256(post_texto_map[post_url].encode("utf-8")).hexdigest()[:12]
        piezas.append(f"P|{post_url}|{texto_hash}")

    for c in sorted(comentarios, key=lambda x: str(x.get("id") or "")):
        piezas.append(
            "C|{}|{}|{}|{}".format(
                c.get("id") or "",
                c.get("tono") or "",
                int(c.get("likes") or 0),
                c.get("post_url") or "",
            )
        )

    raw = "\n".join(piezas)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _cache_key(
    url_perfil: str,
    max_posts: int,
    max_comentarios: int,
    fingerprint: str,
) -> str:
    return (
        f"{url_perfil.strip()}::posts_{max_posts}::comments_{max_comentarios}"
        f"::v_{ANALISIS_VERSION}::data_{fingerprint}"
    )


def db_obtener_cache(
    url_perfil: str,
    max_posts: int,
    max_comentarios: int,
    fingerprint: str,
) -> Optional[ResultadoSocial]:
    """La caché es opcional: un fallo de caché no debe romper el pipeline."""
    ckey = _cache_key(url_perfil, max_posts, max_comentarios, fingerprint)
    try:
        with basedatos.conectar() as conn:
            fila = conn.execute(
                "SELECT datos_json FROM social_cache WHERE url_perfil = ?",
                (ckey,),
            ).fetchone()
        if fila and fila["datos_json"]:
            return ResultadoSocial(**json.loads(fila["datos_json"]))
    except Exception as exc:
        log.warning("No fue posible leer la caché social: %s", exc)
    return None


def db_guardar_cache(
    url_perfil: str,
    resultado: ResultadoSocial,
    max_posts: int,
    max_comentarios: int,
    fingerprint: str,
) -> None:
    ckey = _cache_key(url_perfil, max_posts, max_comentarios, fingerprint)
    try:
        with basedatos.transaccion() as conn:
            conn.execute(
                """
                INSERT INTO social_cache (url_perfil, datos_json, actualizado)
                VALUES (?, ?, ?)
                ON CONFLICT(url_perfil) DO UPDATE SET
                    datos_json = excluded.datos_json,
                    actualizado = excluded.actualizado
                """,
                (ckey, resultado.model_dump_json(), basedatos.ahora()),
            )
    except Exception as exc:
        # El resultado sigue siendo válido aunque la caché falle.
        log.warning("No fue posible guardar la caché social: %s", exc)


def _cache_key_manual(
    sorted_post_urls: list[str],
    max_comentarios: int,
    fingerprint: str,
) -> str:
    urls_str = ",".join(sorted_post_urls)
    if len(urls_str) > 80:
        urls_str = hashlib.sha256(urls_str.encode("utf-8")).hexdigest()[:24]
    return (
        f"manual_posts::{urls_str}::comments_{max_comentarios}"
        f"::v_{ANALISIS_VERSION}_{COMPARISON_VERSION}::data_{fingerprint}"
    )


def db_obtener_cache_manual(
    sorted_post_urls: list[str],
    max_comentarios: int,
    fingerprint: str,
) -> Optional[ResultadoPostsManual]:
    """Obtiene el resultado completo de análisis manual desde la caché en BD."""
    ckey = _cache_key_manual(sorted_post_urls, max_comentarios, fingerprint)
    try:
        with basedatos.conectar() as conn:
            fila = conn.execute(
                "SELECT datos_json FROM social_cache WHERE url_perfil = ?",
                (ckey,),
            ).fetchone()
        if fila and fila["datos_json"]:
            return ResultadoPostsManual(**json.loads(fila["datos_json"]))
    except Exception as exc:
        log.warning("No fue posible leer la caché de publicaciones manuales: %s", exc)
    return None


def db_guardar_cache_manual(
    sorted_post_urls: list[str],
    max_comentarios: int,
    fingerprint: str,
    resultado: ResultadoPostsManual,
) -> None:
    """Guarda el resultado de análisis manual en la caché de BD."""
    ckey = _cache_key_manual(sorted_post_urls, max_comentarios, fingerprint)
    try:
        with basedatos.transaccion() as conn:
            conn.execute(
                """
                INSERT INTO social_cache (url_perfil, datos_json, actualizado)
                VALUES (?, ?, ?)
                ON CONFLICT(url_perfil) DO UPDATE SET
                    datos_json = excluded.datos_json,
                    actualizado = excluded.actualizado
                """,
                (ckey, resultado.model_dump_json(), basedatos.ahora()),
            )
    except Exception as exc:
        log.warning("No fue posible guardar la caché de publicaciones manuales: %s", exc)


# ---------------------------------------------------------------------------
# Control y Auditoría de Consumo de Apify
# ---------------------------------------------------------------------------


def db_registrar_consumo_apify(
    usuario: str,
    tipo: str,
    actor: str,
    url_objetivo: str,
    posts_scrapeados: int = 0,
    comentarios_scrapeados: int = 0,
    run_id: Optional[str] = None,
    detalles: Optional[dict[str, Any]] = None,
) -> None:
    """Registra una llamada real a Apify para control de consumo por usuario."""
    asegurar_esquema_social()
    ahora_iso = datetime.now(timezone.utc).isoformat()
    detalles_str = json.dumps(detalles, ensure_ascii=False) if detalles else None
    user_clean = (usuario or "").strip() or "anonimo"

    try:
        with basedatos.transaccion() as conn:
            conn.execute(
                """
                INSERT INTO social_apify_consumo (
                    timestamp, usuario, tipo, actor, url_objetivo,
                    posts_scrapeados, comentarios_scrapeados, run_id, detalles_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ahora_iso,
                    user_clean,
                    tipo,
                    actor,
                    url_objetivo,
                    int(posts_scrapeados or 0),
                    int(comentarios_scrapeados or 0),
                    run_id,
                    detalles_str,
                ),
            )
    except Exception as exc:
        log.warning("No fue posible registrar el consumo de Apify: %s", exc)


def db_obtener_resumen_consumo(
    usuario: str,
    periodo: str = "mes",
    ver_global: bool = False,
) -> dict[str, Any]:
    """Obtiene el acumulado de posts, comentarios y llamadas a Apify por periodo y usuario."""
    asegurar_esquema_social()
    ahora = datetime.now(timezone.utc)
    user_clean = (usuario or "").strip() or "anonimo"

    if periodo == "hoy":
        desde_dt: Optional[datetime] = datetime(ahora.year, ahora.month, ahora.day, 0, 0, 0, tzinfo=timezone.utc)
    elif periodo == "semana":
        desde_dt = ahora - timedelta(days=7)
    elif periodo == "todo":
        desde_dt = None
    else:  # 'mes' por defecto
        periodo = "mes"
        desde_dt = datetime(ahora.year, ahora.month, 1, 0, 0, 0, tzinfo=timezone.utc)

    desde_iso = desde_dt.isoformat() if desde_dt else ""

    where_clauses: list[str] = []
    params: list[Any] = []

    if not ver_global:
        where_clauses.append("usuario = ?")
        params.append(user_clean)

    if desde_iso:
        where_clauses.append("timestamp >= ?")
        params.append(desde_iso)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with basedatos.conectar() as conn:
        fila_totales = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(posts_scrapeados), 0) AS total_posts,
                COALESCE(SUM(comentarios_scrapeados), 0) AS total_comentarios,
                COUNT(*) AS total_llamadas
            FROM social_apify_consumo
            {where_sql}
            """,
            params,
        ).fetchone()

        total_posts = int(fila_totales["total_posts"] if fila_totales else 0)
        total_comentarios = int(fila_totales["total_comentarios"] if fila_totales else 0)
        total_llamadas = int(fila_totales["total_llamadas"] if fila_totales else 0)

        where_inicio = "" if ver_global else "WHERE usuario = ?"
        params_inicio = [] if ver_global else [user_clean]
        fila_inicio = conn.execute(
            f"""
            SELECT MIN(timestamp) AS primera_fecha
            FROM social_apify_consumo
            {where_inicio}
            """,
            params_inicio,
        ).fetchone()

        primera_fecha_raw = fila_inicio["primera_fecha"] if fila_inicio else None
        if primera_fecha_raw:
            fecha_inicio_conteo = str(primera_fecha_raw)[:10]
        else:
            fecha_inicio_conteo = ahora.strftime("%Y-%m-%d")

        filas_recientes = conn.execute(
            f"""
            SELECT id, timestamp, usuario, tipo, actor, url_objetivo,
                   posts_scrapeados, comentarios_scrapeados, run_id
            FROM social_apify_consumo
            {where_sql}
            ORDER BY id DESC
            LIMIT 10
            """,
            params,
        ).fetchall()

        recientes = [
            {
                "id": f["id"],
                "timestamp": f["timestamp"],
                "usuario": f["usuario"],
                "tipo": f["tipo"],
                "actor": f["actor"],
                "url_objetivo": f["url_objetivo"],
                "posts_scrapeados": f["posts_scrapeados"],
                "comentarios_scrapeados": f["comentarios_scrapeados"],
                "run_id": f["run_id"],
            }
            for f in filas_recientes
        ]

    try:
        costo_post = float(os.getenv("APIFY_COSTO_POR_POST", "0.005"))
    except ValueError:
        costo_post = 0.005

    try:
        costo_comentario = float(os.getenv("APIFY_COSTO_POR_COMENTARIO", "0.0005"))
    except ValueError:
        costo_comentario = 0.0005

    costo_estimado_usd = round(
        (total_posts * costo_post) + (total_comentarios * costo_comentario),
        4,
    )

    try:
        max_posts_mes = int(os.getenv("SOCIAL_LIMITE_POSTS_MES", "5000"))
    except ValueError:
        max_posts_mes = 5000

    try:
        max_coms_mes = int(os.getenv("SOCIAL_LIMITE_COMS_MES", "75000"))
    except ValueError:
        max_coms_mes = 75000

    if periodo == "mes" and not ver_global:
        posts_mes = total_posts
        coms_mes = total_comentarios
    else:
        inicio_mes_iso = datetime(ahora.year, ahora.month, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
        with basedatos.conectar() as conn:
            where_cuota = "WHERE timestamp >= ?" if ver_global else "WHERE usuario = ? AND timestamp >= ?"
            params_cuota = [inicio_mes_iso] if ver_global else [user_clean, inicio_mes_iso]
            f_mes = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(posts_scrapeados), 0) AS p,
                    COALESCE(SUM(comentarios_scrapeados), 0) AS c
                FROM social_apify_consumo
                {where_cuota}
                """,
                params_cuota,
            ).fetchone()
            posts_mes = int(f_mes["p"] if f_mes else 0)
            coms_mes = int(f_mes["c"] if f_mes else 0)

    cuota_excedida = (posts_mes >= max_posts_mes) or (coms_mes >= max_coms_mes)
    pct_posts = round(min((posts_mes / max_posts_mes) * 100, 100.0), 1) if max_posts_mes > 0 else 100.0
    pct_coms = round(min((coms_mes / max_coms_mes) * 100, 100.0), 1) if max_coms_mes > 0 else 100.0

    return {
        "usuario": "Todos (Global)" if ver_global else user_clean,
        "es_global": ver_global,
        "periodo": periodo,
        "fecha_inicio_conteo": fecha_inicio_conteo,
        "desde": desde_iso,
        "posts_scrapeados": total_posts,
        "comentarios_scrapeados": total_comentarios,
        "llamadas_apify": total_llamadas,
        "costo_estimado_usd": costo_estimado_usd,
        "actividades_recientes": recientes,
        "limite_posts_mes": max_posts_mes,
        "limite_comentarios_mes": max_coms_mes,
        "posts_mes": posts_mes,
        "comentarios_mes": coms_mes,
        "cuota_excedida": cuota_excedida,
        "porcentaje_posts_mes": pct_posts,
        "porcentaje_comentarios_mes": pct_coms,
    }


def db_verificar_cuota_scrapeo(
    usuario: str,
    limite_posts: Optional[int] = None,
    limite_comentarios: Optional[int] = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Verifica si el usuario ha alcanzado o superado el límite mensual de posts o comentarios.

    Límites por defecto: 5,000 posts y 75,000 comentarios al mes por usuario.
    Devuelve:
        (permitido, mensaje_error, info_cuota)
    """
    asegurar_esquema_social()
    try:
        max_p = limite_posts if limite_posts is not None else int(os.getenv("SOCIAL_LIMITE_POSTS_MES", "5000"))
    except ValueError:
        max_p = 5000
    try:
        max_c = limite_comentarios if limite_comentarios is not None else int(os.getenv("SOCIAL_LIMITE_COMS_MES", "75000"))
    except ValueError:
        max_c = 75000

    ahora = datetime.now(timezone.utc)
    inicio_mes_iso = datetime(ahora.year, ahora.month, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    user_clean = (usuario or "").strip() or "anonimo"

    with basedatos.conectar() as conn:
        f = conn.execute(
            """
            SELECT
                COALESCE(SUM(posts_scrapeados), 0) AS total_posts,
                COALESCE(SUM(comentarios_scrapeados), 0) AS total_comentarios
            FROM social_apify_consumo
            WHERE usuario = ? AND timestamp >= ?
            """,
            (user_clean, inicio_mes_iso),
        ).fetchone()

        posts_usados = int(f["total_posts"] if f else 0)
        coms_usados = int(f["total_comentarios"] if f else 0)

    excedido_posts = posts_usados >= max_p
    excedido_coms = coms_usados >= max_c
    excedido = excedido_posts or excedido_coms

    pct_posts = round(min((posts_usados / max_p) * 100, 100.0), 1) if max_p > 0 else 100.0
    pct_coms = round(min((coms_usados / max_c) * 100, 100.0), 1) if max_c > 0 else 100.0

    info_cuota = {
        "usuario": user_clean,
        "limite_posts": max_p,
        "limite_comentarios": max_c,
        "posts_usados": posts_usados,
        "comentarios_usados": coms_usados,
        "porcentaje_posts": pct_posts,
        "porcentaje_comentarios": pct_coms,
        "excedido": excedido,
        "excedido_posts": excedido_posts,
        "excedido_comentarios": excedido_coms,
    }

    if not excedido:
        return True, "", info_cuota

    if excedido_posts and excedido_coms:
        det = f"has alcanzado el límite mensual de publicaciones ({posts_usados:,}/{max_p:,}) y de comentarios ({coms_usados:,}/{max_c:,})"
    elif excedido_posts:
        det = f"has alcanzado el límite mensual de publicaciones ({posts_usados:,}/{max_p:,} posts)"
    else:
        det = f"has alcanzado el límite mensual de comentarios ({coms_usados:,}/{max_c:,} comentarios)"

    msg = (
        f"Límite mensual alcanzado: {det}. "
        "No es posible realizar nuevas extracciones en vivo de redes sociales este mes. "
        "Comunícate con el administrador para ampliar tu cuota."
    )
    return False, msg, info_cuota
