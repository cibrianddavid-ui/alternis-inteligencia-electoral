"""Lógica de análisis LLM para Social Listening: clasificación, temas y resúmenes."""
from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from typing import Any, Optional

from .models import (
    ANALISIS_VERSION,
    LLM_BATCH_SIZE,
    THEME_SAMPLE_SIZE,
    TONOS_VALIDOS,
    ComentarioClasificado,
    ResultadoSocial,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM — utilidades de parsing/validación
# ---------------------------------------------------------------------------


def _extraer_objeto_json(contenido: str) -> dict:
    contenido = (contenido or "").strip()
    if not contenido:
        raise ValueError("Respuesta LLM vacía")

    try:
        data = json.loads(contenido)
        if not isinstance(data, dict):
            raise ValueError("Se esperaba un objeto JSON")
        return data
    except json.JSONDecodeError:
        inicio = contenido.find("{")
        fin = contenido.rfind("}")
        if inicio < 0 or fin <= inicio:
            raise
        data = json.loads(contenido[inicio : fin + 1])
        if not isinstance(data, dict):
            raise ValueError("Se esperaba un objeto JSON")
        return data


def _parsear_clasificaciones(
    contenido: str,
    ids_permitidos: set[int],
) -> dict[int, str]:
    obj = _extraer_objeto_json(contenido)
    lista = obj.get("clasificaciones")
    if not isinstance(lista, list):
        raise ValueError("Falta array 'clasificaciones'")

    resultado: dict[int, str] = {}
    for item in lista:
        if not isinstance(item, dict):
            continue
        idx = item.get("id")
        tono = str(item.get("tono") or "").strip().lower()

        # bool es subclass de int; se excluye explícitamente.
        if isinstance(idx, bool) or not isinstance(idx, int):
            continue
        if idx not in ids_permitidos:
            log.warning("LLM devolvió ID fuera del batch: %r", idx)
            continue
        if tono not in TONOS_VALIDOS:
            log.warning("LLM devolvió tono inválido para ID %s: %r", idx, tono)
            continue
        if idx in resultado:
            log.warning("LLM devolvió ID duplicado: %s", idx)
            continue
        resultado[idx] = tono

    return resultado


# ---------------------------------------------------------------------------
# LLM — Etapa 1: postura/sentimiento dirigido por post
# ---------------------------------------------------------------------------


def _clasificar_postura_batch(
    comentarios_batch: list[dict],
    post_texto: str,
    url_perfil: str,
    client: Any,
    model_name: str,
    max_reintentos: int = 2,
) -> dict[int, str]:
    """Clasifica un batch perteneciente a UN SOLO post con contexto político mexicano."""
    if not comentarios_batch:
        return {}

    muestra = [
        {"id": i, "texto": c.get("texto", "")}
        for i, c in enumerate(comentarios_batch)
    ]
    ids_esperados = set(range(len(muestra)))
    resultado: dict[int, str] = {}

    sistema = """
Eres un analista experto en escucha social política y clasificación de postura en redes sociales mexicanas.

Tu objetivo es clasificar la POSTURA de CADA comentario de Facebook con respecto al ACTOR POLÍTICO, CANDIDATO, AUTOR DE LA PUBLICACIÓN O PROYECTO POLÍTICO representado en el post.

DEFINICIÓN RIGUROSA DE LAS 3 ETIQUETAS:

1. "positivo":
- Expresa apoyo, felicitación, simpatía, ánimo, admiración, defensa, voto favorable o agradecimiento hacia el autor, diputado, candidato o su movimiento/partido político.
- Ejemplos: "Excelente trabajo diputado", "Con todo", "4T hoy mañana y siempre ❤️‍🩹", "El mejor candidato", "Muchas bendiciones y éxito", "Vamos a ganar", "Orgullo potosino", aplausos o corazones de respaldo.

2. "negativo":
- Expresa crítica, rechazo, desaprobación, reclamo, denuncia, queja, burla, ironía, sarcasmo, desconfianza o descalificación hacia el autor, su partido, o su actitud.
- DETECCIÓN CRÍTICA DE SARCASMO E IDIOMATISMOS MEXICANOS:
  * Sarcasmo y burlas de "humildad" o "simulación": "El humilde le dicen jajaja", "Mi compa el humilde no mames", "Ya salió el de la pala", "Puro show", "Ponte a trabajar", "Que ridículo" son DESCALIFICACIONES y DEBEN ser clasificadas como NEGATIVAS.
  * Descalificaciones políticas y apodos peyorativos: "El gato del pollo", "Tapete del gobernador", "Vendido", "Títere", "Ratas", "Lacras", "Pinches corruptos", "CHSPM", "Pendejos", "Bola de parásitos" son estrictamente NEGATIVOS.
  * Cuestionamientos a cifras o discursos oficiales: "13.5 millones salieron de la pobreza... la familia de los políticos", "Pura mentira", "Dónde quedó el cambio" son NEGATIVOS.

3. "neutro":
- ÚNICAMENTE comentarios sin ninguna postura evaluativa ni carga afectiva hacia la publicación o el autor:
  * Preguntas de servicio o datos objetivos: "¿Dónde están ubicados?", "¿A qué hora empieza?", "¿Habrá transmisión?".
  * Comentarios que solo hablan de deportes o temas ajenos sin opinar del político ni del post: "Que se arme la reta de softbol 🏆".
  * Menciones o saludos aislados totalmente carentes de juicio de valor.
- ADVERTENCIA: NUNCA clasifiques como neutro un comentario sarcástico, de mofa o con insultos. Si hay burla ("jajaja", "no mames", "el humilde"), es NEGATIVO.

REGLAS DE SALIDA:
- Clasifica CADA comentario suministrado utilizando su ID numérico ("id": <int>).
- Genera ÚNICAMENTE un objeto JSON válido con este formato exacto:
{"clasificaciones": [{"id": 0, "tono": "positivo"}, {"id": 1, "tono": "negativo"}]}
""".strip()

    for intento in range(max_reintentos + 1):
        pendientes_ids = ids_esperados - set(resultado)
        if not pendientes_ids:
            break

        pendientes = [muestra[i] for i in sorted(pendientes_ids)]
        try:
            resp = client.chat.completions.create(
                model=model_name,
                temperature=0.0,
                max_tokens=2500,
                messages=[
                    {"role": "system", "content": sistema},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "target": {"actor_o_url": url_perfil},
                                "post_context": (post_texto or "")[:1200],
                                "comentarios": pendientes,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                response_format={"type": "json_object"},
            )
            contenido = (resp.choices[0].message.content or "").strip()
            parcial = _parsear_clasificaciones(contenido, pendientes_ids)
            resultado.update(parcial)
        except Exception as exc:
            log.warning(
                "Error clasificando postura (intento %d/%d): %s",
                intento + 1,
                max_reintentos + 1,
                exc,
            )
            if "429" in str(exc) or "rate_limit" in str(exc).lower():
                time.sleep(4.0)
            elif intento < max_reintentos:
                time.sleep(1.0)

    # Sub-batch rescue si quedan IDs pendientes para garantizar cobertura completa
    faltantes = ids_esperados - set(resultado)
    if faltantes:
        log.warning(
            "Reintentando %d IDs faltantes en sub-batches de rescate (tamaño 5)...",
            len(faltantes),
        )
        faltantes_lista = sorted(faltantes)
        for sub_inicio in range(0, len(faltantes_lista), 5):
            sub_ids = set(faltantes_lista[sub_inicio : sub_inicio + 5])
            sub_muestra = [muestra[i] for i in sorted(sub_ids)]
            try:
                sub_resp = client.chat.completions.create(
                    model=model_name,
                    temperature=0.0,
                    max_tokens=1500,
                    messages=[
                        {"role": "system", "content": sistema},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "target": {"actor_o_url": url_perfil},
                                    "post_context": (post_texto or "")[:1200],
                                    "comentarios": sub_muestra,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    response_format={"type": "json_object"},
                )
                sub_contenido = (sub_resp.choices[0].message.content or "").strip()
                sub_parcial = _parsear_clasificaciones(sub_contenido, sub_ids)
                resultado.update(sub_parcial)
            except Exception as sub_exc:
                log.warning("Fallo en sub-batch de rescate: %s", sub_exc)

    faltantes_final = ids_esperados - set(resultado)
    if faltantes_final:
        log.warning(
            "IDs sin clasificación tras sub-batches de rescate; quedan analizado=0: %s",
            sorted(faltantes_final),
        )

    return resultado


def _agrupar_por_post(comentarios: list[dict]) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = defaultdict(list)
    for c in comentarios:
        grupos[str(c.get("post_url") or "")].append(c)
    return dict(grupos)


def _clasificar_postura_todos(
    comentarios: list[dict],
    post_texto_map: dict[str, str],
    url_perfil: str,
    client: Any,
    model_name: str,
) -> dict[str, str]:
    """Agrupa por post y después crea batches: nunca mezcla contextos distintos."""
    resultados_bd: dict[str, str] = {}
    grupos = _agrupar_por_post(comentarios)

    for post_url, comentarios_post in grupos.items():
        post_texto = post_texto_map.get(post_url, "")
        total = len(comentarios_post)
        n_batches = (total + LLM_BATCH_SIZE - 1) // LLM_BATCH_SIZE
        log.info(
            "Clasificando %d comentarios del post %s en %d batches de tamaño %d.",
            total,
            post_url,
            n_batches,
            LLM_BATCH_SIZE,
        )

        for inicio in range(0, total, LLM_BATCH_SIZE):
            batch = comentarios_post[inicio : inicio + LLM_BATCH_SIZE]
            mapa_local = _clasificar_postura_batch(
                batch,
                post_texto,
                url_perfil,
                client,
                model_name,
            )
            for idx_local, tono in mapa_local.items():
                if not 0 <= idx_local < len(batch):
                    continue
                bd_id = str(batch[idx_local].get("id") or "")
                if bd_id:
                    resultados_bd[bd_id] = tono

    return resultados_bd


# ---------------------------------------------------------------------------
# LLM — Etapa 2: segunda pasada de neutros por post y batch
# ---------------------------------------------------------------------------


def _validar_neutros_batch(
    neutros_batch: list[dict],
    post_texto: str,
    url_perfil: str,
    client: Any,
    model_name: str,
) -> dict[int, str]:
    if not neutros_batch:
        return {}

    muestra = [{"id": i, "texto": c.get("texto", "")} for i, c in enumerate(neutros_batch)]
    ids_esperados = set(range(len(muestra)))

    sistema = """
Eres un auditor independiente de control de calidad para análisis de postura política en redes sociales mexicanas.

Se te presenta una lista de comentarios que fueron provisionalmente clasificados como "neutro". Tu misión es identificar si alguno de ellos en realidad contiene SARCASMO, BURLA, CRÍTICA POLÍTICA ENCUBIERTA o APOYO IMPLÍCITO, para corregirlo.

CRITERIOS DE REVISIÓN:
- "negativo": Si contiene ironía o sarcasmo ("el humilde", "ya salió el de la pala", "no mames", "jajaja"), apodos satíricos ("gato de...", "tapete"), quejas encubiertas, reclamos o insultos. ¡Cámbialo a "negativo"!
- "positivo": Si contiene felicitación, respaldo ("con todo", "a la orden", "4T"), bendiciones o agradecimiento genuino. ¡Cámbialo a "positivo"!
- "neutro": Mantén "neutro" SOLO si es una pregunta logística, un dato objetivo o un comentario completamente ajeno a la política sin valoración alguna.

FORMATO DE RESPUESTA:
Retorna ÚNICAMENTE un objeto JSON:
{"clasificaciones": [{"id": 0, "tono": "negativo"}]}
""".strip()

    resultado: dict[int, str] = {}
    for intento in range(2):
        pendientes_ids = ids_esperados - set(resultado)
        if not pendientes_ids:
            break
        pendientes = [muestra[i] for i in sorted(pendientes_ids)]
        try:
            resp = client.chat.completions.create(
                model=model_name,
                temperature=0.0,
                max_tokens=2500,
                messages=[
                    {"role": "system", "content": sistema},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "target": {"actor_o_url": url_perfil},
                                "post_context": (post_texto or "")[:1200],
                                "comentarios": pendientes,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                response_format={"type": "json_object"},
            )
            contenido = (resp.choices[0].message.content or "").strip()
            resultado.update(_parsear_clasificaciones(contenido, pendientes_ids))
        except Exception as exc:
            log.warning(
                "Error validando neutros (intento %d/2): %s",
                intento + 1,
                exc,
            )

    # Sub-batch rescue de neutros
    faltantes = ids_esperados - set(resultado)
    if faltantes:
        faltantes_lista = sorted(faltantes)
        for sub_inicio in range(0, len(faltantes_lista), 5):
            sub_ids = set(faltantes_lista[sub_inicio : sub_inicio + 5])
            sub_muestra = [muestra[i] for i in sorted(sub_ids)]
            try:
                sub_resp = client.chat.completions.create(
                    model=model_name,
                    temperature=0.0,
                    max_tokens=1500,
                    messages=[
                        {"role": "system", "content": sistema},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "target": {"actor_o_url": url_perfil},
                                    "post_context": (post_texto or "")[:1200],
                                    "comentarios": sub_muestra,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    response_format={"type": "json_object"},
                )
                sub_contenido = (sub_resp.choices[0].message.content or "").strip()
                sub_parcial = _parsear_clasificaciones(sub_contenido, sub_ids)
                resultado.update(sub_parcial)
            except Exception as sub_exc:
                log.warning("Fallo en sub-batch de rescate de neutros: %s", sub_exc)

    return resultado


def _validar_neutros_todos(
    neutros: list[dict],
    post_texto_map: dict[str, str],
    url_perfil: str,
    client: Any,
    model_name: str,
) -> dict[str, str]:
    cambios: dict[str, str] = {}

    for post_url, grupo in _agrupar_por_post(neutros).items():
        post_texto = post_texto_map.get(post_url, "")
        for inicio in range(0, len(grupo), LLM_BATCH_SIZE):
            batch = grupo[inicio : inicio + LLM_BATCH_SIZE]
            revisados = _validar_neutros_batch(
                batch,
                post_texto,
                url_perfil,
                client,
                model_name,
            )
            for idx_local, tono_final in revisados.items():
                if not 0 <= idx_local < len(batch):
                    continue
                if tono_final == "neutro":
                    continue
                bd_id = str(batch[idx_local].get("id") or "")
                if bd_id:
                    cambios[bd_id] = tono_final

    return cambios


# ---------------------------------------------------------------------------
# LLM — Etapa 3: muestreo estratificado y temas
# ---------------------------------------------------------------------------


def _muestreo_temas(comentarios: list[dict], limite: int = THEME_SAMPLE_SIZE) -> list[dict]:
    """Muestreo determinista por post+tono, evitando usar solo los de más likes."""
    clasificados = [c for c in comentarios if c.get("tono") in TONOS_VALIDOS]
    if len(clasificados) <= limite:
        return clasificados

    estratos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in clasificados:
        estratos[(str(c.get("post_url") or ""), str(c.get("tono")))].append(c)

    # Dentro de cada estrato, alternar recientes y relevantes para no depender solo de likes.
    for clave, items in estratos.items():
        items.sort(key=lambda x: (int(x.get("likes") or 0), int(x.get("rid") or 0)), reverse=True)
        estratos[clave] = items

    claves = sorted(estratos.keys())
    indices = {k: 0 for k in claves}
    muestra: list[dict] = []

    while len(muestra) < limite:
        agrego = False
        for clave in claves:
            idx = indices[clave]
            items = estratos[clave]
            if idx < len(items):
                muestra.append(items[idx])
                indices[clave] += 1
                agrego = True
                if len(muestra) >= limite:
                    break
        if not agrego:
            break

    return muestra


def _extraer_temas_extractivos(comentarios_clasificados: list[dict]) -> list[dict]:
    """Fallback extractivo para generar ejes temáticos cuando el LLM falla o devuelve vacío."""
    if not comentarios_clasificados:
        return []

    stopwords = {
        "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
        "un", "para", "con", "no", "una", "su", "al", "lo", "como", "mas", "más",
        "pero", "sus", "le", "ya", "o", "este", "si", "porque", "esta", "son",
        "entre", "cuando", "muy", "sin", "sobre", "ser", "tiene", "también", "tambien",
        "me", "hasta", "hay", "donde", "quien", "desde", "todo", "nos", "durante",
        "todos", "uno", "les", "ni", "contra", "otros", "ese", "eso", "ante", "ellos",
        "e", "esto", "mí", "antes", "algunos", "qué", "unos", "yo", "otro", "otras",
        "otra", "él", "tanto", "esa", "estos", "mucho", "quienes", "nada", "muchos",
        "cual", "poco", "ella", "estar", "estas", "algunas", "algo", "nosotros", "mi",
        "mis", "tus", "tu", "te", "ti", "facebook", "https", "com", "www", "jajaja", "jaja"
    }

    frecuencias: dict[str, list[dict]] = defaultdict(list)

    for c in comentarios_clasificados:
        texto = str(c.get("texto") or "").lower()
        tokens = [w for w in re.findall(r"\b[a-záéíóúñ]{4,}\b", texto) if w not in stopwords]
        if not tokens:
            continue
        vistos_en_comentario = set()
        for w in tokens:
            if w not in vistos_en_comentario:
                frecuencias[w].append(c)
                vistos_en_comentario.add(w)
        for i in range(len(tokens) - 1):
            bg = f"{tokens[i]} {tokens[i+1]}"
            if bg not in vistos_en_comentario:
                frecuencias[bg].append(c)
                vistos_en_comentario.add(bg)

    min_freq = 2 if len(comentarios_clasificados) >= 5 else 1
    candidatos = [(k, comms) for k, comms in frecuencias.items() if len(comms) >= min_freq]
    candidatos.sort(key=lambda x: len(x[1]), reverse=True)

    temas_salida: list[dict] = []
    usados: set[str] = set()
    for frase, comms in candidatos:
        partes_f = frase.split()
        if any(w in usados for w in partes_f):
            continue
        for w in partes_f:
            usados.add(w)
        nombre_tema = frase.capitalize()
        temas_salida.append({
            "texto": nombre_tema,
            "comentarios_ids": [str(c.get("id")) for c in comms[:15]],
            "explicacion": f"Eje de conversación centrado en {frase} observado en los comentarios.",
        })
        if len(temas_salida) >= 5:
            break

    return temas_salida


def _extraer_temas(
    comentarios_clasificados: list[dict],
    client: Any,
    model_name: str,
) -> list[dict]:
    muestra = _muestreo_temas(comentarios_clasificados)
    if not muestra:
        return []

    muestreados = muestra[:80]
    id_map = {i: str(c.get("id") or "") for i, c in enumerate(muestreados)}
    payload = [
        {"id": i, "texto": c.get("texto", "")}
        for i, c in enumerate(muestreados)
        if c.get("texto")
    ]

    sistema = """
Eres un analista de escucha social política y extracción temática.

Analiza los comentarios de Facebook provistos e identifica de 3 a 6 ejes temáticos sustantivos que resuman las principales discusiones, acusaciones, quejas, defensas o temas que la ciudadanía expresa.

REGLAS OBLIGATORIAS:
1. Cada tema debe tener un nombre claro y sustantivo en español de 2 a 5 palabras (por ejemplo: "Sarcasmo y falsa humildad", "Respaldo y orgullo por la 4T", "Acusaciones de servilismo político", "Apoyo al deporte comunitario").
2. Incluye una breve explicación analítica de lo que expresa la gente en ese tema.
3. En "comentarios_ids", coloca una lista con los IDs NUMÉRICOS de los comentarios del listado que sustentan ese tema (ejemplo: [0, 2, 5]).
4. PROHIBIDO inventar temas no sustentados en los comentarios.

FORMATO EXACTO:
Retorna ÚNICAMENTE un objeto JSON válido con la siguiente estructura:
{
  "frases_clave": [
    {
      "texto": "Nombre del tema en español",
      "comentarios_ids": [0, 2, 5],
      "explicacion": "Breve explicación de la dinámica observada"
    }
  ]
}
""".strip()

    try:
        resp = client.chat.completions.create(
            model=model_name,
            temperature=0.1,
            max_tokens=3000,
            messages=[
                {"role": "system", "content": sistema},
                {
                    "role": "user",
                    "content": json.dumps({"comentarios": payload}, ensure_ascii=False),
                },
            ],
            response_format={"type": "json_object"},
        )
        datos = _extraer_objeto_json((resp.choices[0].message.content or "").strip())
        temas = datos.get("frases_clave")
        if isinstance(temas, list) and temas:
            temas_reales = []
            for t in temas:
                if not isinstance(t, dict):
                    continue
                raw_ids = t.get("comentarios_ids") or t.get("ids") or []
                mapped_ids = []
                for idx in raw_ids:
                    try:
                        idx_int = int(idx)
                        if idx_int in id_map:
                            mapped_ids.append(id_map[idx_int])
                    except (ValueError, TypeError):
                        if str(idx) in id_map.values():
                            mapped_ids.append(str(idx))
                texto_t = str(t.get("texto") or "").strip()
                if texto_t:
                    temas_reales.append({
                        "texto": texto_t,
                        "comentarios_ids": mapped_ids,
                        "explicacion": str(t.get("explicacion") or "Eje observado en la conversación.").strip(),
                    })
            if temas_reales:
                return temas_reales
    except Exception as exc:
        log.warning("Error extrayendo temas con LLM: %s", exc)

    return _extraer_temas_extractivos(comentarios_clasificados)


def _procesar_temas(
    temas_raw: list[dict],
    comentarios_clasificados: list[dict],
) -> list[dict]:
    """Enriquece temas usando IDs reales; no usa keywords para inventar el tono."""
    por_id = {
        str(c.get("id")): c
        for c in comentarios_clasificados
        if c.get("id") and c.get("tono") in TONOS_VALIDOS
    }
    salida: list[dict] = []

    for tema in temas_raw:
        if not isinstance(tema, dict):
            continue
        texto_tema = str(tema.get("texto") or "").strip()
        if len(texto_tema) < 3:
            continue

        raw_ids = tema.get("comentarios_ids") or tema.get("ids") or []
        if not isinstance(raw_ids, list):
            raw_ids = []

        asociados: list[dict] = []
        vistos: set[str] = set()
        for raw_id in raw_ids:
            cid = str(raw_id)
            if cid in por_id and cid not in vistos:
                asociados.append(por_id[cid])
                vistos.add(cid)

        # Fallback conservador si el modelo no asoció IDs válidos.
        if not asociados:
            tokens = [t.lower() for t in texto_tema.split() if len(t) >= 4]
            if tokens:
                asociados = [
                    c
                    for c in comentarios_clasificados
                    if any(t in str(c.get("texto") or "").lower() for t in tokens)
                ][:20]

        pos = sum(1 for c in asociados if c.get("tono") == "positivo")
        neg = sum(1 for c in asociados if c.get("tono") == "negativo")
        neu = sum(1 for c in asociados if c.get("tono") == "neutro")

        if pos > max(neg, neu):
            tono_tema = "positivo"
        elif neg > max(pos, neu):
            tono_tema = "negativo"
        else:
            tono_tema = "neutro"

        salida.append(
            {
                "texto": texto_tema,
                "frecuencia": len(asociados),
                "tono": tono_tema,
                "explicacion": str(
                    tema.get("explicacion")
                    or "Eje temático identificado en la conversación."
                ),
                "desglose": {"positivo": pos, "negativo": neg, "neutro": neu},
                "comentarios": [
                    {
                        "texto": c.get("texto", ""),
                        "tono": c.get("tono"),
                        "autor_nombre": c.get("autor_nombre") or "Ciudadano",
                        "autor_url": c.get("autor_url"),
                        "comentario_url": c.get("comentario_url"),
                        "likes": int(c.get("likes") or 0),
                    }
                    for c in asociados[:20]
                ],
            }
        )

    salida.sort(key=lambda x: x["frecuencia"], reverse=True)
    return salida


# ---------------------------------------------------------------------------
# LLM — Etapa 4: resumen ejecutivo
# ---------------------------------------------------------------------------


def _generar_resumen(
    url_perfil: str,
    total_comentarios: int,
    distribucion: dict,
    sentimiento: dict,
    temas: list[dict],
    ejemplos_pos: list[str],
    ejemplos_neg: list[str],
    client: Any,
    model_name: str,
) -> str:
    if client is None:
        return ""

    payload = {
        "perfil": url_perfil,
        "total_comentarios_clasificados": total_comentarios,
        "distribucion": distribucion,
        "postura_pct": sentimiento,
        "balance_neto_postura": round(
            float(sentimiento.get("positivo", 0.0))
            - float(sentimiento.get("negativo", 0.0)),
            1,
        ),
        "temas_principales": [
            {
                "texto": t.get("texto"),
                "frecuencia": t.get("frecuencia"),
                "tono": t.get("tono"),
                "explicacion": t.get("explicacion"),
            }
            for t in temas[:8]
        ],
        "ejemplos_positivos": ejemplos_pos[:5],
        "ejemplos_negativos": ejemplos_neg[:5],
    }

    sistema = """
Eres un analista senior de inteligencia política y opinión pública digital en redes sociales mexicanas.

Redacta un resumen ejecutivo en ESPAÑOL de EXACTAMENTE 3 párrafos sustantivos y profundos analizando la conversación ciudadana observada en la publicación.

REGLAS OBLIGATORIAS DE CONTENIDO:
1. PROHIBIDO TERMINANTEMENTE hacer meta-análisis técnico sobre el sistema, la cobertura estadística, el porcentaje de comentarios clasificados vs no clasificados, o el software.
2. Centra el análisis 100% en los CONTENIDOS, ARGUMENTOS, CRÍTICAS, BURLAS, EXPRESIONES DE APOYO Y TEMAS que la ciudadanía realmente expresó en los comentarios.
3. Párrafo 1 — Clima general y balance de postura: Describe la orientación dominante de la conversación, contrastando el nivel de aprobación o descontento hacia la figura política o publicación, relacionando los porcentajes de balance con el contenido del post.
4. Párrafo 2 — Ejes de cuestionamiento, críticas y vulnerabilidades: Detalla los argumentos negativos, reclamos, ironías, señalamientos de simulación o apodos utilizados por los usuarios críticos (por ejemplo: acusaciones de servilismo, simulación de humildad, reclamos por su gestión). Cita o sintetiza frases textuales de los comentarios críticos.
5. Párrafo 3 — Argumentos de apoyo, narrativas favorables y balance estratégico: Explica qué sectores o mensajes respaldan la publicación (defensa partidista/4T, agradecimientos comunitarios, muestras de simpatía personal) y ofrece una conclusión estratégica sobre la percepción pública del actor.

ESTILO:
- Riguroso, analítico, profesional y directo.
- No uses viñetas, títulos ni encabezados. Retorna ÚNICAMENTE los 3 párrafos separados por doble salto de línea.
""".strip()

    try:
        resp = client.chat.completions.create(
            model=model_name,
            temperature=0.3,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": sistema},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        log.warning("Error generando resumen ejecutivo: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Resultado / estadísticas
# ---------------------------------------------------------------------------


def _calcular_estadisticas(comentarios: list[dict]) -> tuple[dict, dict, int]:
    clasificados = [c for c in comentarios if c.get("tono") in TONOS_VALIDOS]
    total = len(clasificados)
    distribucion = {
        "positivo": sum(1 for c in clasificados if c.get("tono") == "positivo"),
        "negativo": sum(1 for c in clasificados if c.get("tono") == "negativo"),
        "neutro": sum(1 for c in clasificados if c.get("tono") == "neutro"),
    }

    if total == 0:
        sentimiento = {"positivo": 0.0, "negativo": 0.0, "neutro": 0.0}
    else:
        sentimiento = {
            tono: round(cantidad / total * 100, 1)
            for tono, cantidad in distribucion.items()
        }
    return distribucion, sentimiento, total


def _seleccionar_top_comentarios(comentarios: list[dict]) -> list[ComentarioClasificado]:
    seleccionados: list[ComentarioClasificado] = []

    for tono in ("negativo", "positivo", "neutro"):
        grupo = [c for c in comentarios if c.get("tono") == tono]
        grupo.sort(
            key=lambda x: (int(x.get("likes") or 0), int(x.get("rid") or 0)),
            reverse=True,
        )
        for c in grupo[:10]:
            seleccionados.append(
                ComentarioClasificado(
                    texto=str(c.get("texto") or ""),
                    tono=tono,
                    autor_nombre=c.get("autor_nombre"),
                    autor_url=c.get("autor_url"),
                    comentario_url=c.get("comentario_url"),
                    likes=int(c.get("likes") or 0),
                )
            )

    return seleccionados


def _resultado_sin_comentarios(url_perfil: str, posts: int) -> ResultadoSocial:
    return ResultadoSocial(
        url_perfil=url_perfil,
        posts_analizados=max(posts, 1),
        total_comentarios=0,
        sentimiento={"positivo": 0.0, "neutro": 0.0, "negativo": 0.0},
        distribucion={"positivo": 0, "neutro": 0, "negativo": 0},
        ngramas=[],
        resumen="Los posts analizados no tienen comentarios públicos accesibles en la base de datos.",
        metodo_resumen="extractivo",
        comentarios_clasificados=[],
        aprobacion_pct=0.0,
    )
