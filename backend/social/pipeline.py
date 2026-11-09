"""Pipelines de alto nivel para Social Listening: analizar_perfil y analizar_posts_manual."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from .models import (
    SolicitudSocial,
    SolicitudPostsSocial,
    ResultadoSocial,
    ResultadoPostsManual,
    ResultadoPostIndividual,
    ComparacionPosts,
    ErrorPost,
    ApifyScrapeError,
    CuotaScrapingExcedidaError,
    SocialListeningError,
    _limpiar_url_facebook,
    _validar_urls_facebook,
    ANALISIS_VERSION,
    TONOS_VALIDOS,
)
from .db import (
    asegurar_esquema_social,
    db_guardar_posts,
    db_invalidar_analisis_posts,
    db_obtener_estado_posts,
    db_marcar_post_refrescado,
    db_obtener_posts_meta,
    db_obtener_post_meta_por_url,
    db_guardar_comentarios,
    db_actualizar_tonos_batch,
    db_obtener_comentarios,
    db_obtener_cache,
    db_guardar_cache,
    db_obtener_cache_manual,
    db_guardar_cache_manual,
    db_verificar_cuota_scrapeo,
    _deduplicar_comentarios,
    _fingerprint_dataset,
    _inferir_nombre_perfil,
)
from .scraping import _obtener_posts, _obtener_comentarios, _debe_refrescar_post, _debe_refrescar_perfil_posts
from .analysis import (
    _clasificar_postura_todos,
    _validar_neutros_todos,
    _extraer_temas,
    _procesar_temas,
    _generar_resumen,
    _calcular_estadisticas,
    _seleccionar_top_comentarios,
    _resultado_sin_comentarios,
    _extraer_objeto_json,
)
from .. import basedatos
import sys

log = logging.getLogger(__name__)


def _get_mod_fn(fn_name: str, default_fn: Any) -> Any:
    mod = sys.modules.get("backend.social_listening")
    if mod and hasattr(mod, fn_name):
        return getattr(mod, fn_name)
    return default_fn


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------


def analizar_perfil(
    solicitud: SolicitudSocial,
    groq_client: Any = None,
    groq_model: str = "llama3-70b-8192",
    usuario: str = "anonimo",
) -> ResultadoSocial:
    """Ejecuta adquisición incremental + persistencia + análisis semántico.

    Flujo:
    1. Asegurar esquema.
    2. Consultar posts recientes en Apify (sin retorno anticipado de caché).
    3. Guardar posts/top-comments.
    4. Refrescar comentarios de posts nuevos, recientes, desactualizados o cuando el
       usuario pide un ``max_comentarios`` superior al ya adquirido.
    5. Cargar desde BD como fuente de verdad.
    6. Analizar solo pendientes/versiones antiguas, agrupados por post.
    7. Revalidar neutros por post.
    8. Calcular estadísticas en Python.
    9. Reutilizar caché solo si la huella exacta del dataset coincide.
    10. Extraer temas, resumen, top comentarios y guardar nueva caché.
    """
    asegurar_esquema_social()

    url_p = str(solicitud.url_perfil).strip()

    # Cliente LLM.
    if groq_client is None:
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if api_key:
            try:
                from groq import Groq  # noqa: PLC0415

                groq_client = Groq(api_key=api_key)
            except Exception as exc:
                log.warning("No se pudo instanciar Groq: %s", exc)

    if not groq_model or groq_model == "llama3-70b-8192":
        groq_model = os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b"

    token_apify = os.environ.get("APIFY_API_TOKEN", "").strip()
    if not token_apify:
        return ResultadoSocial(
            url_perfil=url_p,
            posts_analizados=0,
            total_comentarios=0,
            sentimiento={},
            distribucion={},
            ngramas=[],
            resumen="",
            metodo_resumen="",
            error="Falta configurar APIFY_API_TOKEN en el archivo .env.",
        )

    try:
        from apify_client import ApifyClient  # noqa: PLC0415

        client_apify = ApifyClient(token_apify)

        # ------------------------------------------------------------------
        # 1) Sincronizar posts: Solo consultar Apify si no están en BD o están vencidos
        # ------------------------------------------------------------------
        debe_refrescar_posts, posts_db = _get_mod_fn(
            "_debe_refrescar_perfil_posts", _debe_refrescar_perfil_posts
        )(
            url_p,
            solicitud.max_posts,
            solicitud.forzar_refresco,
        )

        if debe_refrescar_posts:
            permitido_posts, msg_cuota_p, _ = db_verificar_cuota_scrapeo(usuario)
            if not permitido_posts:
                if posts_db and not solicitud.forzar_refresco:
                    log.warning(
                        "Usuario %s excedió cuota mensual de scraping; usando %d posts existentes en BD.",
                        usuario,
                        len(posts_db),
                    )
                    debe_refrescar_posts = False
                    posts_meta = posts_db
                    top_comentarios = []
                else:
                    raise CuotaScrapingExcedidaError(msg_cuota_p)
            else:
                try:
                    posts_meta, top_comentarios = _get_mod_fn("_obtener_posts", _obtener_posts)(
                        client_apify,
                        url_p,
                        solicitud.max_posts,
                        usuario=usuario,
                    )
                except ApifyScrapeError as exc:
                    log.warning("No se pudieron actualizar posts de Apify; usando datos de BD: %s", exc)
                    posts_meta = posts_db or db_obtener_posts_meta(url_p, solicitud.max_posts)
                    top_comentarios = []
        else:
            log.info(
                "Perfil %s: reutilizando %d posts de BD (recientemente sincronizados, costo Apify $0).",
                url_p,
                len(posts_db),
            )
            posts_meta = posts_db
            top_comentarios = []

        if not posts_meta:
            return ResultadoSocial(
                url_perfil=url_p,
                posts_analizados=0,
                total_comentarios=0,
                sentimiento={},
                distribucion={},
                ngramas=[],
                resumen="",
                metodo_resumen="",
                error=(
                    "No se encontraron posts públicos en Apify ni posts históricos en la BD. "
                    "El perfil puede ser privado, no existir o no tener posts accesibles."
                ),
            )

        # Deduplicar posts por URL preservando el orden del scraper.
        vistos_posts: set[str] = set()
        posts_meta_unicos: list[dict] = []
        for p in posts_meta:
            purl = str(p.get("url") or "")
            if purl and purl not in vistos_posts:
                vistos_posts.add(purl)
                posts_meta_unicos.append(p)
        posts_meta = posts_meta_unicos[: solicitud.max_posts]

        post_urls = [str(p["url"]) for p in posts_meta]
        post_texto_map = {str(p["url"]): str(p.get("texto") or "") for p in posts_meta}

        if debe_refrescar_posts:
            contexto_cambiado = db_guardar_posts(url_p, posts_meta)
            if contexto_cambiado:
                log.info(
                    "Cambió el texto/contexto de %d posts; invalidando análisis asociado.",
                    len(contexto_cambiado),
                )
                db_invalidar_analisis_posts(contexto_cambiado)

            # Top comments embebidos también se guardan ANTES del LLM.
            if top_comentarios:
                db_guardar_comentarios(
                    url_p,
                    _deduplicar_comentarios(top_comentarios),
                )

        # ------------------------------------------------------------------
        # 2) Refresh incremental de comentarios.
        # ------------------------------------------------------------------
        estados = db_obtener_estado_posts(url_p, post_urls)
        # Si el actor de posts omitió texto en una ejecución, conservar el contexto ya
        # persistido en BD en lugar de analizar con contexto vacío.
        for purl in post_urls:
            if not post_texto_map.get(purl):
                post_texto_map[purl] = str(estados.get(purl, {}).get("texto_post") or "")

        posts_a_refrescar: list[str] = []

        for posicion, p in enumerate(posts_meta):
            purl = str(p["url"])
            if _debe_refrescar_post(
                p,
                estados.get(purl),
                solicitud.max_comentarios,
                solicitud.forzar_refresco,
                posicion,
            ):
                posts_a_refrescar.append(purl)

        if posts_a_refrescar:
            permitido_coms, msg_cuota_c, _ = db_verificar_cuota_scrapeo(usuario)
            if not permitido_coms:
                coms_en_bd = db_obtener_comentarios(url_p, post_urls=post_urls, limite_por_post=1)
                if not coms_en_bd or solicitud.forzar_refresco:
                    raise CuotaScrapingExcedidaError(msg_cuota_c)
                log.warning(
                    "Usuario %s excedió cuota mensual de scraping; se omiten llamadas de comentarios en Apify.",
                    usuario,
                )
                posts_a_refrescar = []

        refrescos_exitosos = 0
        nuevos_comentarios = 0

        for purl in posts_a_refrescar:
            try:
                extraidos = _get_mod_fn("_obtener_comentarios", _obtener_comentarios)(
                    client_apify,
                    purl,
                    solicitud.max_comentarios,
                    usuario=usuario,
                )
                stats = db_guardar_comentarios(
                    url_p,
                    _deduplicar_comentarios(extraidos),
                    post_url_defecto=purl,
                )
                # Solo después de una ejecución válida de Apify.
                db_marcar_post_refrescado(purl, solicitud.max_comentarios)
                refrescos_exitosos += 1
                nuevos_comentarios += stats["insertados"]
            except ApifyScrapeError as exc:
                # No se marca actualizado/completado.
                log.warning("Refresh de comentarios falló para %s: %s", purl, exc)

        log.info(
            "Refresh comentarios: %d/%d posts correctos; %d comentarios nuevos.",
            refrescos_exitosos,
            len(posts_a_refrescar),
            nuevos_comentarios,
        )

        # ------------------------------------------------------------------
        # 3) BD = fuente de verdad; max_comentarios se aplica POR POST.
        # ------------------------------------------------------------------
        comentarios_db = db_obtener_comentarios(
            url_p,
            post_urls=post_urls,
            limite_por_post=solicitud.max_comentarios,
        )

        if not comentarios_db:
            return _resultado_sin_comentarios(url_p, len(post_urls))

        pendientes = [
            c
            for c in comentarios_db
            if c.get("analizado") == 0
            or c.get("analisis_version") != ANALISIS_VERSION
            or c.get("tono") not in TONOS_VALIDOS
        ]

        log.info(
            "Dataset activo: %d comentarios; pendientes de %s: %d.",
            len(comentarios_db),
            ANALISIS_VERSION,
            len(pendientes),
        )

        # Si no hay pendientes, la huella permite reutilizar exactamente el resultado
        # correspondiente al dataset actual, pero SOLO después de sincronizar Apify/BD.
        if not pendientes and not solicitud.forzar_refresco:
            fingerprint_pre = _fingerprint_dataset(comentarios_db, post_texto_map)
            cached = db_obtener_cache(
                url_p,
                solicitud.max_posts,
                solicitud.max_comentarios,
                fingerprint_pre,
            )
            if cached:
                log.info("Retornando caché válida para la huella %s.", fingerprint_pre)
                ultimo_sinc = max((str(p.get("actualizado") or "") for p in posts_meta), default="") or basedatos.ahora()
                cached.origen_datos = "cache_local"
                cached.sincronizado_en = cached.sincronizado_en or ultimo_sinc
                cached.comentarios_nuevos = nuevos_comentarios
                return cached

        if groq_client is None:
            raise SocialListeningError("Cliente LLM no inicializado en el servidor.")

        # ------------------------------------------------------------------
        # 4) Etapa 1: postura por post y batch.
        # ------------------------------------------------------------------
        if pendientes:
            mapa_tonos = _clasificar_postura_todos(
                pendientes,
                post_texto_map,
                url_p,
                groq_client,
                groq_model,
            )
            if mapa_tonos:
                db_actualizar_tonos_batch(mapa_tonos.items())

            # Segunda pasada solo sobre los que la primera etapa sí clasificó neutros.
            ids_neutros = {cid for cid, tono in mapa_tonos.items() if tono == "neutro"}
            neutros_objs = [c for c in pendientes if str(c.get("id")) in ids_neutros]

            if neutros_objs:
                cambios = _validar_neutros_todos(
                    neutros_objs,
                    post_texto_map,
                    url_p,
                    groq_client,
                    groq_model,
                )
                if cambios:
                    db_actualizar_tonos_batch(cambios.items())

        # ------------------------------------------------------------------
        # 5) Recargar estado definitivo desde BD.
        # ------------------------------------------------------------------
        comentarios_db = db_obtener_comentarios(
            url_p,
            post_urls=post_urls,
            limite_por_post=solicitud.max_comentarios,
        )

        distribucion, sentimiento, total_analizados = _calcular_estadisticas(comentarios_db)
        if total_analizados == 0:
            raise SocialListeningError(
                "Hay comentarios almacenados, pero ninguno pudo clasificarse correctamente."
            )

        faltantes = len(comentarios_db) - total_analizados
        if faltantes:
            log.warning(
                "%d comentarios quedaron sin clasificación y se excluyen de porcentajes.",
                faltantes,
            )

        # Ahora que los tonos están definitivos, una caché previa de la misma huella puede
        # reutilizarse incluso si al comienzo había filas con versión antigua que finalmente
        # quedaron idénticas. forzar_refresco siempre obliga a regenerar presentación.
        fingerprint = _fingerprint_dataset(comentarios_db, post_texto_map)
        if not solicitud.forzar_refresco:
            cached = db_obtener_cache(
                url_p,
                solicitud.max_posts,
                solicitud.max_comentarios,
                fingerprint,
            )
            if cached:
                ultimo_sinc = max((str(p.get("actualizado") or "") for p in posts_meta), default="") or basedatos.ahora()
                cached.origen_datos = "cache_local"
                cached.sincronizado_en = cached.sincronizado_en or ultimo_sinc
                cached.comentarios_nuevos = nuevos_comentarios
                return cached

        # ------------------------------------------------------------------
        # 6) Etapa 3: temas; solo comentarios efectivamente clasificados.
        # ------------------------------------------------------------------
        clasificados = [c for c in comentarios_db if c.get("tono") in TONOS_VALIDOS]
        temas_raw = _extraer_temas(clasificados, groq_client, groq_model)
        ngramas = _procesar_temas(temas_raw, clasificados)

        # ------------------------------------------------------------------
        # 7) Etapa 4: resumen ejecutivo.
        # ------------------------------------------------------------------
        positivos = sorted(
            [c for c in clasificados if c.get("tono") == "positivo"],
            key=lambda x: int(x.get("likes") or 0),
            reverse=True,
        )
        negativos = sorted(
            [c for c in clasificados if c.get("tono") == "negativo"],
            key=lambda x: int(x.get("likes") or 0),
            reverse=True,
        )

        resumen = _generar_resumen(
            url_perfil=url_p,
            total_comentarios=total_analizados,
            distribucion=distribucion,
            sentimiento=sentimiento,
            temas=ngramas,
            ejemplos_pos=[str(c.get("texto") or "")[:180] for c in positivos[:5]],
            ejemplos_neg=[str(c.get("texto") or "")[:180] for c in negativos[:5]],
            client=groq_client,
            model_name=groq_model,
        )
        if not resumen:
            raise SocialListeningError("El LLM no pudo completar el resumen ejecutivo.")

        # ------------------------------------------------------------------
        # 8) Resultado público. ``aprobacion_pct`` se mantiene por compatibilidad,
        #    pero conceptualmente es balance neto de postura.
        # ------------------------------------------------------------------
        pos_pct = float(sentimiento.get("positivo", 0.0))
        neg_pct = float(sentimiento.get("negativo", 0.0))
        balance_neto = round(pos_pct - neg_pct, 1)

        es_en_vivo = debe_refrescar_posts or len(posts_a_refrescar) > 0 or solicitud.forzar_refresco
        resultado = ResultadoSocial(
            url_perfil=url_p,
            posts_analizados=len(post_urls),
            total_comentarios=len(comentarios_db),
            sentimiento=sentimiento,
            distribucion=distribucion,
            ngramas=ngramas,
            resumen=resumen,
            metodo_resumen="modelo de lenguaje (semántico, postura dirigida)",
            comentarios_clasificados=_seleccionar_top_comentarios(clasificados),
            aprobacion_pct=balance_neto,
            origen_datos="en_vivo" if es_en_vivo else "cache_local",
            sincronizado_en=basedatos.ahora(),
            comentarios_nuevos=nuevos_comentarios,
        )

        db_guardar_cache(
            url_p,
            resultado,
            solicitud.max_posts,
            solicitud.max_comentarios,
            fingerprint,
        )
        return resultado

    except Exception as exc:
        log.error("Error en pipeline Social Listening: %s", exc, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Pipeline de análisis manual de publicaciones y comparación inter-posts
# ---------------------------------------------------------------------------


def _sincronizar_post_manual(
    client_apify: Optional[Any],
    post_url: str,
    max_comentarios: int,
    forzar_refresco: bool,
    usuario: str = "anonimo",
) -> dict:
    """Sincroniza un post manual individual: obtiene metadatos y comentarios, persiste en BD.

    Resuelve URLs canónicas y enlaces de compartir (/share/p/..., fb.watch, etc.)
    garantizando que comentarios y metadatos queden unificados.
    Retorna el dict post_meta. Lanza ApifyScrapeError si falla y no hay datos en BD.
    """
    post_url_limpia = _limpiar_url_facebook(post_url)
    post_db = db_obtener_post_meta_por_url(post_url) or db_obtener_post_meta_por_url(post_url_limpia)
    post_meta: Optional[dict] = post_db

    # 1. Obtener / refrescar metadatos si no existen en BD o se fuerza refresco o texto_post está vacío
    necesita_metadata = (
        post_db is None
        or forzar_refresco
        or not (post_db.get("texto") or "").strip()
    )

    if necesita_metadata and client_apify is not None:
        permitido_m, msg_cuota_m, _ = db_verificar_cuota_scrapeo(usuario)
        if not permitido_m:
            if post_meta is None or forzar_refresco:
                raise CuotaScrapingExcedidaError(msg_cuota_m)
            necesita_metadata = False
        else:
            try:
                posts_scraped, top_comentarios = _get_mod_fn("_obtener_posts", _obtener_posts)(
                    client_apify, post_url, 1, usuario=usuario
                )
                if posts_scraped:
                    meta_item = posts_scraped[0]
                    canonical_url = _limpiar_url_facebook(meta_item.get("url") or post_url)
                    meta_item["url"] = canonical_url
                    meta_item["canonical_url"] = canonical_url

                    # Guardar el post bajo la URL canónica
                    db_guardar_posts(meta_item.get("perfil_url") or "", [meta_item])

                    # Si la URL solicitada o su versión limpia difiere de la canónica, guardar alias
                    variantes_alias = {post_url.rstrip("/"), post_url_limpia.rstrip("/")}
                    for var_alias in variantes_alias:
                        if var_alias and var_alias != canonical_url.rstrip("/"):
                            alias_item = dict(meta_item)
                            alias_item["url"] = var_alias
                            alias_item["canonical_url"] = canonical_url
                            db_guardar_posts(meta_item.get("perfil_url") or "", [alias_item])

                    if top_comentarios:
                        for tc in top_comentarios:
                            tc["post_url"] = canonical_url
                        db_guardar_comentarios(
                            meta_item.get("perfil_url") or "",
                            _deduplicar_comentarios(top_comentarios),
                            post_url_defecto=canonical_url,
                        )
                    post_meta = db_obtener_post_meta_por_url(canonical_url) or meta_item
            except CuotaScrapingExcedidaError:
                raise
            except Exception as exc:
                log.warning("No se pudieron obtener metadatos de Apify para %s: %s", post_url, exc)
                if post_meta is None:
                    raise ApifyScrapeError(
                        f"No se pudieron obtener metadatos de la publicación: {exc}"
                    ) from exc

    if post_meta is None:
        post_meta = {
            "url": post_url_limpia or post_url,
            "canonical_url": post_url_limpia or post_url,
            "requested_url": post_url,
            "post_id": "",
            "texto": "",
            "fecha": "",
            "likes": 0,
            "perfil_nombre": "Facebook",
            "perfil_url": "",
        }

    canonical_url = str(post_meta.get("canonical_url") or post_meta.get("url") or post_url).strip().rstrip("/")

    # 2. Verificar si se deben refrescar comentarios
    estado = db_obtener_estado_posts(post_meta.get("perfil_url") or "", [canonical_url, post_url, post_url_limpia]).get(canonical_url)
    if not estado:
        estado = db_obtener_estado_posts(post_meta.get("perfil_url") or "", [post_url]).get(post_url)

    if _debe_refrescar_post(post_meta, estado, max_comentarios, forzar_refresco, posicion=0):
        if client_apify is not None:
            permitido_c, msg_cuota_c, _ = db_verificar_cuota_scrapeo(usuario)
            if not permitido_c:
                comentarios_existentes = db_obtener_comentarios(post_urls=[canonical_url, post_url], limite_por_post=1)
                if not comentarios_existentes or forzar_refresco:
                    raise CuotaScrapingExcedidaError(msg_cuota_c)
                log.warning(
                    "Usuario %s excedió cuota mensual de scraping; se omiten comentarios en vivo de %s.",
                    usuario,
                    post_url,
                )
            else:
                try:
                    # Usar post_url para el scraper de comentarios de Apify
                    comentarios_extra = _get_mod_fn("_obtener_comentarios", _obtener_comentarios)(
                        client_apify, post_url, max_comentarios, usuario=usuario
                    )
                    for c in comentarios_extra:
                        c["post_url"] = canonical_url

                    db_guardar_comentarios(
                        post_meta.get("perfil_url") or "",
                        _deduplicar_comentarios(comentarios_extra),
                        post_url_defecto=canonical_url,
                    )

                    db_marcar_post_refrescado(canonical_url, max_comentarios)
                    if post_url.rstrip("/") != canonical_url:
                        db_marcar_post_refrescado(post_url, max_comentarios)
                except CuotaScrapingExcedidaError:
                    raise
                except Exception as exc:
                    log.warning("Error refrescando comentarios para %s: %s", post_url, exc)
                    comentarios_existentes = db_obtener_comentarios(post_urls=[canonical_url, post_url], limite_por_post=1)
                    if not comentarios_existentes:
                        raise ApifyScrapeError(f"Fallo al obtener comentarios del post: {exc}") from exc

    # Unificar siempre en BD cualquier comentario previo asociado a alias
    alias_a_unificar = [
        post_url,
        post_url.rstrip("/") if post_url.endswith("/") else post_url + "/",
        post_url_limpia,
        post_url_limpia.rstrip("/") if post_url_limpia.endswith("/") else post_url_limpia + "/",
    ]
    alias_a_unificar = [a for a in set(alias_a_unificar) if a.rstrip("/") != canonical_url]
    if alias_a_unificar:
        try:
            with basedatos.transaccion() as conn:
                placeholders = ",".join("?" for _ in alias_a_unificar)
                conn.execute(
                    f"""
                    UPDATE social_comentarios
                    SET post_url = ?
                    WHERE post_url IN ({placeholders})
                    """,
                    [canonical_url, *alias_a_unificar],
                )
        except Exception as exc:
            log.warning("Error unificando comentarios alias: %s", exc)

    return post_meta


def _analizar_post_individual(
    post_meta: dict,
    alias: str,
    max_comentarios: int,
    groq_client: Any,
    groq_model: str,
) -> ResultadoPostIndividual:
    """Ejecuta el análisis semántico de un post específico utilizando su propio contexto."""
    post_url = str(post_meta.get("canonical_url") or post_meta.get("url") or "").strip()
    post_texto = str(post_meta.get("texto") or "").strip()
    perfil_url = str(post_meta.get("perfil_url") or post_meta.get("url_perfil") or "").strip()
    perfil_nombre = _inferir_nombre_perfil(perfil_url, str(post_meta.get("perfil_nombre") or ""))
    post_id = str(post_meta.get("post_id") or "").strip()
    fecha_post = str(post_meta.get("fecha") or "").strip()
    post_likes = int(post_meta.get("likes") or 0)

    # 1. Cargar comentarios de BD (incluyendo posibles alias por seguridad)
    urls_consulta = [post_url]
    if post_meta.get("requested_url") and post_meta["requested_url"] != post_url:
        urls_consulta.append(post_meta["requested_url"])
    comentarios = db_obtener_comentarios(post_urls=urls_consulta, limite_por_post=max_comentarios)
    total_comentarios = len(comentarios)

    if total_comentarios == 0:
        return ResultadoPostIndividual(
            alias=alias,
            post_url=post_url,
            post_id=post_id,
            perfil_nombre=perfil_nombre,
            perfil_url=perfil_url,
            texto_post=post_texto,
            fecha_post=fecha_post,
            post_likes=post_likes,
            total_comentarios=0,
            comentarios_clasificados=0,
            comentarios_no_clasificados=0,
            cobertura_pct=0.0,
            distribucion={"positivo": 0, "neutro": 0, "negativo": 0},
            porcentajes={"positivo": 0.0, "neutro": 0.0, "negativo": 0.0},
            balance_postura=0.0,
            temas=[],
            resumen="La publicación no tiene comentarios públicos registrados en la base de datos.",
            comentarios_representativos=[],
        )

    # 2. Identificar pendientes de clasificar
    pendientes = [
        c for c in comentarios
        if c.get("analizado") == 0
        or c.get("analisis_version") != ANALISIS_VERSION
        or c.get("tono") not in TONOS_VALIDOS
    ]

    if pendientes and groq_client is not None:
        post_texto_map = {post_url: post_texto}
        target_nombre = perfil_nombre if perfil_nombre != "Facebook" else ""
        target = f"{target_nombre} ({perfil_url or post_url})".strip() if target_nombre else (perfil_url or post_url)
        mapa_tonos = _clasificar_postura_todos(pendientes, post_texto_map, target, groq_client, groq_model)
        if mapa_tonos:
            db_actualizar_tonos_batch(mapa_tonos.items())

        # Validación de neutros
        ids_neutros = {cid for cid, tono in mapa_tonos.items() if tono == "neutro"}
        neutros_objs = [c for c in pendientes if str(c.get("id")) in ids_neutros]
        if neutros_objs:
            cambios = _validar_neutros_todos(neutros_objs, post_texto_map, target, groq_client, groq_model)
            if cambios:
                db_actualizar_tonos_batch(cambios.items())

        # Recargar comentarios con clasificaciones actualizadas
        comentarios = db_obtener_comentarios(post_urls=[post_url], limite_por_post=max_comentarios)

    # 3. Estadísticas en Python
    distribucion, porcentajes, total_analizados = _calcular_estadisticas(comentarios)
    comentarios_no_clasificados = total_comentarios - total_analizados
    cobertura_pct = round((total_analizados / total_comentarios * 100), 1) if total_comentarios > 0 else 0.0
    pos_pct = float(porcentajes.get("positivo", 0.0))
    neg_pct = float(porcentajes.get("negativo", 0.0))
    balance_postura = round(pos_pct - neg_pct, 1)

    # 4. Temas
    clasificados = [c for c in comentarios if c.get("tono") in TONOS_VALIDOS]
    temas_raw = _extraer_temas(clasificados, groq_client, groq_model)
    temas = _procesar_temas(temas_raw, clasificados)

    # 5. Resumen
    positivos = sorted(
        [c for c in clasificados if c.get("tono") == "positivo"],
        key=lambda x: int(x.get("likes") or 0),
        reverse=True,
    )
    negativos = sorted(
        [c for c in clasificados if c.get("tono") == "negativo"],
        key=lambda x: int(x.get("likes") or 0),
        reverse=True,
    )
    resumen = _generar_resumen(
        url_perfil=perfil_url or post_url,
        total_comentarios=total_analizados,
        distribucion=distribucion,
        sentimiento=porcentajes,
        temas=temas,
        ejemplos_pos=[str(c.get("texto") or "")[:180] for c in positivos[:5]],
        ejemplos_neg=[str(c.get("texto") or "")[:180] for c in negativos[:5]],
        client=groq_client,
        model_name=groq_model,
    )
    if not resumen:
        resumen = "Análisis completado para la publicación."

    # 6. Comentarios representativos
    rep = _seleccionar_top_comentarios(clasificados)

    return ResultadoPostIndividual(
        alias=alias,
        post_url=post_url,
        post_id=post_id,
        perfil_nombre=perfil_nombre,
        perfil_url=perfil_url,
        texto_post=post_texto,
        fecha_post=fecha_post,
        post_likes=post_likes,
        total_comentarios=total_comentarios,
        comentarios_clasificados=total_analizados,
        comentarios_no_clasificados=comentarios_no_clasificados,
        cobertura_pct=cobertura_pct,
        distribucion=distribucion,
        porcentajes=porcentajes,
        balance_postura=balance_postura,
        temas=temas,
        resumen=resumen,
        comentarios_representativos=rep,
    )


def _consolidar_temas_llm(
    temas_textos: list[str],
    client: Any,
    model_name: str,
) -> dict[str, str]:
    """Agrupa frases temáticas semánticamente equivalentes bajo un concepto canónico.

    Retorna un diccionario {variante_lower: tema_canonico}.
    El LLM NUNCA calcula frecuencias ni porcentajes.
    """
    if not temas_textos:
        return {}
    if len(temas_textos) == 1 or client is None:
        return {t.strip().lower(): t.strip() for t in temas_textos}

    sistema = """
You are a political social-listening thematic consolidation expert.

Given a list of citizen discussion topics extracted from Facebook posts, consolidate semantically equivalent or strongly overlapping topics under ONE clear canonical concept in Spanish (2 to 5 words).

RULES
1. Group phrases that discuss the exact same issue, problem, or topic (for example: "inseguridad pública", "problemas de seguridad", and "falta de seguridad" belong together).
2. Do NOT group distinct topics into broad categories. Keep genuinely different concepts separate.
3. Every input topic MUST be accounted for in the output, either within a group or as its own canonical topic.
4. Do NOT calculate any numbers, statistics, or frequencies.
5. Canonical concepts must be in Spanish.

OUTPUT
Return ONLY one valid JSON object:
{
  "grupos": [
    {
      "tema_canonico": "<canonical concept in Spanish (2-5 words)>",
      "variantes": ["<exact input phrase 1>", "<exact input phrase 2>"]
    }
  ]
}
""".strip()

    try:
        resp = client.chat.completions.create(
            model=model_name,
            temperature=0.0,
            max_tokens=1000,
            messages=[
                {"role": "system", "content": sistema},
                {
                    "role": "user",
                    "content": json.dumps({"temas": temas_textos}, ensure_ascii=False),
                },
            ],
            response_format={"type": "json_object"},
        )
        contenido = (resp.choices[0].message.content or "").strip()
        data = _extraer_objeto_json(contenido)
        grupos = data.get("grupos", [])
        mapa: dict[str, str] = {}
        for g in grupos:
            if not isinstance(g, dict):
                continue
            canon = str(g.get("tema_canonico") or "").strip()
            if not canon:
                continue
            for var in g.get("variantes", []):
                mapa[str(var).strip().lower()] = canon
        for t in temas_textos:
            tl = t.strip().lower()
            if tl not in mapa:
                mapa[tl] = t.strip()
        return mapa
    except Exception as exc:
        log.warning("Error consolidando temas semánticos con LLM: %s", exc)
        return {t.strip().lower(): t.strip() for t in temas_textos}


def _analizar_temas_comparativos(
    posts: list[ResultadoPostIndividual],
    client: Any,
    model_name: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Compara temas entre publicaciones calculando frecuencias y distribuciones en Python.

    Retorna (todos_los_temas, temas_compartidos, temas_distintivos).
    """
    todos_textos: list[str] = []
    vistos: set[str] = set()
    for p in posts:
        for t in p.temas:
            txt = str(t.get("texto") or "").strip()
            if txt and txt.lower() not in vistos:
                vistos.add(txt.lower())
                todos_textos.append(txt)

    if not todos_textos:
        return [], [], []

    mapa_canonico = _consolidar_temas_llm(todos_textos, client, model_name)

    temas_agrupados: dict[str, dict] = {}
    for p in posts:
        for t in p.temas:
            txt = str(t.get("texto") or "").strip()
            if not txt:
                continue
            canon = mapa_canonico.get(txt.lower(), txt)

            if canon not in temas_agrupados:
                temas_agrupados[canon] = {
                    "tema": canon,
                    "variantes": set(),
                    "posts_dict": {},
                }

            temas_agrupados[canon]["variantes"].add(txt)

            if p.alias not in temas_agrupados[canon]["posts_dict"]:
                temas_agrupados[canon]["posts_dict"][p.alias] = {
                    "alias": p.alias,
                    "post_url": p.post_url,
                    "perfil_nombre": p.perfil_nombre,
                    "menciones": 0,
                    "positivo": 0,
                    "negativo": 0,
                    "neutro": 0,
                }

            entry = temas_agrupados[canon]["posts_dict"][p.alias]
            entry["menciones"] += int(t.get("frecuencia") or 0)
            desglose = t.get("desglose") or {}
            entry["positivo"] += int(desglose.get("positivo") or 0)
            entry["negativo"] += int(desglose.get("negativo") or 0)
            entry["neutro"] += int(desglose.get("neutro") or 0)

    todos_los_temas: list[dict] = []
    temas_compartidos: list[dict] = []
    temas_distintivos: list[dict] = []
    total_comentarios_map = {p.alias: p.total_comentarios for p in posts}

    for canon, info in temas_agrupados.items():
        post_entries: list[dict] = []
        presente_en: list[str] = []
        total_menciones = 0

        for p in posts:
            if p.alias in info["posts_dict"]:
                entry = info["posts_dict"][p.alias]
                tot_c = total_comentarios_map.get(p.alias, 0)
                pct = round(entry["menciones"] / tot_c * 100, 1) if tot_c > 0 else 0.0
                pos = entry["positivo"]
                neg = entry["negativo"]
                neu = entry["neutro"]

                if pos > max(neg, neu):
                    tono_pred = "positivo"
                elif neg > max(pos, neu):
                    tono_pred = "negativo"
                else:
                    tono_pred = "neutro"

                post_entry = {
                    "alias": p.alias,
                    "post_url": p.post_url,
                    "perfil_nombre": p.perfil_nombre,
                    "menciones": entry["menciones"],
                    "porcentaje": pct,
                    "positivo": pos,
                    "negativo": neg,
                    "neutro": neu,
                    "tono_predominante": tono_pred,
                }
                post_entries.append(post_entry)
                presente_en.append(p.alias)
                total_menciones += entry["menciones"]

        item_tema = {
            "tema": canon,
            "variantes": sorted(list(info["variantes"])),
            "presente_en_posts": presente_en,
            "total_menciones": total_menciones,
            "posts": post_entries,
        }

        todos_los_temas.append(item_tema)
        if len(presente_en) >= 2:
            temas_compartidos.append(item_tema)
        elif len(presente_en) == 1:
            temas_distintivos.append(item_tema)

    todos_los_temas.sort(key=lambda x: x["total_menciones"], reverse=True)
    temas_compartidos.sort(key=lambda x: x["total_menciones"], reverse=True)
    temas_distintivos.sort(key=lambda x: x["total_menciones"], reverse=True)

    return todos_los_temas, temas_compartidos, temas_distintivos


def _comparar_distribucion_postura(posts: list[ResultadoPostIndividual]) -> list[dict]:
    """Calcula contrastes de volumen y postura entre publicaciones con umbrales descriptivos."""
    difs: list[dict] = []

    for p in posts:
        difs.append({
            "tipo": "resumen_post",
            "alias": p.alias,
            "post_url": p.post_url,
            "perfil_nombre": p.perfil_nombre,
            "total_comentarios": p.total_comentarios,
            "comentarios_clasificados": p.comentarios_clasificados,
            "cobertura_pct": p.cobertura_pct,
            "positivo_pct": p.porcentajes.get("positivo", 0.0),
            "negativo_pct": p.porcentajes.get("negativo", 0.0),
            "neutro_pct": p.porcentajes.get("neutro", 0.0),
            "balance_postura": p.balance_postura,
        })

    def _describir_magnitud(d: float) -> str:
        ad = abs(d)
        if ad < 3.0:
            return "mínima (< 3 pp)"
        elif ad < 10.0:
            return "moderada (3–10 pp)"
        return "marcada (≥ 10 pp)"

    for i in range(len(posts)):
        for j in range(i + 1, len(posts)):
            pa = posts[i]
            pb = posts[j]
            diff_pos = round(pa.porcentajes.get("positivo", 0.0) - pb.porcentajes.get("positivo", 0.0), 1)
            diff_neg = round(pa.porcentajes.get("negativo", 0.0) - pb.porcentajes.get("negativo", 0.0), 1)
            diff_neu = round(pa.porcentajes.get("neutro", 0.0) - pb.porcentajes.get("neutro", 0.0), 1)
            diff_bal = round(pa.balance_postura - pb.balance_postura, 1)

            difs.append({
                "tipo": "contraste_par",
                "post_a": pa.alias,
                "post_b": pb.alias,
                "diferencia_positivo_pp": diff_pos,
                "diferencia_negativo_pp": diff_neg,
                "diferencia_neutro_pp": diff_neu,
                "diferencia_balance_pp": diff_bal,
                "magnitud_diferencia_negativa": _describir_magnitud(diff_neg),
                "magnitud_diferencia_positiva": _describir_magnitud(diff_pos),
                "magnitud_diferencia_balance": _describir_magnitud(diff_bal),
            })

    return difs


def _resumen_comparativo_extractivo(
    posts: list[ResultadoPostIndividual],
    temas_compartidos: list[dict],
    temas_distintivos: list[dict],
) -> str:
    """Genera un resumen comparativo objetivo en español sin depender del LLM."""
    n_posts = len(posts)
    total_c = sum(p.total_comentarios for p in posts)

    lineas_p1 = [
        f"Se analizaron comparativamente {n_posts} publicaciones de Facebook con un total de {total_c} comentarios procesados."
    ]
    for p in posts:
        lineas_p1.append(
            f"El {p.alias} ({p.perfil_nombre}) registró {p.total_comentarios} comentarios "
            f"({p.porcentajes.get('positivo', 0.0):.1f}% positivo, "
            f"{p.porcentajes.get('negativo', 0.0):.1f}% negativo, "
            f"{p.porcentajes.get('neutro', 0.0):.1f}% neutro; balance neto de {p.balance_postura:+.1f}%)."
        )
    parrafo1 = " ".join(lineas_p1)

    if temas_compartidos:
        nombres_comp = [f"«{t['tema']}» ({t['total_menciones']} menciones)" for t in temas_compartidos[:4]]
        parrafo2 = (
            f"Entre los ejes temáticos transversales observados en las publicaciones analizadas destacan {', '.join(nombres_comp)}. "
            "Estos temas compartidos permiten contrastar las diferencias de postura y argumentación expresadas por los ciudadanos ante cada publicación."
        )
    else:
        parrafo2 = (
            "No se identificaron temas compartidos predominantes entre las publicaciones evaluadas, lo que "
            "indica que la conversación ciudadana se concentró en aspectos diferenciados para cada contenido."
        )

    if temas_distintivos:
        nombres_dist = [f"«{t['tema']}» en {t['presente_en_posts'][0]}" for t in temas_distintivos[:4]]
        parrafo3 = (
            f"Asimismo, se observaron temas concentrados particularmente en publicaciones específicas, tales como {', '.join(nombres_dist)}. "
            "En síntesis, los resultados reflejan dinámicas de conversación particulares para cada contenido dentro de las muestras recopiladas."
        )
    else:
        parrafo3 = (
            "En síntesis, los contrastes estadísticos calculados proporcionan una visión clara de la interacción y de las posturas observadas en los comentarios recopilados."
        )

    return f"{parrafo1}\n\n{parrafo2}\n\n{parrafo3}"


def _generar_resumen_comparativo(
    posts: list[ResultadoPostIndividual],
    diferencias: list[dict],
    temas_compartidos: list[dict],
    temas_distintivos: list[dict],
    client: Any,
    model_name: str,
) -> str:
    """Genera un resumen comparativo de 3 párrafos mediante LLM a partir de inteligencia política sustantiva."""
    if client is None:
        return _resumen_comparativo_extractivo(posts, temas_compartidos, temas_distintivos)

    posts_payload = []
    for p in posts:
        ejemplos_pos = [
            c.texto for c in p.comentarios_representativos if c.tono == "positivo"
        ][:3]
        ejemplos_neg = [
            c.texto for c in p.comentarios_representativos if c.tono == "negativo"
        ][:3]

        actor_label = p.perfil_nombre if p.perfil_nombre and p.perfil_nombre != "Facebook" else (p.perfil_url or p.post_url)
        posts_payload.append({
            "alias": p.alias,
            "actor_o_perfil": actor_label,
            "post_url": p.post_url,
            "texto_post": (p.texto_post or "")[:250],
            "positivo_pct": p.porcentajes.get("positivo", 0.0),
            "negativo_pct": p.porcentajes.get("negativo", 0.0),
            "neutro_pct": p.porcentajes.get("neutro", 0.0),
            "balance_postura": p.balance_postura,
            "temas_principales": [
                {
                    "tema": t.get("texto"),
                    "tono": t.get("tono"),
                    "menciones": t.get("frecuencia"),
                    "explicacion": t.get("explicacion"),
                }
                for t in p.temas[:5]
            ],
            "comentarios_apoyo_destacados": ejemplos_pos,
            "comentarios_critica_destacados": ejemplos_neg,
        })

    payload = {
        "posts": posts_payload,
        "contrastes_clave": [d for d in diferencias if d.get("tipo") == "contraste_par"],
        "temas_compartidos": [
            {
                "tema": t["tema"],
                "total_menciones": t["total_menciones"],
                "desglose_por_post": t["posts"],
            }
            for t in temas_compartidos[:6]
        ],
        "temas_distintivos": [
            {
                "tema": t["tema"],
                "presente_en": t["presente_en_posts"],
                "menciones": t["total_menciones"],
            }
            for t in temas_distintivos[:6]
        ],
    }

    sistema = """
Eres un consultor senior de análisis político digital y escucha social comparativa.

Redacta un resumen ejecutivo comparativo en ESPAÑOL compuesto por EXACTAMENTE 3 párrafos sustantivos y profundos, contrastando la conversación ciudadana, las narrativas y la percepción pública entre las publicaciones analizadas.

REGLA CRÍTICA DE CONTENIDO:
- PROHIBIDO TERMINANTEMENTE hablar sobre la cobertura de clasificación, cantidad de comentarios analizados vs no analizados, porcentajes de cobertura, o el funcionamiento interno del software.
- El análisis debe ser 100% de INTELIGENCIA POLÍTICA basada en los argumentos, críticas, burlas, apoyos y temas expresados por los ciudadanos en los comentarios de cada publicación.

ESTRUCTURA OBLIGATORIA DE LOS 3 PÁRRAFOS:

Párrafo 1 — Clima de opinión y balance político comparativo:
Contrasta la receptividad política de cada publicación. Explica cuál generó mayor nivel de rechazo o cuestionamiento y cuál obtuvo mayor simpatía o aprobación, asociando los porcentajes de balance con los actores y mensajes de cada post.

Párrafo 2 — Ejes de crítica, señalamientos y vulnerabilidades de los actores:
Analiza en detalle las descalificaciones y cuestionamientos de la ciudadanía hacia cada figura o contenido. Cita o sintetiza los argumentos reales de los comentarios negativos (por ejemplo: acusaciones de servilismo político, señalamientos de simulación o falsa humildad, burlas por el contenido, apodos peyorativos, reclamos de gestión).

Párrafo 3 — Respaldos, narrativas afines y síntesis estratégica:
Describe de dónde provienen los comentarios positivos y los apoyos recibidos (lealtad partidista/4T, reconocimiento a la labor del actor, expresiones de afecto o bendiciones) y qué ejes temáticos compartidos o diferenciados definen el debate general. Concluye con una síntesis estratégica del impacto reputacional observado.

ESTILO:
- Riguroso, directo, profesional y enfocado en inteligencia política para toma de decisiones.
- No uses viñetas, títulos ni encabezados. Retorna ÚNICAMENTE los 3 párrafos de texto continuo separados por doble salto de línea.
""".strip()

    try:
        resp = client.chat.completions.create(
            model=model_name,
            temperature=0.3,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": sistema},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        log.warning("Error generando resumen comparativo con LLM: %s", exc)
        return _resumen_comparativo_extractivo(posts, temas_compartidos, temas_distintivos)


def _comparar_posts(
    posts: list[ResultadoPostIndividual],
    groq_client: Any,
    groq_model: str,
) -> ComparacionPosts:
    """Ejecuta la comparación estadística y temática entre dos o más publicaciones."""
    total_posts = len(posts)
    total_comentarios = sum(p.total_comentarios for p in posts)

    diferencias_sentimiento = _comparar_distribucion_postura(posts)
    todos_los_temas, temas_compartidos, temas_distintivos = _analizar_temas_comparativos(
        posts, groq_client, groq_model
    )

    resumen_comparativo = _generar_resumen_comparativo(
        posts=posts,
        diferencias=diferencias_sentimiento,
        temas_compartidos=temas_compartidos,
        temas_distintivos=temas_distintivos,
        client=groq_client,
        model_name=groq_model,
    )

    return ComparacionPosts(
        total_posts=total_posts,
        total_comentarios=total_comentarios,
        diferencias_sentimiento=diferencias_sentimiento,
        temas_compartidos=temas_compartidos,
        temas_distintivos=temas_distintivos,
        todos_los_temas=todos_los_temas,
        resumen_comparativo=resumen_comparativo,
    )


def analizar_posts_manual(
    solicitud: SolicitudPostsSocial,
    groq_client: Any = None,
    groq_model: str = "llama3-70b-8192",
    usuario: str = "anonimo",
) -> ResultadoPostsManual:
    """Analiza publicaciones manuales específicas de Facebook con pipeline individual y comparativo."""
    asegurar_esquema_social()

    urls_limpias = _validar_urls_facebook(solicitud.post_urls)

    # Cliente Groq
    if groq_client is None:
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if api_key:
            try:
                from groq import Groq  # noqa: PLC0415

                groq_client = Groq(api_key=api_key)
            except Exception as exc:
                log.warning("No se pudo instanciar Groq: %s", exc)

    if not groq_model or groq_model == "llama3-70b-8192":
        groq_model = os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b"

    # Cliente Apify
    token_apify = os.environ.get("APIFY_API_TOKEN", "").strip()
    client_apify = None
    if token_apify:
        try:
            from apify_client import ApifyClient  # noqa: PLC0415

            client_apify = ApifyClient(token_apify)
        except Exception as exc:
            log.warning("No se pudo instanciar ApifyClient: %s", exc)

    posts_exitosos: list[ResultadoPostIndividual] = []
    posts_con_error: list[ErrorPost] = []

    for i, post_url in enumerate(urls_limpias):
        alias = f"Post {chr(65 + i)}" if i < 26 else f"Post {i + 1}"
        try:
            post_meta = _get_mod_fn("_sincronizar_post_manual", _sincronizar_post_manual)(
                client_apify=client_apify,
                post_url=post_url,
                max_comentarios=solicitud.max_comentarios,
                forzar_refresco=solicitud.forzar_refresco,
                usuario=usuario,
            )
            res_ind = _analizar_post_individual(
                post_meta=post_meta,
                alias=alias,
                max_comentarios=solicitud.max_comentarios,
                groq_client=groq_client,
                groq_model=groq_model,
            )
            posts_exitosos.append(res_ind)
        except Exception as exc:
            log.warning("Error procesando publicación manual %s: %s", post_url, exc)
            posts_con_error.append(ErrorPost(post_url=post_url, error=str(exc)))

    if not posts_exitosos:
        return ResultadoPostsManual(
            posts=[],
            posts_con_error=posts_con_error,
            comparacion=None,
        )

    comparacion: Optional[ComparacionPosts] = None
    if len(posts_exitosos) >= 2:
        sorted_urls = sorted([p.post_url for p in posts_exitosos])
        all_comentarios = db_obtener_comentarios(
            post_urls=sorted_urls,
            limite_por_post=solicitud.max_comentarios,
        )
        post_texto_map = {p.post_url: p.texto_post for p in posts_exitosos}
        fingerprint = _fingerprint_dataset(all_comentarios, post_texto_map)

        if not solicitud.forzar_refresco:
            cached_res = db_obtener_cache_manual(
                sorted_post_urls=sorted_urls,
                max_comentarios=solicitud.max_comentarios,
                fingerprint=fingerprint,
            )
            if cached_res and cached_res.comparacion:
                log.info("Retornando análisis comparativo previo de caché en BD.")
                return ResultadoPostsManual(
                    posts=posts_exitosos,
                    posts_con_error=posts_con_error,
                    comparacion=cached_res.comparacion,
                )

        comparacion = _comparar_posts(posts_exitosos, groq_client, groq_model)

        resultado_final = ResultadoPostsManual(
            posts=posts_exitosos,
            posts_con_error=posts_con_error,
            comparacion=comparacion,
        )
        db_guardar_cache_manual(
            sorted_post_urls=sorted_urls,
            max_comentarios=solicitud.max_comentarios,
            fingerprint=fingerprint,
            resultado=resultado_final,
        )
        return resultado_final

    return ResultadoPostsManual(
        posts=posts_exitosos,
        posts_con_error=posts_con_error,
        comparacion=None,
    )
