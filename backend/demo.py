"""Datos de demostración: una campaña de ejemplo ligada a territorios reales de la base.

Todo lo que se crea aquí lleva la marca demo=1, de modo que puede retirarse en un
solo paso sin afectar los datos reales de la campaña.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from . import campania
from .territorio import Territorio


def _territorios(territorio: Optional[Territorio]) -> dict[str, Any]:
    """Elige territorios interesantes de la propia base: el distrito más disputado y los municipios más grandes."""
    elegido: dict[str, Any] = {"distrito": None, "municipio": None, "seccion": None}
    if territorio is None:
        return elegido
    niveles = {n["valor"]: n["unidades"] for n in territorio.catalogo()["niveles"]}
    mejores = None
    for unidad in niveles.get("id_distrito_local", []):
        perfil = territorio.perfil("id_distrito_local", unidad)
        resumen = perfil["resumen"]
        if resumen and resumen["margen_pp"] is not None and (mejores is None or resumen["margen_pp"] < mejores[0]):
            mejores = (resumen["margen_pp"], unidad, perfil)
    if mejores:
        _, unidad, perfil = mejores
        elegido["distrito"] = {"valor": unidad, "secciones": perfil["secciones"] or 20}
        if perfil["secciones_competidas"]:
            elegido["seccion"] = perfil["secciones_competidas"][0]["seccion"]
    if niveles.get("entidad"):
        municipios = territorio.perfil("entidad", niveles["entidad"][0])["municipios"]
        if municipios:
            elegido["municipio"] = municipios[min(1, len(municipios) - 1)]["municipio"]
    return elegido


def sembrar(territorio: Optional[Territorio] = None) -> dict[str, int]:
    if campania.hay_demo():
        raise ValueError("Ya hay datos de demostración cargados.")
    lugares = _territorios(territorio)
    hoy = date.today()

    def fecha(dias: int) -> str:
        return (hoy + timedelta(days=dias)).isoformat()

    conteo = {"areas": 0, "personas": 0, "metas": 0, "tareas": 0}
    with campania.conectar() as db:
        def insertar(tabla: str, **campos: Any) -> int:
            campos["demo"] = 1
            cursor = db.execute(
                f"INSERT INTO {tabla} ({', '.join(campos)}) VALUES ({', '.join('?' for _ in campos)})",
                list(campos.values()))
            conteo[tabla] += 1
            return cursor.lastrowid

        operacion = insertar("areas", nombre="Operación territorial", descripcion="Recorridos y trabajo de campo (demo)")
        comunicacion = insertar("areas", nombre="Comunicación", descripcion="Mensajes y calendario de publicaciones (demo)")
        juridico = insertar("areas", nombre="Jurídico y fiscalización", descripcion="Representantes y propaganda (demo)")

        laura = insertar("personas", nombre="Laura Méndez (demo)", contacto="demo@example.com", area_id=operacion)
        carlos = insertar("personas", nombre="Carlos Ibarra (demo)", contacto="demo@example.com", area_id=operacion)
        sofia = insertar("personas", nombre="Sofía Robles (demo)", contacto="demo@example.com", area_id=comunicacion)
        andres = insertar("personas", nombre="Andrés Vega (demo)", contacto="demo@example.com", area_id=juridico)

        distrito, municipio, seccion = lugares["distrito"], lugares["municipio"], lugares["seccion"]
        meta_distrito = insertar(
            "metas", titulo=f"Recorrer las secciones del distrito local {distrito['valor']}" if distrito else "Recorrer las secciones prioritarias",
            descripcion="Visitar cada sección al menos una vez antes del cierre de precampaña.",
            responsable_id=laura, area_id=operacion, fecha_limite=fecha(30), indicador="secciones visitadas",
            objetivo=distrito["secciones"] if distrito else 20, avance=round((distrito["secciones"] if distrito else 20) * 0.16),
            territorio_tipo="id_distrito_local" if distrito else None, territorio_valor=str(distrito["valor"]) if distrito else None)
        meta_municipio = insertar(
            "metas", titulo=f"Casa por casa en {municipio.title()}" if municipio else "Casa por casa en el municipio",
            descripcion="Contacto directo con hogares de las colonias de mayor participación.",
            responsable_id=carlos, area_id=operacion, fecha_limite=fecha(45), indicador="hogares visitados",
            objetivo=1500, avance=620, territorio_tipo="municipio" if municipio else None, territorio_valor=municipio)
        meta_seccion = insertar(
            "metas", titulo=f"Reforzar la representación en la sección {seccion}" if seccion else "Reforzar representantes en secciones competidas",
            descripcion="Acreditar representantes en las casillas de la sección más competida.",
            responsable_id=andres, area_id=juridico, fecha_limite=fecha(20), indicador="representantes acreditados",
            objetivo=4, avance=1, territorio_tipo="seccion" if seccion else None, territorio_valor=seccion)
        meta_mensajes = insertar(
            "metas", titulo="Calendario de mensajes del mes", descripcion="Una publicación por día hábil.",
            responsable_id=sofia, area_id=comunicacion, fecha_limite=fecha(28), indicador="publicaciones",
            objetivo=20, avance=8)

        insertar("tareas", titulo="Mapear las rutas de la semana", meta_id=meta_distrito, persona_id=laura,
                 prioridad="alta", peso=3, estado="finalizada", fecha_limite=fecha(-3), fecha_finalizacion=fecha(-4),
                 evidencia="Ruta_semana_1.pdf (demo)")
        insertar("tareas", titulo="Reunión con líderes de colonia", meta_id=meta_distrito, persona_id=carlos,
                 prioridad="alta", peso=5, estado="en_proceso", fecha_limite=fecha(3))
        insertar("tareas", titulo="Imprimir volantes informativos", meta_id=meta_municipio, persona_id=carlos,
                 prioridad="media", peso=3, estado="por_hacer", fecha_limite=fecha(-2),
                 descripcion="Vencida a propósito para mostrar las alertas del tablero.")
        insertar("tareas", titulo="Acreditar representantes de casilla", meta_id=meta_seccion, persona_id=andres,
                 prioridad="critica", peso=8, estado="en_proceso", fecha_limite=fecha(5))
        insertar("tareas", titulo="Capacitar a representantes", meta_id=meta_seccion,
                 prioridad="alta", peso=5, estado="por_hacer", fecha_limite=fecha(10),
                 descripcion="Sin responsable a propósito para mostrar las alertas del tablero.")
        insertar("tareas", titulo="Definir el calendario de publicaciones", meta_id=meta_mensajes, persona_id=sofia,
                 prioridad="media", peso=3, estado="finalizada", fecha_limite=fecha(-5), fecha_finalizacion=fecha(-6))
        insertar("tareas", titulo="Aprobar el guion del video de presentación", meta_id=meta_mensajes, persona_id=sofia,
                 prioridad="alta", peso=5, estado="por_hacer", fecha_limite=fecha(7))
        insertar("tareas", titulo="Revisar la propaganda antes de imprimir", persona_id=andres,
                 prioridad="alta", peso=3, estado="por_hacer", fecha_limite=fecha(14))
    return conteo
