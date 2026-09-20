"""Motor del asistente electoral en lenguaje natural.

Diseñado para Google Colab + Groq + SQLite. El LLM interpreta la pregunta,
pero nunca escribe ni ejecuta SQL directamente: devuelve un plan JSON que
Python valida y convierte en una consulta parametrizada.
"""

# En Colab ejecutar una sola vez:
# !pip install -q groq rapidfuzz

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from rapidfuzz import fuzz, process


# ---------------------------------------------------------------------------
# 1. Configuracion
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    sheet_id: str = "1ytiQRi8MB3-JOdbqimJr7Virk5MkWYEqFhb1SozatyQ"
    gid: str = "1450398291"
    ruta_bd: str = "electoral.db"
    tabla: str = "resultados"
    modelo: str = "openai/gpt-oss-120b"
    max_filas_resultado: int = 5000
    umbral_coincidencia: int = 72

    @property
    def url_csv(self) -> str:
        return (
            f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/"
            f"export?format=csv&gid={self.gid}"
        )


CONFIG = Config()


# Los alias permiten adaptar el codigo si el archivo cambia ligeramente.
ALIAS_COLUMNAS = {
    "entidad": ["entidad", "estado"],
    "municipio": ["municipio", "nom_municipio"],
    "seccion": ["seccion", "seccion_electoral"],
    "id_distrito_local": ["id_distrito_local", "distrito_local"],
    "id_distrito_federal": ["id_distrito_federal", "distrito_federal"],
    "tipo_eleccion": ["tipo_eleccion", "eleccion", "cargo"],
    "partido": ["partido", "coalicion", "opcion_politica"],
    "votos": ["votos", "total_votos", "cantidad_votos"],
    "anio": ["anio", "ano", "year"],
}

DIMENSIONES = [
    "anio", "entidad", "municipio", "seccion",
    "id_distrito_local", "id_distrito_federal", "tipo_eleccion", "partido",
]

METRICAS_VALIDAS = {
    "total_votos", "ranking", "ganador", "comparacion", "margen",
    "competitividad", "participacion", "detalle", "resumen",
}

EXCLUIR_DE_VOTACION = {"LISTA_NOMINAL", "TOTAL_VOTOS_CALCULADOS"}


# ---------------------------------------------------------------------------
# 2. Carga y limpieza
# ---------------------------------------------------------------------------

def normalizar_texto(valor: Any) -> str:
    """Normaliza para buscar; no altera el valor que se muestra al usuario."""
    if pd.isna(valor):
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^A-Z0-9]+", " ", texto.upper()).strip()
    return re.sub(r"\s+", " ", texto)


def normalizar_nombre_columna(nombre: str) -> str:
    nombre = normalizar_texto(nombre).lower().replace(" ", "_")
    return re.sub(r"_+", "_", nombre).strip("_")


def columnas_unicas(columnas) -> list[str]:
    """Evita nombres repetidos despues de quitar acentos y espacios."""
    usadas: dict[str, int] = {}
    salida = []
    for columna in columnas:
        base = normalizar_nombre_columna(columna) or "columna"
        usadas[base] = usadas.get(base, 0) + 1
        salida.append(base if usadas[base] == 1 else f"{base}_{usadas[base]}")
    return salida


def encontrar_columna(columnas: list[str], concepto: str) -> str | None:
    for alias in ALIAS_COLUMNAS[concepto]:
        if alias in columnas:
            return alias
    return None


def cargar_datos(config: Config = CONFIG) -> tuple[pd.DataFrame, dict[str, str]]:
    # keep_default_na=False: el partido "NA" (Nueva Alianza) no debe leerse como valor
    # faltante. Solo las celdas realmente vacías se consideran nulas.
    df = pd.read_csv(config.url_csv, keep_default_na=False, na_values=[""])
    df.columns = columnas_unicas(df.columns)

    mapa = {
        concepto: col
        for concepto in ALIAS_COLUMNAS
        if (col := encontrar_columna(df.columns.tolist(), concepto)) is not None
    }
    faltantes = {"partido", "votos", "tipo_eleccion"} - mapa.keys()
    if faltantes:
        raise ValueError(f"Faltan columnas indispensables: {sorted(faltantes)}")

    df[mapa["votos"]] = pd.to_numeric(df[mapa["votos"]], errors="coerce")
    if df[mapa["votos"]].isna().any():
        n = int(df[mapa["votos"]].isna().sum())
        raise ValueError(f"La columna de votos contiene {n} valores no numericos.")
    if (df[mapa["votos"]] < 0).any():
        raise ValueError("La columna de votos contiene cantidades negativas.")

    # Columnas auxiliares para comparar sin problemas de acentos o abreviaturas.
    for concepto in DIMENSIONES:
        if concepto in mapa:
            df[f"__norm_{concepto}"] = df[mapa[concepto]].map(normalizar_texto)

    return df, mapa


def guardar_sqlite(df: pd.DataFrame, config: Config = CONFIG) -> None:
    with sqlite3.connect(config.ruta_bd) as conn:
        df.to_sql(config.tabla, conn, if_exists="replace", index=False)
        for col in (c for c in df.columns if c.startswith("__norm_")):
            nombre_indice = re.sub(r"\W", "_", f"idx_{config.tabla}_{col}")
            conn.execute(
                f'CREATE INDEX IF NOT EXISTS "{nombre_indice}" '
                f'ON "{config.tabla}" ("{col}")'
            )


def preparar_base(config: Config = CONFIG) -> tuple[pd.DataFrame, dict[str, str]]:
    df, mapa = cargar_datos(config)
    guardar_sqlite(df, config)
    print(f"Base creada: {len(df):,} filas y {len(df.columns):,} columnas.")
    print("Columnas reconocidas:", mapa)
    return df, mapa


# ---------------------------------------------------------------------------
# 3. Catalogo y resolucion tolerante de valores
# ---------------------------------------------------------------------------

class Catalogo:
    def __init__(self, df: pd.DataFrame, mapa: dict[str, str]):
        self.mapa = mapa
        self.valores: dict[str, list[str]] = {}
        self.normalizados: dict[str, dict[str, list[str]]] = {}

        for concepto in DIMENSIONES:
            if concepto not in mapa:
                continue
            valores = sorted(df[mapa[concepto]].dropna().astype(str).unique().tolist())
            self.valores[concepto] = valores
            indice: dict[str, list[str]] = {}
            for valor in valores:
                indice.setdefault(normalizar_texto(valor), []).append(valor)
            self.normalizados[concepto] = indice

    def resumen_para_modelo(self, max_por_columna: int = 120) -> str:
        partes = []
        for concepto, valores in self.valores.items():
            if len(valores) <= max_por_columna:
                partes.append(f"{concepto}: {valores}")
            else:
                partes.append(
                    f"{concepto}: {len(valores)} valores; el usuario puede indicar uno "
                    "aunque no se liste completo."
                )
        return "\n".join(partes)

    def resolver(self, concepto: str, solicitado: Any, umbral: int) -> str:
        if concepto not in self.valores:
            raise ValueError(f"La base no contiene la dimension '{concepto}'.")

        buscado = normalizar_texto(solicitado)
        indice = self.normalizados[concepto]
        if buscado in indice:
            candidatos = indice[buscado]
            if len(candidatos) == 1:
                return candidatos[0]
            raise ValueError(f"'{solicitado}' es ambiguo: {candidatos[:8]}")

        coincidencia = process.extractOne(buscado, list(indice), scorer=fuzz.WRatio)
        if not coincidencia or coincidencia[1] < umbral:
            sugerencias = process.extract(buscado, list(indice), scorer=fuzz.WRatio, limit=3)
            legibles = [indice[x[0]][0] for x in sugerencias]
            raise ValueError(
                f"No reconoci '{solicitado}' como {concepto}. "
                f"Opciones parecidas: {legibles}."
            )
        return indice[coincidencia[0]][0]


# ---------------------------------------------------------------------------
# 4. Interpretacion: el LLM produce un plan, no SQL
# ---------------------------------------------------------------------------

PLAN_EJEMPLO = {
    "metrica": "comparacion",
    "filtros": {
        "municipio": ["MEXQUITIC DE CARMONA"],
        "tipo_eleccion": ["DIPUTACION_LOC"],
        "partido": ["PAN", "PVEM"],
    },
    "agrupar_por": ["municipio", "partido"],
    "orden": "votos_desc",
    "limite": 20,
    "analisis_amplio": True,
    "aclaracion": None,
}


def extraer_json(texto: str) -> dict[str, Any]:
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto, flags=re.I)
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fin < inicio:
        raise ValueError("El modelo no devolvio un objeto JSON.")
    return json.loads(texto[inicio:fin + 1])


def interpretar_pregunta(
    pregunta: str,
    client,
    catalogo: Catalogo,
    config: Config,
    contexto_anterior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = f"""
Eres el interprete de un sistema electoral de San Luis Potosi. Convierte la
pregunta en UN objeto JSON; nunca escribas SQL ni texto adicional.

Dimensiones disponibles: {[x for x in DIMENSIONES if x in catalogo.mapa]}
Metricas permitidas: {sorted(METRICAS_VALIDAS)}
Catalogo:\n{catalogo.resumen_para_modelo()}

Formato exacto de salida:
{json.dumps(PLAN_EJEMPLO, ensure_ascii=False)}

Reglas:
- filtros es un objeto dimension -> lista de valores mencionados.
- agrupar_por solo contiene dimensiones disponibles.
- Comprende sinonimos y lenguaje cotidiano: alcaldia/municipio, casilla o zona
  cuando claramente se refiera a seccion, distrito local/federal, votos,
  gano, quedo en segundo, diferencia, cerrado, competitivo, participacion,
  abstencion, top/mejores/peores, contra/versus/compara.
- 'PAN vs PVEM' son dos filtros de partido.
- Para ganador o ranking sin partido explicito, no filtres partido.
- Para desglose territorial agrega la geografia pedida y partido al grupo.
- Para participacion usa metrica 'participacion'.
- Para las unidades mas competidas usa orden 'margen_asc'; para las mayores
  ventajas usa 'margen_desc'; para mas o menos votos usa 'votos_desc' o
  'votos_asc'. Extrae en limite el numero solicitado por el usuario.
- Si pide explicar patrones, fortalezas, concentracion o analisis, activa
  analisis_amplio.
- Si faltan datos indispensables o la pregunta admite interpretaciones
  materialmente distintas, escribe una pregunta breve en aclaracion. No
  inventes el dato.
- Una pregunta de seguimiento puede heredar filtros del contexto anterior.

Contexto anterior: {json.dumps(contexto_anterior, ensure_ascii=False) if contexto_anterior else 'ninguno'}
"""
    respuesta = client.chat.completions.create(
        model=config.modelo,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": pregunta},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return extraer_json(respuesta.choices[0].message.content)


def validar_plan(
    plan: dict[str, Any], catalogo: Catalogo, config: Config
) -> dict[str, Any]:
    metrica = plan.get("metrica", "resumen")
    if metrica not in METRICAS_VALIDAS:
        raise ValueError(f"Metrica no permitida: {metrica}")

    filtros_resueltos = {}
    for concepto, valores in (plan.get("filtros") or {}).items():
        if concepto not in catalogo.mapa:
            raise ValueError(f"La base no permite filtrar por '{concepto}'.")
        if not isinstance(valores, list):
            valores = [valores]
        filtros_resueltos[concepto] = [
            catalogo.resolver(concepto, valor, config.umbral_coincidencia)
            for valor in valores
        ]

    grupos = list(dict.fromkeys(plan.get("agrupar_por") or []))
    invalidos = [g for g in grupos if g not in catalogo.mapa]
    if invalidos:
        raise ValueError(f"Agrupaciones no disponibles: {invalidos}")

    # Evita mezclar cargos o años distintos en una sola cifra o ganador.
    for dimension, etiqueta in (("tipo_eleccion", "tipo de eleccion"), ("anio", "año")):
        opciones = catalogo.valores.get(dimension, [])
        if len(opciones) > 1 and dimension not in filtros_resueltos and dimension not in grupos:
            muestra = opciones[:8]
            raise ValueError(
                f"Necesito que indiques el {etiqueta} o pidas un desglose por esa "
                f"dimension. Opciones: {muestra}."
            )

    # El partido es necesario para rankings, ganador y comparaciones.
    if metrica in {"ranking", "ganador", "comparacion", "margen", "competitividad"}:
        if "partido" not in grupos:
            grupos.append("partido")

    return {
        "metrica": metrica,
        "filtros": filtros_resueltos,
        "agrupar_por": grupos,
        "orden": plan.get("orden", "votos_desc"),
        "limite": min(max(int(plan.get("limite") or 20), 1), 200),
        "analisis_amplio": bool(plan.get("analisis_amplio", False)),
        "aclaracion": plan.get("aclaracion"),
    }


# ---------------------------------------------------------------------------
# 5. SQL seguro y calculos deterministas
# ---------------------------------------------------------------------------

def q(nombre: str) -> str:
    return '"' + nombre.replace('"', '""') + '"'


def construir_sql(
    plan: dict[str, Any], catalogo: Catalogo, config: Config
) -> tuple[str, list[Any]]:
    mapa = catalogo.mapa
    grupos = plan["agrupar_por"]
    columnas_select = [f"{q(mapa[g])} AS {q(g)}" for g in grupos]
    columnas_select.append(f"SUM({q(mapa['votos'])}) AS votos")

    condiciones, parametros = [], []
    for concepto, valores in plan["filtros"].items():
        norm_col = f"__norm_{concepto}"
        marcas = ", ".join("?" for _ in valores)
        condiciones.append(f"{q(norm_col)} IN ({marcas})")
        parametros.extend(normalizar_texto(v) for v in valores)

    # Por defecto solo se suman votos reales. Las filas de control se consultan
    # aparte para participacion y cuando el usuario las pide expresamente.
    excluidos_normalizados = {normalizar_texto(v) for v in EXCLUIR_DE_VOTACION}
    partido_solicitado = {
        normalizar_texto(v) for v in plan["filtros"].get("partido", [])
    }
    if not partido_solicitado.intersection(excluidos_normalizados):
        condiciones.append(
            f"{q('__norm_partido')} NOT IN (?, ?)"
        )
        parametros.extend(sorted(excluidos_normalizados))

    sql = f"SELECT {', '.join(columnas_select)} FROM {q(config.tabla)}"
    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)
    if grupos:
        sql += " GROUP BY " + ", ".join(q(mapa[g]) for g in grupos)
    sql += " ORDER BY votos DESC"
    sql += f" LIMIT {config.max_filas_resultado}"
    return sql, parametros


def ejecutar_sql(sql: str, parametros: list[Any], config: Config) -> pd.DataFrame:
    with sqlite3.connect(config.ruta_bd) as conn:
        return pd.read_sql_query(sql, conn, params=parametros)


def columnas_geograficas(datos: pd.DataFrame) -> list[str]:
    return [
        c for c in [
            "anio", "entidad", "municipio", "seccion",
            "id_distrito_local", "id_distrito_federal", "tipo_eleccion",
        ] if c in datos.columns
    ]


def calcular_metricas(datos: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Calcula resultados sin delegar aritmetica al modelo."""
    if datos.empty:
        return datos, {"mensaje": "No se encontraron datos."}

    salida = datos.copy()
    resumen: dict[str, Any] = {"filas": len(salida)}
    if "partido" not in salida.columns:
        resumen["total_votos"] = float(salida["votos"].sum())
        return salida, resumen

    grupos = columnas_geograficas(salida)
    llaves = grupos if grupos else ["__total"]
    if not grupos:
        salida["__total"] = "TOTAL"

    total_grupo = salida.groupby(llaves, dropna=False)["votos"].transform("sum")
    salida["porcentaje"] = (
        salida["votos"].div(total_grupo.where(total_grupo.ne(0))).mul(100).round(2)
    )
    salida["posicion"] = (
        salida.groupby(llaves, dropna=False)["votos"]
        .rank(method="min", ascending=False)
        .astype("Int64")
    )

    ganadores = []
    for clave, sub in salida.groupby(llaves, dropna=False):
        sub = sub.sort_values(["votos", "partido"], ascending=[False, True])
        primero = sub.iloc[0]
        segundo = sub.iloc[1] if len(sub) > 1 else None
        clave = clave if isinstance(clave, tuple) else (clave,)
        fila = dict(zip(llaves, clave))
        fila.update({
            "ganador": primero["partido"],
            "votos_ganador": float(primero["votos"]),
            "porcentaje_ganador": float(primero["porcentaje"]),
            "segundo_lugar": None if segundo is None else segundo["partido"],
            "votos_segundo": None if segundo is None else float(segundo["votos"]),
            "margen_votos": None if segundo is None else float(primero["votos"] - segundo["votos"]),
            "margen_puntos": None if segundo is None else round(
                float(primero["porcentaje"] - segundo["porcentaje"]), 2
            ),
            "total_votos": float(sub["votos"].sum()),
        })
        ganadores.append(fila)

    resumen["ganadores"] = ganadores[:200]
    if "__total" in salida:
        salida = salida.drop(columns="__total")
    return salida.sort_values([*grupos, "votos"], ascending=[True] * len(grupos) + [False]), resumen


def calcular_participacion(
    plan: dict[str, Any], catalogo: Catalogo, config: Config
) -> pd.DataFrame:
    """Participacion = votos validos calculados / lista nominal.

    Se mantiene separada porque LISTA_NOMINAL no debe mezclarse con partidos.
    """
    # Partido no es una dimension valida para esta tasa: la participacion se
    # calcula para una unidad geografica/eleccion, no por opcion politica.
    grupos_participacion = [g for g in plan["agrupar_por"] if g != "partido"]
    base = {**plan, "metrica": "total_votos", "agrupar_por": grupos_participacion}
    base["filtros"] = {k: v for k, v in plan["filtros"].items() if k != "partido"}

    votos_plan = {**base, "filtros": {**base["filtros"]}}
    sql_votos, par_votos = construir_sql(votos_plan, catalogo, config)
    votos = ejecutar_sql(sql_votos, par_votos, config).rename(columns={"votos": "votos_emitidos"})

    lista_plan = {**base, "filtros": {**base["filtros"], "partido": ["LISTA_NOMINAL"]}}
    sql_lista, par_lista = construir_sql(lista_plan, catalogo, config)
    lista = ejecutar_sql(sql_lista, par_lista, config).rename(columns={"votos": "lista_nominal"})

    grupos = grupos_participacion
    if grupos:
        resultado = votos.merge(lista, on=grupos, how="left")
    else:
        resultado = pd.DataFrame({
            "votos_emitidos": [votos["votos_emitidos"].sum()],
            "lista_nominal": [lista["lista_nominal"].sum()],
        })
    resultado["participacion_pct"] = (
        resultado["votos_emitidos"] / resultado["lista_nominal"] * 100
    ).round(2)
    resultado["abstencion_pct"] = (100 - resultado["participacion_pct"]).round(2)
    return resultado


# ---------------------------------------------------------------------------
# 6. Explicacion para una persona no tecnica
# ---------------------------------------------------------------------------

def compactar_resultado(datos: pd.DataFrame, max_filas: int = 80) -> list[dict[str, Any]]:
    copia = datos.head(max_filas).copy()
    copia = copia.where(pd.notna(copia), None)
    return copia.to_dict(orient="records")


def redactar_respuesta(
    pregunta: str,
    plan: dict[str, Any],
    datos: pd.DataFrame,
    resumen: dict[str, Any],
    client,
    config: Config,
) -> str:
    paquete = {
        "pregunta": pregunta,
        "interpretacion": plan,
        "resumen_calculado": resumen,
        "datos": compactar_resultado(datos),
        "filas_totales": len(datos),
    }
    instrucciones = """
Eres un analista electoral neutral que explica resultados a personas sin
conocimientos tecnicos. Usa UNICAMENTE los numeros entregados; no recalcules,
inventes ni atribuyas causas. Empieza con la respuesta directa.

Despues, segun corresponda, incluye ganador, segundo lugar, votos, porcentajes,
margen, comparacion territorial, concentracion y casos competitivos. Distingue
votos de porcentaje. Aclara los filtros interpretados si eso evita confusion.
No llames participacion electoral al porcentaje interno de votos de un partido.
Las coaliciones son opciones electorales independientes: no repartas sus votos
entre sus partidos integrantes.

Si analisis_amplio es falso, responde brevemente. Si es verdadero, agrega:
- hallazgos cuantitativos realmente visibles;
- unidades que mas y menos destacan;
- limites: eleccion historica, sin causalidad ni pronostico;
- datos adicionales utiles, solo cuando sean pertinentes.

Si se truncaron filas, dilo. No expongas SQL ni detalles internos.
"""
    respuesta = client.chat.completions.create(
        model=config.modelo,
        messages=[
            {"role": "system", "content": instrucciones},
            {"role": "user", "content": json.dumps(paquete, ensure_ascii=False, default=str)},
        ],
        temperature=0.15,
    )
    return respuesta.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# 7. Interfaz principal con memoria de seguimiento
# ---------------------------------------------------------------------------

@dataclass
class AsistenteElectoral:
    client: Any
    df: pd.DataFrame
    mapa: dict[str, str]
    config: Config = CONFIG
    ultimo_plan: dict[str, Any] | None = field(default=None, init=False)

    def __post_init__(self):
        self.catalogo = Catalogo(self.df, self.mapa)

    def preguntar(self, pregunta: str, mostrar_diagnostico: bool = False) -> dict[str, Any]:
        if not pregunta or not pregunta.strip():
            return {"respuesta": "Escribe una pregunta sobre los resultados electorales."}

        try:
            bruto = interpretar_pregunta(
                pregunta, self.client, self.catalogo, self.config, self.ultimo_plan
            )
            if bruto.get("aclaracion"):
                return {"respuesta": bruto["aclaracion"], "requiere_aclaracion": True}
            plan = validar_plan(bruto, self.catalogo, self.config)

            if plan["metrica"] == "participacion":
                datos = calcular_participacion(plan, self.catalogo, self.config)
                resumen = {"filas": len(datos), "tipo": "participacion_electoral"}
                sql = None
                parametros = None
            else:
                sql, parametros = construir_sql(plan, self.catalogo, self.config)
                datos_crudos = ejecutar_sql(sql, parametros, self.config)
                datos, resumen = calcular_metricas(datos_crudos)
                if "ganadores" in resumen:
                    ganadores = resumen["ganadores"]
                    orden = plan["orden"]
                    if orden in {"margen_asc", "margen_desc"}:
                        ganadores.sort(
                            key=lambda x: float("inf") if x["margen_puntos"] is None
                            else x["margen_puntos"],
                            reverse=orden == "margen_desc",
                        )
                    elif orden in {"votos_asc", "votos_desc"}:
                        ganadores.sort(
                            key=lambda x: x["votos_ganador"],
                            reverse=orden == "votos_desc",
                        )
                    resumen["ganadores"] = ganadores[:plan["limite"]]

            if datos.empty:
                return {
                    "respuesta": "No encontre resultados con esos filtros. "
                    "Prueba con otro municipio, eleccion, partido o nivel geografico.",
                    "plan": plan,
                }

            respuesta = redactar_respuesta(
                pregunta, plan, datos, resumen, self.client, self.config
            )
            self.ultimo_plan = plan
            salida = {"respuesta": respuesta, "datos": datos, "plan": plan}
            if mostrar_diagnostico:
                salida.update({"sql": sql, "parametros": parametros, "resumen": resumen})
            return salida
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            return {
                "respuesta": f"No pude interpretar la consulta con seguridad: {error}",
                "error": str(error),
            }
        except sqlite3.Error:
            return {
                "respuesta": "Ocurrio un problema al consultar la base. "
                "Revisa que la base haya sido creada correctamente."
            }


# ---------------------------------------------------------------------------
# 8. Uso en Google Colab
# ---------------------------------------------------------------------------

"""
from google.colab import userdata
from groq import Groq

df, mapa = preparar_base()
client = Groq(api_key=userdata.get("API_KEY_ELECCIONES"))
asistente = AsistenteElectoral(client=client, df=df, mapa=mapa)

consulta = asistente.preguntar(
    "Compara PAN contra PVEM en Mexquitic de Carmona para diputacion local y "
    "dime quien gano, por cuanto y que tan competida fue",
    mostrar_diagnostico=True,
)
print(consulta["respuesta"])
display(consulta["datos"])

# Seguimiento: hereda el contexto de la pregunta anterior.
consulta = asistente.preguntar("Ahora desglosalo por seccion")
print(consulta["respuesta"])
display(consulta["datos"])
"""


# Preguntas que ya puede atender:
PREGUNTAS_DE_EJEMPLO = [
    "¿Cuantos votos obtuvo el PAN en Ciudad del Maiz para diputacion local?",
    "¿Quien gano en cada municipio y cual fue su margen sobre el segundo?",
    "Compara PAN, PVEM y MORENA en el distrito local 5.",
    "¿Cuales fueron las 10 secciones mas competidas de San Luis Potosi?",
    "Dame el ranking de partidos en Mexquitic de Carmona.",
    "¿En que municipios tuvo mas votos el PVEM?",
    "¿Que porcentaje de la votacion obtuvo cada partido en la seccion 1200?",
    "¿Cual fue la participacion y abstencion por municipio?",
    "Analiza la concentracion territorial del PAN por distrito local.",
    "¿Donde fue mayor la diferencia entre primero y segundo lugar?",
]
