"""Consultas deterministas para el modulo de resultados graficos."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from pydantic import BaseModel, Field, validator


PARTIDOS_AUXILIARES = {"LISTA_NOMINAL", "TOTAL_VOTOS_CALCULADOS"}
PARTIDOS_NO_VALIDOS_COALICION = PARTIDOS_AUXILIARES | {"NULOS", "NO_REGISTRADAS"}
NIVELES = {
    "entidad": "Estado",
    "municipio": "Municipio",
    "id_distrito_local": "Distrito local",
    "id_distrito_federal": "Distrito federal",
    "seccion": "Seccion",
}


class GrupoPolitico(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    partidos: List[str] = Field(default_factory=list)

    @validator("nombre")
    def limpiar_nombre(cls, valor: str) -> str:
        return valor.strip()


class ConsultaGraficos(BaseModel):
    modo: str = "temporal"
    nivel: str
    unidades: List[str] = Field(default_factory=list)
    anios: List[str] = Field(default_factory=list)
    elecciones: List[str] = Field(default_factory=list)
    partidos: List[str] = Field(default_factory=list)
    grupos: List[GrupoPolitico] = Field(default_factory=list)
    metrica: str = "porcentaje"

    @validator("modo")
    def validar_modo(cls, valor: str) -> str:
        if valor not in {"temporal", "diferenciado", "coaliciones"}:
            raise ValueError("Modo de analisis no valido.")
        return valor

    @validator("metrica")
    def validar_metrica(cls, valor: str) -> str:
        if valor not in {"votos", "porcentaje"}:
            raise ValueError("Metrica no valida.")
        return valor


def _serie_texto(df: pd.DataFrame, columna: str) -> pd.Series:
    return df[columna].astype("string").fillna("")


def _ordenar_valores(valores: List[Any]) -> List[str]:
    textos = [str(v) for v in valores if pd.notna(v) and str(v).strip()]

    def clave(valor: str) -> Tuple[int, Any]:
        try:
            return 0, float(valor)
        except ValueError:
            return 1, valor

    return sorted(set(textos), key=clave)


def _registros_json(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Convierte NaN/NA a None para que FastAPI produzca JSON valido."""
    salida: List[Dict[str, Any]] = []
    for registro in df.to_dict(orient="records"):
        salida.append({
            clave: None if pd.isna(valor) else valor
            for clave, valor in registro.items()
        })
    return salida


def construir_catalogo(df: pd.DataFrame, mapa: Dict[str, str]) -> Dict[str, Any]:
    """Devuelve los valores disponibles para construir filtros dependientes."""
    dimensiones: Dict[str, List[str]] = {}
    for concepto in ("anio", "tipo_eleccion", "partido"):
        if concepto in mapa:
            dimensiones[concepto] = _ordenar_valores(df[mapa[concepto]].unique().tolist())

    niveles = []
    for clave, etiqueta in NIVELES.items():
        if clave not in mapa:
            continue
        niveles.append({
            "valor": clave,
            "etiqueta": etiqueta,
            "unidades": _ordenar_valores(df[mapa[clave]].unique().tolist()),
        })

    partidos = [
        partido for partido in dimensiones.get("partido", [])
        if partido.upper() not in PARTIDOS_AUXILIARES
    ]
    partidos_coalicion = [
        partido for partido in partidos
        if partido.upper() not in PARTIDOS_NO_VALIDOS_COALICION
    ]
    return {
        "anios": dimensiones.get("anio", []),
        "elecciones": dimensiones.get("tipo_eleccion", []),
        "partidos": partidos,
        "partidos_coalicion": partidos_coalicion,
        "niveles": niveles,
        "tiene_anio": "anio" in mapa,
    }


def _validar_consulta(
    consulta: ConsultaGraficos,
    df: pd.DataFrame,
    mapa: Dict[str, str],
) -> None:
    if consulta.nivel not in NIVELES or consulta.nivel not in mapa:
        raise ValueError("El nivel de agregacion seleccionado no existe en la base.")
    if not consulta.unidades:
        raise ValueError("Selecciona al menos una unidad geografica.")
    if not consulta.elecciones:
        raise ValueError("Selecciona al menos un tipo de eleccion.")
    if "anio" in mapa and not consulta.anios:
        raise ValueError("Selecciona al menos un año.")
    if not consulta.partidos and not consulta.grupos:
        raise ValueError("Selecciona partidos o crea al menos una coalicion.")

    usados: Set[str] = set()
    nombres: Set[str] = set()
    for grupo in consulta.grupos:
        if not grupo.partidos:
            raise ValueError(f"El grupo '{grupo.nombre}' no contiene partidos.")
        nombre_norm = grupo.nombre.casefold()
        if nombre_norm in nombres:
            raise ValueError("Los nombres de las coaliciones deben ser diferentes.")
        nombres.add(nombre_norm)
        repetidos = usados.intersection(grupo.partidos)
        if repetidos:
            raise ValueError(
                "Un partido no puede pertenecer a dos coaliciones: "
                + ", ".join(sorted(repetidos))
            )
        usados.update(grupo.partidos)

    if usados.intersection(consulta.partidos):
        raise ValueError(
            "Un partido no puede mostrarse individualmente y dentro de una coalicion."
        )


def _filtrar(df: pd.DataFrame, columna: str, valores: List[str]) -> pd.DataFrame:
    if not valores:
        return df
    return df[_serie_texto(df, columna).isin({str(v) for v in valores})]


def _registros_faltantes(
    datos: pd.DataFrame,
    consulta: ConsultaGraficos,
    mapa: Dict[str, str],
) -> List[str]:
    avisos: List[str] = []
    if "anio" not in mapa or len(consulta.anios) < 2:
        return avisos
    presentes = set(_serie_texto(datos, mapa["anio"]).unique())
    faltantes = [anio for anio in consulta.anios if str(anio) not in presentes]
    if faltantes:
        avisos.append("No se encontraron registros para: " + ", ".join(faltantes) + ".")
    return avisos


def analizar_resultados(
    df: pd.DataFrame,
    mapa: Dict[str, str],
    consulta: ConsultaGraficos,
) -> Dict[str, Any]:
    """Filtra, agrega y calcula votos, porcentaje y variacion temporal."""
    _validar_consulta(consulta, df, mapa)

    columnas_filtro = [mapa[consulta.nivel], mapa["tipo_eleccion"]]
    valores_filtro = [consulta.unidades, consulta.elecciones]
    if "anio" in mapa:
        columnas_filtro.append(mapa["anio"])
        valores_filtro.append(consulta.anios)

    datos = df
    for columna, valores in zip(columnas_filtro, valores_filtro):
        datos = _filtrar(datos, columna, valores)

    avisos = _registros_faltantes(datos, consulta, mapa)
    if datos.empty:
        return {"filas": [], "resumen": {}, "avisos": avisos + ["La seleccion no produjo datos."]}

    col_partido = mapa["partido"]
    col_votos = mapa["votos"]
    contexto = [mapa[consulta.nivel], mapa["tipo_eleccion"]]
    if "anio" in mapa:
        contexto.insert(0, mapa["anio"])

    normal_partido = _serie_texto(datos, col_partido).str.upper()
    totales = datos[normal_partido == "TOTAL_VOTOS_CALCULADOS"].groupby(
        contexto, dropna=False
    )[col_votos].sum().rename("total_votos").reset_index()

    series: List[pd.DataFrame] = []
    for partido in consulta.partidos:
        bloque = datos[_serie_texto(datos, col_partido) == str(partido)]
        agregado = bloque.groupby(contexto, dropna=False)[col_votos].sum().reset_index()
        agregado["serie"] = partido
        series.append(agregado)

    for grupo in consulta.grupos:
        bloque = datos[_serie_texto(datos, col_partido).isin(set(grupo.partidos))]
        agregado = bloque.groupby(contexto, dropna=False)[col_votos].sum().reset_index()
        agregado["serie"] = grupo.nombre
        series.append(agregado)

    if not series:
        return {"filas": [], "resumen": {}, "avisos": avisos + ["No hay series para comparar."]}

    resultado = pd.concat(series, ignore_index=True)
    resultado = resultado.merge(totales, on=contexto, how="left")
    resultado["porcentaje"] = (
        resultado[col_votos] / resultado["total_votos"].where(resultado["total_votos"] > 0) * 100
    )

    renombres = {
        mapa[consulta.nivel]: "unidad",
        mapa["tipo_eleccion"]: "tipo_eleccion",
        col_votos: "votos",
    }
    if "anio" in mapa:
        renombres[mapa["anio"]] = "anio"
    resultado = resultado.rename(columns=renombres)
    resultado["votos"] = resultado["votos"].round().astype("Int64")
    resultado["total_votos"] = resultado["total_votos"].round().astype("Int64")
    resultado["porcentaje"] = resultado["porcentaje"].round(3)

    if "anio" in resultado.columns:
        resultado["__anio_num"] = pd.to_numeric(resultado["anio"], errors="coerce")
        resultado = resultado.sort_values(
            ["serie", "unidad", "tipo_eleccion", "__anio_num", "anio"]
        )
        grupos_delta = ["serie", "unidad", "tipo_eleccion"]
        resultado["variacion_votos"] = resultado.groupby(grupos_delta)["votos"].diff()
        resultado["variacion_pp"] = (
            resultado.groupby(grupos_delta)["porcentaje"].diff().round(3)
        )
        resultado = resultado.drop(columns="__anio_num")
    else:
        resultado["variacion_votos"] = pd.NA
        resultado["variacion_pp"] = pd.NA

    contexto_salida = [c for c in ("anio", "unidad", "tipo_eleccion") if c in resultado.columns]
    indices_lider = resultado.groupby(contexto_salida)["votos"].idxmax()
    lideres = resultado.loc[indices_lider, contexto_salida + ["serie", "votos", "porcentaje"]]

    filas = _registros_json(resultado)
    resumen = {
        "votos_representados": int(resultado["votos"].sum()),
        "comparaciones": int(resultado[contexto_salida].drop_duplicates().shape[0]),
        "series": int(resultado["serie"].nunique()),
        "lideres": _registros_json(lideres),
    }
    if totales.empty:
        avisos.append(
            "No se encontro TOTAL_VOTOS_CALCULADOS; los porcentajes no estan disponibles."
        )

    return {"filas": filas, "resumen": resumen, "avisos": avisos}
