"""Limpieza y auditoría de la base electoral.

Tres usos:
  * normalizar(df, mapa): correcciones seguras al cargar (espacios, mayúsculas, filas idénticas repetidas).
    Nunca cambia votos.
  * auditar(df, mapa): revisa la base y devuelve hallazgos con qué hacer en la fuente. No modifica nada.
  * votos_por_bloque(df, mapa): tabla limpia, una fila por sección y elección, con votos por bloque.

Por consola (sin abrir la app):
    python -m backend.limpieza                 # lee data/electoral.db y escribe data/reporte_calidad.md
    python -m backend.limpieza --exportar      # además escribe data/resultados_por_bloque.csv
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from . import bloques

AUXILIARES = {"LISTA_NOMINAL", "TOTAL_VOTOS_CALCULADOS"}
NO_OPCION = AUXILIARES | {"NULOS", "NO_REGISTRADAS"}
CONCEPTOS = ["anio", "id_entidad", "entidad", "municipio", "seccion", "id_distrito_local", "id_distrito_federal",
             "tipo_eleccion", "partido", "votos"]
TEXTO = ["entidad", "municipio", "tipo_eleccion", "partido"]
MAYUSCULAS = ["tipo_eleccion", "partido"]


# --------------------------------------------------------------------------- normalizar
def normalizar(df: pd.DataFrame, mapa: dict[str, str]) -> pd.DataFrame:
    """Correcciones que no alteran ningún resultado. Deja un resumen en df.attrs['limpieza']."""
    df = df.copy()
    cambios: dict[str, int] = {}
    for concepto in TEXTO:
        col = mapa.get(concepto)
        if not col or col not in df:
            continue
        original = df[col].astype("string")
        limpio = original.str.strip().str.replace(r"\s+", " ", regex=True)
        if concepto in MAYUSCULAS:
            limpio = limpio.str.upper()
        cambios[concepto] = int((original.fillna("") != limpio.fillna("")).sum())
        limpio = limpio.astype(object)
        df[col] = limpio.where(limpio.notna(), None)
    antes = len(df)
    df = df.drop_duplicates()
    df.attrs["limpieza"] = {"textos_corregidos": cambios, "filas_repetidas_eliminadas": antes - len(df)}
    return df


# --------------------------------------------------------------------------- auditoría
def _hallazgo(codigo: str, gravedad: str, titulo: str, detalle: str, cantidad: int,
              ejemplos: list[Any], que_hacer: str) -> dict[str, Any]:
    return {"codigo": codigo, "gravedad": gravedad, "titulo": titulo, "detalle": detalle,
            "cantidad": int(cantidad), "ejemplos": ejemplos[:8], "que_hacer": que_hacer}


def auditar(df: pd.DataFrame, mapa: dict[str, str]) -> dict[str, Any]:
    c = {k: mapa[k] for k in CONCEPTOS if k in mapa and mapa[k] in df}
    d = pd.DataFrame({k: df[v] for k, v in c.items()})
    d["partido"] = d["partido"].astype(str).str.upper().str.strip()
    d["votos"] = pd.to_numeric(d["votos"], errors="coerce").fillna(0.0)
    hallazgos: list[dict[str, Any]] = []
    tiene_seccion = "seccion" in d

    clave = [k for k in ("anio", "seccion", "tipo_eleccion", "partido") if k in d]
    dup = int(d.duplicated(clave).sum())
    if dup:
        hallazgos.append(_hallazgo(
            "duplicados", "alta", "Filas repetidas para la misma sección, elección y partido",
            f"{dup:,} filas comparten {', '.join(clave)}. Sumarían votos dos veces.", dup,
            d[d.duplicated(clave, keep=False)].head(8).to_dict("records"),
            "Revisa la hoja de origen: deja una sola fila por combinación, o suma los votos si eran capturas parciales."))

    if tiene_seccion:
        for campo in ("municipio", "id_distrito_local", "id_distrito_federal"):
            if campo in d:
                g = d.groupby([k for k in ("anio", "seccion") if k in d])[campo].nunique()
                malas = g[g > 1]
                if len(malas):
                    hallazgos.append(_hallazgo(
                        f"jerarquia_{campo}", "alta", f"Secciones asignadas a más de un valor de {campo}",
                        "Una sección debe pertenecer a un solo municipio y distrito.", len(malas),
                        [str(i) for i in malas.index[:8]], "Corrige la columna en la hoja de origen."))

        base = ["seccion", "tipo_eleccion"] + (["municipio"] if "municipio" in d else [])
        opciones = d[~d["partido"].isin(AUXILIARES)].groupby(base)["votos"].sum().rename("opciones")
        total = d[d["partido"] == "TOTAL_VOTOS_CALCULADOS"].groupby(base)["votos"].sum().rename("total")
        lista = d[d["partido"] == "LISTA_NOMINAL"].groupby(base)["votos"].sum().rename("lista")
        j = pd.concat([opciones, total, lista], axis=1).fillna(0).reset_index()

        # El total calculado debe igualar la suma de opciones (incluidos nulos y no registrados).
        j["falta"] = j["total"] - j["opciones"]
        desc = j[(j["total"] > 0) & (j["falta"].abs() > 1)]
        if len(desc):
            por = desc.groupby(["tipo_eleccion"] + (["municipio"] if "municipio" in desc else [])).agg(
                secciones=("seccion", "count"), votos_faltantes=("falta", "sum")).reset_index()
            por = por.sort_values("secciones", ascending=False)
            hallazgos.append(_hallazgo(
                "suma_no_cuadra", "alta", "El total calculado no coincide con la suma de las opciones",
                f"{len(desc):,} combinaciones sección-elección; faltan {int(desc['falta'].sum()):,} votos en total "
                "(si es positivo, hay votos en el total que no están en ninguna opción).",
                len(desc), por.head(8).to_dict("records"),
                "Casi siempre es una columna no capturada (candidatura independiente, otra coalición, voto en casilla "
                "especial). Compara esas secciones contra el acta o el cómputo oficial y agrega la opción faltante."))

        fuera = j[(j["lista"] > 0) & (j["opciones"] > j["lista"])]
        if len(fuera):
            hallazgos.append(_hallazgo(
                "participacion_mayor_100", "media", "Votos emitidos mayores que la lista nominal",
                f"{len(fuera):,} combinaciones sección-elección superan el 100 % de participación. Suele deberse a "
                "casillas especiales, donde vota gente de otras secciones.", len(fuera),
                fuera.sort_values("opciones", ascending=False).head(8)[base + ["opciones", "lista"]].to_dict("records"),
                "Marca esas secciones como casilla especial y no las uses para calcular participación; a nivel "
                "municipio el efecto se compensa."))

        ln = lista.reset_index().pivot_table(index="seccion", columns="tipo_eleccion", values="lista", aggfunc="sum")
        distinta = ln[ln.nunique(axis=1) > 1]
        if len(distinta):
            hallazgos.append(_hallazgo(
                "lista_nominal_distinta", "info", "La lista nominal cambia entre elecciones en la misma sección",
                "Puede ser legítimo (cortes distintos); conviene saberlo al comparar participación.", len(distinta),
                [str(s) for s in distinta.index[:8]], "Sin acción si cada elección usa su propio corte."))

        sin_votos = d[d["partido"] == "TOTAL_VOTOS_CALCULADOS"].groupby("seccion")["votos"].sum()
        sin_votos = sin_votos[sin_votos == 0]
        if len(sin_votos):
            hallazgos.append(_hallazgo("secciones_sin_votos", "media", "Secciones sin ningún voto registrado",
                                       "Pueden ser secciones sin resultados capturados.", len(sin_votos),
                                       [str(s) for s in sin_votos.index[:8]], "Verifica si falta la captura."))

    # Opciones que existen en unas elecciones y no en otras (p. ej. coaliciones).
    combos = d[(~d["partido"].isin(NO_OPCION)) & d["partido"].str.contains("_") & (d["votos"] > 0)]
    por_eleccion = combos.groupby("tipo_eleccion")["partido"].agg(lambda s: set(s.unique()))
    todas = set().union(*por_eleccion.values) if len(por_eleccion) else set()
    faltan = {e: sorted(todas - por_eleccion.get(e, set())) for e in d["tipo_eleccion"].unique()}
    faltan = {e: v for e, v in faltan.items() if v}
    if faltan:
        hallazgos.append(_hallazgo(
            "opciones_distintas", "alta", "Hay coaliciones registradas en unas elecciones y no en otras",
            "Si una coalición no aparece, sus votos están dentro de otra opción (probablemente un partido suelto). "
            "Comparar partido contra partido entre esas elecciones da diferencias que no son reales.",
            len(faltan), [{"eleccion": e, "sin_registrar": v} for e, v in faltan.items()],
            "Para comparar, usa los bloques (backend/bloques.py), que suman partidos y coaliciones. En la fuente, "
            "confirma cómo se capturó esa elección y documéntalo."))

    sin_clasificar = sorted({p for p in d.loc[~d["partido"].isin(NO_OPCION), "partido"].unique()
                             if bloques.bloque_de(p) in {"SIN_BLOQUE", "MIXTO"}})
    if sin_clasificar:
        hallazgos.append(_hallazgo(
            "sin_bloque", "media", "Opciones que no pertenecen a ningún bloque",
            "No entran en las comparaciones por bloque.", len(sin_clasificar), sin_clasificar,
            "Agrégalas a BLOQUES en backend/bloques.py."))

    orden = {"alta": 0, "media": 1, "info": 2}
    hallazgos.sort(key=lambda h: orden[h["gravedad"]])
    return {
        "filas": int(len(d)),
        "elecciones": sorted(d["tipo_eleccion"].unique().tolist()),
        "anios": sorted(d["anio"].unique().tolist()) if "anio" in d else [],
        "secciones": int(d["seccion"].nunique()) if tiene_seccion else None,
        "limpieza_aplicada": df.attrs.get("limpieza", {}),
        "hallazgos": hallazgos,
    }


# --------------------------------------------------------------------------- tabla limpia por bloque
def votos_por_bloque(df: pd.DataFrame, mapa: dict[str, str]) -> pd.DataFrame:
    """Una fila por sección y elección: votos por bloque, nulos, lista nominal, participación y ganador."""
    c = {k: mapa[k] for k in CONCEPTOS if k in mapa and mapa[k] in df}
    d = pd.DataFrame({k: df[v] for k, v in c.items()})
    d["partido"] = d["partido"].astype(str).str.upper().str.strip()
    d["votos"] = pd.to_numeric(d["votos"], errors="coerce").fillna(0.0)
    llaves = [k for k in ("anio", "entidad", "municipio", "id_distrito_local", "id_distrito_federal", "seccion",
                          "tipo_eleccion") if k in d]

    op = d[~d["partido"].isin(NO_OPCION)].copy()
    traduce = {p: bloques.bloque_de(p) for p in op["partido"].unique()}
    op["bloque"] = op["partido"].map(traduce)
    ancho = op.pivot_table(index=llaves, columns="bloque", values="votos", aggfunc="sum", fill_value=0)
    ancho.columns = [f"votos_{x}" for x in ancho.columns]
    for nombre, partido in (("nulos", "NULOS"), ("no_registradas", "NO_REGISTRADAS"),
                            ("lista_nominal", "LISTA_NOMINAL"), ("total_calculado", "TOTAL_VOTOS_CALCULADOS")):
        s = d[d["partido"] == partido].groupby(llaves)["votos"].sum().rename(nombre)
        ancho = ancho.join(s, how="left")
    ancho = ancho.fillna(0).reset_index()
    cols_bloque = [x for x in ancho.columns if x.startswith("votos_")]
    ancho["votos_emitidos"] = ancho[cols_bloque].sum(axis=1) + ancho["nulos"] + ancho["no_registradas"]
    base = ancho["total_calculado"].where(ancho["total_calculado"] > 0, ancho["votos_emitidos"])
    for col in cols_bloque:
        ancho[col.replace("votos_", "pct_")] = (ancho[col] / base * 100).round(2)
    ancho["participacion_pct"] = (ancho["votos_emitidos"] / ancho["lista_nominal"].where(ancho["lista_nominal"] > 0) * 100).round(2)
    ancho["bloque_ganador"] = ancho[cols_bloque].idxmax(axis=1).str.replace("votos_", "", regex=False)
    ancho["descuadre_votos"] = (ancho["total_calculado"] - ancho["votos_emitidos"]).round(0)
    ancho["casilla_especial_probable"] = (ancho["lista_nominal"] > 0) & (ancho["votos_emitidos"] > ancho["lista_nominal"])
    return ancho


# --------------------------------------------------------------------------- informe
def reporte_markdown(resultado: dict[str, Any]) -> str:
    lineas = ["# Reporte de calidad de datos", "",
              f"- Filas: {resultado['filas']:,}", f"- Elecciones: {', '.join(resultado['elecciones'])}",
              f"- Años: {', '.join(map(str, resultado['anios'])) or '—'}",
              f"- Secciones: {resultado['secciones'] or '—'}", ""]
    etiqueta = {"alta": "Alta", "media": "Media", "info": "Informativo"}
    if not resultado["hallazgos"]:
        lineas.append("No se encontraron problemas.")
    for h in resultado["hallazgos"]:
        lineas += [f"## [{etiqueta[h['gravedad']]}] {h['titulo']}", "", h["detalle"], "",
                   f"**Qué hacer:** {h['que_hacer']}", ""]
        if h["ejemplos"]:
            lineas += ["Ejemplos:", ""] + [f"- {e}" for e in h["ejemplos"]] + [""]
    return "\n".join(lineas)


def _cargar_desde_sqlite(ruta: Path) -> tuple[pd.DataFrame, dict[str, str]]:
    with sqlite3.connect(ruta) as conn:
        df = pd.read_sql("SELECT * FROM resultados", conn)
    return df, {k: k for k in CONCEPTOS if k in df.columns}


def main(argv: Optional[list[str]] = None) -> None:
    carpeta = Path(__file__).resolve().parent.parent / "data"
    ap = argparse.ArgumentParser(description="Audita la base electoral y exporta una tabla limpia por bloque.")
    ap.add_argument("--db", type=Path, default=carpeta / "electoral.db")
    ap.add_argument("--salida", type=Path, default=carpeta)
    ap.add_argument("--exportar", action="store_true", help="Escribe también resultados_por_bloque.csv")
    args = ap.parse_args(argv)
    df, mapa = _cargar_desde_sqlite(args.db)
    df = normalizar(df, mapa)
    resultado = auditar(df, mapa)
    args.salida.mkdir(parents=True, exist_ok=True)
    (args.salida / "reporte_calidad.md").write_text(reporte_markdown(resultado), encoding="utf-8")
    print(f"Reporte: {args.salida / 'reporte_calidad.md'} ({len(resultado['hallazgos'])} hallazgos)")
    if args.exportar:
        tabla = votos_por_bloque(df, mapa)
        tabla.to_csv(args.salida / "resultados_por_bloque.csv", index=False, encoding="utf-8-sig")
        print(f"Tabla: {args.salida / 'resultados_por_bloque.csv'} ({len(tabla):,} filas)")


if __name__ == "__main__":
    main()
