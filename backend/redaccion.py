"""Redacción de discursos: formatos, ajustes rápidos, prompts y verificación de cifras."""
from __future__ import annotations

import re
from typing import Any, Optional

FORMATOS: dict[str, dict[str, str]] = {
    "libre": {
        "nombre": "Libre",
        "descripcion": "Tú defines la ocasión, el tono y la duración.",
        "instruccion": "",
    },
    "mitin": {
        "nombre": "Mitin (5 a 7 min)",
        "descripcion": "Discurso para un evento público, con saludo, ideas y cierre.",
        "instruccion": ("Formato: discurso de mitin de 5 a 7 minutos (unas 650 a 900 palabras). Estructura: saludo, "
                        "contexto local, tres ideas principales con ejemplos y cierre con un llamado a la participación. "
                        "Frases cortas, pensadas para decirse en voz alta."),
    },
    "debate": {
        "nombre": "Debate (2 min)",
        "descripcion": "Intervención breve y firme para un debate.",
        "instruccion": ("Formato: intervención de debate de 2 minutos (unas 250 a 300 palabras). Abre con la idea central, "
                        "incluye una o dos propuestas o datos y cierra con una frase memorable. Tono firme y respetuoso, "
                        "sin ataques personales."),
    },
    "whatsapp": {
        "nombre": "Mensaje de WhatsApp",
        "descripcion": "Mensaje directo y corto para difundir.",
        "instruccion": ("Formato: mensaje de WhatsApp de máximo 120 palabras, en tono directo y cercano, con un párrafo "
                        "corto y un llamado a la acción claro. Sin hashtags."),
    },
    "boletin": {
        "nombre": "Boletín de prensa",
        "descripcion": "Titular, párrafo inicial y cuerpo en tercera persona.",
        "instruccion": ("Formato: boletín de prensa. Primera línea: el titular. Después, un párrafo inicial de una oración "
                        "y 3 o 4 párrafos breves en tercera persona. Cierra con una cita entre comillas atribuida a "
                        "[nombre y cargo]."),
    },
    "spot": {
        "nombre": "Guion de 30 segundos",
        "descripcion": "Guion de radio o video con una sola idea.",
        "instruccion": ("Formato: guion de radio o video de 30 segundos (unas 70 a 80 palabras). Una sola idea y un cierre "
                        "con la frase de campaña [lema]."),
    },
}

AJUSTES: dict[str, dict[str, Any]] = {
    "mas_corto": {"etiqueta": "Más corto", "instruccion":
                  "Reescribe el texto completo reduciéndolo aproximadamente a la mitad, conservando la idea central y el llamado principal."},
    "mas_largo": {"etiqueta": "Más largo", "instruccion":
                  "Reescribe el texto completo ampliándolo alrededor de un 50 %, desarrollando mejor las ideas sin inventar hechos ni cifras."},
    "mas_formal": {"etiqueta": "Más formal", "instruccion":
                   "Reescribe el texto completo con un tono más formal e institucional, sin perder claridad."},
    "mas_cercano": {"etiqueta": "Más cercano", "instruccion":
                    "Reescribe el texto completo con un tono más cercano y conversacional, con frases cortas y lenguaje cotidiano."},
    "llamado": {"etiqueta": "Agregar llamado a la acción", "instruccion":
                "Reescribe el texto completo agregando al cierre un llamado a la acción claro y concreto."},
    "sin_tecnicismos": {"etiqueta": "Sin tecnicismos", "instruccion":
                        "Reescribe el texto completo sustituyendo tecnicismos y términos burocráticos por palabras sencillas."},
    "usar_datos": {"etiqueta": "Incluir cifras del territorio", "requiere_datos": True, "instruccion":
                   "Reescribe el texto completo incorporando dos o tres cifras de la FICHA DE DATOS VERIFICADOS, copiadas exactamente y bien contextualizadas."},
    "corregir_cifras": {"etiqueta": "Corregir cifras", "especial": True, "instruccion":
                        "Reescribe el texto completo corrigiendo las cifras que no coinciden con los datos: usa únicamente cifras "
                        "de la ficha de datos o de lo que la persona te dio, y donde falte una escribe [dato por verificar]."},
}

BASE = (
    "Eres un redactor en español para intervenciones públicas de una campaña política en México. "
    "Entrega el texto listo para usarse, en prosa y sin Markdown (sin asteriscos, almohadillas ni viñetas), "
    "salvo que el formato pida un titular en la primera línea. "
    "Si piden ajustes, reescribe el texto completo tomando en cuenta la conversación. "
    "No inventes nombres, cargos, logros, promesas concretas ni hechos que no te hayan proporcionado; "
    "usa marcadores como [nombre] o [dato por verificar] cuando falte información. "
    "No inventes cifras: usa solo las de la ficha de datos o las que la persona te dé en la conversación. "
    "Evita dirigirte a segmentos demográficos específicos o personalizar mensajes para cambiar sus preferencias políticas. "
    "Pregunta solo cuando no puedas producir un borrador útil."
)


def opciones() -> dict[str, Any]:
    return {
        "formatos": [{"id": k, "nombre": v["nombre"], "descripcion": v["descripcion"]} for k, v in FORMATOS.items()],
        "ajustes": [{"id": k, "etiqueta": v["etiqueta"], "requiere_datos": bool(v.get("requiere_datos"))}
                    for k, v in AJUSTES.items()],
    }


def prompt_sistema(formato: str, ficha: Optional[list[str]] = None) -> str:
    partes = [BASE]
    instruccion = FORMATOS.get(formato, FORMATOS["libre"])["instruccion"]
    if instruccion:
        partes.append(instruccion)
    if ficha:
        partes.append(
            "FICHA DE DATOS VERIFICADOS (calculados por el sistema, no por ti):\n" + "\n".join(f"- {l}" for l in ficha)
            + "\nReglas: usa únicamente estas cifras y cópialas exactamente como aparecen. No calcules, sumes ni redondees "
              "otras. Si necesitas una cifra que no está en la ficha, escribe [dato por verificar]. Puedes omitir las "
              "cifras que no aporten al mensaje.")
    return "\n\n".join(partes)


def construir_mensajes(previos: list[dict[str, str]], mensaje: str, formato: str,
                       ficha: Optional[list[str]] = None) -> list[dict[str, str]]:
    mensajes = [{"role": "system", "content": prompt_sistema(formato, ficha)}]
    for item in previos[-12:]:
        if item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str):
            mensajes.append({"role": item["role"], "content": item["content"][:5000]})
    mensajes.append({"role": "user", "content": mensaje.strip()})
    return mensajes


def instruccion_ajuste(ajuste: str, verificacion_previa: Optional[dict[str, Any]] = None) -> str:
    definicion = AJUSTES[ajuste]
    texto = definicion["instruccion"]
    if ajuste == "corregir_cifras" and verificacion_previa:
        dudosas = ", ".join(f"«{c['texto']}»" for c in verificacion_previa.get("no_respaldadas", [])[:12])
        if dudosas:
            texto += f" Cifras a corregir: {dudosas}."
    return texto


# ---------------------------------------------------------------------------
# Verificación de cifras
# ---------------------------------------------------------------------------

_NUMERO = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(\s*(?:%|(?:por\s+ciento|puntos\s+porcentuales|puntos|millones|mill[oó]n|mil)\b))?",
    re.IGNORECASE,
)


def _interpretar(token: str) -> tuple[float, int, bool]:
    """Devuelve (valor, decimales, usa_separador_de_miles)."""
    miles = re.fullmatch(r"(\d{1,3}(?:[.,]\d{3})+)(?:[.,](\d+))?", token)
    if miles:
        entero = re.sub(r"[.,]", "", miles.group(1))
        fraccion = miles.group(2) or ""
        return float(f"{entero}.{fraccion}" if fraccion else entero), len(fraccion), True
    if re.search(r"[.,]", token):
        entero, fraccion = re.split(r"[.,]", token, maxsplit=1)
        return float(f"{entero}.{fraccion}"), len(fraccion), False
    return float(token), 0, False


def _extraer(texto: str) -> list[dict[str, Any]]:
    cifras = []
    for m in _NUMERO.finditer(texto):
        valor, decimales, miles = _interpretar(m.group(1))
        sufijo = (m.group(2) or "").strip().lower()
        multiplicador = 1_000_000 if sufijo.startswith("mill") else 1000 if sufijo == "mil" else 1
        cifras.append({
            "texto": m.group(0), "inicio": m.start(), "fin": m.end(),
            "valor": valor, "decimales": decimales, "miles": miles, "porcentaje": sufijo.startswith(("%", "por", "punto")),
            "multiplicador": multiplicador,
        })
    return cifras


def _es_relevante(c: dict[str, Any]) -> bool:
    """Solo se revisan cifras que parecen datos: no años ni enteros pequeños ('3 propuestas')."""
    if c["porcentaje"] or c["multiplicador"] != 1 or c["decimales"] > 0 or c["miles"]:
        return True
    es_anio = 1900 <= c["valor"] <= 2100 and not c["miles"]
    return c["valor"] >= 100 and not es_anio


def _igual(a: float, b: float, decimales: int) -> bool:
    return abs(round(a, decimales) - round(b, decimales)) < 10 ** -(decimales + 1)


def _respaldada(c: dict[str, Any], permitidos: list[dict[str, Any]]) -> bool:
    if c["multiplicador"] != 1:
        objetivo = c["valor"] * c["multiplicador"]
        tolerancia = 0.5 * (10 ** -c["decimales"]) * c["multiplicador"]
        # Estricto: la diferencia debe ser menor que media unidad ('3 millones' no aprueba 2.5 millones).
        return any(abs(p["valor"] - objetivo) < tolerancia for p in permitidos if p["tipo"] in {"votos", "cantidad"})
    if c["porcentaje"] or c["decimales"] > 0:
        return any(_igual(p["valor"], c["valor"], c["decimales"]) for p in permitidos if p["tipo"] == "porcentaje")
    return any(abs(p["valor"] - c["valor"]) < 0.5 for p in permitidos if p["tipo"] in {"votos", "cantidad", "id"})


def cifras_de_usuario(mensajes: list[str]) -> list[dict[str, Any]]:
    """Las cifras que la persona escribió cuentan como respaldadas: son su fuente."""
    permitidos = []
    for texto in mensajes:
        for c in _extraer(texto):
            valor = c["valor"] * c["multiplicador"]
            permitidos += [{"valor": valor, "tipo": "cantidad"}, {"valor": c["valor"], "tipo": "porcentaje"},
                           {"valor": valor, "tipo": "id"}]
    return permitidos


def _utf16(texto: str, posicion: int) -> int:
    """Posición equivalente en JavaScript (UTF-16), para resaltar en el navegador."""
    return len(texto[:posicion].encode("utf-16-le")) // 2


def verificar_cifras(texto: str, permitidos: list[dict[str, Any]], mensajes_usuario: list[str],
                     territorio: Optional[str] = None) -> dict[str, Any]:
    todos = list(permitidos) + cifras_de_usuario(mensajes_usuario)
    respaldadas, dudosas = [], []
    for c in _extraer(texto):
        if not _es_relevante(c):
            continue
        item = {"texto": c["texto"], "inicio": _utf16(texto, c["inicio"]), "fin": _utf16(texto, c["fin"])}
        (respaldadas if _respaldada(c, todos) else dudosas).append(item)
    estado = "sin_cifras" if not (respaldadas or dudosas) else "revisar" if dudosas else "ok"
    return {"aplicada": bool(territorio), "territorio": territorio, "estado": estado,
            "respaldadas": respaldadas, "no_respaldadas": dudosas}
