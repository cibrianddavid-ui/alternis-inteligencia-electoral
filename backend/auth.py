"""Acceso a la plataforma: usuarios con roles, contraseñas con scrypt y sesiones por cookie.

Roles (de menor a mayor permiso):
  consulta     ver resultados, perfiles, gráficas y la campaña; usar el asistente.
  coordinador  además: semáforo, discursos y escribir en la campaña.
  admin        además: administrar usuarios y datos de demostración.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import basedatos

ROLES = ("consulta", "coordinador", "admin")
NIVEL = {rol: i for i, rol in enumerate(ROLES)}
COOKIE = "sesion_ie"
DURACION = timedelta(days=7)
CLAVE_MIN = 8
PBKDF2_ITERACIONES = 600_000  # recomendación vigente de OWASP para PBKDF2-HMAC-SHA256
log = logging.getLogger("uvicorn.error")
MAX_INTENTOS_USUARIO = 8
MAX_INTENTOS_IP = 30
VENTANA_INTENTOS = 600  # segundos

router = APIRouter(prefix="/api/auth", tags=["acceso"])
router_usuarios = APIRouter(prefix="/api/usuarios", tags=["usuarios"])


# ---------------------------------------------------------------------------
# Contraseñas y tokens
# ---------------------------------------------------------------------------

def hashear_clave(clave: str) -> str:
    """scrypt cuando el Python lo incluye; si no (p. ej. compilado con LibreSSL), PBKDF2-HMAC-SHA256.

    El esquema usado queda escrito en el propio hash, así que ambos conviven en la misma base.
    """
    sal = os.urandom(16)
    if hasattr(hashlib, "scrypt"):
        try:
            huella = hashlib.scrypt(clave.encode("utf-8"), salt=sal, n=2**14, r=8, p=1, dklen=32)
            return f"scrypt$16384$8$1${sal.hex()}${huella.hex()}"
        except (ValueError, MemoryError, OSError):  # límite de memoria de OpenSSL: se usa PBKDF2
            pass
    huella = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), sal, PBKDF2_ITERACIONES, dklen=32)
    return f"pbkdf2_sha256${PBKDF2_ITERACIONES}${sal.hex()}${huella.hex()}"


def verificar_clave(clave: str, guardado: str) -> bool:
    try:
        partes = guardado.split("$")
        if partes[0] == "scrypt" and len(partes) == 6 and hasattr(hashlib, "scrypt"):
            _, n, r, p, sal, esperado = partes
            esperado_b = bytes.fromhex(esperado)
            huella = hashlib.scrypt(clave.encode("utf-8"), salt=bytes.fromhex(sal), n=int(n),
                                    r=int(r), p=int(p), dklen=len(esperado_b))
        elif partes[0] == "pbkdf2_sha256" and len(partes) == 4:
            _, iteraciones, sal, esperado = partes
            esperado_b = bytes.fromhex(esperado)
            huella = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), bytes.fromhex(sal), int(iteraciones),
                                         dklen=len(esperado_b))
        else:  # esquema desconocido, o un hash scrypt en un Python que no lo incluye
            return False
        return hmac.compare_digest(huella, esperado_b)
    except (ValueError, TypeError, MemoryError, OSError):
        return False


_DUMMY: Optional[str] = None


def _hash_falso() -> str:
    """Se verifica contra este hash cuando el usuario no existe, para no revelarlo por tiempo."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hashear_clave(secrets.token_hex(8))
    return _DUMMY


def _huella_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _iso(momento: datetime) -> str:
    return momento.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------

def _publico(fila) -> dict[str, Any]:
    return {
        "id": fila["id"], "usuario": fila["usuario"], "nombre": fila["nombre"], "rol": fila["rol"],
        "activo": bool(fila["activo"]), "creado": fila["creado"], "ultimo_acceso": fila["ultimo_acceso"],
    }


def hay_usuarios() -> bool:
    with basedatos.transaccion() as db:
        return db.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone() is not None


def _validar(usuario: str, clave: Optional[str], rol: Optional[str]) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._@-]{3,60}", usuario or ""):
        raise ValueError("El usuario debe tener de 3 a 60 caracteres: letras, números, punto, guion, guion bajo o arroba.")
    if clave is not None:
        if len(clave) < CLAVE_MIN:
            raise ValueError(f"La contraseña debe tener al menos {CLAVE_MIN} caracteres.")
        if clave.lower() == usuario.lower():
            raise ValueError("La contraseña no puede ser igual al usuario.")
    if rol is not None and rol not in ROLES:
        raise ValueError("Rol no válido.")


def crear_usuario(usuario: str, nombre: str, clave: str, rol: str) -> dict[str, Any]:
    usuario = usuario.strip()
    _validar(usuario, clave, rol)
    try:
        with basedatos.transaccion() as db:
            cursor = db.execute(
                "INSERT INTO usuarios (usuario, nombre, clave_hash, rol, creado) VALUES (?,?,?,?,?)",
                (usuario, nombre.strip(), hashear_clave(clave), rol, basedatos.ahora()))
            return _publico(db.execute("SELECT * FROM usuarios WHERE id=?", (cursor.lastrowid,)).fetchone())
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise ValueError("Ya existe un usuario con ese nombre.") from exc
        raise


def listar_usuarios() -> list[dict[str, Any]]:
    with basedatos.transaccion() as db:
        return [_publico(f) for f in db.execute("SELECT * FROM usuarios ORDER BY activo DESC, usuario")]


def _admins_activos(db, excluyendo: Optional[int] = None) -> int:
    fila = db.execute("SELECT COUNT(*) FROM usuarios WHERE rol='admin' AND activo=1 AND id IS NOT ?",
                      (excluyendo,)).fetchone()
    return fila[0]


def actualizar_usuario(usuario_id: int, cambios: dict[str, Any]) -> dict[str, Any]:
    with basedatos.transaccion() as db:
        actual = db.execute("SELECT * FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
        if actual is None:
            raise LookupError("Usuario no encontrado.")
        nuevo_rol = cambios.get("rol", actual["rol"])
        nuevo_activo = int(cambios["activo"]) if cambios.get("activo") is not None else actual["activo"]
        _validar(actual["usuario"], cambios.get("clave"), nuevo_rol)
        # Siempre debe quedar al menos un administrador activo.
        pierde_admin = actual["rol"] == "admin" and actual["activo"] and (nuevo_rol != "admin" or not nuevo_activo)
        if pierde_admin and _admins_activos(db, excluyendo=usuario_id) == 0:
            raise ValueError("Debe quedar al menos un administrador activo.")
        sets, valores = ["rol=?", "activo=?", "nombre=?"], [nuevo_rol, nuevo_activo,
                                                             cambios.get("nombre", actual["nombre"]).strip()]
        if cambios.get("clave"):
            sets.append("clave_hash=?")
            valores.append(hashear_clave(cambios["clave"]))
        db.execute(f"UPDATE usuarios SET {', '.join(sets)} WHERE id=?", (*valores, usuario_id))
        if cambios.get("clave") or not nuevo_activo or nuevo_rol != actual["rol"]:
            db.execute("DELETE FROM sesiones_auth WHERE usuario_id=?", (usuario_id,))
        return _publico(db.execute("SELECT * FROM usuarios WHERE id=?", (usuario_id,)).fetchone())


def cambiar_clave_propia(usuario_id: int, actual: str, nueva: str) -> None:
    with basedatos.transaccion() as db:
        fila = db.execute("SELECT * FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
        if fila is None or not verificar_clave(actual, fila["clave_hash"]):
            raise ValueError("La contraseña actual no es correcta.")
        _validar(fila["usuario"], nueva, None)
        db.execute("UPDATE usuarios SET clave_hash=? WHERE id=?", (hashear_clave(nueva), usuario_id))
        db.execute("DELETE FROM sesiones_auth WHERE usuario_id=?", (usuario_id,))


def crear_admin_inicial_desde_entorno() -> None:
    """Para despliegues en servidor: ADMIN_USUARIO y ADMIN_CLAVE crean el primer administrador.

    Cualquier problema se avisa en la consola pero no impide que la aplicación arranque:
    el administrador puede crearse después desde la pantalla de acceso.
    """
    usuario, clave = os.getenv("ADMIN_USUARIO", "").strip(), os.getenv("ADMIN_CLAVE", "")
    if not (usuario and clave):
        return
    try:
        if hay_usuarios():
            return
        crear_usuario(usuario, "Administrador", clave, "admin")
        print(f"[acceso] Administrador «{usuario}» creado desde ADMIN_USUARIO y ADMIN_CLAVE.")
    except Exception as exc:  # noqa: BLE001 - el arranque no debe caerse por esto
        print(f"[acceso] AVISO: no se pudo crear el administrador desde .env ({type(exc).__name__}: {exc}). "
              "La aplicación arrancó igual; créalo desde la pantalla de acceso.")


# ---------------------------------------------------------------------------
# Sesiones
# ---------------------------------------------------------------------------

def abrir_sesion(usuario_id: int) -> str:
    token = secrets.token_urlsafe(32)
    ahora = datetime.now(timezone.utc)
    with basedatos.transaccion() as db:
        db.execute("DELETE FROM sesiones_auth WHERE expira < ?", (_iso(ahora),))
        db.execute("INSERT INTO sesiones_auth (token_hash, usuario_id, creado, expira) VALUES (?,?,?,?)",
                   (_huella_token(token), usuario_id, _iso(ahora), _iso(ahora + DURACION)))
        db.execute("UPDATE usuarios SET ultimo_acceso=? WHERE id=?", (_iso(ahora), usuario_id))
    return token


def usuario_por_token(token: Optional[str]) -> Optional[dict[str, Any]]:
    if not token:
        return None
    with basedatos.transaccion() as db:
        fila = db.execute(
            "SELECT u.* FROM sesiones_auth s JOIN usuarios u ON u.id = s.usuario_id "
            "WHERE s.token_hash=? AND s.expira > ? AND u.activo=1",
            (_huella_token(token), _iso(datetime.now(timezone.utc)))).fetchone()
        return _publico(fila) if fila else None


def cerrar_sesion(token: Optional[str]) -> None:
    if token:
        with basedatos.transaccion() as db:
            db.execute("DELETE FROM sesiones_auth WHERE token_hash=?", (_huella_token(token),))


# ---------------------------------------------------------------------------
# Límite de intentos y verificación de origen
# ---------------------------------------------------------------------------

_intentos: dict[str, list[float]] = {}
_candado = threading.Lock()


def _vigentes(clave: str) -> list[float]:
    corte = time.time() - VENTANA_INTENTOS
    lista = [t for t in _intentos.get(clave, []) if t > corte]
    _intentos[clave] = lista
    return lista


def _limitar(clave: str, maximo: int) -> None:
    with _candado:
        if len(_vigentes(clave)) >= maximo:
            raise HTTPException(status_code=429, detail="Demasiados intentos. Espera unos minutos e inténtalo de nuevo.")


def _registrar_fallo(*claves: str) -> None:
    with _candado:
        for clave in claves:
            _vigentes(clave).append(time.time())


def _limpiar_fallos(*claves: str) -> None:
    with _candado:
        for clave in claves:
            _intentos.pop(clave, None)


def reiniciar_limites() -> None:
    with _candado:
        _intentos.clear()


def origen_permitido(request: Request) -> bool:
    """Bloquea peticiones que modifican datos si provienen de otro sitio web."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    origen = request.headers.get("origin")
    if not origen:
        return True
    return urlparse(origen).netloc == request.headers.get("host", "")


# ---------------------------------------------------------------------------
# Dependencias para proteger endpoints
# ---------------------------------------------------------------------------

def usuario_opcional(request: Request) -> Optional[dict[str, Any]]:
    return usuario_por_token(request.cookies.get(COOKIE))


def usuario_actual(request: Request) -> dict[str, Any]:
    usuario = usuario_opcional(request)
    if usuario is None:
        raise HTTPException(status_code=401, detail="Inicia sesión para continuar.")
    return usuario


def requiere(rol_minimo: str) -> Callable[..., dict[str, Any]]:
    def dependencia(usuario: dict[str, Any] = Depends(usuario_actual)) -> dict[str, Any]:
        if NIVEL[usuario["rol"]] < NIVEL[rol_minimo]:
            raise HTTPException(status_code=403, detail="Tu rol no tiene permiso para esta acción.")
        return usuario
    return dependencia


def _poner_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=int(DURACION.total_seconds()), httponly=True,
                        samesite="lax", secure=os.getenv("COOKIE_SECURE", "0") == "1", path="/")


# ---------------------------------------------------------------------------
# Endpoints de acceso
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    usuario: str = Field(min_length=1, max_length=60)
    clave: str = Field(min_length=1, max_length=200)


class SetupRequest(BaseModel):
    usuario: str = Field(max_length=60)
    nombre: str = Field(default="", max_length=120)
    clave: str = Field(max_length=200)


class ClaveRequest(BaseModel):
    actual: str = Field(max_length=200)
    nueva: str = Field(max_length=200)


class UsuarioNuevo(BaseModel):
    usuario: str = Field(max_length=60)
    nombre: str = Field(default="", max_length=120)
    clave: str = Field(max_length=200)
    rol: str = "coordinador"


class UsuarioCambios(BaseModel):
    nombre: Optional[str] = Field(default=None, max_length=120)
    rol: Optional[str] = None
    activo: Optional[bool] = None
    clave: Optional[str] = Field(default=None, max_length=200)


@router.get("/estado")
def estado(request: Request) -> dict[str, Any]:
    return {"hay_usuarios": hay_usuarios(), "usuario": usuario_opcional(request)}


@router.post("/setup")
def configurar_primer_admin(datos: SetupRequest, response: Response) -> dict[str, Any]:
    if hay_usuarios():
        raise HTTPException(status_code=409, detail="La plataforma ya tiene usuarios configurados.")
    try:
        usuario = crear_usuario(datos.usuario, datos.nombre or "Administrador", datos.clave, "admin")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - se explica en pantalla y se registra completo en la consola
        log.exception("No se pudo crear el primer administrador")
        raise HTTPException(status_code=500, detail=(
            f"No se pudo crear el usuario ({type(exc).__name__}: {exc}). "
            "Ejecuta «python verificar_instalacion.py» o revisa la consola donde corre la aplicación.")) from exc
    _poner_cookie(response, abrir_sesion(usuario["id"]))
    return {"usuario": usuario}


@router.post("/login")
def iniciar_sesion(datos: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    ip = f"ip:{request.client.host if request.client else '?'}"
    por_usuario = f"u:{datos.usuario.strip().lower()}"
    _limitar(ip, MAX_INTENTOS_IP)
    _limitar(por_usuario, MAX_INTENTOS_USUARIO)
    with basedatos.transaccion() as db:
        fila = db.execute("SELECT * FROM usuarios WHERE usuario=?", (datos.usuario.strip(),)).fetchone()
    correcto = verificar_clave(datos.clave, fila["clave_hash"] if fila else _hash_falso())
    if fila is None or not correcto or not fila["activo"]:
        _registrar_fallo(ip, por_usuario)
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")
    _limpiar_fallos(por_usuario)
    _poner_cookie(response, abrir_sesion(fila["id"]))
    return {"usuario": _publico(fila)}


@router.post("/logout")
def cerrar(request: Request, response: Response) -> dict[str, bool]:
    cerrar_sesion(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/clave")
def cambiar_clave(datos: ClaveRequest, response: Response,
                  usuario: dict[str, Any] = Depends(usuario_actual)) -> dict[str, bool]:
    try:
        cambiar_clave_propia(usuario["id"], datos.actual, datos.nueva)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _poner_cookie(response, abrir_sesion(usuario["id"]))  # las demás sesiones quedaron cerradas
    return {"ok": True}


# ---------------------------------------------------------------------------
# Administración de usuarios
# ---------------------------------------------------------------------------

@router_usuarios.get("")
def usuarios(_: dict[str, Any] = Depends(requiere("admin"))) -> list[dict[str, Any]]:
    return listar_usuarios()


@router_usuarios.post("")
def alta_usuario(datos: UsuarioNuevo, _: dict[str, Any] = Depends(requiere("admin"))) -> dict[str, Any]:
    try:
        return crear_usuario(datos.usuario, datos.nombre, datos.clave, datos.rol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router_usuarios.patch("/{usuario_id}")
def modificar_usuario(usuario_id: int, datos: UsuarioCambios,
                      _: dict[str, Any] = Depends(requiere("admin"))) -> dict[str, Any]:
    try:
        return actualizar_usuario(usuario_id, datos.model_dump(exclude_none=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
