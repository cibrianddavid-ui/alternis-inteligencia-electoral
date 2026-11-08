"""Casillas de Google Sheets; caché CSV local opcional para trabajo sin red."""
import csv
import io
import json
import logging
import os
from dotenv import load_dotenv
from urllib.parse import urlencode
from pathlib import Path
from urllib.request import urlopen, Request

load_dotenv()
SHEET_ID = os.getenv("GOOGLE_SHEET_CASILLAS_ID", "1eXXKieq9YybGlk12BIePDY7VVd53zAy6TNIdwH0WaW4")
SHEET_GID = os.getenv("GOOGLE_SHEET_CASILLAS_GID", "171473950")
URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"

def coordenada(valor, latitud=True):
    s = str(valor or "").strip().replace(",", ".")
    try:
        x = float(s)
        if abs(x) <= (90 if latitud else 180):
            return x
    except ValueError:
        pass
    # 22.399.897.021 -> 22.399897021; -101.167.025.867 -> -101.167025867
    partes = s.split(".")
    try:
        x = float(partes[0] + "." + "".join(partes[1:]))
        return x if abs(x) <= (90 if latitud else 180) else None
    except ValueError:
        return None

def cargar_casillas(ruta: Path):
    """CSV local o descarga pública, con diagnóstico explícito y sin puntos ficticios."""
    contenido = None
    origen = ""
    errores = []
    # Primero usar CSV local si existe: no depende de permisos ni de conectividad.
    if ruta.is_file():
        try:
            local = ruta.read_text(encoding="utf-8-sig")
            if "latitud" in local[:3000].lower() and "seccion" in local[:3000].lower():
                contenido, origen = local, "CSV local"
            else:
                errores.append("CSV local sin encabezados requeridos")
        except Exception as exc:
            errores.append(f"Lectura CSV local: {exc}")
    if contenido is None:
        urls = [URL, f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&gid={SHEET_GID}"]
        for url in urls:
            try:
                request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"})
                with urlopen(request, timeout=12) as response:
                    candidato = response.read().decode("utf-8-sig")
                if "<html" in candidato[:500].lower() or "<!doctype" in candidato[:500].lower():
                    raise ValueError("Google devolvió HTML; revisar acceso público")
                encabezado = next(csv.reader(io.StringIO(candidato)), [])
                if not {"seccion", "latitud", "longitud"}.issubset({c.strip().lower() for c in encabezado}):
                    raise ValueError("El Sheet no contiene seccion, latitud y longitud")
                contenido, origen = candidato, "Google Sheets"
                ruta.parent.mkdir(parents=True, exist_ok=True)
                ruta.write_text(contenido, encoding="utf-8-sig")
                break
            except Exception as exc:
                errores.append(str(exc))
    if contenido is None:
        return {"type": "FeatureCollection", "features": [], "registros": [], "total": 0,
                "advertencia": "No se pudieron cargar casillas. Descarga el Sheet como CSV y guárdalo en data/casillas_2024.csv.",
                "diagnostico": "; ".join(errores)[:700]}
    lector = csv.DictReader(io.StringIO(contenido))
    columnas = [str(c or "").strip().lower() for c in (lector.fieldnames or [])]
    if not {"latitud", "longitud"}.issubset(columnas):
        return {"type": "FeatureCollection", "features": [], "total": 0, "advertencia": "El CSV de casillas no tiene columnas latitud y longitud."}
    features = []
    invalidas = 0
    registros = []
    for fila in lector:
        fila = {str(k or "").strip().lower(): v for k,v in fila.items()}
        registros.append(fila)
        lat = coordenada(fila.get("latitud"), True)
        lon = coordenada(fila.get("longitud"), False)
        if lat is None or lon is None or not (20 <= lat <= 25 and -103 <= lon <= -97):
            invalidas += 1
            continue
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": fila})
    result = {"type": "FeatureCollection", "features": features, "total": len(features), "coordenadas_invalidas": invalidas, "origen": origen, "registros": registros, "total_registros": len(registros), "diagnostico": "; ".join(errores)[:400]}
    if not features:
        result["advertencia"] = f"Se leyó {origen}, pero ninguna casilla tiene coordenadas válidas; registros descartados: {invalidas}."
    elif invalidas:
        result["advertencia"] = f"{invalidas} casillas sin coordenadas válidas fueron omitidas."
    return result
