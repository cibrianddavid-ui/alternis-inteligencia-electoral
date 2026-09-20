"""Comprueba que la plataforma esté completa: archivos presentes, nombres correctos e imports sin errores.

Uso (desde la carpeta del proyecto):   python verificar_instalacion.py
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent

BACKEND = [
    "__init__", "main", "auth", "basedatos", "asistente_electoral", "resultados_graficos", "posicionamiento",
    "informes", "territorio", "redaccion", "historial", "demo", "campania", "discursos",
]
FRONTEND = ["index.html", "assets/styles.css", "assets/favicon.svg", "assets/app.js", "assets/auth.js",
            "assets/territorio.js", "assets/discursos.js", "assets/admin.js", "assets/tour.js"]
RAIZ_ARCHIVOS = ["run.py", "requirements.txt"]

problemas: list[str] = []

for nombre in BACKEND:
    if not (RAIZ / "backend" / f"{nombre}.py").is_file():
        problemas.append(f"Falta backend/{nombre}.py")
for ruta in FRONTEND:
    if not (RAIZ / "frontend" / ruta).is_file():
        problemas.append(f"Falta frontend/{ruta}")
for ruta in RAIZ_ARCHIVOS:
    if not (RAIZ / ruta).is_file():
        problemas.append(f"Falta {ruta}")

# Errores frecuentes de nombre
for mal, bien in (("redacción.py", "redaccion.py"), ("main_nuevo.py", "main.py")):
    if (RAIZ / "backend" / mal).exists():
        problemas.append(f"backend/{mal} debe llamarse backend/{bien} (sin acento ni sufijos)")

# Que los módulos importen sin error
if not any(p.startswith("Falta backend/") for p in problemas):
    sys.path.insert(0, str(RAIZ))
    for nombre in BACKEND[1:]:
        try:
            importlib.import_module(f"backend.{nombre}")
        except Exception as exc:  # noqa: BLE001 - se informa cualquier fallo de importación
            problemas.append(f"backend/{nombre}.py no se puede importar: {type(exc).__name__}: {exc}")

for modulo, paquete in (("docx", "python-docx"), ("reportlab", "reportlab"), ("fastapi", "fastapi"),
                        ("pandas", "pandas"), ("groq", "groq"), ("trafilatura", "trafilatura"), ("spacy", "spacy")):
    try:
        importlib.import_module(modulo)
    except ImportError:
        problemas.append(f"Falta instalar el paquete «{paquete}»: pip install -r requirements.txt")

# Pruebas de funcionamiento: escritura en disco y creación real de un usuario (en una base temporal)
if not problemas:
    import hashlib
    import tempfile

    print(f"Python {sys.version.split()[0]} · OpenSSL: {getattr(__import__('ssl'), 'OPENSSL_VERSION', '?')}"
          f" · scrypt {'disponible' if hasattr(hashlib, 'scrypt') else 'NO disponible (se usará PBKDF2, es normal)'}")
    try:
        datos = RAIZ / "data"
        datos.mkdir(exist_ok=True)
        prueba = datos / ".prueba_escritura"
        prueba.write_text("ok")
        prueba.unlink()
    except OSError as exc:
        problemas.append(f"No se puede escribir en la carpeta data/: {exc}")
    try:
        from backend import auth, basedatos

        with tempfile.TemporaryDirectory() as carpeta:
            basedatos.DATA, basedatos.RUTA = Path(carpeta), Path(carpeta) / "prueba.db"
            basedatos.iniciar()
            auth.crear_usuario("prueba.admin", "Prueba", "clave-de-prueba-1", "admin")
            fila = auth.listar_usuarios()[0]
            if not (fila["rol"] == "admin" and auth.usuario_por_token(auth.abrir_sesion(fila["id"]))):
                problemas.append("La creación de usuarios y sesiones no devolvió lo esperado.")
    except Exception as exc:  # noqa: BLE001 - se informa cualquier fallo
        problemas.append(f"No se pudo crear un usuario de prueba: {type(exc).__name__}: {exc}")

if problemas:
    print("Se encontraron problemas:\n")
    for p in problemas:
        print("  ✗", p)
    print("\nCorrígelos y vuelve a ejecutar este script.")
    sys.exit(1)
print("✓ Instalación completa: archivos, importaciones, escritura en data/ y creación de usuarios funcionan.")
print("  Inicia la plataforma con:  python run.py")
