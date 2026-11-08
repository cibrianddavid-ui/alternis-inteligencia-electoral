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

-- Social Listening: persistencia incremental de posts, comentarios y caché
CREATE TABLE IF NOT EXISTS social_posts (
    post_url TEXT PRIMARY KEY,
    url_perfil TEXT NOT NULL,
    comentarios_scrapeados INTEGER NOT NULL DEFAULT 0,
    creado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_posts_perfil ON social_posts (url_perfil);

CREATE TABLE IF NOT EXISTS social_comentarios (
    id TEXT PRIMARY KEY,
    url_perfil TEXT NOT NULL,
    post_url TEXT NOT NULL,
    texto TEXT NOT NULL,
    autor_nombre TEXT,
    autor_url TEXT,
    comentario_url TEXT,
    likes INTEGER NOT NULL DEFAULT 0,
    tono TEXT,
    creado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_comentarios_perfil ON social_comentarios (url_perfil);
CREATE INDEX IF NOT EXISTS idx_social_comentarios_post ON social_comentarios (post_url);

CREATE TABLE IF NOT EXISTS social_cache (
    url_perfil TEXT PRIMARY KEY,
    datos_json TEXT NOT NULL,
    actualizado TEXT NOT NULL
);
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

    # Migraciones incrementales: una por una en try/except porque SQLite no
    # soporta ALTER TABLE … IF NOT EXISTS.
    _migraciones = [
        # social_comentarios — trazabilidad de análisis LLM
        "ALTER TABLE social_comentarios ADD COLUMN analizado INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE social_comentarios ADD COLUMN analisis_version TEXT",
        "ALTER TABLE social_comentarios ADD COLUMN analizado_en TEXT",
        # social_posts — metadatos extendidos del post
        "ALTER TABLE social_posts ADD COLUMN post_id TEXT",
        "ALTER TABLE social_posts ADD COLUMN texto_post TEXT",
        "ALTER TABLE social_posts ADD COLUMN fecha_post TEXT",
        "ALTER TABLE social_posts ADD COLUMN post_likes INTEGER DEFAULT 0",
        "ALTER TABLE social_posts ADD COLUMN actualizado TEXT",
        "ALTER TABLE social_posts ADD COLUMN comentarios_actualizados_en TEXT",
        "ALTER TABLE social_posts ADD COLUMN max_comentarios_scrapeados INTEGER DEFAULT 0",
        "ALTER TABLE social_posts ADD COLUMN perfil_nombre TEXT",
        "ALTER TABLE social_posts ADD COLUMN canonical_url TEXT",
        "CREATE INDEX IF NOT EXISTS idx_social_posts_post_id ON social_posts(post_id)",
        "CREATE INDEX IF NOT EXISTS idx_social_posts_canonical ON social_posts(canonical_url)",
        "CREATE INDEX IF NOT EXISTS idx_social_comentarios_post_url ON social_comentarios(post_url)",
        # Normalización de post_url sin slash final para consistencia de consultas
        "UPDATE social_posts SET post_url = rtrim(post_url, '/') WHERE post_url LIKE '%/'",
        "UPDATE social_comentarios SET post_url = rtrim(post_url, '/') WHERE post_url LIKE '%/'",
        "UPDATE social_posts SET canonical_url = post_url WHERE canonical_url IS NULL OR canonical_url = ''",
    ]
    conn = conectar()
    try:
        for sql in _migraciones:
            try:
                conn.execute(sql)
            except Exception:
                pass  # columna ya existe o error no crítico — se ignora
        conn.commit()
    finally:
        conn.close()
