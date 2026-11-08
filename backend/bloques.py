"""Agrupación de partidos y coaliciones en bloques, para comparar entre elecciones.

Por qué existe: en la base cada coalición es una opción distinta (PAN_PRI, PAN_PRI_PRD, PVEM_PT_MORENA...) y
no todas las elecciones las registran igual. Por ejemplo, el Senado no trae combinaciones con Morena: sus votos
parecen estar dentro de PVEM. Comparar partido contra partido entre elecciones daría diferencias que no son reales.
Al sumar por bloque, esas diferencias de captura desaparecen.

ESTA ES UNA DECISIÓN POLÍTICA, no técnica: edita BLOQUES para que refleje cómo quieres leer el mapa.
Si en un municipio el PVEM compitió contra Morena, aquí seguirá sumado al mismo bloque.
"""
from __future__ import annotations

BLOQUES: dict[str, dict[str, object]] = {
    "MORENA_ALIADOS": {"etiqueta": "Morena, PT y PVEM", "color": "#7e2a4f", "partidos": {"MORENA", "PT", "PVEM"}},
    "PAN_PRI_PRD": {"etiqueta": "PAN, PRI y PRD", "color": "#0b5fa5", "partidos": {"PAN", "PRI", "PRD"}},
    "MC": {"etiqueta": "Movimiento Ciudadano", "color": "#ff8200", "partidos": {"MC"}},
    # Partidos locales o pequeños: cada uno es su propia opción, porque compiten entre sí y sumarlos
    # produciría un "ganador" que no existe en la boleta.
    "NA": {"etiqueta": "Nueva Alianza", "color": "#00a7b5", "partidos": {"NA"}},
    "PES": {"etiqueta": "PES", "color": "#5b3f9e", "partidos": {"PES"}},
    "CP": {"etiqueta": "CP", "color": "#8d6e63", "partidos": {"CP"}},
    "ML": {"etiqueta": "ML", "color": "#b0a04a", "partidos": {"ML"}},
}
MIXTO = {"etiqueta": "Coalición entre bloques", "color": "#9aa3af"}
SIN_BLOQUE = {"etiqueta": "Sin clasificar", "color": "#c3c8d0"}

_PARTIDO_A_BLOQUE = {p: clave for clave, b in BLOQUES.items() for p in b["partidos"]}  # type: ignore[union-attr]


def bloque_de(opcion: str) -> str:
    """Bloque de un partido o coalición ('PAN_PRI' -> 'PAN_PRI_PRD'). Devuelve 'MIXTO' si cruza bloques."""
    partes = [p for p in str(opcion).upper().strip().split("_") if p]
    encontrados = {_PARTIDO_A_BLOQUE.get(p) for p in partes}
    if None in encontrados:
        return "SIN_BLOQUE"
    return encontrados.pop() if len(encontrados) == 1 else "MIXTO"


def etiqueta(clave: str) -> str:
    return str((BLOQUES.get(clave) or (MIXTO if clave == "MIXTO" else SIN_BLOQUE))["etiqueta"])


def color(clave: str) -> str:
    return str((BLOQUES.get(clave) or (MIXTO if clave == "MIXTO" else SIN_BLOQUE))["color"])


def definicion() -> list[dict[str, str]]:
    claves = [*BLOQUES, "MIXTO", "SIN_BLOQUE"]
    return [{"clave": c, "etiqueta": etiqueta(c), "color": color(c)} for c in claves]
