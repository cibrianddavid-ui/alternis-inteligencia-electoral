"""Persistencia local de la planeacion y las tareas de campaña."""
import sqlite3
from datetime import date
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "campania.db"
ESTADOS = {"por_hacer", "en_proceso", "finalizada"}
PRIORIDADES = {"baja", "media", "alta", "critica"}


def conectar():
    DB.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def iniciar():
    with conectar() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS areas (
                id INTEGER PRIMARY KEY, nombre TEXT NOT NULL UNIQUE,
                descripcion TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS metas (
                id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, descripcion TEXT NOT NULL DEFAULT '',
                responsable_id INTEGER REFERENCES personas(id) ON DELETE SET NULL,
                area_id INTEGER REFERENCES areas(id) ON DELETE SET NULL,
                fecha_limite TEXT, indicador TEXT NOT NULL DEFAULT '',
                objetivo REAL
            );
            CREATE TABLE IF NOT EXISTS personas (
                id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, contacto TEXT NOT NULL DEFAULT '',
                area_id INTEGER REFERENCES areas(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS tareas (
                id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, descripcion TEXT NOT NULL DEFAULT '',
                meta_id INTEGER REFERENCES metas(id) ON DELETE SET NULL,
                persona_id INTEGER REFERENCES personas(id) ON DELETE SET NULL,
                fecha_limite TEXT, prioridad TEXT NOT NULL DEFAULT 'media', peso INTEGER NOT NULL DEFAULT 3,
                estado TEXT NOT NULL DEFAULT 'por_hacer', fecha_finalizacion TEXT,
                evidencia TEXT NOT NULL DEFAULT ''
                    CHECK (estado IN ('por_hacer','en_proceso','finalizada'))
            );
        """)
        # Migracion no destructiva para bases creadas con versiones anteriores.
        migraciones = {
            "personas": {"area_id": "INTEGER REFERENCES areas(id) ON DELETE SET NULL"},
            "metas": {
                "responsable_id": "INTEGER REFERENCES personas(id) ON DELETE SET NULL",
                "area_id": "INTEGER REFERENCES areas(id) ON DELETE SET NULL",
                "fecha_limite": "TEXT", "indicador": "TEXT NOT NULL DEFAULT ''", "objetivo": "REAL"
            },
            "tareas": {
                "prioridad": "TEXT NOT NULL DEFAULT 'media'", "peso": "INTEGER NOT NULL DEFAULT 3",
                "fecha_finalizacion": "TEXT", "evidencia": "TEXT NOT NULL DEFAULT ''"
            }
        }
        for tabla, columnas in migraciones.items():
            existentes = {fila[1] for fila in db.execute(f"PRAGMA table_info({tabla})")}
            for columna, definicion in columnas.items():
                if columna not in existentes:
                    db.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")


def listar():
    with conectar() as db:
        return {tabla: [dict(row) for row in db.execute(f"SELECT * FROM {tabla} ORDER BY id DESC")]
                for tabla in ("areas", "metas", "personas", "tareas")}


def crear(tabla, datos):
    campos = {
        "areas": ("nombre", "descripcion"),
        "metas": ("titulo", "descripcion", "responsable_id", "area_id", "fecha_limite", "indicador", "objetivo"),
        "personas": ("nombre", "contacto", "area_id"),
        "tareas": ("titulo", "descripcion", "meta_id", "persona_id", "fecha_limite", "prioridad", "peso", "evidencia")
    }[tabla]
    with conectar() as db:
        cursor = db.execute(f"INSERT INTO {tabla} ({','.join(campos)}) VALUES ({','.join('?' for _ in campos)})",
                            [datos.get(c) for c in campos])
        return dict(db.execute(f"SELECT * FROM {tabla} WHERE id=?", (cursor.lastrowid,)).fetchone())


def mover(tarea_id, estado):
    if estado not in ESTADOS:
        raise ValueError("Estado de tarea inválido.")
    with conectar() as db:
        finalizacion = date.today().isoformat() if estado == "finalizada" else None
        db.execute("UPDATE tareas SET estado=?, fecha_finalizacion=? WHERE id=?",
                   (estado, finalizacion, tarea_id))
        row = db.execute("SELECT * FROM tareas WHERE id=?", (tarea_id,)).fetchone()
        return dict(row) if row else None


def eliminar(tabla, item_id):
    if tabla not in ("areas", "metas", "personas", "tareas"):
        raise ValueError("Sección inválida.")
    with conectar() as db:
        return bool(db.execute(f"DELETE FROM {tabla} WHERE id=?", (item_id,)).rowcount)
