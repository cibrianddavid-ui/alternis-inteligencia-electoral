"""Persistencia local de metas, personas y tareas de campaña."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "campania.db"
ESTADOS = {"por_hacer", "en_proceso", "finalizada"}


def conectar():
    DB.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def iniciar():
    with conectar() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS metas (
                id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, descripcion TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS personas (
                id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, contacto TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS tareas (
                id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, descripcion TEXT NOT NULL DEFAULT '',
                meta_id INTEGER REFERENCES metas(id) ON DELETE SET NULL,
                persona_id INTEGER REFERENCES personas(id) ON DELETE SET NULL,
                fecha_limite TEXT, estado TEXT NOT NULL DEFAULT 'por_hacer'
                    CHECK (estado IN ('por_hacer','en_proceso','finalizada'))
            );
        """)


def listar():
    with conectar() as db:
        return {tabla: [dict(row) for row in db.execute(f"SELECT * FROM {tabla} ORDER BY id DESC")]
                for tabla in ("metas", "personas", "tareas")}


def crear(tabla, datos):
    campos = {"metas": ("titulo", "descripcion"),
              "personas": ("nombre", "contacto"),
              "tareas": ("titulo", "descripcion", "meta_id", "persona_id", "fecha_limite")}[tabla]
    with conectar() as db:
        cursor = db.execute(f"INSERT INTO {tabla} ({','.join(campos)}) VALUES ({','.join('?' for _ in campos)})",
                            [datos.get(c) for c in campos])
        return dict(db.execute(f"SELECT * FROM {tabla} WHERE id=?", (cursor.lastrowid,)).fetchone())


def mover(tarea_id, estado):
    if estado not in ESTADOS:
        raise ValueError("Estado de tarea inválido.")
    with conectar() as db:
        db.execute("UPDATE tareas SET estado=? WHERE id=?", (estado, tarea_id))
        row = db.execute("SELECT * FROM tareas WHERE id=?", (tarea_id,)).fetchone()
        return dict(row) if row else None


def eliminar(tabla, item_id):
    if tabla not in ("metas", "personas", "tareas"):
        raise ValueError("Sección inválida.")
    with conectar() as db:
        return bool(db.execute(f"DELETE FROM {tabla} WHERE id=?", (item_id,)).rowcount)
