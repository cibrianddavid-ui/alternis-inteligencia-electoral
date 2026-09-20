"""Historial persistente de discursos: cada persona ve solo sus propias conversaciones."""
from __future__ import annotations

import json
from typing import Any, Optional

from . import basedatos

MAX_CONVERSACIONES = 200


def _titulo(mensaje: str, formato_nombre: str = "") -> str:
    limpio = " ".join(mensaje.split())
    base = limpio[:60].rstrip() + ("…" if len(limpio) > 60 else "")
    return f"{formato_nombre}: {base}" if formato_nombre and formato_nombre != "Libre" else base or "Nuevo discurso"


def titulo_automatico(mensaje: str, formato_nombre: str = "") -> str:
    return _titulo(mensaje, formato_nombre)


def crear(usuario_id: int, titulo: str, formato: str, territorio: Optional[dict[str, Any]]) -> int:
    ahora = basedatos.ahora()
    with basedatos.transaccion() as db:
        cursor = db.execute(
            "INSERT INTO discursos (usuario_id, titulo, formato, territorio, creado, actualizado) VALUES (?,?,?,?,?,?)",
            (usuario_id, titulo[:150], formato, json.dumps(territorio) if territorio else None, ahora, ahora))
        # Se conservan las conversaciones más recientes de cada persona.
        db.execute(
            "DELETE FROM discursos WHERE usuario_id=? AND id NOT IN "
            "(SELECT id FROM discursos WHERE usuario_id=? ORDER BY actualizado DESC, id DESC LIMIT ?)",
            (usuario_id, usuario_id, MAX_CONVERSACIONES))
        return cursor.lastrowid


def _existe(db, usuario_id: int, discurso_id: int):
    return db.execute("SELECT * FROM discursos WHERE id=? AND usuario_id=?", (discurso_id, usuario_id)).fetchone()


def configurar(usuario_id: int, discurso_id: int, formato: str, territorio: Optional[dict[str, Any]]) -> None:
    with basedatos.transaccion() as db:
        if _existe(db, usuario_id, discurso_id) is None:
            raise LookupError("Conversación no encontrada.")
        db.execute("UPDATE discursos SET formato=?, territorio=? WHERE id=?",
                   (formato, json.dumps(territorio) if territorio else None, discurso_id))


def agregar_mensaje(usuario_id: int, discurso_id: int, rol: str, contenido: str,
                    verificacion: Optional[dict[str, Any]] = None) -> None:
    with basedatos.transaccion() as db:
        if _existe(db, usuario_id, discurso_id) is None:
            raise LookupError("Conversación no encontrada.")
        ahora = basedatos.ahora()
        db.execute("INSERT INTO discurso_mensajes (discurso_id, rol, contenido, verificacion, creado) VALUES (?,?,?,?,?)",
                   (discurso_id, rol, contenido[:30000], json.dumps(verificacion) if verificacion else None, ahora))
        db.execute("UPDATE discursos SET actualizado=? WHERE id=?", (ahora, discurso_id))


def listar(usuario_id: int, limite: int = 100) -> list[dict[str, Any]]:
    with basedatos.transaccion() as db:
        filas = db.execute(
            "SELECT d.id, d.titulo, d.formato, d.actualizado, "
            "(SELECT COUNT(*) FROM discurso_mensajes m WHERE m.discurso_id = d.id) AS mensajes "
            "FROM discursos d WHERE d.usuario_id=? ORDER BY d.actualizado DESC, d.id DESC LIMIT ?",
            (usuario_id, limite)).fetchall()
        return [dict(f) for f in filas]


def obtener(usuario_id: int, discurso_id: int) -> Optional[dict[str, Any]]:
    with basedatos.transaccion() as db:
        cabecera = _existe(db, usuario_id, discurso_id)
        if cabecera is None:
            return None
        mensajes = db.execute(
            "SELECT rol, contenido, verificacion, creado FROM discurso_mensajes WHERE discurso_id=? ORDER BY id",
            (discurso_id,)).fetchall()
    return {
        "id": cabecera["id"], "titulo": cabecera["titulo"], "formato": cabecera["formato"],
        "territorio": json.loads(cabecera["territorio"]) if cabecera["territorio"] else None,
        "actualizado": cabecera["actualizado"],
        "mensajes": [{"role": m["rol"], "content": m["contenido"], "creado": m["creado"],
                      "verificacion": json.loads(m["verificacion"]) if m["verificacion"] else None} for m in mensajes],
    }


def renombrar(usuario_id: int, discurso_id: int, titulo: str) -> bool:
    with basedatos.transaccion() as db:
        return db.execute("UPDATE discursos SET titulo=? WHERE id=? AND usuario_id=?",
                          (titulo.strip()[:150] or "Nuevo discurso", discurso_id, usuario_id)).rowcount > 0


def eliminar(usuario_id: int, discurso_id: int) -> bool:
    with basedatos.transaccion() as db:
        return db.execute("DELETE FROM discursos WHERE id=? AND usuario_id=?", (discurso_id, usuario_id)).rowcount > 0
