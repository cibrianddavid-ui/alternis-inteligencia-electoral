"""Etiquetas y colores por opción registrada, sin agrupar aliados."""
import hashlib

COLORES = {
    "PAN": "#005ca9", "PRI": "#d71920", "PRD": "#d4a900",
    "MORENA": "#8b2131", "PVEM": "#4d9221", "PT": "#b32323",
    "MC": "#ff8200", "NA": "#00a7b5", "PES": "#5b3f9e",
    "CP": "#8d6e63", "ML": "#b0a04a",
}


def etiqueta(clave: str) -> str:
    return str(clave).replace("_", "-")


def color(clave: str) -> str:
    if clave in COLORES:
        return COLORES[clave]
    # Color estable para coaliciones y otras opciones, sin adjudicarlas a un aliado.
    tono = int(hashlib.sha256(str(clave).encode()).hexdigest()[:8], 16) % 360
    return f"hsl({tono}, 45%, 42%)"
