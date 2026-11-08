"""Sistema de respaldo (fallback) léxico y estadístico para Social Listening.

Este módulo se aísla de social_listening.py para garantizar que el sistema
siempre priorice al 100% el análisis semántico con el LLM (Groq).
Solo se invoca desde main.py si el proceso del LLM falla de forma explícita.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from typing import Any, List, Optional

from .social_listening import (
    ComentarioClasificado,
    ResultadoSocial,
    SolicitudSocial,
    asegurar_esquema_social,
    db_obtener_comentarios,
    db_obtener_posts_meta,
    db_guardar_cache,
    _fingerprint_dataset,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Léxicos de respaldo en español
# ---------------------------------------------------------------------------
POSITIVAS = set(
    "apoya apoyo avance avances celebra celebró cumple cumplió crecimiento logro logros "
    "mejora mejoró positivo positiva reconocimiento reconocen éxito exitoso fortalece "
    "inversión acuerdo acuerdos beneficio beneficios gana ganó ganamos victoria triunfa "
    "triunfo bien buena bueno excelente progreso trabaja trabajando compromiso "
    "entrega honesto honesta transparente seguro segura resultado resultados propuesta "
    "construye construyendo eficiente eficacia eficaz impulsa impulso vamos animo "
    "felicidades bendiciones orgullo orgulloso unidos adelante campeon arriba viva "
    "esperanza transformar juntos líder felicito equipazo ganador ganadora".split()
)
NEGATIVAS = set(
    "acusa acusado acusación critica crítico crítica cuestiona cuestionó denuncia "
    "denunciado polémica conflicto crisis falla fallas incumple incumplimiento "
    "corrupción delito investigación protesta rechazo riesgo sanción violencia "
    "malo mala mentira mentiras engaña fraude culpa culpable robo roba corruptos "
    "ineficiente ineficaz problema problemas queja quejas desacuerdo desaparece "
    "abuso pésimo terrible negligente negligencia escandalo escándalos rata ratas "
    "rateros peor encubrimiento encubrir risa verguenza asco chspm traidores fuera "
    "inutil fracaso ladron ladrones delincuentes cinicos cinismo descaro basura".split()
)
STOP_ES = set(
    "de del la las el los a al en con por para y o un una unos unas que se su sus "
    "es fue son como desde sobre entre ante tras más muy este esta estos estas año "
    "años san si no también hay si ya lo le les nos mi mis tu tus se él ella ellos "
    "ellas me te nos vos pero sino aunque porque si cuando donde como cual cuales "
    "todo toda todos todas hay ser estar ha he has han había hubiera haya siendo "
    "estado siendo aquí ahí allá así bien mal hace hacer hecho tengo tiene tienen "
    "tenemos voy va van vamos quiero quiere quieren".split()
)


def sin_acentos(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in base if not unicodedata.combining(c)).lower().strip()


def limpiar_texto(texto: str) -> str:
    texto = re.sub(r"https?://\S+", " ", texto)
    texto = re.sub(r"@\w+", " ", texto)
    texto = re.sub(r"[^\w\sáéíóúüñÁÉÍÓÚÜÑ]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def clasificar_comentario_lexico(texto: str) -> str:
    """Clasificación léxica de regla simple para contingencias."""
    palabras = [sin_acentos(w) for w in re.findall(r"[a-záéíóúüñ]+", texto, re.I)]
    pos = sum(w in POSITIVAS for w in palabras)
    neg = sum(w in NEGATIVAS for w in palabras)
    if pos > neg:
        return "positivo"
    if neg > pos:
        return "negativo"
    return "neutro"


def analizar_sentimiento_lexico(textos: list[str]) -> tuple[dict, dict]:
    conteo: Counter = Counter()
    for t in textos:
        conteo[clasificar_comentario_lexico(t)] += 1
    total = sum(conteo.values()) or 1
    porcentajes = {
        "positivo": round(conteo["positivo"] / total * 100, 1),
        "neutro":   round(conteo["neutro"]   / total * 100, 1),
        "negativo": round(conteo["negativo"] / total * 100, 1),
    }
    distribucion = dict(conteo)
    distribucion.setdefault("positivo", 0)
    distribucion.setdefault("neutro",   0)
    distribucion.setdefault("negativo", 0)
    return porcentajes, distribucion


def extraer_ngramas_estadisticos(
    textos: list[str],
    n: int = 2,
    top_k: int = 20,
    comentarios_objs: Optional[list[dict]] = None,
) -> list[dict]:
    if not textos:
        return []
    try:
        from sklearn.feature_extraction.text import CountVectorizer  # noqa: PLC0415
        corpus = [" ".join(limpiar_texto(c) for c in textos)]
        vec = CountVectorizer(
            ngram_range=(n, n),
            stop_words=list(STOP_ES),
            min_df=1,
            max_features=500,
        )
        X = vec.fit_transform(corpus)
        nombres = vec.get_feature_names_out()
        valores = X.toarray()[0]
        pares = sorted(zip(nombres, valores), key=lambda x: x[1], reverse=True)
        resultado = []
        c_objs = comentarios_objs or []
        for t, f in pares[:top_k]:
            if f <= 0:
                continue
            asociados = [
                c for c in c_objs
                if t in limpiar_texto(c.get("texto", ""))
            ]
            pos = sum(1 for c in asociados if c.get("tono") == "positivo")
            neg = sum(1 for c in asociados if c.get("tono") == "negativo")
            neu = sum(1 for c in asociados if c.get("tono") == "neutro")

            tono = "negativo" if neg > pos else ("positivo" if pos > neg else clasificar_comentario_lexico(t))

            resultado.append({
                "texto": t,
                "frecuencia": int(f),
                "tono": tono,
                "explicacion": "Mención recurrente (extracción estadística de respaldo)",
                "desglose": {"positivo": pos, "negativo": neg, "neutro": neu},
                "comentarios": [
                    {
                        "texto": c.get("texto", ""),
                        "tono": c.get("tono", "neutro"),
                        "autor_nombre": c.get("autor_nombre") or "Ciudadano",
                        "autor_url": c.get("autor_url"),
                        "comentario_url": c.get("comentario_url"),
                        "likes": c.get("likes", 0),
                    }
                    for c in asociados[:10]
                ],
            })
        return resultado
    except Exception as exc:
        log.warning("Error extrayendo n-gramas estadísticos: %s", exc)
        return []


def generar_resumen_extractivo(url_perfil: str, sentimiento: dict, ngramas: list[dict], total: int) -> str:
    tono_principal = max(sentimiento, key=sentimiento.get) if sentimiento else "neutro"
    temas = ", ".join(ng["texto"] for ng in ngramas[:6]) or "sin frases dominantes"
    return (
        f"[ANÁLISIS DE RESPALDO LÉXICO] Se analizaron {total} comentarios del perfil {url_perfil}. "
        f"El {sentimiento.get(tono_principal, 0):.1f}% de las menciones se encuadran en tono {tono_principal}. "
        f"Los términos y patrones más recurrentes detectados fueron: {temas}. "
        "Nota: Este análisis se generó mediante el sistema léxico de contingencia al no estar disponible el modelo de lenguaje."
    )


def analizar_perfil_fallback(solicitud: SolicitudSocial, error_origen: str = "") -> ResultadoSocial:
    """Ejecuta el análisis de respaldo cuando el pipeline LLM no puede completarse."""
    url_p = str(solicitud.url_perfil).strip()
    log.info("Ejecutando análisis de respaldo léxico para %s (motivo: %s)", url_p, error_origen)

    asegurar_esquema_social()
    posts_meta = db_obtener_posts_meta(url_p, limite=solicitud.max_posts)
    post_urls = [p["url"] for p in posts_meta] if posts_meta else None
    comentarios = db_obtener_comentarios(
        url_p,
        post_urls=post_urls,
        limite_por_post=solicitud.max_comentarios,
    )
    textos = [c["texto"] for c in comentarios]

    if not comentarios:
        return ResultadoSocial(
            url_perfil=url_p,
            posts_analizados=0,
            total_comentarios=0,
            sentimiento={"positivo": 0.0, "neutro": 100.0, "negativo": 0.0},
            distribucion={"positivo": 0, "neutro": 0, "negativo": 0},
            ngramas=[],
            resumen="No hay comentarios disponibles para analizar.",
            metodo_resumen="extractivo (respaldo)",
            error=f"Falló el LLM ({error_origen}) y no se encontraron comentarios previos en base de datos.",
        )

    # Clasificar comentarios con léxico
    for c in comentarios:
        c["tono"] = clasificar_comentario_lexico(c["texto"])

    sentimiento, distribucion = analizar_sentimiento_lexico(textos)
    ngramas = extraer_ngramas_estadisticos(textos, n=2, top_k=15, comentarios_objs=comentarios)
    resumen = generar_resumen_extractivo(url_p, sentimiento, ngramas, len(comentarios))

    # Seleccionar top clasificados
    negativos = [c for c in comentarios if c.get("tono") == "negativo"]
    positivos = [c for c in comentarios if c.get("tono") == "positivo"]
    neutros   = [c for c in comentarios if c.get("tono") == "neutro"]

    negativos.sort(key=lambda x: x.get("likes", 0), reverse=True)
    positivos.sort(key=lambda x: x.get("likes", 0), reverse=True)
    neutros.sort(key=lambda x: x.get("likes", 0), reverse=True)

    seleccionados: list[ComentarioClasificado] = []
    for grupo in (negativos[:10], positivos[:10], neutros[:10]):
        for item in grupo:
            seleccionados.append(
                ComentarioClasificado(
                    texto=item["texto"],
                    tono=item.get("tono", "neutro"),
                    autor_nombre=item.get("autor_nombre"),
                    autor_url=item.get("autor_url"),
                    comentario_url=item.get("comentario_url"),
                    likes=item.get("likes", 0),
                )
            )

    pos = float(sentimiento.get("positivo", 0.0))
    neg = float(sentimiento.get("negativo", 0.0))
    aprobacion_pct = round(pos - neg, 1)

    resultado = ResultadoSocial(
        url_perfil=url_p,
        posts_analizados=max(len(posts_meta), 1),
        total_comentarios=len(comentarios),
        sentimiento=sentimiento,
        distribucion=distribucion,
        ngramas=ngramas,
        resumen=resumen,
        metodo_resumen="extractivo (respaldo)",
        comentarios_clasificados=seleccionados,
        aprobacion_pct=aprobacion_pct,
    )

    return resultado
