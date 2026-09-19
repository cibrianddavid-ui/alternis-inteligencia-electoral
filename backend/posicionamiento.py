"""Recuperación de notas y extracción de asociaciones con evidencia."""
from __future__ import annotations

import os
import json
import re
from collections import Counter, defaultdict
from datetime import date
from typing import Optional
from urllib.parse import urlparse

import requests
import spacy
import trafilatura

STOP = set('de del la las el los a al en con por para y o un una unos unas que se su sus es fue son como desde sobre entre ante tras más muy este esta estos estas año años san luis potosí mexico méxico noticia noticias sitio inicio leer compartir facebook twitter x'.split())
HTTP = requests.Session()
HTTP.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; ElectoralResearch/1.0)'})
_nlp = None

POSITIVAS = set('apoya apoyo avance avances celebra celebró cumple cumplió crecimiento logro logros mejora mejoró positivo positiva reconocimiento reconocen éxito exitoso fortalece inversión acuerdo acuerdos beneficio beneficios'.split())
NEGATIVAS = set('acusa acusado acusación critica crítico crítica cuestiona cuestionó denuncia denunciado polémica conflicto crisis falla fallas incumple incumplimiento corrupción delito investigación protesta rechazo riesgo sanción violencia'.split())


def modelo():
    global _nlp
    if _nlp is None:
        try:
            _nlp = spacy.load('es_core_news_sm')
        except OSError:
            # Respaldo para despliegues donde el wheel del modelo no pudo
            # descargarse. Permite leer oraciones y generar el reporte básico.
            _nlp = spacy.blank('es')
            _nlp.add_pipe('sentencizer')
    return _nlp


def fechas(inicio: date, fin: date) -> str:
    if fin < inicio:
        raise ValueError('La fecha final debe ser igual o posterior a la inicial.')
    return f'cdr:1,cd_min:{inicio:%m/%d/%Y},cd_max:{fin:%m/%d/%Y}'


def _analisis_basico(nombre: str, noticias: list[dict], asociaciones: list[dict]) -> dict:
    fuentes = sorted({n['fuente'] for n in noticias if n.get('fuente')})
    temas = [a['frase'] for a in asociaciones[:6]]
    nombre_tokens = {x.lower() for x in re.findall(r'\w+', nombre)}

    def temas_del_tono(tono: str) -> list[str]:
        contador = Counter()
        for noticia in noticias:
            if noticia.get('tono') != tono:
                continue
            texto = ' '.join([noticia.get('titulo', ''), noticia.get('resumen', '')])
            palabras = {x.lower() for x in re.findall(r'[a-záéíóúüñ]+', texto, re.I)
                        if len(x) >= 4 and x.lower() not in STOP and x.lower() not in nombre_tokens}
            contador.update(palabras)
        return [palabra for palabra, _ in contador.most_common(5)]

    def sintesis_extractiva(tono: str, etiqueta: str) -> str:
        grupo = [n for n in noticias if n.get('tono') == tono]
        if not grupo:
            return f'No se identificaron notas clasificadas como {tono}s.'
        enfoques = []
        for noticia in grupo:
            fragmento = (noticia.get('resumen') or noticia.get('titulo') or '').strip()
            fragmento = re.sub(r'\s+', ' ', fragmento).rstrip(' .')
            if fragmento and fragmento.casefold() not in {x.casefold() for x in enfoques}:
                enfoques.append(fragmento[:260])
            if len(enfoques) == 3:
                break
        temas_tono = temas_del_tono(tono)
        partes = [f'{etiqueta} reúnen {len(grupo)} nota' + ('s.' if len(grupo) != 1 else '.')]
        if enfoques:
            partes.append('Entre los principales contenidos se señala que ' + enfoques[0] + '.')
        if len(enfoques) > 1:
            partes.append('También se aborda que ' + enfoques[1] + '.')
        if len(enfoques) > 2:
            partes.append('Otro enfoque encontrado indica que ' + enfoques[2] + '.')
        if temas_tono:
            partes.append('En conjunto, estos textos se relacionan principalmente con ' +
                          ', '.join(temas_tono[:4]) + '.')
        return ' '.join(partes)

    por_tono = {}
    etiquetas = {'negativo': 'Las notas negativas o críticas',
                 'neutro': 'Las notas neutras', 'positivo': 'Las notas positivas'}
    for tono, etiqueta in etiquetas.items():
        por_tono[tono] = sintesis_extractiva(tono, etiqueta)
    if not noticias:
        resumen = f'No se encontraron notas sobre {nombre} en el periodo seleccionado.'
        lectura = 'No hay evidencia suficiente para describir su cobertura mediática en este periodo.'
    else:
        resumen = (f'En conjunto se localizaron {len(noticias)} notas sobre {nombre} '
                   f'en {len(fuentes)} fuentes. ' +
                   (f'La cobertura se concentra principalmente en {", ".join(temas[:4])}. '
                    f'Estas expresiones son las asociaciones más recurrentes dentro del material consultado.'
                    if temas else 'El material disponible no presenta suficientes temas recurrentes para elaborar una síntesis temática.'))
        lectura = ('El resultado describe frecuencia y encuadres de la cobertura publicada; '
                   'no mide aprobación, intención de voto ni opinión pública.')
    return {'resumen': resumen, 'por_tono': por_tono, 'temas_clave': temas, 'lectura': lectura,
            'metodo_resumen': 'respaldo extractivo',
            'alcance': f'{len(noticias)} notas de {len(fuentes)} fuentes'}


def _analisis_con_modelo(nombre: str, noticias: list[dict], asociaciones: list[dict], client, model_name: str) -> Optional[dict]:
    if client is None or not noticias:
        return None
    evidencia = [{'titulo': n['titulo'], 'resumen_buscador': n['resumen'],
                  'texto_extraido': n.get('_texto', '')[:6000], 'fuente': n['fuente'],
                  'tono_asignado': n.get('tono', 'neutro')}
                 for n in noticias[:20]]
    repetidas = [{'frase': a['frase'], 'noticias': a['noticias'], 'contexto': a['contexto']}
                 for a in asociaciones[:12]]
    instrucciones = (
        'Analiza conjuntamente toda la evidencia periodística proporcionada. Devuelve JSON válido con '
        'resumen (2 o 3 oraciones que sinteticen qué se dice en general, no estadísticas ni una lista de notas), '
        'por_tono (objeto con las claves negativo, neutro y positivo; en cada valor resume en 1 a 3 oraciones '
        'qué dicen en conjunto las notas de ese tono, sus temas y señalamientos principales; si no hay notas '
        'de una categoría indícalo), temas_clave (lista de hasta 6 frases) y lectura (2 a 3 oraciones sobre '
        'qué representan los encuadres). No te limites a enumerar palabras frecuentes. No inventes hechos, no '
        'determines verdad o falsedad, no infieras opinión pública, intención de voto ni rasgos personales. '
        'Si hay poca evidencia, dilo. Evita recomendaciones electorales o persuasivas.'
    )
    mensajes = [{'role': 'system', 'content': instrucciones},
                {'role': 'user', 'content': json.dumps({'persona': nombre, 'notas': evidencia,
                                                       'asociaciones': repetidas}, ensure_ascii=False)}]
    # Algunos modelos o proveedores no aceptan response_format. Se intenta
    # primero en modo JSON y después como texto, extrayendo el objeto aunque
    # venga dentro de un bloque ```json ... ```.
    for forzar_json in (True, False):
        try:
            opciones = {'model': model_name, 'temperature': 0.15,
                        'max_tokens': 2200, 'messages': mensajes}
            if forzar_json:
                opciones['response_format'] = {'type': 'json_object'}
            respuesta = client.chat.completions.create(**opciones)
            contenido = (respuesta.choices[0].message.content or '').strip()
            inicio, fin = contenido.find('{'), contenido.rfind('}')
            if inicio < 0 or fin <= inicio:
                continue
            datos = json.loads(contenido[inicio:fin + 1])
            if not all(isinstance(datos.get(k), t) for k, t in
                       [('resumen', str), ('por_tono', dict), ('temas_clave', list), ('lectura', str)]):
                continue
            if not all(isinstance(datos['por_tono'].get(k), str)
                       for k in ('negativo', 'neutro', 'positivo')):
                continue
            datos['temas_clave'] = [str(x)[:120] for x in datos['temas_clave'][:6]]
            datos['metodo_resumen'] = 'modelo de lenguaje'
            datos['alcance'] = f"{len(noticias)} notas de {len({n['fuente'] for n in noticias})} fuentes"
            return datos
        except Exception:
            continue
    return None


def _tono_lexico(texto: str) -> str:
    palabras = [x.lower() for x in re.findall(r'[a-záéíóúüñ]+', texto, re.I)]
    positivos = sum(x in POSITIVAS for x in palabras)
    negativos = sum(x in NEGATIVAS for x in palabras)
    if negativos >= positivos + 2:
        return 'negativo'
    if positivos >= negativos + 2:
        return 'positivo'
    return 'neutro'


def _clasificar_tonos(nombre: str, noticias: list[dict], client, model_name: str) -> dict:
    tonos = {i: _tono_lexico(' '.join([n.get('titulo', ''), n.get('resumen', ''),
                                       n.get('_texto', '')[:4000]]))
             for i, n in enumerate(noticias)}
    metodo = 'respaldo léxico'
    if client is not None and noticias:
        muestras = [{'indice': i, 'titulo': n.get('titulo', ''),
                     'resumen': n.get('resumen', ''), 'texto': n.get('_texto', '')[:4000]}
                    for i, n in enumerate(noticias[:30])]
        instrucciones = (
            'Clasifica el tono de cada nota hacia la persona indicada, no el tono general del suceso. '
            'Usa negativo si predomina crítica, cuestionamiento, acusación o perjuicio reputacional; '
            'positivo si predomina reconocimiento, apoyo, logro o beneficio; neutro si es principalmente '
            'informativa, equilibrada o no hay evidencia suficiente. No evalúes la veracidad. Devuelve '
            'solo JSON válido: {"clasificaciones":[{"indice":0,"tono":"negativo|neutro|positivo"}]}.'
        )
        try:
            respuesta = client.chat.completions.create(
                model=model_name, temperature=0, max_tokens=1000,
                response_format={'type': 'json_object'},
                messages=[{'role': 'system', 'content': instrucciones},
                          {'role': 'user', 'content': json.dumps({'persona': nombre, 'notas': muestras},
                                                               ensure_ascii=False)}])
            datos = json.loads(respuesta.choices[0].message.content or '{}')
            validas = 0
            for item in datos.get('clasificaciones', []):
                indice, tono = item.get('indice'), str(item.get('tono', '')).lower()
                if isinstance(indice, int) and indice in tonos and tono in {'negativo', 'neutro', 'positivo'}:
                    tonos[indice] = tono
                    validas += 1
            if validas:
                metodo = 'modelo de lenguaje'
        except Exception:
            pass

    conteos = Counter(tonos.values())
    total = len(noticias)
    negativo = round(100 * conteos['negativo'] / total, 1) if total else 0.0
    positivo = round(100 * conteos['positivo'] / total, 1) if total else 0.0
    neutro = round(100 - negativo - positivo, 1) if total else 0.0
    for indice, noticia in enumerate(noticias):
        noticia['tono'] = tonos.get(indice, 'neutro')
    return {
        'total': total, 'metodo': metodo,
        'negativo': {'notas': conteos['negativo'], 'porcentaje': negativo},
        'neutro': {'notas': conteos['neutro'], 'porcentaje': neutro},
        'positivo': {'notas': conteos['positivo'], 'porcentaje': positivo},
        'aviso': 'El tono describe el encuadre de la nota hacia la persona; no verifica los hechos ni mide opinión pública.',
    }


def buscar(nombre: str, inicio: date, fin: date, paginas: int = 2, client=None,
           model_name: str = 'openai/gpt-oss-120b') -> dict:
    api_key = os.getenv('SERPAPI_KEY', '').strip()
    if not api_key:
        raise RuntimeError('Configura SERPAPI_KEY en .env para analizar noticias.')
    nlp = modelo()
    vistos = set()
    noticias = []
    for pagina in range(paginas):
        respuesta = HTTP.get('https://serpapi.com/search.json', params={
            'api_key': api_key, 'engine': 'google', 'q': f'"{nombre}"',
            'tbm': 'nws', 'tbs': fechas(inicio, fin), 'hl': 'es', 'gl': 'mx',
            'num': 10, 'start': pagina * 10,
        }, timeout=35)
        respuesta.raise_for_status()
        datos = respuesta.json()
        if datos.get('error'):
            raise RuntimeError(f'SerpAPI: {datos["error"]}')
        resultados = datos.get('news_results') or datos.get('organic_results') or []
        for item in resultados:
            url = item.get('link', '')
            if not url.startswith(('https://', 'http://')) or url in vistos:
                continue
            vistos.add(url)
            fuente = item.get('source') or urlparse(url).netloc
            if isinstance(fuente, dict):
                fuente = fuente.get('name', '')
            titulo = item.get('title') or ''
            resumen = item.get('snippet') or ''
            texto = ''
            try:
                pagina_web = HTTP.get(url, timeout=12)
                pagina_web.raise_for_status()
                if 'html' in pagina_web.headers.get('Content-Type', '').lower():
                    texto = trafilatura.extract(pagina_web.text, favor_precision=True, include_comments=False) or ''
            except (requests.RequestException, ValueError):
                pass
            material = ' '.join([titulo, resumen, texto])
            if not re.search(re.escape(nombre), material, re.I):
                continue
            noticias.append({
                'titulo': titulo, 'resumen': resumen, 'fuente': str(fuente),
                'fecha_buscador': item.get('date') or '', 'url': url,
                'texto_disponible': bool(texto), '_texto': material[:80000],
            })
        if len(resultados) < 10:
            break

    grupos = defaultdict(lambda: {'noticias': set(), 'fuentes': set(), 'enlaces': set(), 'ejemplo': '', 'frase': ''})
    palabras_por_noticia = defaultdict(set)
    nombre_tokens = {x.lower() for x in re.findall(r'\w+', nombre)}
    for indice, noticia in enumerate(noticias):
        doc = nlp(noticia['_texto'])
        # La nube no depende del parser: cuenta en cuántas notas aparece cada
        # palabra relevante, tanto si se cargó el modelo completo como si no.
        for token in doc:
            palabra = token.text.lower()
            if (token.is_alpha and len(palabra) >= 4 and palabra not in STOP
                    and palabra not in nombre_tokens and not token.is_stop):
                palabras_por_noticia[palabra].add(indice)
        # Solo oraciones que mencionan el nombre completo; así evitamos
        # atribuirle asuntos que aparecen en la misma nota sobre otra persona.
        for oracion in doc.sents:
            if not re.search(re.escape(nombre), oracion.text, re.I):
                continue
            # noun_chunks requiere el parser del modelo entrenado. Si estamos
            # usando el respaldo en blanco, se omite y el reporte sigue vivo.
            bloques = list(oracion.noun_chunks) if 'parser' in nlp.pipe_names else []
            for bloque in bloques:
                tokens = [t for t in bloque if not t.is_punct and not t.is_space]
                while tokens and (tokens[0].is_stop or tokens[0].lower_ in STOP):
                    tokens.pop(0)
                while tokens and (tokens[-1].is_stop or tokens[-1].lower_ in STOP):
                    tokens.pop()
                if not 2 <= len(tokens) <= 4 or not any(t.pos_ in {'NOUN', 'PROPN'} for t in tokens):
                    continue
                frase = ' '.join(t.text for t in tokens)
                if nombre.lower() in frase.lower() or all(t.is_stop for t in tokens):
                    continue
                if sum(t.is_alpha for t in tokens) < 2:
                    continue
                clave = ' '.join(t.lemma_.lower() for t in tokens)
                grupo = grupos[clave]
                grupo['frase'] = frase
                grupo['noticias'].add(indice)
                grupo['fuentes'].add(noticia['fuente'])
                grupo['enlaces'].add(noticia['url'])
                if not grupo['ejemplo']:
                    grupo['ejemplo'] = oracion.text[:300].strip()

    asociaciones = sorted(({
        'frase': x['frase'], 'noticias': len(x['noticias']),
        'fuentes': len(x['fuentes']), 'contexto': x['ejemplo'],
        'enlaces': sorted(x['enlaces'])[:5],
    } for x in grupos.values() if len(x['noticias']) >= 2),
        key=lambda x: (x['noticias'], x['fuentes']), reverse=True)[:20]
    # Completa la nube con palabras frecuentes aunque las frases nominales no
    # se repitan literalmente. Así siempre hay visualización si existen notas.
    existentes = {a['frase'].casefold() for a in asociaciones}
    minimo = 2 if len(noticias) >= 2 else 1
    for palabra, indices in sorted(palabras_por_noticia.items(),
                                   key=lambda x: (-len(x[1]), x[0])):
        if len(indices) < minimo or palabra.casefold() in existentes:
            continue
        fuentes = {noticias[i]['fuente'] for i in indices}
        enlaces = [noticias[i]['url'] for i in sorted(indices)[:5]]
        asociaciones.append({'frase': palabra, 'noticias': len(indices),
                              'fuentes': len(fuentes),
                              'contexto': 'Palabra recurrente en el conjunto de notas consultadas.',
                              'enlaces': enlaces})
        existentes.add(palabra.casefold())
        if len(asociaciones) == 30:
            break
    asociaciones = sorted(asociaciones, key=lambda x: (x['noticias'], x['fuentes']), reverse=True)[:30]
    semaforo = _clasificar_tonos(nombre, noticias, client, model_name)
    analisis = (_analisis_con_modelo(nombre, noticias, asociaciones, client, model_name)
                or _analisis_basico(nombre, noticias, asociaciones))
    for noticia in noticias:
        noticia.pop('_texto', None)
    return {'nombre': nombre, 'fecha_inicio': inicio.isoformat(), 'fecha_fin': fin.isoformat(),
            'noticias': noticias, 'asociaciones': asociaciones,
            'analisis': analisis, 'semaforo': semaforo,
            'aviso': 'La fecha del buscador puede diferir de la publicación original. Una asociación textual no verifica una afirmación.'}
