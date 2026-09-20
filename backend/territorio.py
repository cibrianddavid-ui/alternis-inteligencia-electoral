"""Perfil territorial: resultados, competitividad y participación de una unidad geográfica.

Todo el cálculo es determinista (pandas); ningún número pasa por un modelo de lenguaje.
Convenciones, iguales a las del resto de la plataforma:
  * Porcentaje de una opción = votos / TOTAL_VOTOS_CALCULADOS (si falta, suma de opciones).
  * Participación = votos emitidos (opciones + nulos + no registrados) / LISTA_NOMINAL.
  * Cada coalición es una opción independiente: sus votos no se reparten entre partidos.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

NIVELES = {
    "entidad": "Estado",
    "municipio": "Municipio",
    "id_distrito_local": "Distrito local",
    "id_distrito_federal": "Distrito federal",
    "seccion": "Sección",
}
ELECCIONES = {
    "DIPUTACION_LOC": "Diputación local", "DIP_FEDERAL": "Diputación federal",
    "AYUNTAMIENTO": "Ayuntamiento", "PRESIDENCIA": "Presidencia", "SENADO": "Senado",
}
AUXILIARES = {"LISTA_NOMINAL", "TOTAL_VOTOS_CALCULADOS"}
NO_OPCION = AUXILIARES | {"NULOS", "NO_REGISTRADAS"}
PRINCIPAL = {"municipio": "AYUNTAMIENTO", "id_distrito_local": "DIPUTACION_LOC", "id_distrito_federal": "DIP_FEDERAL"}
MAX_MUNICIPIOS = 60
# Una sección con muy pocos votos puede empatar por azar; no sirve para priorizar.
MIN_VOTOS_SECCION = 50


def etiqueta_eleccion(codigo: str) -> str:
    return ELECCIONES.get(codigo, str(codigo).replace("_", " ").capitalize())


def _como_texto(serie: pd.Series) -> pd.Series:
    """Texto limpio; los identificadores numéricos con vacíos (8.0) se leen como '8'."""
    if pd.api.types.is_float_dtype(serie):
        sin_nulos = serie.dropna()
        if (sin_nulos % 1 == 0).all():
            serie = serie.astype("Int64")
    return serie.astype("string").str.strip()


def _orden_natural(valor: Any) -> tuple[int, Any]:
    try:
        return 0, float(valor)
    except (TypeError, ValueError):
        return 1, str(valor)


def _competitividad(margen_pp: Optional[float], margen_votos: Optional[int] = None) -> str:
    """Etiqueta descriptiva con umbrales fijos (criterio editorial, no estadístico)."""
    if margen_pp is None:
        return "Sin comparación"
    if margen_votos == 0:
        return "Empate exacto"
    if margen_pp < 3:
        return "Muy reñida"
    if margen_pp < 8:
        return "Competida"
    if margen_pp < 15:
        return "Ventaja clara"
    return "Ventaja amplia"


class Territorio:
    def __init__(self, df: pd.DataFrame, mapa: dict[str, str]) -> None:
        self.tiene_anio = "anio" in mapa
        datos = pd.DataFrame({
            "eleccion": df[mapa["tipo_eleccion"]].astype("string").str.strip(),
            "partido": df[mapa["partido"]].astype("string").str.strip().fillna("SIN_PARTIDO"),
            "votos": pd.to_numeric(df[mapa["votos"]], errors="coerce").fillna(0.0).astype(float),
        })
        datos["norm"] = datos["partido"].str.upper()
        for nivel in NIVELES:
            if nivel in mapa:
                datos[nivel] = _como_texto(df[mapa[nivel]]).astype("category")
        if self.tiene_anio:
            datos["anio"] = _como_texto(df[mapa["anio"]])
        self.datos = datos
        self._catalogo: Optional[dict[str, Any]] = None

    # ------------------------------------------------------------------ catálogo
    def catalogo(self) -> dict[str, Any]:
        if self._catalogo is None:
            niveles = []
            for clave, etiqueta in NIVELES.items():
                if clave in self.datos:
                    unidades = sorted(self.datos[clave].dropna().unique().tolist(), key=_orden_natural)
                    niveles.append({"valor": clave, "etiqueta": etiqueta, "unidades": unidades})
            anios = sorted(self.datos["anio"].dropna().unique().tolist(), key=_orden_natural) if self.tiene_anio else []
            elecciones = sorted(self.datos["eleccion"].dropna().unique().tolist())
            self._catalogo = {
                "niveles": niveles, "anios": anios,
                "elecciones": [{"valor": e, "etiqueta": etiqueta_eleccion(e)} for e in elecciones],
            }
        return self._catalogo

    # ------------------------------------------------------------------- bloques
    @staticmethod
    def _agregar(bloque: pd.DataFrame) -> pd.DataFrame:
        return bloque.groupby(["partido", "norm"], observed=True, as_index=False)["votos"].sum()

    def _bloque_eleccion(self, eleccion: str, bloque: pd.DataFrame) -> dict[str, Any]:
        agg = self._agregar(bloque)

        def suma(nombre: str) -> float:
            return float(agg.loc[agg["norm"] == nombre, "votos"].sum())

        lista, total_calc = suma("LISTA_NOMINAL"), suma("TOTAL_VOTOS_CALCULADOS")
        nulos, no_reg = suma("NULOS"), suma("NO_REGISTRADAS")
        emitidos = float(agg.loc[~agg["norm"].isin(AUXILIARES), "votos"].sum())
        base = total_calc if total_calc > 0 else emitidos

        opciones = agg[~agg["norm"].isin(NO_OPCION) & (agg["votos"] > 0)].sort_values(
            ["votos", "partido"], ascending=[False, True])
        ranking = [{"partido": r.partido, "votos": int(round(r.votos)),
                    "porcentaje": round(r.votos / base * 100, 2) if base else None}
                   for r in opciones.itertuples()]

        primero = ranking[0] if ranking else None
        segundo = ranking[1] if len(ranking) > 1 else None
        margen_votos = margen_pp = None
        if primero and segundo:
            margen_votos = primero["votos"] - segundo["votos"]
            margen_pp = round(primero["porcentaje"] - segundo["porcentaje"], 2) if base else None

        participacion = round(emitidos / lista * 100, 2) if lista > 0 else None
        cuadra = total_calc <= 0 or abs(emitidos - total_calc) <= max(1.0, 0.005 * total_calc)
        calidad: dict[str, Any] = {"cuadra": bool(cuadra), "diferencia": int(round(total_calc - emitidos)) if total_calc > 0 else 0}
        if not cuadra:
            calidad["nota"] = (
                f"La suma de las opciones ({int(round(emitidos)):,}) difiere del total calculado de la base "
                f"({int(round(total_calc)):,}) en {abs(calidad['diferencia']):,} votos. Los porcentajes usan el total calculado.")
        return {
            "tipo": eleccion, "etiqueta": etiqueta_eleccion(eleccion),
            "lista_nominal": int(round(lista)), "votos_emitidos": int(round(emitidos)),
            "total_calculado": int(round(total_calc)), "nulos": int(round(nulos)), "no_registradas": int(round(no_reg)),
            "participacion_pct": participacion,
            "abstencion_pct": round(100 - participacion, 2) if participacion is not None else None,
            "ranking": ranking, "ganador": primero, "segundo": segundo,
            "margen_votos": margen_votos, "margen_pp": margen_pp, "competitividad": _competitividad(margen_pp, margen_votos),
            "empate": margen_votos == 0, "calidad": calidad,
        }

    def _secciones_competidas(self, bloque: pd.DataFrame, top: int = 10) -> list[dict[str, Any]]:
        if "seccion" not in bloque or bloque["seccion"].isna().all():
            return []
        opciones = bloque[~bloque["norm"].isin(NO_OPCION)]
        por = (opciones.groupby(["seccion", "partido"], observed=True, as_index=False)["votos"].sum()
               .sort_values(["seccion", "votos", "partido"], ascending=[True, False, True]))
        por["puesto"] = por.groupby("seccion", observed=True).cumcount()
        primeros = por[por["puesto"] == 0].set_index("seccion")
        segundos = por[por["puesto"] == 1].set_index("seccion")
        base = bloque[bloque["norm"] == "TOTAL_VOTOS_CALCULADOS"].groupby("seccion", observed=True)["votos"].sum()
        emitidos = bloque[~bloque["norm"].isin(AUXILIARES)].groupby("seccion", observed=True)["votos"].sum()
        base = base.where(base > 0, emitidos)
        filas = []
        for seccion in segundos.index:
            total = float(base.get(seccion, 0))
            v1, v2 = float(primeros.at[seccion, "votos"]), float(segundos.at[seccion, "votos"])
            emitidos_sec = float(emitidos.get(seccion, 0))
            if total <= 0 or v2 <= 0 or emitidos_sec < MIN_VOTOS_SECCION:
                continue
            filas.append({
                "seccion": str(seccion), "ganador": primeros.at[seccion, "partido"],
                "segundo": segundos.at[seccion, "partido"], "margen_votos": int(round(v1 - v2)),
                "margen_pp": round((v1 - v2) / total * 100, 2), "votos_emitidos": int(round(emitidos_sec)), "empate": v1 == v2,
            })
        filas.sort(key=lambda f: (f["margen_pp"], -f["votos_emitidos"]))
        return filas[:top]

    def _municipios(self, bloque: pd.DataFrame) -> list[dict[str, Any]]:
        if "municipio" not in bloque or bloque["municipio"].isna().all():
            return []
        opciones = bloque[~bloque["norm"].isin(NO_OPCION)]
        por = (opciones.groupby(["municipio", "partido"], observed=True, as_index=False)["votos"].sum()
               .sort_values(["municipio", "votos", "partido"], ascending=[True, False, True]))
        ganadores = por.groupby("municipio", observed=True).head(1).set_index("municipio")
        base = bloque[bloque["norm"] == "TOTAL_VOTOS_CALCULADOS"].groupby("municipio", observed=True)["votos"].sum()
        emitidos = bloque[~bloque["norm"].isin(AUXILIARES)].groupby("municipio", observed=True)["votos"].sum()
        base = base.where(base > 0, emitidos)
        filas = []
        for municipio, fila in ganadores.iterrows():
            total = float(base.get(municipio, 0))
            filas.append({
                "municipio": str(municipio), "ganador": fila["partido"], "votos_ganador": int(round(fila["votos"])),
                "porcentaje": round(float(fila["votos"]) / total * 100, 2) if total else None,
                "votos_emitidos": int(round(float(emitidos.get(municipio, 0)))),
            })
        filas.sort(key=lambda f: -f["votos_emitidos"])
        return filas[:MAX_MUNICIPIOS]

    # -------------------------------------------------------------------- perfil
    def perfil(self, nivel: str, unidad: str, anio: Optional[str] = None) -> dict[str, Any]:
        if nivel not in NIVELES or nivel not in self.datos:
            raise ValueError("El nivel territorial no existe en la base.")
        unidad = str(unidad).strip()
        sub = self.datos[self.datos[nivel] == unidad]
        if sub.empty:
            raise ValueError(f"No hay resultados para {NIVELES[nivel].lower()} «{unidad}».")

        anios = sorted(sub["anio"].dropna().unique().tolist(), key=_orden_natural) if self.tiene_anio else []
        if self.tiene_anio:
            anio = str(anio).strip() if anio else (anios[-1] if anios else None)
            if anio not in anios:
                raise ValueError(f"No hay resultados de {anio} para «{unidad}».")
            sub_anio = sub[sub["anio"] == anio]
        else:
            anio, sub_anio = None, sub

        principal = PRINCIPAL.get(nivel)
        codigos = sorted(sub_anio["eleccion"].dropna().unique().tolist(), key=lambda e: (e != principal, e))
        principal = codigos[0] if codigos and (principal not in codigos) else principal
        elecciones = [self._bloque_eleccion(e, sub_anio[sub_anio["eleccion"] == e]) for e in codigos]
        bloque_principal = sub_anio[sub_anio["eleccion"] == principal] if principal else sub_anio.iloc[0:0]

        historia = []
        if len(anios) > 1 and principal:
            for a in anios:
                bloque_a = sub[(sub["anio"] == a) & (sub["eleccion"] == principal)]
                if not bloque_a.empty:
                    b = self._bloque_eleccion(principal, bloque_a)
                    historia.append({"anio": a, "ganador": b["ganador"], "margen_pp": b["margen_pp"],
                                     "participacion_pct": b["participacion_pct"]})

        resumen = None
        if elecciones:
            e = elecciones[0]
            resumen = {"eleccion": e["tipo"], "etiqueta": e["etiqueta"], "ganador": e["ganador"],
                       "margen_pp": e["margen_pp"], "margen_votos": e["margen_votos"], "empate": e["empate"],
                       "participacion_pct": e["participacion_pct"], "competitividad": e["competitividad"]}
        return {
            "nivel": nivel, "nivel_etiqueta": NIVELES[nivel], "unidad": unidad, "anio": anio,
            "anios_disponibles": anios, "principal": principal,
            "secciones": int(sub_anio["seccion"].nunique()) if "seccion" in sub_anio else None,
            "resumen": resumen, "elecciones": elecciones,
            "secciones_competidas": self._secciones_competidas(bloque_principal) if nivel != "seccion" else [],
            "municipios": self._municipios(bloque_principal) if nivel not in {"municipio", "seccion"} else [],
            "historia": historia,
        }


# ---------------------------------------------------------------------------
# Hechos verificables para redactar discursos
# ---------------------------------------------------------------------------

def _miles(valor: float) -> str:
    return f"{int(round(valor)):,}"


def descripcion(perfil: dict[str, Any]) -> str:
    partes = [f"{perfil['nivel_etiqueta']} {perfil['unidad']}"]
    if perfil.get("anio"):
        partes.append(str(perfil["anio"]))
    return " · ".join(partes)


def hechos(perfil: dict[str, Any], eleccion: Optional[str] = None) -> dict[str, Any]:
    """Ficha de datos para el prompt y lista de cifras que el verificador acepta."""
    bloques = perfil["elecciones"]
    bloque = next((b for b in bloques if b["tipo"] == eleccion), None) or (bloques[0] if bloques else None)
    if bloque is None:
        raise ValueError("El territorio no tiene resultados para redactar.")

    permitidos: list[dict[str, Any]] = []

    def agregar(valor: Optional[float], tipo: str) -> None:
        if valor is not None:
            permitidos.append({"valor": float(valor), "tipo": tipo})

    lineas = [f"Territorio: {descripcion(perfil)}. Elección: {bloque['etiqueta']}."]
    if perfil["nivel"] in {"id_distrito_local", "id_distrito_federal", "seccion"}:
        agregar(float(perfil["unidad"]) if str(perfil["unidad"]).isdigit() else None, "id")
    if perfil.get("anio") and str(perfil["anio"]).isdigit():
        agregar(float(perfil["anio"]), "id")
    if perfil.get("secciones"):
        lineas.append(f"Secciones electorales incluidas: {_miles(perfil['secciones'])}.")
        agregar(perfil["secciones"], "votos")

    if bloque["lista_nominal"]:
        agregar(bloque["lista_nominal"], "votos")
        agregar(bloque["votos_emitidos"], "votos")
        linea = f"Lista nominal: {_miles(bloque['lista_nominal'])} personas. Votos emitidos: {_miles(bloque['votos_emitidos'])}."
        if bloque["participacion_pct"] is not None:
            linea += f" Participación: {bloque['participacion_pct']:.1f} %."
            agregar(bloque["participacion_pct"], "porcentaje")
            agregar(bloque["abstencion_pct"], "porcentaje")
        lineas.append(linea)

    for i, fila in enumerate(bloque["ranking"][:5]):
        agregar(fila["votos"], "votos")
        agregar(fila["porcentaje"], "porcentaje")
        lugar = ["Primer", "Segundo", "Tercer", "Cuarto", "Quinto"][i]
        lineas.append(f"{lugar} lugar: {fila['partido'].replace('_', '-')} con {_miles(fila['votos'])} votos "
                      f"({fila['porcentaje']:.1f} %).")
    if bloque["empate"]:
        lineas.append("Los dos primeros lugares quedaron empatados en votos.")
    elif bloque["margen_votos"] is not None:
        agregar(bloque["margen_votos"], "votos")
        agregar(bloque["margen_pp"], "porcentaje")
        lineas.append(f"Diferencia entre primero y segundo: {_miles(bloque['margen_votos'])} votos "
                      f"({bloque['margen_pp']:.1f} puntos porcentuales). Competitividad: {bloque['competitividad'].lower()}.")
    if bloque["nulos"]:
        agregar(bloque["nulos"], "votos")
        lineas.append(f"Votos nulos: {_miles(bloque['nulos'])}.")

    competidas = perfil.get("secciones_competidas", [])[:3]
    if competidas and bloque["tipo"] == perfil.get("principal"):
        partes = []
        for s in competidas:
            agregar(float(s["seccion"]), "id")
            agregar(s["margen_votos"], "votos")
            agregar(s["margen_pp"], "porcentaje")
            partes.append(f"sección {s['seccion']} (diferencia de {_miles(s['margen_votos'])} votos, {s['margen_pp']:.1f} puntos)")
        lineas.append("Secciones más competidas: " + "; ".join(partes) + ".")

    return {"lineas": lineas, "permitidos": permitidos, "eleccion": bloque["tipo"], "descripcion": descripcion(perfil)}
