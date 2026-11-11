"""Generación de reportes PDF para los distintos modos de Social Listening.

Soporta los 4 funcionamientos del módulo:
1. 'perfil': Análisis integral de un perfil público de Facebook.
2. 'posts': Análisis detallado de publicaciones específicas.
3. 'comparativa': Comparativa inter-posts con contrastes y ejes temáticos compartidos.
4. 'temporal': Evolución temporal de percepción ciudadana y temas por periodos.
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import date
from typing import Any, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.informes import _registrar_fuentes

# ---------------------------------------------------------------------------
# Constantes visuales y paleta de la aplicación
# ---------------------------------------------------------------------------

MARGEN = 40.0
ANCHO_DISPONIBLE = letter[0] - (2 * MARGEN)  # 532 pt en carta

COLOR_NAVY = colors.HexColor("#0f172a")        # Slate 900
COLOR_NAVY_LIGHT = colors.HexColor("#1e293b")  # Slate 800
COLOR_INDIGO = colors.HexColor("#4338ca")      # Indigo 700
COLOR_INDIGO_BG = colors.HexColor("#e0e7ff")   # Indigo 100
COLOR_GRIS = colors.HexColor("#64748b")        # Slate 500
COLOR_GRIS_FONDO = colors.HexColor("#f8fafc")  # Slate 50
COLOR_LINEA = colors.HexColor("#e2e8f0")       # Slate 200

COLOR_VERDE = colors.HexColor("#15803d")       # Green 700
COLOR_VERDE_BG = colors.HexColor("#dcfce7")    # Green 100
COLOR_ROJO = colors.HexColor("#b91c1c")        # Red 700
COLOR_ROJO_BG = colors.HexColor("#fee2e2")     # Red 100
COLOR_AMBAR = colors.HexColor("#b45309")       # Amber 700
COLOR_AMBAR_BG = colors.HexColor("#fef3c7")    # Amber 100


# ---------------------------------------------------------------------------
# Helpers de saneamiento de texto y fuentes
# ---------------------------------------------------------------------------


def _limpio(valor: Any, limite: Optional[int] = None, saltos: bool = False) -> str:
    """Sanea texto eliminando emojis astrales y escapando HTML para Paragraph de ReportLab."""
    if valor is None:
        return ""
    texto = re.sub(r"[\U00010000-\U0010ffff]", "", str(valor))
    if not saltos:
        texto = re.sub(r"\s+", " ", texto).strip()
    else:
        texto = texto.strip()
    if limite and len(texto) > limite:
        texto = texto[: limite - 1].rstrip() + "…"
    escapado = escape(texto)
    if saltos:
        escapado = escapado.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
    return escapado


def _slug(texto: str) -> str:
    """Genera un slug alfanumérico seguro para el nombre del archivo descargado."""
    ascii_ = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_).strip("_").lower()[:32] or "social"


def _inferir_nombre(url_o_texto: str) -> str:
    """Extrae un identificador amigable del candidato a partir de la URL."""
    limpia = re.sub(r"^https?://(www\.)?facebook\.com/", "", str(url_o_texto or "")).strip("/")
    partes = [p for p in limpia.split("/") if p and p not in ("posts", "videos", "reel", "profile.php")]
    if partes:
        return partes[0].replace(".", "_")
    return "perfil"


def _estilos(normal: str, negrita: str) -> dict[str, ParagraphStyle]:
    return {
        "kicker": ParagraphStyle(
            "kicker",
            fontName=negrita,
            fontSize=7.5,
            leading=9.5,
            textColor=COLOR_INDIGO,
            spaceAfter=2,
            textTransform="uppercase",
        ),
        "titulo": ParagraphStyle(
            "titulo",
            fontName=negrita,
            fontSize=16,
            leading=20,
            textColor=COLOR_NAVY,
            spaceAfter=3,
        ),
        "sub": ParagraphStyle(
            "sub",
            fontName=normal,
            fontSize=8.5,
            leading=12,
            textColor=COLOR_GRIS,
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2",
            fontName=negrita,
            fontSize=11.5,
            leading=15,
            textColor=COLOR_NAVY,
            spaceBefore=10,
            spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "h3",
            fontName=negrita,
            fontSize=9.5,
            leading=12.5,
            textColor=COLOR_NAVY_LIGHT,
            spaceBefore=6,
            spaceAfter=3,
        ),
        "cuerpo": ParagraphStyle(
            "cuerpo",
            fontName=normal,
            fontSize=8.5,
            leading=12.5,
            textColor=COLOR_NAVY_LIGHT,
            spaceAfter=4,
        ),
        "cuerpo_b": ParagraphStyle(
            "cuerpo_b",
            fontName=negrita,
            fontSize=8.5,
            leading=12.5,
            textColor=COLOR_NAVY_LIGHT,
        ),
        "quote": ParagraphStyle(
            "quote",
            fontName=normal,
            fontSize=8,
            leading=11.5,
            textColor=colors.HexColor("#334155"),
        ),
        "chico": ParagraphStyle(
            "chico",
            fontName=normal,
            fontSize=7.5,
            leading=10,
            textColor=COLOR_GRIS,
        ),
        "celda": ParagraphStyle(
            "celda",
            fontName=normal,
            fontSize=8,
            leading=11,
            textColor=COLOR_NAVY_LIGHT,
        ),
        "celda_c": ParagraphStyle(
            "celda_c",
            fontName=normal,
            fontSize=8,
            leading=11,
            textColor=COLOR_NAVY_LIGHT,
            alignment=1,  # Center
        ),
        "celda_b": ParagraphStyle(
            "celda_b",
            fontName=negrita,
            fontSize=8,
            leading=11,
            textColor=COLOR_NAVY,
        ),
        "celda_bc": ParagraphStyle(
            "celda_bc",
            fontName=negrita,
            fontSize=8,
            leading=11,
            textColor=COLOR_NAVY,
            alignment=1,  # Center
        ),
        "celda_h": ParagraphStyle(
            "celda_h",
            fontName=negrita,
            fontSize=8,
            leading=11,
            textColor=COLOR_NAVY,
        ),
        "celda_hc": ParagraphStyle(
            "celda_hc",
            fontName=negrita,
            fontSize=8,
            leading=11,
            textColor=COLOR_NAVY,
            alignment=1,  # Center
        ),
        "kpi_num": ParagraphStyle(
            "kpi_num",
            fontName=negrita,
            fontSize=15,
            leading=17,
            alignment=1,
        ),
        "kpi_lbl": ParagraphStyle(
            "kpi_lbl",
            fontName=negrita,
            fontSize=7.5,
            leading=9.5,
            textColor=COLOR_GRIS,
            alignment=1,
            textTransform="uppercase",
        ),
    }


def _crear_pie(titulo_doc: str, normal_font: str):
    """Genera callback para encabezado sutil y pie de página en cada hoja."""
    def pie(lienzo, doc):
        lienzo.saveState()
        lienzo.setFont(normal_font, 7.5)
        lienzo.setFillColor(COLOR_GRIS)
        # Pie de página
        lienzo.drawString(MARGEN, 25, f"ALTERNIS · Inteligencia Electoral | {titulo_doc}")
        lienzo.drawRightString(letter[0] - MARGEN, 25, f"Página {doc.page}")
        # Encabezado en páginas subsiguientes
        if doc.page > 1:
            lienzo.setStrokeColor(COLOR_LINEA)
            lienzo.setLineWidth(0.5)
            lienzo.line(MARGEN, letter[1] - 32, letter[0] - MARGEN, letter[1] - 32)
            lienzo.drawString(MARGEN, letter[1] - 26, titulo_doc[:90])
        lienzo.restoreState()
    return pie


# ---------------------------------------------------------------------------
# Bloques visuales reutilizables
# ---------------------------------------------------------------------------


def _bloque_kpis_sentimiento(
    pos_pct: float,
    neu_pct: float,
    neg_pct: float,
    balance: float,
    est: dict,
    ancho: float,
) -> Table:
    """Renderiza 4 tarjetas horizontales de KPI con porcentajes y balance neto."""
    bal_signo = "+" if balance > 0 else ""
    bal_color = "#15803d" if balance > 10 else ("#b91c1c" if balance < -15 else "#b45309")
    bal_bg = "#dcfce7" if balance > 10 else ("#fee2e2" if balance < -15 else "#fef3c7")

    c_pos = Paragraph(f'<font color="#15803d">{pos_pct:.1f}%</font>', est["kpi_num"])
    l_pos = Paragraph("Positivo", est["kpi_lbl"])

    c_neu = Paragraph(f'<font color="#64748b">{neu_pct:.1f}%</font>', est["kpi_num"])
    l_neu = Paragraph("Neutro", est["kpi_lbl"])

    c_neg = Paragraph(f'<font color="#b91c1c">{neg_pct:.1f}%</font>', est["kpi_num"])
    l_neg = Paragraph("Negativo", est["kpi_lbl"])

    c_bal = Paragraph(f'<font color="{bal_color}">{bal_signo}{balance:.1f}%</font>', est["kpi_num"])
    l_bal = Paragraph("Balance Neto", est["kpi_lbl"])

    datos = [
        [l_pos, l_neu, l_neg, l_bal],
        [c_pos, c_neu, c_neg, c_bal],
    ]
    col_w = ancho / 4.0
    tabla = Table(datos, colWidths=[col_w] * 4)
    tabla.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, 1), colors.HexColor("#f0fdf4")),
            ("BACKGROUND", (1, 0), (1, 1), COLOR_GRIS_FONDO),
            ("BACKGROUND", (2, 0), (2, 1), colors.HexColor("#fef2f2")),
            ("BACKGROUND", (3, 0), (3, 1), colors.HexColor(bal_bg)),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
            ("LINEABOVE", (0, 0), (0, 0), 2.5, COLOR_VERDE),
            ("LINEABOVE", (1, 0), (1, 0), 2.5, COLOR_GRIS),
            ("LINEABOVE", (2, 0), (2, 0), 2.5, COLOR_ROJO),
            ("LINEABOVE", (3, 0), (3, 0), 2.5, colors.HexColor(bal_color)),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
            ("TOPPADDING", (0, 1), (-1, 1), 2),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )
    return tabla


def _barra_sentimiento_horizontal(
    pos_pct: float, neu_pct: float, neg_pct: float, ancho: float
) -> Optional[Table]:
    """Genera barra visual horizontal segmentada con colores verde, gris y rojo."""
    total = pos_pct + neu_pct + neg_pct
    if total <= 0:
        return None
    w_pos = max(1.0, ancho * (pos_pct / total))
    w_neu = max(1.0, ancho * (neu_pct / total))
    w_neg = max(1.0, ancho * (neg_pct / total))
    # Ajuste fino para sumar exactamente ancho
    suma_w = w_pos + w_neu + w_neg
    col_widths = [ancho * (w / suma_w) for w in (w_pos, w_neu, w_neg)]

    t = Table([["", "", ""]], colWidths=col_widths, rowHeights=[6])
    t.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), COLOR_VERDE),
            ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#94a3b8")),
            ("BACKGROUND", (2, 0), (2, 0), COLOR_ROJO),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    return t


# ---------------------------------------------------------------------------
# 1. Reporte PDF: Perfil de Facebook
# ---------------------------------------------------------------------------


def pdf_perfil(data: dict[str, Any]) -> bytes:
    """Genera reporte PDF del análisis de perfil público de Facebook."""
    normal, negrita = _registrar_fuentes()
    est = _estilos(normal, negrita)
    url_perfil = str(data.get("url_perfil") or "").strip()
    nombre_perfil = _inferir_nombre(url_perfil)
    posts_n = int(data.get("posts_analizados") or 0)
    comms_n = int(data.get("total_comentarios") or 0)
    sent = data.get("sentimiento") or {}
    dist = data.get("distribucion") or {}

    def _extraer_pct(tono: str) -> float:
        v_sent = sent.get(tono)
        if isinstance(v_sent, (int, float)):
            return float(v_sent)
        if isinstance(v_sent, dict):
            return float(v_sent.get("porcentaje") or v_sent.get("pct") or 0)
        v_dist = dist.get(tono)
        if isinstance(v_dist, dict):
            return float(v_dist.get("porcentaje") or v_dist.get("pct") or 0)
        if isinstance(v_dist, (int, float)) and comms_n > 0:
            return round(float(v_dist) / comms_n * 100, 1)
        return 0.0

    pos_pct = _extraer_pct("positivo")
    neu_pct = _extraer_pct("neutro")
    neg_pct = _extraer_pct("negativo")
    balance = float(data.get("aprobacion_pct") if data.get("aprobacion_pct") is not None else (pos_pct - neg_pct))

    hoy_str = date.today().strftime("%d/%m/%Y")
    titulo_doc = f"Social Listening · Perfil: {nombre_perfil}"

    historia: list = [
        Paragraph("ALTERNIS · INTELIGENCIA ELECTORAL | SOCIAL LISTENING", est["kicker"]),
        Paragraph("Reporte de Opinión Pública en Redes Sociales", est["titulo"]),
        Paragraph(
            f"<b>Perfil:</b> {_limpio(url_perfil)} · <b>Emisión:</b> {hoy_str} · "
            f"<b>Muestra:</b> {posts_n} publicaciones analizadas · {comms_n} comentarios procesados",
            est["sub"],
        ),
        Spacer(1, 4),
        Paragraph("Semáforo y Posicionamiento General", est["h2"]),
        _bloque_kpis_sentimiento(pos_pct, neu_pct, neg_pct, balance, est, ANCHO_DISPONIBLE),
        Spacer(1, 4),
    ]

    barra = _barra_sentimiento_horizontal(pos_pct, neu_pct, neg_pct, ANCHO_DISPONIBLE)
    if barra:
        historia.append(barra)
        historia.append(Spacer(1, 8))

    # Resumen Ejecutivo
    resumen = data.get("resumen") or "Sin resumen disponible."
    metodo = "IA Semántica (LLM)" if "modelo" in str(data.get("metodo_resumen") or "") else "Extractivo"
    historia.append(KeepTogether([
        Paragraph(f"Resumen Ejecutivo <font color='#64748b' size='7.5'>({metodo})</font>", est["h2"]),
        Paragraph(_limpio(resumen, saltos=True), est["cuerpo"]),
        Spacer(1, 6),
    ]))

    # Ejes Temáticos / N-gramas
    ngramas = data.get("ngramas") or []
    if ngramas:
        historia.append(Paragraph("Ejes Temáticos Más Frecuentes", est["h2"]))
        filas_temas = [
            [
                Paragraph("<b>Tema / Frase Clave</b>", est["celda_h"]),
                Paragraph("<b>Menciones</b>", est["celda_hc"]),
                Paragraph("<b>Presencia (%)</b>", est["celda_hc"]),
            ]
        ]
        for t in ngramas[:12]:
            texto_t = _limpio(t.get("texto") or t.get("tema") or "")
            freq_t = str(t.get("frecuencia") or 0)
            pct_t = f"{float(t.get('porcentaje') or 0):.1f}%"
            filas_temas.append([
                Paragraph(texto_t, est["celda"]),
                Paragraph(freq_t, est["celda_c"]),
                Paragraph(pct_t, est["celda_c"]),
            ])
        t_temas = Table(filas_temas, colWidths=[ANCHO_DISPONIBLE * 0.6, ANCHO_DISPONIBLE * 0.2, ANCHO_DISPONIBLE * 0.2])
        t_temas.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        historia.append(t_temas)
        historia.append(Spacer(1, 10))

    # Muestra de Comentarios Clasificados
    comms = data.get("comentarios_clasificados") or []
    if comms:
        historia.append(PageBreak())
        historia.append(Paragraph("Bandeja de Opinión Ciudadana (Comentarios Representativos)", est["h2"]))
        historia.append(Paragraph(
            "Selección de intervenciones ciudadanas clasificadas por postura para análisis cualitativo y territorio.",
            est["sub"],
        ))

        # Dividir por tono
        positivos = [c for c in comms if str(c.get("tono")) == "positivo"][:6]
        negativos = [c for c in comms if str(c.get("tono")) == "negativo"][:6]
        neutros = [c for c in comms if str(c.get("tono")) == "neutro"][:4]

        grupos = [
            ("Críticas, Demandas y Reclamos", negativos, COLOR_ROJO, "#fef2f2"),
            ("Apoyos y Simpatizantes", positivos, COLOR_VERDE, "#f0fdf4"),
            ("Comentarios Informativos y Neutros", neutros, COLOR_GRIS, "#f8fafc"),
        ]

        for etiqueta, lista, col_borde, col_bg in grupos:
            if not lista:
                continue
            historia.append(Spacer(1, 4))
            historia.append(Paragraph(f"<b>{etiqueta}</b> ({len(lista)} comentarios mostrados)", est["h3"]))
            for item in lista:
                texto_c = _limpio(item.get("texto"), 260)
                autor_c = _limpio(item.get("autor_nombre") or "Ciudadano anónimo")
                fila_card = [
                    [Paragraph(f"<b>{autor_c}:</b> «{texto_c}»", est["quote"])],
                ]
                t_card = Table(fila_card, colWidths=[ANCHO_DISPONIBLE])
                t_card.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(col_bg) if isinstance(col_bg, str) else col_bg),
                    ("LINELEFT", (0, 0), (0, 0), 2.5, col_borde),
                    ("BOX", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]))
                historia.append(t_card)
                historia.append(Spacer(1, 4))

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=MARGEN,
        rightMargin=MARGEN,
        topMargin=42,
        bottomMargin=42,
        title=titulo_doc[:200],
        author="Alternis Inteligencia Electoral",
    ).build(historia, onFirstPage=_crear_pie(titulo_doc, normal), onLaterPages=_crear_pie(titulo_doc, normal))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 2. Reporte PDF: Posts Específicos
# ---------------------------------------------------------------------------


def pdf_posts(data: dict[str, Any]) -> bytes:
    """Genera reporte PDF detallado de publicaciones individuales analizadas."""
    normal, negrita = _registrar_fuentes()
    est = _estilos(normal, negrita)
    posts = data.get("posts") or []
    hoy_str = date.today().strftime("%d/%m/%Y")
    total_comms = sum(int(p.get("total_comentarios") or 0) for p in posts)
    titulo_doc = f"Social Listening · Detalle de {len(posts)} Publicaciones"

    historia: list = [
        Paragraph("ALTERNIS · INTELIGENCIA ELECTORAL | SOCIAL LISTENING", est["kicker"]),
        Paragraph("Análisis de Publicaciones Específicas", est["titulo"]),
        Paragraph(
            f"<b>Muestra analizada:</b> {len(posts)} publicación(es) de Facebook · "
            f"<b>Comentarios procesados:</b> {total_comms} · <b>Fecha:</b> {hoy_str}",
            est["sub"],
        ),
        Spacer(1, 4),
    ]

    for idx, p in enumerate(posts):
        alias = _limpio(p.get("alias") or f"Publicación #{idx + 1}")
        url_p = str(p.get("post_url") or "").strip()
        fecha_p = _limpio(str(p.get("fecha_post") or "")[:10]) or "Fecha no registrada"
        pcts = p.get("porcentajes") or {}
        dist_p = p.get("distribucion") or {}
        comms_p = int(p.get("total_comentarios") or 0)
        def _get_val_post(k: str) -> float:
            v = pcts.get(k)
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, dict):
                return float(v.get("porcentaje") or v.get("pct") or 0)
            vd = dist_p.get(k)
            if isinstance(vd, dict):
                return float(vd.get("porcentaje") or vd.get("pct") or 0)
            if isinstance(vd, (int, float)) and comms_p > 0:
                return round(float(vd) / comms_p * 100, 1)
            return 0.0

        pos = _get_val_post("positivo")
        neu = _get_val_post("neutro")
        neg = _get_val_post("negativo")
        bal = float(p.get("balance_postura") if p.get("balance_postura") is not None else (pos - neg))

        bloque_post: list = []
        if idx > 0:
            bloque_post.append(PageBreak())

        # Cabecera de la publicación
        bloque_post.append(Paragraph(f"<b>{alias}</b>", est["h2"]))
        enlace_txt = f'<a href="{escape(url_p, {chr(34): "&quot;"})}" color="#4338ca">{_limpio(url_p, 85)}</a>' if url_p else "URL no indicada"
        bloque_post.append(Paragraph(f"<b>Fecha:</b> {fecha_p} · <b>Enlace:</b> {enlace_txt}", est["chico"]))
        bloque_post.append(Spacer(1, 4))

        # Texto del post si existe
        texto_post = _limpio(p.get("texto_post"), 450, saltos=True)
        if texto_post:
            t_txt = Table(
                [[Paragraph(f"<b>Texto de la publicación:</b><br/>{texto_post}", est["celda"])]],
                colWidths=[ANCHO_DISPONIBLE],
            )
            t_txt.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), COLOR_GRIS_FONDO),
                ("BOX", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
                ("LINELEFT", (0, 0), (0, 0), 2.5, COLOR_INDIGO),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]))
            bloque_post.append(t_txt)
            bloque_post.append(Spacer(1, 6))

        # KPIs del post
        bloque_post.append(_bloque_kpis_sentimiento(pos, neu, neg, bal, est, ANCHO_DISPONIBLE))
        barra = _barra_sentimiento_horizontal(pos, neu, neg, ANCHO_DISPONIBLE)
        if barra:
            bloque_post.append(Spacer(1, 3))
            bloque_post.append(barra)
        bloque_post.append(Spacer(1, 6))

        # Resumen del post
        resumen_p = p.get("resumen") or ""
        if resumen_p:
            bloque_post.append(Paragraph("<b>Síntesis de Reacciones Ciudadanas</b>", est["h3"]))
            bloque_post.append(Paragraph(_limpio(resumen_p, saltos=True), est["cuerpo"]))
            bloque_post.append(Spacer(1, 4))

        # Temas del post
        temas = p.get("temas") or []
        if temas:
            temas_str = ", ".join(f"<b>{_limpio(t.get('tema') or '')}</b> ({int(t.get('frecuencia') or 0)})" for t in temas[:8])
            bloque_post.append(Paragraph(f"<b>Ejes temáticos predominantes:</b> {temas_str}", est["chico"]))
            bloque_post.append(Spacer(1, 6))

        # Comentarios muestra
        repres = p.get("comentarios_representativos") or []
        if repres:
            bloque_post.append(Paragraph("<b>Muestra de Comentarios en esta Publicación</b>", est["h3"]))
            filas_c = []
            for c in repres[:5]:
                tono_c = str(c.get("tono") or "neutro")
                badge_col = "#15803d" if tono_c == "positivo" else ("#b91c1c" if tono_c == "negativo" else "#64748b")
                autor = _limpio(c.get("autor_nombre") or "Ciudadano")
                texto_c = _limpio(c.get("texto"), 200)
                filas_c.append([
                    Paragraph(f'<font color="{badge_col}"><b>[{tono_c.upper()}]</b></font> <b>{autor}:</b> {texto_c}', est["quote"])
                ])
            t_comms = Table(filas_c, colWidths=[ANCHO_DISPONIBLE])
            t_comms.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ]))
            bloque_post.append(t_comms)
            bloque_post.append(Spacer(1, 8))

        historia += bloque_post

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=MARGEN,
        rightMargin=MARGEN,
        topMargin=42,
        bottomMargin=42,
        title=titulo_doc[:200],
        author="Alternis Inteligencia Electoral",
    ).build(historia, onFirstPage=_crear_pie(titulo_doc, normal), onLaterPages=_crear_pie(titulo_doc, normal))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 3. Reporte PDF: Comparativa Inter-Posts
# ---------------------------------------------------------------------------


def pdf_comparativa_posts(data: dict[str, Any]) -> bytes:
    """Genera reporte PDF enfocado en el contraste comparativo entre 2 o más posts."""
    normal, negrita = _registrar_fuentes()
    est = _estilos(normal, negrita)
    posts = data.get("posts") or []
    comp = data.get("comparacion") or {}
    hoy_str = date.today().strftime("%d/%m/%Y")
    total_posts = comp.get("total_posts") or len(posts)
    total_comms = comp.get("total_comentarios") or sum(int(p.get("total_comentarios") or 0) for p in posts)
    titulo_doc = f"Social Listening · Comparativa de {total_posts} Publicaciones"

    historia: list = [
        Paragraph("ALTERNIS · INTELIGENCIA ELECTORAL | SOCIAL LISTENING", est["kicker"]),
        Paragraph("Comparativa de Publicaciones en Redes Sociales", est["titulo"]),
        Paragraph(
            f"<b>Contraste de {total_posts} publicaciones</b> · <b>Total comentarios:</b> {total_comms} · "
            f"<b>Fecha de emisión:</b> {hoy_str}",
            est["sub"],
        ),
        Spacer(1, 4),
    ]

    # Resumen Ejecutivo Comparativo
    res_comp = comp.get("resumen_comparativo") or "Sin resumen comparativo disponible."
    historia.append(KeepTogether([
        Paragraph("Síntesis Ejecutiva de la Comparativa", est["h2"]),
        Paragraph(_limpio(res_comp, saltos=True), est["cuerpo"]),
        Spacer(1, 8),
    ]))

    # Tabla Matriz Comparativa
    historia.append(Paragraph("Matriz de Percepción y Balance entre Publicaciones", est["h2"]))
    filas_matriz = [
        [
            Paragraph("<b>Publicación</b>", est["celda_h"]),
            Paragraph("<b>Fecha</b>", est["celda_hc"]),
            Paragraph("<b>Comentarios</b>", est["celda_hc"]),
            Paragraph("<b>Pos.</b>", est["celda_hc"]),
            Paragraph("<b>Neu.</b>", est["celda_hc"]),
            Paragraph("<b>Neg.</b>", est["celda_hc"]),
            Paragraph("<b>Balance</b>", est["celda_hc"]),
        ]
    ]

    for p in posts:
        alias = _limpio(p.get("alias") or "Post", 25)
        fecha_p = _limpio(str(p.get("fecha_post") or "")[:10]) or "—"
        tot_c = str(p.get("total_comentarios") or 0)
        tot_c_int = int(p.get("total_comentarios") or 0)
        pcts = p.get("porcentajes") or {}
        dist_p = p.get("distribucion") or {}
        def _get_val_comp(k: str) -> float:
            v = pcts.get(k)
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, dict):
                return float(v.get("porcentaje") or v.get("pct") or 0)
            vd = dist_p.get(k)
            if isinstance(vd, dict):
                return float(vd.get("porcentaje") or vd.get("pct") or 0)
            if isinstance(vd, (int, float)) and tot_c_int > 0:
                return round(float(vd) / tot_c_int * 100, 1)
            return 0.0

        pos = _get_val_comp("positivo")
        neu = _get_val_comp("neutro")
        neg = _get_val_comp("negativo")
        bal = float(p.get("balance_postura") if p.get("balance_postura") is not None else (pos - neg))
        signo = "+" if bal > 0 else ""
        bal_col = "#15803d" if bal > 10 else ("#b91c1c" if bal < -15 else "#b45309")

        filas_matriz.append([
            Paragraph(alias, est["celda_b"]),
            Paragraph(fecha_p, est["celda_c"]),
            Paragraph(tot_c, est["celda_c"]),
            Paragraph(f"{pos:.1f}%", est["celda_c"]),
            Paragraph(f"{neu:.1f}%", est["celda_c"]),
            Paragraph(f"{neg:.1f}%", est["celda_c"]),
            Paragraph(f'<font color="{bal_col}"><b>{signo}{bal:.1f}%</b></font>', est["celda_c"]),
        ])

    t_matriz = Table(
        filas_matriz,
        colWidths=[
            ANCHO_DISPONIBLE * 0.28,
            ANCHO_DISPONIBLE * 0.14,
            ANCHO_DISPONIBLE * 0.14,
            ANCHO_DISPONIBLE * 0.11,
            ANCHO_DISPONIBLE * 0.11,
            ANCHO_DISPONIBLE * 0.11,
            ANCHO_DISPONIBLE * 0.11,
        ],
    )
    t_matriz.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    historia.append(t_matriz)
    historia.append(Spacer(1, 10))

    # Temas Compartidos y Contrastes
    temas_comp = comp.get("temas_compartidos") or []
    if temas_comp:
        historia.append(KeepTogether([
            Paragraph("Ejes Temáticos Compartidos y Frecuencia Total", est["h2"]),
            Paragraph(
                ", ".join(
                    f"<b>{_limpio(t.get('tema') or '')}</b> ({int(t.get('frecuencia_total') or 0)} com.)"
                    for t in temas_comp[:12]
                ),
                est["cuerpo"],
            ),
            Spacer(1, 8),
        ]))

    # Fichas Breves de cada Publicación Comparada
    historia.append(Paragraph("Detalle de las Publicaciones Contrastadas", est["h2"]))
    for p in posts:
        alias = _limpio(p.get("alias") or "Post")
        url_p = str(p.get("post_url") or "")
        texto_p = _limpio(p.get("texto_post"), 180)
        res_p = _limpio(p.get("resumen"), 240)
        pcts = p.get("porcentajes") or {}
        pos = float(pcts.get("positivo") or 0)
        neu = float(pcts.get("neutro") or 0)
        neg = float(pcts.get("negativo") or 0)
        bal = float(p.get("balance_postura") or (pos - neg))
        bal_str = f"{'+' if bal > 0 else ''}{bal:.1f}%"

        card_content = [
            Paragraph(f"<b>{alias}</b> · Balance: <b>{bal_str}</b> (Positivo: {pos:.0f}% · Neutro: {neu:.0f}% · Negativo: {neg:.0f}%)", est["h3"]),
            Paragraph(f"<b>Texto:</b> «{texto_p}»", est["chico"]),
            Paragraph(f"<b>Reacciones:</b> {res_p}", est["quote"]),
        ]
        t_card = Table([[c] for c in card_content], colWidths=[ANCHO_DISPONIBLE])
        t_card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), COLOR_GRIS_FONDO),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        historia.append(t_card)
        historia.append(Spacer(1, 5))

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=MARGEN,
        rightMargin=MARGEN,
        topMargin=42,
        bottomMargin=42,
        title=titulo_doc[:200],
        author="Alternis Inteligencia Electoral",
    ).build(historia, onFirstPage=_crear_pie(titulo_doc, normal), onLaterPages=_crear_pie(titulo_doc, normal))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 4. Reporte PDF: Evolución Temporal
# ---------------------------------------------------------------------------


def pdf_evolucion_temporal(data: dict[str, Any]) -> bytes:
    """Genera reporte PDF de evolución temporal contrastando periodos por fechas."""
    normal, negrita = _registrar_fuentes()
    est = _estilos(normal, negrita)
    url_perfil = str(data.get("url_perfil") or "").strip()
    nombre_perfil = _inferir_nombre(url_perfil)
    periodos = data.get("periodos") or []
    comp = data.get("comparacion") or {}
    hoy_str = date.today().strftime("%d/%m/%Y")
    titulo_doc = f"Social Listening · Evolución Temporal: {nombre_perfil}"

    historia: list = [
        Paragraph("ALTERNIS · INTELIGENCIA ELECTORAL | SOCIAL LISTENING", est["kicker"]),
        Paragraph("Reporte de Evolución Temporal y Opinión Ciudadana", est["titulo"]),
        Paragraph(
            f"<b>Perfil:</b> {_limpio(url_perfil)} · <b>Periodos contrastados:</b> {len(periodos)} · "
            f"<b>Fecha de emisión:</b> {hoy_str}",
            est["sub"],
        ),
        Spacer(1, 4),
    ]

    # Síntesis Temporal IA
    resumen_comp = comp.get("resumen") or ""
    if resumen_comp:
        historia.append(KeepTogether([
            Paragraph("Síntesis y Evolución Temporal <font color='#64748b' size='7.5'>(IA Semántica)</font>", est["h2"]),
            Paragraph(_limpio(resumen_comp, saltos=True), est["cuerpo"]),
            Spacer(1, 8),
        ]))

    # Matriz Comparativa por Periodo
    historia.append(Paragraph("Comparativa de Sentimiento y Balance por Periodo", est["h2"]))
    filas_periodos = [
        [
            Paragraph("<b>Periodo</b>", est["celda_h"]),
            Paragraph("<b>Rango de Fechas</b>", est["celda_hc"]),
            Paragraph("<b>Posts / Comms.</b>", est["celda_hc"]),
            Paragraph("<b>Pos.</b>", est["celda_hc"]),
            Paragraph("<b>Neu.</b>", est["celda_hc"]),
            Paragraph("<b>Neg.</b>", est["celda_hc"]),
            Paragraph("<b>Balance</b>", est["celda_hc"]),
        ]
    ]

    for p in periodos:
        nom = _limpio(p.get("nombre") or "Periodo")
        fechas = f"{_limpio(p.get('fecha_inicio'))} al {_limpio(p.get('fecha_fin'))}"
        posts_cnt = int(p.get("total_posts") or 0)
        comms_cnt = int(p.get("total_comentarios") or 0)
        volumen = f"{posts_cnt} p · {comms_cnt} c"

        if posts_cnt == 0:
            filas_periodos.append([
                Paragraph(nom, est["celda_b"]),
                Paragraph(fechas, est["celda_c"]),
                Paragraph(volumen, est["celda_c"]),
                Paragraph("—", est["celda_c"]),
                Paragraph("—", est["celda_c"]),
                Paragraph("—", est["celda_c"]),
                Paragraph("Sin posts", est["celda_c"]),
            ])
            continue

        pos = float(p.get("positivo") or 0)
        neu = float(p.get("neutro") or 0)
        neg = float(p.get("negativo") or 0)
        bal = float(p.get("balance_postura") or (pos - neg))
        bal_signo = "+" if bal > 0 else ""
        bal_col = "#15803d" if bal > 10 else ("#b91c1c" if bal < -15 else "#b45309")

        filas_periodos.append([
            Paragraph(nom, est["celda_b"]),
            Paragraph(fechas, est["celda_c"]),
            Paragraph(volumen, est["celda_c"]),
            Paragraph(f"{pos:.1f}%", est["celda_c"]),
            Paragraph(f"{neu:.1f}%", est["celda_c"]),
            Paragraph(f"{neg:.1f}%", est["celda_c"]),
            Paragraph(f'<font color="{bal_col}"><b>{bal_signo}{bal:.1f}%</b></font>', est["celda_c"]),
        ])

    t_per = Table(
        filas_periodos,
        colWidths=[
            ANCHO_DISPONIBLE * 0.24,
            ANCHO_DISPONIBLE * 0.24,
            ANCHO_DISPONIBLE * 0.16,
            ANCHO_DISPONIBLE * 0.09,
            ANCHO_DISPONIBLE * 0.09,
            ANCHO_DISPONIBLE * 0.09,
            ANCHO_DISPONIBLE * 0.09,
        ],
    )
    t_per.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    historia.append(t_per)
    historia.append(Spacer(1, 10))

    # Variaciones de Sentimiento (pp)
    cambios_sent = comp.get("cambios_sentimiento") or []
    if cambios_sent:
        historia.append(Paragraph("Variaciones Porcentuales de Sentimiento entre Periodos", est["h2"]))
        filas_cs = [
            [
                Paragraph("<b>Tono</b>", est["celda_h"]),
                Paragraph("<b>Comparativa</b>", est["celda_h"]),
                Paragraph("<b>Variación</b>", est["celda_hc"]),
                Paragraph("<b>Dirección</b>", est["celda_hc"]),
            ]
        ]
        for cs in cambios_sent:
            tono = _limpio(cs.get("tono") or "").capitalize()
            p_a = _limpio(cs.get("periodo_a") or "")
            p_b = _limpio(cs.get("periodo_b") or "")
            v_a = float(cs.get("valor_a") or 0)
            v_b = float(cs.get("valor_b") or 0)
            diff = float(cs.get("diferencia_pp") or 0)
            dir_txt = _limpio(cs.get("direccion") or "")

            signo = "+" if diff > 0 else ""
            is_pos = tono.lower() == "positivo"
            diff_col = "#15803d" if (is_pos and diff >= 0) or (not is_pos and diff <= 0) else "#b91c1c"

            filas_cs.append([
                Paragraph(f"<b>{tono}</b>", est["celda"]),
                Paragraph(f"{p_a} ({v_a:.1f}%) → {p_b} ({v_b:.1f}%)", est["celda"]),
                Paragraph(f'<font color="{diff_col}"><b>{signo}{diff:.1f} pp</b></font>', est["celda_c"]),
                Paragraph(dir_txt, est["celda_c"]),
            ])
        t_cs = Table(filas_cs, colWidths=[ANCHO_DISPONIBLE * 0.2, ANCHO_DISPONIBLE * 0.45, ANCHO_DISPONIBLE * 0.17, ANCHO_DISPONIBLE * 0.18])
        t_cs.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f8fafc")),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        historia.append(t_cs)
        historia.append(Spacer(1, 10))

    # Evolución de Temas Clave
    cambios_temas = comp.get("cambios_temas") or []
    if cambios_temas:
        historia.append(Paragraph("Evolución de Ejes Temáticos Clave", est["h2"]))
        filas_temas = [
            [
                Paragraph("<b>Tema Clave</b>", est["celda_h"]),
                Paragraph("<b>Presencia y Tono por Periodo</b>", est["celda_h"]),
                Paragraph("<b>Observación</b>", est["celda_h"]),
            ]
        ]
        for ct in cambios_temas[:10]:
            tema_txt = _limpio(ct.get("tema") or "").capitalize()
            obs_txt = _limpio(ct.get("observacion") or "")
            pres = ct.get("presencia_por_periodo") or {}
            tonos = ct.get("tono_predominante_por_periodo") or {}

            detalles_p = []
            for p in periodos:
                nom_p = p.get("nombre") or ""
                f_p = int(pres.get(nom_p) or 0)
                t_p = str(tonos.get(nom_p) or "neutro")
                detalles_p.append(f"{nom_p}: {f_p} com. [{t_p}]")

            filas_temas.append([
                Paragraph(f"<b>«{tema_txt}»</b>", est["celda_b"]),
                Paragraph(" · ".join(detalles_p), est["celda"]),
                Paragraph(obs_txt, est["chico"]),
            ])
        t_ct = Table(filas_temas, colWidths=[ANCHO_DISPONIBLE * 0.28, ANCHO_DISPONIBLE * 0.50, ANCHO_DISPONIBLE * 0.22])
        t_ct.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f8fafc")),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        historia.append(t_ct)
        historia.append(Spacer(1, 10))

    # Fichas Individuales de Periodo
    historia.append(Paragraph("Fichas de Detalle por Periodo", est["h2"]))
    for idx_p, p in enumerate(periodos):
        nom = _limpio(p.get("nombre") or "Periodo")
        fechas = f"{_limpio(p.get('fecha_inicio'))} al {_limpio(p.get('fecha_fin'))}"
        posts_cnt = int(p.get("total_posts") or 0)
        comms_cnt = int(p.get("total_comentarios") or 0)
        res_p = _limpio(p.get("resumen"), saltos=True)
        temas_p = p.get("temas") or []

        pos = float(p.get("positivo") or 0)
        neu = float(p.get("neutro") or 0)
        neg = float(p.get("negativo") or 0)
        bal = float(p.get("balance_postura") or (pos - neg))
        bal_str = f"{'+' if bal > 0 else ''}{bal:.1f}%"

        card_p = [
            Paragraph(f"<b>Periodo {idx_p + 1}: {nom}</b> ({fechas}) · Balance: <b>{bal_str}</b>", est["h3"]),
            Paragraph(f"<b>Volumen:</b> {posts_cnt} publicaciones identificadas · {comms_cnt} comentarios clasificados (Positivo: {pos:.0f}% · Neutro: {neu:.0f}% · Negativo: {neg:.0f}%)", est["chico"]),
        ]
        if res_p:
            card_p.append(Paragraph(f"<b>Resumen:</b> {res_p}", est["quote"]))
        if temas_p:
            t_str = ", ".join(f"{_limpio(t.get('tema') or '')} ({int(t.get('frecuencia') or 0)})" for t in temas_p[:6])
            card_p.append(Paragraph(f"<b>Temas recurrentes:</b> {t_str}", est["chico"]))

        t_card = Table([[c] for c in card_p], colWidths=[ANCHO_DISPONIBLE])
        t_card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), COLOR_GRIS_FONDO),
            ("LINELEFT", (0, 0), (0, 0), 2.5, COLOR_INDIGO),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        historia.append(t_card)
        historia.append(Spacer(1, 5))

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=MARGEN,
        rightMargin=MARGEN,
        topMargin=42,
        bottomMargin=42,
        title=titulo_doc[:200],
        author="Alternis Inteligencia Electoral",
    ).build(historia, onFirstPage=_crear_pie(titulo_doc, normal), onLaterPages=_crear_pie(titulo_doc, normal))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 5. Reporte PDF: Histórico por Perfil (BD Local)
# ---------------------------------------------------------------------------


def pdf_historico_perfil(data: dict[str, Any]) -> bytes:
    """Genera reporte PDF de evolución histórica de postura por perfil (BD local)."""
    normal, negrita = _registrar_fuentes()
    est = _estilos(normal, negrita)
    url_perfil = str(data.get("url_perfil") or "").strip()
    nombre_perfil = str(data.get("perfil_nombre") or "").strip() or _inferir_nombre(url_perfil)
    agrupacion = str(data.get("agrupacion") or "mes").capitalize()
    serie = data.get("serie_temporal") or []
    tot_posts = int(data.get("total_posts") or 0)
    tot_comms = int(data.get("total_comentarios") or 0)
    tot_clas = int(data.get("total_clasificados") or 0)
    hoy_str = date.today().strftime("%d/%m/%Y")
    titulo_doc = f"Social Listening · Histórico de Postura: {nombre_perfil}"

    historia: list = [
        Paragraph("ALTERNIS · INTELIGENCIA ELECTORAL | SOCIAL LISTENING", est["kicker"]),
        Paragraph("Evolución Histórica de la Postura en Comentarios", est["titulo"]),
        Paragraph(
            f"<b>Perfil:</b> {_limpio(url_perfil)} · <b>Agrupación:</b> {agrupacion} · "
            f"<b>Muestra total en BD:</b> {tot_posts} publicaciones · {tot_comms} comentarios ({tot_clas} clasificados) · "
            f"<b>Emisión:</b> {hoy_str}",
            est["sub"],
        ),
        Spacer(1, 4),
    ]

    # Síntesis Ejecutiva
    resumen = data.get("resumen_ejecutivo") or ""
    if resumen:
        historia.append(KeepTogether([
            Paragraph("Síntesis Ejecutiva de Tendencias <font color='#64748b' size='7.5'>(IA Estratégica)</font>", est["h2"]),
            Paragraph(_limpio(resumen, saltos=True), est["cuerpo"]),
            Spacer(1, 8),
        ]))

    # Matriz Histórica de Postura
    historia.append(Paragraph("Matriz de Postura y Variación por Periodo", est["h2"]))
    filas_tabla = [
        [
            Paragraph("<b>Periodo</b>", est["celda_h"]),
            Paragraph("<b>Fechas</b>", est["celda_hc"]),
            Paragraph("<b>Posts</b>", est["celda_hc"]),
            Paragraph("<b>Comms.</b>", est["celda_hc"]),
            Paragraph("<b>Pos.</b>", est["celda_hc"]),
            Paragraph("<b>Neu.</b>", est["celda_hc"]),
            Paragraph("<b>Neg.</b>", est["celda_hc"]),
            Paragraph("<b>Balance</b>", est["celda_hc"]),
            Paragraph("<b>Variación</b>", est["celda_hc"]),
        ]
    ]

    for p in serie:
        periodo_txt = _limpio(p.get("etiqueta") or p.get("periodo") or "")
        f_ini = _limpio(str(p.get("fecha_inicio") or "")[:10])
        f_fin = _limpio(str(p.get("fecha_fin") or "")[:10])
        fechas_txt = f"{f_ini} al {f_fin}" if (f_ini and f_fin) else "—"
        p_cnt = str(p.get("posts") or 0)
        c_cnt = str(p.get("comentarios") or 0)
        pos = float(p.get("positivo") or 0)
        neu = float(p.get("neutro") or 0)
        neg = float(p.get("negativo") or 0)
        bal = float(p.get("balance_postura") if p.get("balance_postura") is not None else (pos - neg))
        bal_signo = "+" if bal > 0 else ""
        bal_col = "#15803d" if bal > 10 else ("#b91c1c" if bal < -15 else "#b45309")

        dif = p.get("diferencia_balance")
        if dif is not None:
            dif_f = float(dif)
            dif_signo = "+" if dif_f > 0 else ""
            dif_col = "#15803d" if dif_f > 0 else ("#b91c1c" if dif_f < 0 else "#64748b")
            dif_txt = f'<font color="{dif_col}"><b>{dif_signo}{dif_f:.1f} pp</b></font>'
        else:
            dif_txt = "—"

        filas_tabla.append([
            Paragraph(periodo_txt, est["celda_b"]),
            Paragraph(fechas_txt, est["celda_c"]),
            Paragraph(p_cnt, est["celda_c"]),
            Paragraph(c_cnt, est["celda_c"]),
            Paragraph(f"{pos:.1f}%", est["celda_c"]),
            Paragraph(f"{neu:.1f}%", est["celda_c"]),
            Paragraph(f"{neg:.1f}%", est["celda_c"]),
            Paragraph(f'<font color="{bal_col}"><b>{bal_signo}{bal:.1f}%</b></font>', est["celda_c"]),
            Paragraph(dif_txt, est["celda_c"]),
        ])

    t_hist = Table(
        filas_tabla,
        colWidths=[
            ANCHO_DISPONIBLE * 0.20,
            ANCHO_DISPONIBLE * 0.18,
            ANCHO_DISPONIBLE * 0.08,
            ANCHO_DISPONIBLE * 0.09,
            ANCHO_DISPONIBLE * 0.09,
            ANCHO_DISPONIBLE * 0.09,
            ANCHO_DISPONIBLE * 0.09,
            ANCHO_DISPONIBLE * 0.09,
            ANCHO_DISPONIBLE * 0.09,
        ],
    )
    t_hist.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_LINEA),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    historia.append(t_hist)
    historia.append(Spacer(1, 10))

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=MARGEN,
        rightMargin=MARGEN,
        topMargin=42,
        bottomMargin=42,
        title=titulo_doc[:200],
        author="Alternis Inteligencia Electoral",
    ).build(historia, onFirstPage=_crear_pie(titulo_doc, normal), onLaterPages=_crear_pie(titulo_doc, normal))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Router principal de exportación
# ---------------------------------------------------------------------------


def generar_pdf_social(tipo: str, data: dict[str, Any]) -> tuple[bytes, str]:
    """Genera el contenido PDF (bytes) y el nombre de archivo sugerido según el tipo solicitado.

    Tipos soportados:
    - 'perfil': Análisis de perfil de Facebook.
    - 'posts': Análisis de publicaciones individuales específicas.
    - 'comparativa': Comparativa inter-posts.
    - 'historico': Análisis de evolución histórica por perfil (BD local).
    """
    tipo_norm = str(tipo or "").lower().strip()
    fecha_slug = date.today().strftime("%Y%m%d")

    if tipo_norm in ("perfil", "profile"):
        pdf_bytes = pdf_perfil(data)
        nom = _slug(_inferir_nombre(data.get("url_perfil")))
        return pdf_bytes, f"social_perfil_{nom}_{fecha_slug}.pdf"

    elif tipo_norm in ("posts", "post", "especificos"):
        pdf_bytes = pdf_posts(data)
        return pdf_bytes, f"social_posts_especificos_{fecha_slug}.pdf"

    elif tipo_norm in ("comparativa", "comparativo", "comparacion"):
        pdf_bytes = pdf_comparativa_posts(data)
        return pdf_bytes, f"social_comparativa_posts_{fecha_slug}.pdf"

    elif tipo_norm in ("historico", "serie_historica", "historico_perfil"):
        pdf_bytes = pdf_historico_perfil(data)
        nom = _slug(_inferir_nombre(data.get("url_perfil")))
        return pdf_bytes, f"social_historico_{nom}_{fecha_slug}.pdf"

    elif tipo_norm in ("temporal", "evolucion", "evolucion_temporal"):
        nom = _slug(_inferir_nombre(data.get("url_perfil")))
        if "serie_temporal" in data:
            pdf_bytes = pdf_historico_perfil(data)
            return pdf_bytes, f"social_historico_{nom}_{fecha_slug}.pdf"
        else:
            pdf_bytes = pdf_evolucion_temporal(data)
            return pdf_bytes, f"social_evolucion_temporal_{nom}_{fecha_slug}.pdf"

    else:
        raise ValueError(
            f"Tipo de reporte no soportado: '{tipo}'. Debe ser 'perfil', 'posts', 'comparativa' o 'historico'."
        )

