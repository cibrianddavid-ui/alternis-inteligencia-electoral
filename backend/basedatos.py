"""Base de datos de la plataforma: usuarios, sesiones e historial de discursos.

La planeación de campaña sigue en campania.db; esta base guarda lo que pertenece
a las personas que usan el sistema.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DATA = Path(__file__).resolve().parents[1] / "data"
RUTA = DATA / "plataforma.db"  # las pruebas pueden apuntar a otra ruta

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY,
    usuario TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nombre TEXT NOT NULL DEFAULT '',
    clave_hash TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('admin', 'coordinador', 'consulta')),
    activo INTEGER NOT NULL DEFAULT 1,
    creado TEXT NOT NULL,
    ultimo_acceso TEXT
);
CREATE TABLE IF NOT EXISTS sesiones_auth (
    token_hash TEXT PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    creado TEXT NOT NULL,
    expira TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sesiones_usuario ON sesiones_auth (usuario_id);
CREATE TABLE IF NOT EXISTS discursos (
    id INTEGER PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    titulo TEXT NOT NULL DEFAULT 'Nuevo discurso',
    formato TEXT NOT NULL DEFAULT 'libre',
    territorio TEXT,
    creado TEXT NOT NULL,
    actualizado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discursos_usuario ON discursos (usuario_id, actualizado);
CREATE TABLE IF NOT EXISTS discurso_mensajes (
    id INTEGER PRIMARY KEY,
    discurso_id INTEGER NOT NULL REFERENCES discursos(id) ON DELETE CASCADE,
    rol TEXT NOT NULL CHECK (rol IN ('user', 'assistant')),
    contenido TEXT NOT NULL,
    verificacion TEXT,
    creado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mensajes_discurso ON discurso_mensajes (discurso_id, id);
"""


def ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def conectar() -> sqlite3.Connection:
    RUTA.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(RUTA, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaccion() -> Iterator[sqlite3.Connection]:
    """Confirma al salir sin error, revierte si hay excepción y siempre cierra."""
    conn = conectar()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def iniciar() -> None:
    with transaccion() as db:
        db.executescript(ESQUEMA)
