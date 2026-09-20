"""Persistencia local de la planeacion y las tareas de campaña."""
import sqlite3
from datetime import date
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "campania.db"
ESTADOS = {"por_hacer", "en_proceso", "finalizada"}
PRIORIDADES = {"baja", "media", "alta", "critica"}
TERRITORIOS = {"entidad", "municipio", "id_distrito_local", "id_distrito_federal", "seccion"}


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
            "areas": {"demo": "INTEGER NOT NULL DEFAULT 0"},
            "personas": {"area_id": "INTEGER REFERENCES areas(id) ON DELETE SET NULL",
                         "demo": "INTEGER NOT NULL DEFAULT 0"},
            "metas": {
                "responsable_id": "INTEGER REFERENCES personas(id) ON DELETE SET NULL",
                "area_id": "INTEGER REFERENCES areas(id) ON DELETE SET NULL",
                "fecha_limite": "TEXT", "indicador": "TEXT NOT NULL DEFAULT ''", "objetivo": "REAL",
                # Territorio al que se refiere la meta (nivel + valor) y avance acumulado del indicador.
                "territorio_tipo": "TEXT", "territorio_valor": "TEXT",
                "avance": "REAL NOT NULL DEFAULT 0", "demo": "INTEGER NOT NULL DEFAULT 0"
            },
            "tareas": {
                "prioridad": "TEXT NOT NULL DEFAULT 'media'", "peso": "INTEGER NOT NULL DEFAULT 3",
                "fecha_finalizacion": "TEXT", "evidencia": "TEXT NOT NULL DEFAULT ''",
                "demo": "INTEGER NOT NULL DEFAULT 0"
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
        "metas": ("titulo", "descripcion", "responsable_id", "area_id", "fecha_limite", "indicador", "objetivo",
                  "territorio_tipo", "territorio_valor", "avance"),
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


CAMPOS_EDITABLES_META = ("titulo", "descripcion", "responsable_id", "area_id", "fecha_limite", "indicador",
                         "objetivo", "territorio_tipo", "territorio_valor", "avance")


def actualizar_meta(meta_id, datos):
    """Actualiza solo los campos enviados; devuelve la meta o None si no existe."""
    cambios = {k: v for k, v in datos.items() if k in CAMPOS_EDITABLES_META}
    with conectar() as db:
        if cambios:
            db.execute(f"UPDATE metas SET {', '.join(f'{k}=?' for k in cambios)} WHERE id=?",
                       [*cambios.values(), meta_id])
        fila = db.execute("SELECT * FROM metas WHERE id=?", (meta_id,)).fetchone()
        return dict(fila) if fila else None


def hay_demo():
    with conectar() as db:
        return any(db.execute(f"SELECT 1 FROM {t} WHERE demo=1 LIMIT 1").fetchone() for t in
                   ("areas", "personas", "metas", "tareas"))


def quitar_demo():
    """Borra únicamente lo marcado como demostración; los datos reales no se tocan."""
    with conectar() as db:
        total = 0
        for tabla in ("tareas", "metas", "personas", "areas"):
            total += db.execute(f"DELETE FROM {tabla} WHERE demo=1").rowcount
        return total
