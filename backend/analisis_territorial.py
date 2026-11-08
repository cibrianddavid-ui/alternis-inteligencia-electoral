"""Análisis de un territorio: voto diferenciado entre elecciones y evolución entre años.

Todo número sale de pandas. El modelo de lenguaje solo redacta a partir de una ficha de cifras, y después
cada cifra del texto se revisa contra esa ficha (la misma verificación que usan los discursos).
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from . import redaccion
from .partidos import etiqueta, color
from .territorio import AUXILIARES, NIVELES, NO_OPCION, Territorio, _orden_natural, etiqueta_eleccion

REFERENCIA = "PRESIDENCIA"   # Elección con la que se comparan las demás (la de mayor participación y menos ruido local).
MIN_VOTOS_SECCION = 50       # Una sección con muy pocos votos no sirve para decir quién ganó.
MAX_PARTIDOS_FICHA = 5
MAX_CACHE = 200


def _resumen(df: pd.DataFrame) -> dict[str, Any]:
    """Votos y porcentajes por partido u opción registrada de una elección, con las mismas convenciones del Perfil territorial."""
    agg = df.groupby("norm", observed=True)["votos"].sum()
    suma = lambda n: float(agg.get(n, 0.0))  # noqa: E731
    lista, total = suma("LISTA_NOMINAL"), suma("TOTAL_VOTOS_CALCULADOS")
    nulos, no_reg = suma("NULOS"), suma("NO_REGISTRADAS")
    emitidos = float(agg[~agg.index.isin(AUXILIARES)].sum())
    base = total if total > 0 else emitidos
    opciones = agg[~agg.index.isin(NO_OPCION) & (agg > 0)]
    por_partido = opciones
    votos = {k: int(round(v)) for k, v in por_partido.items()}
    pct = {k: round(v / base * 100, 2) for k, v in por_partido.items()} if base else {}
    orden = sorted(votos, key=lambda k: -votos[k])
    margen = round(pct[orden[0]] - pct[orden[1]], 2) if len(orden) > 1 and pct else None
    participacion = round(emitidos / lista * 100, 2) if lista > 0 else None
    return {
        "votos": votos, "pct": pct, "ganador": orden[0] if orden else None, "margen_pp": margen,
        "participacion_pct": participacion, "nulos_pct": round(nulos / base * 100, 2) if base else None,
        "votos_emitidos": int(round(emitidos)), "lista_nominal": int(round(lista)),
        "cuadra": total <= 0 or abs(emitidos - total) <= max(1.0, 0.005 * total),
        "diferencia_total": int(round(total - emitidos)) if total > 0 else 0,
    }


def _ganadores_seccion(df: pd.DataFrame) -> pd.DataFrame:
    op = df[~df["norm"].isin(NO_OPCION)]
    if op.empty:
        return pd.DataFrame(columns=["partido", "total"])
    g = (op.assign(partido=op["norm"], seccion=op["seccion"].astype(str))
         .groupby(["seccion", "partido"], as_index=False)["votos"].sum())
    total = g.groupby("seccion")["votos"].sum()
    g = g.sort_values(["seccion", "votos", "partido"], ascending=[True, False, True]).drop_duplicates("seccion").set_index("seccion")
    g["total"] = total
    return g[g["total"] >= MIN_VOTOS_SECCION][["partido", "total"]]


def comparativo(t: Territorio, nivel: str, unidad: str, anio: Optional[str] = None) -> dict[str, Any]:
    if nivel not in NIVELES or nivel not in t.datos:
        raise ValueError("El nivel territorial no existe en la base.")
    unidad = str(unidad).strip()
    sub = t.datos[t.datos[nivel] == unidad]
    if sub.empty:
        raise ValueError(f"No hay resultados para {NIVELES[nivel].lower()} «{unidad}».")
    anios: list[str] = []
    if t.tiene_anio:
        anios = sorted(sub["anio"].dropna().unique().tolist(), key=_orden_natural)
        anio = str(anio).strip() if anio else (anios[-1] if anios else None)
        if anio not in anios:
            raise ValueError(f"No hay resultados de {anio} para «{unidad}».")
        actual = sub[sub["anio"] == anio]
    else:
        anio, actual = None, sub

    tipos = sorted(actual["eleccion"].dropna().unique().tolist(), key=lambda e: (e != REFERENCIA, e))
    if not tipos:
        raise ValueError("El territorio no tiene elecciones para comparar.")
    ref = tipos[0]
    res = {tipo: _resumen(actual[actual["eleccion"] == tipo]) for tipo in tipos}

    total_por_partido: dict[str, int] = {}
    for r in res.values():
        for k, v in r["votos"].items():
            total_por_partido[k] = total_por_partido.get(k, 0) + v
    orden = sorted(total_por_partido, key=lambda k: -total_por_partido[k])

    elecciones = [{"tipo": tipo, "etiqueta": etiqueta_eleccion(tipo), **res[tipo]} for tipo in tipos]

    diferencias = []
    for tipo in tipos[1:]:
        d = {k: round(res[tipo]["pct"].get(k, 0.0) - res[ref]["pct"].get(k, 0.0), 2) for k in orden}
        part = None
        p1, p0 = res[tipo]["participacion_pct"], res[ref]["participacion_pct"]
        if p1 is not None and p0 is not None and p1 <= 100 and p0 <= 100:
            part = round(res[tipo]["participacion_pct"] - res[ref]["participacion_pct"], 2)
        diferencias.append({"tipo": tipo, "etiqueta": etiqueta_eleccion(tipo), "por_partido": d,
                            "indice": round(0.5 * sum(abs(v) for v in d.values()), 2), "participacion_pp": part})

    secciones = None
    if nivel != "seccion" and "seccion" in actual:
        ganadores = {tipo: _ganadores_seccion(actual[actual["eleccion"] == tipo]) for tipo in tipos}
        tabla = pd.concat({tipo: g["partido"] for tipo, g in ganadores.items()}, axis=1).dropna()
        cambios = []
        for tipo in tipos[1:]:
            j = ganadores[ref].join(ganadores[tipo][["partido"]], lsuffix="_ref", rsuffix="_otra", how="inner")
            dist = j[j["partido_ref"] != j["partido_otra"]]
            flujos = (dist.groupby(["partido_ref", "partido_otra"]).size().sort_values(ascending=False).head(3))
            cambios.append({
                "tipo": tipo, "etiqueta": etiqueta_eleccion(tipo), "evaluadas": int(len(j)), "distintas": int(len(dist)),
                "pct_distintas": round(len(dist) / len(j) * 100, 2) if len(j) else None,
                "flujos": [{"en_referencia": a, "en_otra": b, "secciones": int(n)} for (a, b), n in flujos.items()],
                "ejemplos": [{"seccion": s, "en_referencia": r.partido_ref, "en_otra": r.partido_otra, "votos": int(r.total)}
                             for s, r in dist.sort_values("total", ascending=False).head(5).iterrows()],
            })
        secciones = {"evaluadas": int(len(tabla)), "iguales_en_todas": int((tabla.nunique(axis=1) == 1).sum()),
                     "minimo_votos": MIN_VOTOS_SECCION, "cambios": cambios}

    historico = None
    if len(anios) > 1:
        historico = {}
        for tipo in sorted(sub["eleccion"].dropna().unique()):
            filas = []
            for a in anios:
                bloque = sub[(sub["anio"] == a) & (sub["eleccion"] == tipo)]
                if not bloque.empty:
                    r = _resumen(bloque)
                    filas.append({"anio": a, "ganador": r["ganador"], "pct": r["pct"], "margen_pp": r["margen_pp"],
                                  "participacion_pct": r["participacion_pct"]})
            if len(filas) > 1:
                historico[tipo] = {"etiqueta": etiqueta_eleccion(tipo), "anios": filas}
        historico = historico or None

    avisos = []
    if any("_" in k or "-" in k for k in orden):
        avisos.append("La base contiene coaliciones como opciones independientes. Sus votos no se atribuyen a partidos individuales.")
    avisos.append("Las diferencias entre elecciones describen resultados agregados; no prueban que las mismas personas hayan cambiado su voto.")
    for e in elecciones:
        if not e["cuadra"]:
            avisos.append(f"{e['etiqueta']}: la suma de las opciones difiere del total calculado en "
                          f"{abs(e['diferencia_total']):,} votos; los porcentajes usan el total calculado.")
        if e["participacion_pct"] is not None and e["participacion_pct"] > 100:
            avisos.append(f"{e['etiqueta']}: la participación supera 100 %, probablemente por casillas especiales.")

    return {
        "nivel": nivel, "nivel_etiqueta": NIVELES[nivel], "unidad": unidad, "anio": anio, "anios_disponibles": anios,
        "referencia": ref, "referencia_etiqueta": etiqueta_eleccion(ref),
        "partidos": [{"clave": k, "etiqueta": etiqueta(k), "color": color(k)} for k in orden],
        "elecciones": elecciones, "diferencias": diferencias, "secciones": secciones,
        "historico": historico, "avisos": avisos,
    }


# --------------------------------------------------------------------------- ficha para el modelo
def _lado(valor: float) -> str:
    return "más" if valor > 0 else "menos"


def construir_ficha(c: dict[str, Any]) -> dict[str, Any]:
    permitidos: list[dict[str, Any]] = []

    def agregar(valor: Optional[float], tipo: str) -> None:
        if valor is not None:
            permitidos.append({"valor": abs(float(valor)), "tipo": tipo})

    def etq(clave: str) -> str:
        return etiqueta(clave)

    descripcion = f"{c['nivel_etiqueta']} {c['unidad']}" + (f" · {c['anio']}" if c["anio"] else "")
    if c["anio"] and str(c["anio"]).isdigit():
        agregar(float(c["anio"]), "id")
    lineas = [f"Territorio: {descripcion}."]
    lineas.append("Resultados por partido registrado. Las coaliciones permanecen como opciones independientes; "
                  "no se agrupan en bloques ni se reparten sus votos entre integrantes.")

    for e in c["elecciones"]:
        top = sorted(e["votos"], key=lambda k: -e["votos"][k])[:MAX_PARTIDOS_FICHA]
        partes = []
        for k in top:
            partes.append(f"{etq(k)} {e['pct'][k]:.1f} % ({e['votos'][k]:,} votos)")
            agregar(e["pct"][k], "porcentaje"); agregar(e["votos"][k], "votos")
        linea = f"{e['etiqueta']}: " + ", ".join(partes) + "."
        if e["ganador"] and e["margen_pp"] is not None:
            linea += f" Ganó {etq(e['ganador'])} con {e['margen_pp']:.1f} puntos de ventaja."
            agregar(e["margen_pp"], "porcentaje")
        if e["participacion_pct"] is not None:
            linea += f" Participación {e['participacion_pct']:.1f} %."
            agregar(e["participacion_pct"], "porcentaje")
        agregar(e["votos_emitidos"], "votos"); agregar(e["lista_nominal"], "votos")
        lineas.append(linea)

    for d in c["diferencias"]:
        mayores = sorted(d["por_partido"].items(), key=lambda kv: -abs(kv[1]))[:3]
        partes = []
        for k, v in mayores:
            if abs(v) < 0.1:
                continue
            partes.append(f"{etq(k)} {_lado(v)} {abs(v):.1f}")
            agregar(v, "porcentaje")
        linea = (f"{d['etiqueta']} frente a {c['referencia_etiqueta']} (puntos porcentuales): "
                 + (", ".join(partes) if partes else "sin diferencias relevantes") + ".")
        linea += (f" Índice de diferenciación: {d['indice']:.1f} puntos (la mitad de la suma de diferencias entre opciones registradas; "
                  "0 sería idéntico).")
        agregar(d["indice"], "porcentaje")
        if d["participacion_pp"] is not None and abs(d["participacion_pp"]) >= 0.1:
            linea += f" Participación {_lado(d['participacion_pp'])} {abs(d['participacion_pp']):.1f} puntos."
            agregar(d["participacion_pp"], "porcentaje")
        lineas.append(linea)

    s = c["secciones"]
    if s:
        agregar(s["evaluadas"], "cantidad"); agregar(s["iguales_en_todas"], "cantidad")
        lineas.append(f"Secciones evaluadas (con al menos {s['minimo_votos']} votos): {s['evaluadas']}. En "
                      f"{s['iguales_en_todas']} ganó el misma opción registrada en todas las elecciones.")
        for cam in s["cambios"]:
            agregar(cam["distintas"], "cantidad"); agregar(cam["evaluadas"], "cantidad")
            linea = f"{cam['etiqueta']} frente a {c['referencia_etiqueta']}: ganó otra opción registrada en {cam['distintas']} secciones"
            if cam["pct_distintas"] is not None:
                linea += f" ({cam['pct_distintas']:.1f} %)"
                agregar(cam["pct_distintas"], "porcentaje")
            linea += "."
            if cam["flujos"]:
                partes = []
                for f in cam["flujos"]:
                    partes.append(f"en {f['secciones']} secciones ganó {etq(f['en_referencia'])} en {c['referencia_etiqueta']} "
                                  f"y {etq(f['en_otra'])} en {cam['etiqueta']}")
                    agregar(f["secciones"], "cantidad")
                linea += " Casos más frecuentes: " + "; ".join(partes) + "."
            if cam["ejemplos"]:
                ids = ", ".join(e["seccion"] for e in cam["ejemplos"])
                for e in cam["ejemplos"]:
                    if str(e["seccion"]).isdigit():
                        agregar(float(e["seccion"]), "id")
                linea += f" Secciones grandes con cambio: {ids}."
            lineas.append(linea)

    if c["historico"]:
        for h in c["historico"].values():
            partes = []
            for fila in h["anios"]:
                partes.append(f"{fila['anio']}: {etq(fila['ganador'])} {fila['pct'].get(fila['ganador'], 0):.1f} %"
                              + (f", participación {fila['participacion_pct']:.1f} %" if fila["participacion_pct"] is not None else ""))
                agregar(fila["pct"].get(fila["ganador"]), "porcentaje"); agregar(fila["participacion_pct"], "porcentaje")
                if str(fila["anio"]).isdigit():
                    agregar(float(fila["anio"]), "id")
            lineas.append(f"Evolución en {h['etiqueta']}: " + "; ".join(partes) + ".")
    else:
        lineas.append(f"La base solo tiene datos de {c['anio'] or 'un año'}: no hay forma de describir la evolución entre años.")
    if c["historico"]:
        lineas.append("Las coaliciones y la distritación pueden cambiar entre años; verifica que las opciones y territorios sean comparables.")
    lineas += [f"Aviso de datos: {a}" for a in c["avisos"]]
    return {"lineas": lineas, "permitidos": permitidos, "descripcion": descripcion}


# --------------------------------------------------------------------------- redacción
SISTEMA = (
    "Eres analista electoral. Redactas, para un equipo de campaña, un análisis breve y claro de un territorio usando "
    "EXCLUSIVAMENTE la ficha de datos que recibes.\n"
    "Reglas:\n"
    "1. Toda cifra (porcentaje, votos, puntos, número de secciones) se copia tal cual de la ficha. No calcules, no "
    "redondees, no inventes. Si falta un dato, dilo con palabras y sin número.\n"
    "2. Usa «más» o «menos» en lugar de signos. Los porcentajes llevan ' %'.\n"
    "3. Describe lo que muestran los datos. No presentes causas (candidaturas, campañas, clima) como hechos; si ofreces "
    "una explicación, márcala como hipótesis por validar.\n"
    "4. Describe cada partido u opción registrada por separado. No sumes aliados ni repartas coaliciones. Las diferencias agregadas no demuestran transferencias individuales de votos.\n"
    "5. Si hay avisos de datos, menciónalos en una frase.\n"
    "6. Sin juicios de valor ni recomendaciones partidistas.\n"
    "Estructura exacta, en markdown:\n"
    "## Hallazgos principales  (4 a 6 viñetas)\n"
    "## Voto diferenciado  (un párrafo corto y viñetas con los cambios y secciones más relevantes)\n"
    "## Evolución en el tiempo  (si la ficha no trae otros años, una sola frase que lo diga)\n"
    "## Qué vigilar  (2 o 3 viñetas operativas: qué secciones o diferencias conviene revisar en campo)\n"
    "Máximo 380 palabras."
)

_CACHE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def redactar(client: Any, modelo: str, ficha: dict[str, Any]) -> str:
    cuerpo = "Ficha de datos:\n" + "\n".join(f"- {linea}" for linea in ficha["lineas"]) + "\n\nRedacta el análisis."
    r = client.chat.completions.create(
        model=modelo, temperature=0.3, max_tokens=1500,
        messages=[{"role": "system", "content": SISTEMA}, {"role": "user", "content": cuerpo}])
    texto = (r.choices[0].message.content or "").strip()
    if not texto:
        raise ValueError("La respuesta está vacía.")
    return texto


def analizar(t: Territorio, client: Any, modelo: str, nivel: str, unidad: str, anio: Optional[str] = None,
             regenerar: bool = False) -> dict[str, Any]:
    comp = comparativo(t, nivel, unidad, anio)
    ficha = construir_ficha(comp)
    clave = hashlib.sha256(("\n".join(ficha["lineas"]) + "|" + modelo).encode("utf-8")).hexdigest()
    if not regenerar and clave in _CACHE:
        _CACHE.move_to_end(clave)
        return {**_CACHE[clave], "en_cache": True}
    texto = redactar(client, modelo, ficha)
    v = redaccion.verificar_cifras(texto, ficha["permitidos"], [], ficha["descripcion"])
    resultado = {
        "texto": texto, "modelo": modelo, "generado": datetime.now().astimezone().isoformat(timespec="seconds"),
        "verificacion": {"estado": v["estado"], "respaldadas": len(v["respaldadas"]),
                         "no_respaldadas": [x["texto"] for x in v["no_respaldadas"]]},
        "en_cache": False,
    }
    _CACHE[clave] = resultado
    while len(_CACHE) > MAX_CACHE:
        _CACHE.popitem(last=False)
    return resultado
