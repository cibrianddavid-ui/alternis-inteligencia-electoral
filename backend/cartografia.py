"""Cartografía electoral determinista por sección.

Une la geometría preprocesada (GeoJSON EPSG:4326) con los mismos datos que usa
Perfil territorial. No modifica electoral.db ni interpreta resultados con LLM.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .territorio import AUXILIARES, NO_OPCION, Territorio, etiqueta_eleccion


class Cartografia:
    VARIABLES = [
        {"valor": "ganador", "etiqueta": "Ganador"},
        {"valor": "margen", "etiqueta": "Margen 1.º vs 2.º"},
        {"valor": "participacion", "etiqueta": "Participación"},
    ]

    def __init__(self, territorio: Territorio, ruta_geojson: Path) -> None:
        self.territorio = territorio
        self.ruta_geojson = ruta_geojson
        self._geo: Optional[dict[str, Any]] = None

    def _geometrias(self) -> dict[str, Any]:
        if self._geo is None:
            if not self.ruta_geojson.exists():
                raise FileNotFoundError(f"No se encontró la cartografía: {self.ruta_geojson}")
            self._geo = json.loads(self.ruta_geojson.read_text(encoding="utf-8"))
        return self._geo

    def catalogo(self) -> dict[str, Any]:
        cat = self.territorio.catalogo()
        datos = self.territorio.datos
        niveles = []
        for valor, etiqueta in [("municipio", "Municipio"), ("id_distrito_local", "Distrito local"),
                                ("id_distrito_federal", "Distrito federal"), ("seccion", "Sección")]:
            if valor in datos:
                unidades = sorted(datos[valor].dropna().astype(str).unique().tolist(),
                                  key=lambda x: (0, float(x)) if str(x).replace(".", "", 1).isdigit() else (1, str(x)))
                niveles.append({"valor": valor, "etiqueta": etiqueta, "unidades": unidades})
        return {
            "anios": cat.get("anios", []),
            "elecciones": cat.get("elecciones", []),
            "niveles": niveles,
            "variables": self.VARIABLES,
        }

    @staticmethod
    def _metricas_seccion(bloque: pd.DataFrame) -> dict[str, dict[str, Any]]:
        salida: dict[str, dict[str, Any]] = {}
        for seccion, sec in bloque.groupby("seccion", observed=True):
            agg = sec.groupby(["partido", "norm"], observed=True, as_index=False)["votos"].sum()
            def suma(nombre: str) -> float:
                return float(agg.loc[agg["norm"] == nombre, "votos"].sum())

            lista = suma("LISTA_NOMINAL")
            total_calc = suma("TOTAL_VOTOS_CALCULADOS")
            emitidos = float(agg.loc[~agg["norm"].isin(AUXILIARES), "votos"].sum())
            base = total_calc if total_calc > 0 else emitidos
            opciones = agg[~agg["norm"].isin(NO_OPCION) & (agg["votos"] > 0)].sort_values(
                ["votos", "partido"], ascending=[False, True]
            )
            ranking = []
            for r in opciones.itertuples():
                ranking.append({
                    "partido": str(r.partido),
                    "votos": int(round(r.votos)),
                    "porcentaje": round(float(r.votos) / base * 100, 2) if base else None,
                })
            primero = ranking[0] if ranking else None
            segundo = ranking[1] if len(ranking) > 1 else None
            margen_votos = (primero["votos"] - segundo["votos"]) if primero and segundo else None
            margen_pp = (
                round(primero["porcentaje"] - segundo["porcentaje"], 2)
                if primero and segundo and primero["porcentaje"] is not None and segundo["porcentaje"] is not None
                else None
            )
            participacion = round(emitidos / lista * 100, 2) if lista > 0 else None
            salida[str(seccion)] = {
                "ganador": primero,
                "segundo": segundo,
                "margen_votos": margen_votos,
                "margen_pp": margen_pp,
                "participacion_pct": participacion,
                "votos_emitidos": int(round(emitidos)),
                "lista_nominal": int(round(lista)),
            }
        return salida

    def mapa(self, *, anio: Optional[str], eleccion: str, nivel: Optional[str], unidad: Optional[str],
             variable: str) -> dict[str, Any]:
        if variable not in {x["valor"] for x in self.VARIABLES}:
            raise ValueError("Variable cartográfica no válida.")
        datos = self.territorio.datos
        bloque = datos[datos["eleccion"] == eleccion]
        if self.territorio.tiene_anio and anio:
            bloque = bloque[bloque["anio"] == str(anio)]
        niveles_validos = {"municipio", "id_distrito_local", "id_distrito_federal", "seccion"}
        if nivel and nivel not in niveles_validos:
            raise ValueError("Nivel territorial no válido.")
        if unidad:
            if not nivel or nivel not in bloque:
                raise ValueError("La unidad territorial seleccionada no está disponible en la base.")
            bloque = bloque[bloque[nivel].astype(str) == str(unidad)]
        if bloque.empty:
            raise ValueError("No hay datos para los filtros seleccionados.")
        if "seccion" not in bloque:
            raise ValueError("La base electoral no contiene la dimensión sección.")

        metricas = self._metricas_seccion(bloque)
        features = []
        for feature in self._geometrias().get("features", []):
            props = dict(feature.get("properties") or {})
            sec = props.get("seccion")
            try:
                sec_key = str(int(float(sec)))
            except (TypeError, ValueError):
                sec_key = str(sec).strip()
            metrica = metricas.get(sec_key)
            if metrica is None:
                continue
            props.update(metrica)
            features.append({"type": "Feature", "properties": props, "geometry": feature.get("geometry")})

        return {
            "type": "FeatureCollection",
            "features": features,
            "meta": {
                "anio": anio,
                "eleccion": eleccion,
                "eleccion_etiqueta": etiqueta_eleccion(eleccion),
                "nivel": nivel,
                "unidad": unidad,
                "variable": variable,
                "secciones": len(features),
            },
        }
