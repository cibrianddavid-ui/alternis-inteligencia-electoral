"""Recuperación de notas y extracción de asociaciones con evidencia."""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from datetime import date
from urllib.parse import urlparse

import requests
import spacy
import trafilatura

STOP = set('de del la las el los a al en con por para y o un una unos unas que se su sus es fue son como desde sobre entre ante tras más muy este esta estos estas año años san luis potosí mexico méxico noticia noticias sitio inicio leer compartir facebook twitter x'.split())
HTTP = requests.Session()
HTTP.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; ElectoralResearch/1.0)'})
_nlp = None


def modelo():
    global _nlp
    if _nlp is None:
        try:
            _nlp = spacy.load('es_core_news_sm')
        except OSError as exc:
            raise RuntimeError('Falta el modelo de español. Ejecuta: python -m spacy download es_core_news_sm') from exc
    return _nlp


def fechas(inicio: date, fin: date) -> str:
    if fin < inicio:
        raise ValueError('La fecha final debe ser igual o posterior a la inicial.')
    return f'cdr:1,cd_min:{inicio:%m/%d/%Y},cd_max:{fin:%m/%d/%Y}'


def buscar(nombre: str, inicio: date, fin: date, paginas: int = 2) -> dict:
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
    for indice, noticia in enumerate(noticias):
        doc = nlp(noticia['_texto'])
        # Solo oraciones que mencionan el nombre completo; así evitamos
        # atribuirle asuntos que aparecen en la misma nota sobre otra persona.
        for oracion in doc.sents:
            if not re.search(re.escape(nombre), oracion.text, re.I):
                continue
            for bloque in oracion.noun_chunks:
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
    for noticia in noticias:
        noticia.pop('_texto')
    return {'nombre': nombre, 'fecha_inicio': inicio.isoformat(), 'fecha_fin': fin.isoformat(),
            'noticias': noticias, 'asociaciones': asociaciones,
            'aviso': 'La fecha del buscador puede diferir de la publicación original. Una asociación textual no verifica una afirmación.'}
