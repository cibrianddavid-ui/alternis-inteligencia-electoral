"""Análisis temporal histórico por perfil basado exclusivamente en la base de datos local SQLite.

Principios:
  - NO llama a Apify bajo ninguna circunstancia (gasto $0.00).
  - NO busca datos faltantes en APIs externas.
  - Utiliza todos los posts históricos disponibles en BD para el perfil dado (sin max_posts).
  - Agrupa por semana o mes según lo solicitado.
  - Calcula en Python:
      * total de posts
      * total de comentarios
      * % positivo, % negativo, % neutro
      * balance_postura = positivo - negativo
      * diferencias respecto al periodo anterior
  - Reutiliza comentarios ya clasificados y sus tonos.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

from .. import basedatos
from .db import _inferir_nombre_perfil
from .models import _limpiar_url_facebook, _parse_datetime

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modelos Pydantic de Entrada / Salida
# ---------------------------------------------------------------------------


class SolicitudHistorico(BaseModel):
    """Solicitud de análisis histórico local para un perfil."""

    url_perfil: str = Field(
        min_length=5,
        max_length=512,
        description="URL pública de la página o perfil de Facebook a consultar en la BD.",
    )
    agrupacion: Literal["semana", "mes"] = Field(
        default="mes",
        description="Nivel de agrupación temporal: 'semana' o 'mes'.",
    )


class PuntoSerieTemporal(BaseModel):
    """Métricas agregadas para un periodo temporal específico (semana o mes)."""

    periodo: str = Field(description="Identificador del periodo (ej. '2026-01' o '2026-W38').")
    etiqueta: str = Field(default="", description="Etiqueta legible para mostrar en gráficas y tablas.")
    fecha_inicio: Optional[str] = Field(default=None, description="Fecha de inicio del periodo (YYYY-MM-DD).")
    fecha_fin: Optional[str] = Field(default=None, description="Fecha de fin del periodo (YYYY-MM-DD).")
    posts: int = Field(default=0, description="Cantidad de publicaciones registradas en este periodo.")
    comentarios: int = Field(default=0, description="Total de comentarios asociados a este periodo.")
    comentarios_clasificados: int = Field(default=0, description="Total de comentarios con postura clasificada.")
    positivo: float = Field(default=0.0, description="Porcentaje de comentarios con postura positiva.")
    negativo: float = Field(default=0.0, description="Porcentaje de comentarios con postura negativa.")
    neutro: float = Field(default=0.0, description="Porcentaje de comentarios con postura neutra.")
    balance_postura: float = Field(default=0.0, description="Balance neto = positivo - negativo.")
    diferencia_balance: Optional[float] = Field(default=None, description="Diferencia en balance vs periodo anterior.")
    diferencia_positivo: Optional[float] = Field(default=None, description="Diferencia en % positivo vs periodo anterior.")
    diferencia_negativo: Optional[float] = Field(default=None, description="Diferencia en % negativo vs periodo anterior.")
    diferencia_neutro: Optional[float] = Field(default=None, description="Diferencia en % neutro vs periodo anterior.")


class PerfilDisponibleBD(BaseModel):
    """Resumen de datos históricos disponibles en la BD para un perfil."""

    url_perfil: str
    perfil_nombre: str
    total_posts: int
    total_comentarios: int
    fecha_min: Optional[str] = None
    fecha_max: Optional[str] = None


class ResultadoHistorico(BaseModel):
    """Respuesta completa del análisis temporal histórico."""

    url_perfil: str
    perfil_nombre: str
    agrupacion: Literal["semana", "mes"]
    total_posts: int
    total_comentarios: int
    total_clasificados: int
    resumen_ejecutivo: str = Field(default="", description="Síntesis narrativa ejecutiva de la evolución histórica.")
    serie_temporal: List[PuntoSerieTemporal]


# ---------------------------------------------------------------------------
# Helpers de fechas y claves de agrupación
# ---------------------------------------------------------------------------

MESES_ES = {
    1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"
}

MESES_COMPLETO_ES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
}


def _obtener_clave_periodo(dt: datetime, agrupacion: str) -> tuple[str, str, str, str]:
    """Genera (clave_orden, etiqueta, fecha_inicio_str, fecha_fin_str) según la agrupación."""
    if agrupacion == "semana":
        iso_year, iso_week, _ = dt.isocalendar()
        clave = f"{iso_year}-W{iso_week:02d}"
        
        # Calcular lunes y domingo de la semana ISO
        lunes = dt.date() - timedelta(days=dt.weekday())
        domingo = lunes + timedelta(days=6)
        
        f_ini = lunes.strftime("%Y-%m-%d")
        f_fin = domingo.strftime("%Y-%m-%d")
        
        # Etiqueta amigable: "Semana 38 (14-20 Sep 2026)"
        mes_lunes = MESES_ES.get(lunes.month, "")
        mes_domingo = MESES_ES.get(domingo.month, "")
        if mes_lunes == mes_domingo:
            rango = f"{lunes.day}–{domingo.day} {mes_lunes} {domingo.year}"
        else:
            rango = f"{lunes.day} {mes_lunes} – {domingo.day} {mes_domingo} {domingo.year}"
        etiqueta = f"Sem {iso_week} ({rango})"
        return clave, etiqueta, f_ini, f_fin

    # agrupacion == "mes"
    clave = dt.strftime("%Y-%m")
    mes_nombre = MESES_COMPLETO_ES.get(dt.month, dt.strftime("%B"))
    etiqueta = f"{mes_nombre} {dt.year}"
    
    # Inicio y fin del mes
    f_ini = dt.strftime("%Y-%m-01")
    # Último día del mes: primer día del siguiente mes - 1 día
    if dt.month == 12:
        sig_mes = dt.replace(year=dt.year + 1, month=1, day=1)
    else:
        sig_mes = dt.replace(month=dt.month + 1, day=1)
    fin_mes = sig_mes - timedelta(days=1)
    f_fin = fin_mes.strftime("%Y-%m-%d")
    
    return clave, etiqueta, f_ini, f_fin


# ---------------------------------------------------------------------------
# Consultas directas a SQLite (sin Apify)
# ---------------------------------------------------------------------------


def obtener_perfiles_en_bd() -> list[PerfilDisponibleBD]:
    """Lista todos los perfiles de Facebook que cuentan con datos en social_posts."""
    with basedatos.conectar() as conn:
        filas = conn.execute(
            """
            SELECT 
                p.url_perfil,
                COALESCE(NULLIF(p.perfil_nombre, ''), 'Facebook') as perfil_nombre,
                COUNT(DISTINCT p.post_url) as total_posts,
                MIN(p.fecha_post) as fecha_min,
                MAX(p.fecha_post) as fecha_max
            FROM social_posts p
            WHERE p.url_perfil IS NOT NULL AND p.url_perfil != ''
            GROUP BY p.url_perfil
            ORDER BY total_posts DESC
            """
        ).fetchall()

        # Contar comentarios por perfil
        comms_por_perfil: dict[str, int] = {}
        for row in conn.execute(
            "SELECT url_perfil, COUNT(*) as c FROM social_comentarios GROUP BY url_perfil"
        ).fetchall():
            comms_por_perfil[str(row[0])] = int(row[1])

    resultados: list[PerfilDisponibleBD] = []
    for f in filas:
        u = str(f["url_perfil"])
        nom = _inferir_nombre_perfil(u, str(f["perfil_nombre"] or "Facebook"))
        c_count = comms_por_perfil.get(u, 0)
        resultados.append(
            PerfilDisponibleBD(
                url_perfil=u,
                perfil_nombre=nom,
                total_posts=int(f["total_posts"] or 0),
                total_comentarios=c_count,
                fecha_min=str(f["fecha_min"] or "")[:10] or None,
                fecha_max=str(f["fecha_max"] or "")[:10] or None,
            )
        )
    return resultados


def _resumen_historico_extractivo(
    perfil_nombre: str,
    agrupacion: str,
    serie: list[PuntoSerieTemporal],
) -> str:
    """Genera un resumen analítico estructurado en español sin LLM."""
    if not serie:
        return "No hay suficientes publicaciones registradas para generar la síntesis histórica."

    n = len(serie)
    tipo_periodo = "semanas" if agrupacion == "semana" else "meses"
    primer_p = serie[0]
    ultimo_p = serie[-1]

    p_mejor = max(serie, key=lambda x: x.balance_postura)
    p_peor = min(serie, key=lambda x: x.balance_postura)
    p_mas_coms = max(serie, key=lambda x: x.comentarios)

    dif_neta = round(ultimo_p.balance_postura - primer_p.balance_postura, 1)
    if dif_neta > 3.0:
        tendencia = f"una tendencia general al alza (+{dif_neta} pp de mejora en el balance neto de postura)"
    elif dif_neta < -3.0:
        tendencia = f"una tendencia general a la baja ({dif_neta} pp de retroceso en el balance neto de postura)"
    else:
        tendencia = "un posicionamiento y balance relativamente estable a lo largo del tiempo"

    lineas = [
        f"Se analizó la evolución histórica de «{perfil_nombre}» a lo largo de {n} {tipo_periodo} "
        f"(desde {primer_p.etiqueta or primer_p.periodo} hasta {ultimo_p.etiqueta or ultimo_p.periodo}), "
        f"mostrando {tendencia}.",
        f"El momento de mayor favorabilidad ciudadana se registró en {p_mejor.etiqueta or p_mejor.periodo} con un balance neto de {p_mejor.balance_postura:+.1f}% "
        f"({p_mejor.positivo}% positivo frente a {p_mejor.negativo}% negativo). "
        f"En contraste, el lapso con mayor nivel de crítica social fue {p_peor.etiqueta or p_peor.periodo} "
        f"con un balance neto de {p_peor.balance_postura:+.1f}%.",
        f"El periodo con mayor intensidad de conversación pública fue {p_mas_coms.etiqueta or p_mas_coms.periodo}, "
        f"acumulando {p_mas_coms.comentarios} comentarios distribuidos en {p_mas_coms.posts} publicaciones analizadas.",
    ]
    return " ".join(lineas)


def _generar_resumen_historico(
    perfil_nombre: str,
    agrupacion: str,
    serie: list[PuntoSerieTemporal],
    client: Any = None,
    model_name: str = "openai/gpt-oss-120b",
) -> str:
    """Genera la síntesis ejecutiva utilizando Groq LLM o fallback extractivo."""
    if not serie:
        return ""
    if client is None:
        return _resumen_historico_extractivo(perfil_nombre, agrupacion, serie)

    payload = {
        "profile_name": perfil_nombre,
        "grouping": agrupacion,
        "total_periods": len(serie),
        "periods_data": [
            {
                "period": p.etiqueta or p.periodo,
                "posts": p.posts,
                "comments": p.comentarios,
                "positive_pct": p.positivo,
                "negative_pct": p.negativo,
                "neutral_pct": p.neutro,
                "stance_balance": p.balance_postura,
                "delta_vs_prev": p.diferencia_balance,
            }
            for p in serie
        ],
    }

    prompt_sistema = (
        "You are a senior political intelligence and campaign strategist. "
        "Analyze the provided historical time-series data of citizen sentiment and stance on social media. "
        "Write a concise executive briefing (2-3 paragraphs) in formal, objective SPANISH. "
        "Highlight: 1) The overall trajectory and trend of net stance over time, "
        "2) Critical peak periods of support or pushback, and 3) Strategic implications for political communication. "
        "Do not invent external facts; rely strictly on the provided timeline figures."
    )

    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.3,
            max_tokens=650,
        )
        texto = resp.choices[0].message.content.strip()
        if texto:
            return texto
    except Exception as exc:
        log.warning("Fallo generando resumen histórico con LLM: %s. Usando fallback extractivo.", exc)

    return _resumen_historico_extractivo(perfil_nombre, agrupacion, serie)


def analizar_historico_perfil(
    url_perfil: str,
    agrupacion: Literal["semana", "mes"] = "mes",
    groq_client: Any = None,
    groq_model: str = "openai/gpt-oss-120b",
) -> ResultadoHistorico:
    """Calcula la evolución de la postura histórica en comentarios agrupada por semana o mes.

    Garantías:
      - 100% offline respecto a APIs externas.
      - 0 llamadas a Apify.
      - Utiliza todos los posts históricos disponibles en SQLite sin límite superior.
      - Genera resumen ejecutivo con IA (Groq) o extractivo si no hay cliente.
    """
    if not url_perfil or not str(url_perfil).strip():
        raise ValueError("Se requiere una URL de perfil válida.")

    u_raw = str(url_perfil).strip()
    u_limpia = _limpiar_url_facebook(u_raw)
    u_sin = u_raw.rstrip("/")
    u_con = u_sin + "/"
    u_limpia_sin = u_limpia.rstrip("/")
    u_limpia_con = u_limpia_sin + "/"

    candidatos_url = list(
        dict.fromkeys([u_raw, u_sin, u_con, u_limpia, u_limpia_sin, u_limpia_con])
    )
    ph_perfil = ",".join("?" for _ in candidatos_url)

    with basedatos.conectar() as conn:
        # 1. Obtener todas las publicaciones del perfil guardadas en la BD
        posts_filas = conn.execute(
            f"""
            SELECT post_url, post_id, texto_post, fecha_post, post_likes, perfil_nombre, url_perfil, canonical_url
            FROM social_posts
            WHERE url_perfil IN ({ph_perfil}) OR canonical_url IN ({ph_perfil})
            ORDER BY COALESCE(fecha_post, '') ASC
            """,
            candidatos_url + candidatos_url,
        ).fetchall()

        # Si no encontramos por match exacto, intentar coincidencia por slug si es seguro
        if not posts_filas:
            slug = u_limpia_sin.split("/")[-1]
            if len(slug) >= 4 and not slug.isdigit():
                posts_filas = conn.execute(
                    """
                    SELECT post_url, post_id, texto_post, fecha_post, post_likes, perfil_nombre, url_perfil, canonical_url
                    FROM social_posts
                    WHERE url_perfil LIKE ? OR post_url LIKE ?
                    ORDER BY COALESCE(fecha_post, '') ASC
                    """,
                    (f"%{slug}%", f"%{slug}%"),
                ).fetchall()

        if not posts_filas:
            perfil_nom = _inferir_nombre_perfil(u_raw, "")
            return ResultadoHistorico(
                url_perfil=u_raw,
                perfil_nombre=perfil_nom,
                agrupacion=agrupacion,
                total_posts=0,
                total_comentarios=0,
                total_clasificados=0,
                serie_temporal=[],
            )

        perfil_nom = _inferir_nombre_perfil(
            str(posts_filas[0]["url_perfil"] or u_raw),
            str(posts_filas[0]["perfil_nombre"] or ""),
        )

        # Mapear fecha por post
        post_dt_map: dict[str, datetime] = {}
        todos_post_urls: set[str] = set()

        for p in posts_filas:
            purl = str(p["post_url"] or "").strip()
            if not purl:
                continue
            todos_post_urls.add(purl)
            todos_post_urls.add(purl.rstrip("/"))
            canonical = str(p["canonical_url"] or "").strip()
            if canonical:
                todos_post_urls.add(canonical)
                todos_post_urls.add(canonical.rstrip("/"))

            f_str = p["fecha_post"]
            dt = _parse_datetime(f_str)
            if dt is not None:
                post_dt_map[purl] = dt
                post_dt_map[purl.rstrip("/")] = dt
                if canonical:
                    post_dt_map[canonical] = dt
                    post_dt_map[canonical.rstrip("/")] = dt

        # 2. Cargar todos los comentarios asociados a este perfil o a estos posts
        purls_lista = list(todos_post_urls)
        ph_posts = ",".join("?" for _ in purls_lista)

        comms_filas = conn.execute(
            f"""
            SELECT id, post_url, texto, likes, tono, analizado, fecha_comentario
            FROM social_comentarios
            WHERE url_perfil IN ({ph_perfil}) OR post_url IN ({ph_posts})
            """,
            candidatos_url + purls_lista,
        ).fetchall()

    # 3. Agrupación en Python
    # Estructura de agregación por periodo
    periodos_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "etiqueta": "",
            "fecha_inicio": "",
            "fecha_fin": "",
            "posts_set": set(),
            "comentarios_total": 0,
            "conteo_pos": 0,
            "conteo_neg": 0,
            "conteo_neu": 0,
            "clasificados": 0,
        }
    )

    # 3a. Distribuir posts en periodos
    for p in posts_filas:
        purl = str(p["post_url"] or "").strip()
        dt = post_dt_map.get(purl) or post_dt_map.get(purl.rstrip("/"))
        if dt is None:
            continue
        clave, etiqueta, f_ini, f_fin = _obtener_clave_periodo(dt, agrupacion)
        data_p = periodos_data[clave]
        data_p["etiqueta"] = etiqueta
        data_p["fecha_inicio"] = f_ini
        data_p["fecha_fin"] = f_fin
        data_p["posts_set"].add(purl)

    # 3b. Distribuir comentarios en periodos
    for c in comms_filas:
        purl = str(c["post_url"] or "").strip()
        # Fecha preferida: fecha_comentario si existe, fallback a fecha_post
        f_comm = c["fecha_comentario"]
        dt = _parse_datetime(f_comm) if f_comm else None
        if dt is None:
            dt = post_dt_map.get(purl) or post_dt_map.get(purl.rstrip("/"))

        if dt is None:
            # Si no hay fecha ni del comentario ni del post, no podemos ubicarlo en la serie
            continue

        clave, etiqueta, f_ini, f_fin = _obtener_clave_periodo(dt, agrupacion)
        data_p = periodos_data[clave]
        if not data_p["etiqueta"]:
            data_p["etiqueta"] = etiqueta
            data_p["fecha_inicio"] = f_ini
            data_p["fecha_fin"] = f_fin

        data_p["comentarios_total"] += 1

        tono = str(c["tono"] or "").lower().strip()
        if tono == "positivo":
            data_p["conteo_pos"] += 1
            data_p["clasificados"] += 1
        elif tono == "negativo":
            data_p["conteo_neg"] += 1
            data_p["clasificados"] += 1
        elif tono == "neutro":
            data_p["conteo_neu"] += 1
            data_p["clasificados"] += 1

    # 4. Construir serie temporal ordenada cronológicamente
    serie_temporal: list[PuntoSerieTemporal] = []
    claves_ordenadas = sorted(periodos_data.keys())

    prev_punto: Optional[PuntoSerieTemporal] = None

    total_posts_sum = len({str(p["post_url"]) for p in posts_filas if p["post_url"]})
    total_comms_sum = len(comms_filas)
    total_clasificados_sum = 0

    for clave in claves_ordenadas:
        info = periodos_data[clave]
        n_clasificados = info["clasificados"]
        total_clasificados_sum += n_clasificados

        if n_clasificados > 0:
            pos_pct = round(info["conteo_pos"] / n_clasificados * 100.0, 1)
            neg_pct = round(info["conteo_neg"] / n_clasificados * 100.0, 1)
            neu_pct = round(info["conteo_neu"] / n_clasificados * 100.0, 1)
            bal = round(pos_pct - neg_pct, 1)
        else:
            pos_pct = 0.0
            neg_pct = 0.0
            neu_pct = 0.0
            bal = 0.0

        dif_bal = round(bal - prev_punto.balance_postura, 1) if prev_punto else None
        dif_pos = round(pos_pct - prev_punto.positivo, 1) if prev_punto else None
        dif_neg = round(neg_pct - prev_punto.negativo, 1) if prev_punto else None
        dif_neu = round(neu_pct - prev_punto.neutro, 1) if prev_punto else None

        punto = PuntoSerieTemporal(
            periodo=clave,
            etiqueta=info["etiqueta"] or clave,
            fecha_inicio=info["fecha_inicio"] or None,
            fecha_fin=info["fecha_fin"] or None,
            posts=len(info["posts_set"]),
            comentarios=info["comentarios_total"],
            comentarios_clasificados=n_clasificados,
            positivo=pos_pct,
            negativo=neg_pct,
            neutro=neu_pct,
            balance_postura=bal,
            diferencia_balance=dif_bal,
            diferencia_positivo=dif_pos,
            diferencia_negativo=dif_neg,
            diferencia_neutro=dif_neu,
        )
        serie_temporal.append(punto)
        prev_punto = punto

    log.info(
        "Histórico para '%s' (%s): %d periodos calculados con %d posts y %d comentarios.",
        url_perfil,
        agrupacion,
        len(serie_temporal),
        total_posts_sum,
        total_comms_sum,
    )

    resumen_ejecutivo = _generar_resumen_historico(
        perfil_nombre=perfil_nom,
        agrupacion=agrupacion,
        serie=serie_temporal,
        client=groq_client,
        model_name=groq_model,
    )

    return ResultadoHistorico(
        url_perfil=u_raw,
        perfil_nombre=perfil_nom,
        agrupacion=agrupacion,
        total_posts=total_posts_sum,
        total_comentarios=total_comms_sum,
        total_clasificados=total_clasificados_sum,
        resumen_ejecutivo=resumen_ejecutivo,
        serie_temporal=serie_temporal,
    )
