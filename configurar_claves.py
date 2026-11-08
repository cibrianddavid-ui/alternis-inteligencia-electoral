"""Configura Groq localmente sin mostrar ni enviar la clave."""
import os
from getpass import getpass

from dotenv import dotenv_values, set_key

from backend.configuracion import ROOT, cargar_entorno


def main():
    print(f"Carpeta de esta instalación: {ROOT}")
    ruta = ROOT / ".env"
    print(f"Archivo de configuración: {ruta}")
    cargar_entorno()
    clave = os.getenv("GROQ_API_KEY", "").strip()
    if not clave:
        clave = getpass("Pega tu clave de Groq (no se mostrará en pantalla): ").strip()
    if not clave:
        print("No se guardó ninguna clave.")
        return 1
    # set_key conserva las demás variables. No imprime el contenido del archivo.
    set_key(str(ruta), "GROQ_API_KEY", clave)
    if os.name != "nt":
        ruta.chmod(0o600)
    if dotenv_values(ruta).get("GROQ_API_KEY") != clave:
        print("No fue posible verificar la escritura del archivo.")
        return 1
    print("Clave guardada y lectura local comprobada. Detén el servidor con Ctrl+C y ejecuta python run.py.")
    print("La validez y cuota de la clave se comprobarán cuando uses el asistente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
