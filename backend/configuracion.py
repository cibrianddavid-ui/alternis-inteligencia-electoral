"""Carga la configuración local desde la raíz del proyecto."""
import logging
import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def cargar_entorno(raiz: Path = ROOT) -> bool:
    """Respeta variables no vacías y recupera claves de instalaciones anteriores."""
    cargado = load_dotenv(raiz / ".env", override=False)
    # dotenv no sustituye una variable exportada vacía. Para Groq, vacío significa ausente.
    if not os.getenv("GROQ_API_KEY", "").strip():
        valores = dotenv_values(raiz / ".env")
        clave = (valores.get("GROQ_API_KEY") or "").strip()
        if not clave:
            # Compatibilidad con la versión recibida: solo recuperar una clave con formato Groq,
            # nunca un marcador como 'tu_clave'. Esto no verifica su vigencia ante el proveedor.
            ejemplo = dotenv_values(raiz / ".env.example")
            candidata = (ejemplo.get("GROQ_API_KEY") or "").strip()
            if candidata.startswith("gsk_") and len(candidata) >= 20:
                clave = candidata
                logging.getLogger(__name__).warning(
                    "Configuración antigua: se recuperó GROQ_API_KEY de .env.example. "
                    "Ejecuta python configurar_claves.py para guardarla en .env.")
        if clave:
            os.environ["GROQ_API_KEY"] = clave
            cargado = True
    return cargado
