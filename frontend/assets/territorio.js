// ---------------------------------------------------------------------------
// Perfil territorial: ficha de un estado, municipio, distrito o sección.
// Conecta con el asistente, los discursos y la campaña.
// ---------------------------------------------------------------------------
let territoryCatalog = null;
window.getTerritoryCatalog = async () => {
  if (!territoryCatalog) {
    const response = await fetch('/api/territorio/catalogo');
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo cargar el catálogo territorial.');
    territoryCatalog = data;
  }
  return territoryCatalog;
};

const territoryForm = document.querySelector('#territory-form');
const territoryLevel = document.querySelector('#territory-level');
const territoryUnit = document.querySelector('#territory-unit');
const territoryUnits = document.querySelector('#territory-units');
const territoryYear = document.querySelector('#territory-year');
const territoryStatus = document.querySelector('#territory-status');
const territoryResult = document.querySelector('#territory-result');
let currentProfile = null;
let selectedElection = null;
let territoryReady = false;

// Nombre legible de una unidad: "Distrito local 8", "Sección 101", "Matehuala".
function territoryName(nivel, unidad) {
  if (nivel === 'id_distrito_local' || nivel === 'id_distrito_federal') return `${DIMENSION_LABELS[nivel]} ${unidad}`;
  if (nivel === 'seccion') return `Sección ${unidad}`;
  return friendlyValue(nivel, unidad);
}

const pct = (value, digits = 1) => (value === null || value === undefined ? '—' : `${Number(value).toLocaleString('es-MX', { minimumFractionDigits: digits, maximumFractionDigits: digits })} %`);
const int = (value) => (value === null || value === undefined ? '—' : Number(value).toLocaleString('es-MX'));
const partyLabel = (party) => String(party).replaceAll('_', '-');

function fillUnits() {
  const level = territoryCatalog?.niveles.find(item => item.valor === territoryLevel.value);
  territoryUnits.innerHTML = (level?.unidades || []).map(unit => `<option value="${escapeHtml(unit)}"></option>`).join('');
}

async function initTerritory() {
  if (territoryReady) return;
  try {
    const catalog = await window.getTerritoryCatalog();
    territoryLevel.innerHTML = catalog.niveles.map(level => `<option value="${escapeHtml(level.valor)}">${escapeHtml(level.etiqueta)}</option>`).join('');
    territoryLevel.value = catalog.niveles.some(level => level.valor === 'municipio') ? 'municipio' : catalog.niveles[0]?.valor;
    territoryYear.innerHTML = catalog.anios.map(year => `<option value="${escapeHtml(year)}">${escapeHtml(year)}</option>`).join('');
    if (catalog.anios.length) territoryYear.value = catalog.anios.at(-1);
    document.querySelector('#territory-year-field').hidden = catalog.anios.length < 2;
    fillUnits();
    territoryReady = true;
    territoryStatus.textContent = '';
  } catch (error) { territoryStatus.textContent = error.message; }
}

territoryLevel.addEventListener('change', () => { territoryUnit.value = ''; fillUnits(); });

function profileHash(nivel, unidad, anio) {
  return `#territorio/${nivel}/${encodeURIComponent(unidad)}${anio ? `/${anio}` : ''}`;
}

async function showProfile(nivel, unidad, anio) {
  await initTerritory();
  if (!territoryReady) return;
  territoryLevel.value = nivel; fillUnits();
  territoryUnit.value = unidad;
  if (anio) territoryYear.value = anio;
  territoryStatus.textContent = 'Calculando el perfil…';
  territoryResult.innerHTML = '';
  try {
    const query = new URLSearchParams({ nivel, unidad, ...(anio ? { anio } : {}) });
    const [response, campaignResponse] = await Promise.all([fetch(`/api/territorio/perfil?${query}`), fetch('/api/campania')]);
    const profile = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof profile.detail === 'string' ? profile.detail : 'No se pudo calcular el perfil.');
    const linked = campaignResponse.ok ? (await campaignResponse.json()).metas.filter(goal => goal.territorio_tipo === nivel && String(goal.territorio_valor) === String(unidad)) : [];
    currentProfile = profile;
    selectedElection = profile.principal || profile.elecciones[0]?.tipo || null;
    history.replaceState(null, '', profileHash(nivel, unidad, profile.anio));
    renderProfile(profile, linked);
    window.TerritorioActivo?.set({ nivel, unidad, anio: profile.anio }, { source: 'perfil' });
    territoryStatus.textContent = '';
  } catch (error) {
    currentProfile = null;
    territoryStatus.textContent = error.message;
  }
}
window.showTerritoryProfile = showProfile;

function kpi(label, value, note = '', tone = '') {
  return `<article class="tp-kpi ${tone}"><span>${label}</span><strong>${value}</strong>${note ? `<small>${note}</small>` : ''}</article>`;
}

function competitionBadge(text) {
  const tone = { 'Empate exacto': 'tie', 'Muy reñida': 'hot', 'Competida': 'warm', 'Ventaja clara': 'mild', 'Ventaja amplia': 'cool' }[text] || 'mild';
  return `<span class="tp-badge ${tone}">${escapeHtml(text)}</span>`;
}

function renderProfile(profile, linkedGoals) {
  const name = territoryName(profile.nivel, profile.unidad);
  const summary = profile.resumen;
  const winner = summary?.ganador;
  const sectionsNote = profile.secciones ? `${int(profile.secciones)} ${profile.secciones === 1 ? 'sección' : 'secciones'}` : '';
  const canWrite = hasRole('coordinador');
  const quality = profile.elecciones.filter(item => item.calidad && item.calidad.cuadra === false);

  territoryResult.innerHTML = `
    <article class="tp-card">
      <header class="tp-head">
        <div><p class="eyebrow">${escapeHtml(profile.nivel_etiqueta.toUpperCase())}${profile.anio ? ` · ${escapeHtml(profile.anio)}` : ''}</p><h2>${escapeHtml(name)}</h2><p class="tp-sub">${escapeHtml(sectionsNote)}</p></div>
        <div class="tp-actions">
          <button type="button" class="secondary-button" data-tp="ask">Preguntar al asistente</button>
          ${canWrite ? '<button type="button" class="secondary-button" data-tp="speech">Redactar discurso con estos datos</button>' : ''}
          <button type="button" class="secondary-button" data-tp="link">Copiar enlace</button>
        </div>
      </header>
      ${summary ? `<div class="tp-kpis">
        ${kpi(`Ganador · ${escapeHtml(summary.etiqueta)}`, `${swatchHtml(winner.partido)}${escapeHtml(partyLabel(winner.partido))}`, summary.empate ? 'Empate exacto en votos' : pct(winner.porcentaje))}
        ${kpi('Margen sobre el segundo lugar', summary.margen_votos === null ? '—' : `${int(summary.margen_votos)} votos`, `${pct(summary.margen_pp)} · ${competitionBadge(summary.competitividad)}`)}
        ${kpi('Participación', pct(summary.participacion_pct), `Lista nominal: ${int(profile.elecciones[0].lista_nominal)}`)}
      </div>` : ''}
      ${quality.length ? `<p class="tp-quality" role="note"><strong>Nota sobre los datos:</strong> ${quality.map(item => `${escapeHtml(item.etiqueta)}: ${escapeHtml(item.calidad.nota)}`).join(' ')}</p>` : ''}
      <div class="tp-elections" role="tablist" aria-label="Elección">${profile.elecciones.map(item => `<button type="button" role="tab" class="tp-tab${item.tipo === selectedElection ? ' active' : ''}" data-election="${escapeHtml(item.tipo)}" aria-selected="${item.tipo === selectedElection}">${escapeHtml(item.etiqueta)}</button>`).join('')}</div>
      <div id="tp-election-body"></div>
    </article>
    ${renderSectionsTable(profile)}
    ${renderMunicipalities(profile)}
    ${renderHistory(profile)}
    ${renderLinkedGoals(profile, linkedGoals, canWrite)}
    <div id="tp-analysis-host"></div>`;
  drawElection();
  loadTerritoryAnalysis(profile);
}

function drawElection() {
  const election = currentProfile.elecciones.find(item => item.tipo === selectedElection);
  const host = document.querySelector('#tp-election-body');
  if (!election || !host) return;
  const top = election.ranking.slice(0, 10);
  host.innerHTML = `
    <div class="tp-election-grid">
      <div id="tp-chart" class="tp-chart" role="img" aria-label="Porcentaje de votos por partido en ${escapeHtml(election.etiqueta)}"></div>
      <div class="tp-table-wrap"><table class="tp-table"><thead><tr><th>#</th><th>Opción</th><th>Votos</th><th>%</th></tr></thead><tbody>
        ${top.map((row, index) => `<tr><td>${index + 1}</td><td>${swatchHtml(row.partido)}${escapeHtml(partyLabel(row.partido))}</td><td>${int(row.votos)}</td><td>${pct(row.porcentaje)}</td></tr>`).join('')}
        <tr class="tp-total"><td></td><td>Nulos y no registrados</td><td>${int(election.nulos + election.no_registradas)}</td><td>${election.total_calculado ? pct((election.nulos + election.no_registradas) * 100 / election.total_calculado) : '—'}</td></tr>
      </tbody></table></div>
    </div>
    <p class="tp-meta">Participación: <strong>${pct(election.participacion_pct)}</strong> · Votos emitidos: ${int(election.votos_emitidos)} · Lista nominal: ${int(election.lista_nominal)} · ${competitionBadge(election.competitividad)}</p>`;
  const chart = document.querySelector('#tp-chart');
  if (!window.Plotly) { chart.innerHTML = '<p class="weekly-note">No se pudo cargar la librería de gráficas. Verifica tu conexión a internet.</p>'; return; }
  const ordered = [...top].reverse();
  Plotly.react(chart, [{
    type: 'bar', orientation: 'h', x: ordered.map(row => row.porcentaje), y: ordered.map(row => partyLabel(row.partido)),
    text: ordered.map(row => pct(row.porcentaje)), textposition: 'outside', cliponaxis: false,
    customdata: ordered.map(row => row.votos), hovertemplate: '%{y}<br>%{x:.2f} % · %{customdata:,} votos<extra></extra>',
    marker: { color: ordered.map(row => seriesStyle(row.partido).colors[0]) },
  }], {
    height: Math.max(220, 42 * ordered.length + 50), margin: { l: 120, r: 60, t: 8, b: 34 },
    paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff', font: { family: 'Inter, system-ui, sans-serif', color: '#364155', size: 12 },
    xaxis: { title: '% de los votos', gridcolor: '#eef1f5', ticksuffix: ' %', rangemode: 'tozero' }, yaxis: { automargin: true },
    hoverlabel: { bgcolor: '#111b2b', font: { color: 'white' } },
  }, { responsive: true, displaylogo: false, displayModeBar: false });
}

function renderSectionsTable(profile) {
  const rows = profile.secciones_competidas || [];
  if (!rows.length) return '';
  return `<article class="tp-card"><h3>Secciones más competidas</h3><p class="tp-note">Diferencia más pequeña entre el primero y el segundo lugar (${escapeHtml(profile.elecciones.find(item => item.tipo === profile.principal)?.etiqueta || '')}). Solo secciones con al menos 50 votos emitidos.</p>
    <div class="tp-table-wrap"><table class="tp-table"><thead><tr><th>Sección</th><th>Primero</th><th>Segundo</th><th>Diferencia</th><th>Puntos</th><th>Votos emitidos</th></tr></thead><tbody>
    ${rows.map(row => `<tr><td><button class="tp-link" type="button" data-open="seccion" data-value="${escapeHtml(row.seccion)}">${escapeHtml(row.seccion)}</button></td><td>${swatchHtml(row.ganador)}${escapeHtml(partyLabel(row.ganador))}</td><td>${swatchHtml(row.segundo)}${escapeHtml(partyLabel(row.segundo))}</td><td>${row.empate ? '<span class="tp-badge tie">Empate</span>' : `${int(row.margen_votos)} votos`}</td><td>${pct(row.margen_pp, 2)}</td><td>${int(row.votos_emitidos)}</td></tr>`).join('')}
    </tbody></table></div></article>`;
}

function renderMunicipalities(profile) {
  const rows = profile.municipios || [];
  if (!rows.length) return '';
  return `<article class="tp-card"><h3>Municipios que lo integran</h3><p class="tp-note">Ganador de ${escapeHtml(profile.elecciones.find(item => item.tipo === profile.principal)?.etiqueta || 'la elección principal')}, ordenados por votos emitidos.</p>
    <div class="tp-table-wrap"><table class="tp-table"><thead><tr><th>Municipio</th><th>Ganador</th><th>Votos</th><th>%</th><th>Votos emitidos</th></tr></thead><tbody>
    ${rows.slice(0, 20).map(row => `<tr><td><button class="tp-link" type="button" data-open="municipio" data-value="${escapeHtml(row.municipio)}">${escapeHtml(friendlyValue('municipio', row.municipio))}</button></td><td>${swatchHtml(row.ganador)}${escapeHtml(partyLabel(row.ganador))}</td><td>${int(row.votos_ganador)}</td><td>${pct(row.porcentaje)}</td><td>${int(row.votos_emitidos)}</td></tr>`).join('')}
    </tbody></table></div>${rows.length > 20 ? `<p class="tp-note">Se muestran 20 de ${rows.length} municipios.</p>` : ''}</article>`;
}

function renderHistory(profile) {
  if (!profile.historia?.length) return '';
  return `<article class="tp-card"><h3>Evolución</h3><div class="tp-table-wrap"><table class="tp-table"><thead><tr><th>Año</th><th>Ganador</th><th>Margen (puntos)</th><th>Participación</th></tr></thead><tbody>
    ${profile.historia.map(row => `<tr><td>${escapeHtml(row.anio)}</td><td>${row.ganador ? swatchHtml(row.ganador.partido) + escapeHtml(partyLabel(row.ganador.partido)) : '—'}</td><td>${pct(row.margen_pp, 2)}</td><td>${pct(row.participacion_pct)}</td></tr>`).join('')}</tbody></table></div></article>`;
}

function renderLinkedGoals(profile, goals, canWrite) {
  const defaultTitle = { seccion: 'Reforzar la presencia en', municipio: 'Recorrer', id_distrito_local: 'Recorrer las secciones del', id_distrito_federal: 'Recorrer las secciones del', entidad: 'Recorrer' }[profile.nivel] || 'Trabajar en';
  const title = `${defaultTitle} ${territoryName(profile.nivel, profile.unidad).replace(/^./, c => c.toLowerCase())}`.replace(/\s+/g, ' ');
  return `<article class="tp-card"><h3>Campaña en este territorio</h3>
    <div id="tp-goals">${goals.length ? goals.map(goal => { const p = goalProgress(goal); return `<div class="progress-row"><div><strong>${escapeHtml(goal.titulo)}</strong><span>${escapeHtml(p.label)}${goal.fecha_limite ? ` · vence ${escapeHtml(goal.fecha_limite)}` : ''}</span></div><b>${p.percent}%</b><div class="progress-track"><i style="width:${p.percent}%"></i></div></div>`; }).join('') : '<p class="tp-note">Todavía no hay metas ligadas a este territorio.</p>'}</div>
    ${canWrite ? `<form id="tp-goal-form" class="tp-goal-form"><h4>Crear una meta aquí</h4>
      <input name="titulo" required maxlength="180" value="${escapeHtml(title.charAt(0).toUpperCase() + title.slice(1))}" aria-label="Título de la meta" />
      <input name="indicador" maxlength="180" value="${profile.nivel === 'seccion' ? 'representantes acreditados' : 'secciones visitadas'}" aria-label="Indicador" />
      <input name="objetivo" type="number" min="0" step="any" value="${profile.nivel === 'seccion' ? '' : (profile.secciones || '')}" placeholder="Objetivo" aria-label="Objetivo" />
      <input name="fecha_limite" type="date" aria-label="Fecha límite" />
      <button class="primary-button" type="submit">Crear meta</button></form>` : ''}
  </article>`;
}

// ------------------------------------------------------------------ análisis del territorio
// Voto diferenciado y evolución. Las cifras las calcula el servidor; el texto lo redacta el modelo y se verifica.
const AUTO_ANALYSIS = false; // true: redacta el análisis al abrir cada perfil (más espera y más uso del modelo)
let analysisToken = 0;
let lastAnalysisText = '';

const optionSwatch = (bloc) => `<span class="swatch" style="background:${escapeHtml(bloc.color)}" aria-hidden="true"></span>`;
const oneDecimal = (value) => Number(value).toLocaleString('es-MX', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const signedPoints = (value) => (value === null || value === undefined ? '—' : `${value > 0.05 ? '+' : value < -0.05 ? '−' : ''}${oneDecimal(Math.abs(value))}`);
const deltaClass = (value) => (value === null || value === undefined || Math.abs(value) < 0.1 ? '' : value > 0 ? 'tp-up' : 'tp-down');
const optionLabel = (data, key) => data.partidos.find(item => item.clave === key)?.etiqueta || key;

function renderAnalysisCard(data) {
  const ref = escapeHtml(data.referencia_etiqueta);
  const top = data.partidos;
  const delta = data.diferencias.length ? `
    <h4>Diferencia frente a ${ref}</h4>
    <p class="tp-note">Puntos porcentuales: con signo más, esa opción tuvo mayor porcentaje que en ${ref}. El índice resume diferencias de porcentajes entre opciones; 0 significa idéntico. No mide transferencias individuales de votos.</p>
    <div class="tp-table-wrap"><table class="tp-table"><thead><tr><th>Elección</th>${top.map(item => `<th>${optionSwatch(item)}${escapeHtml(item.etiqueta)}</th>`).join('')}<th>Índice</th><th>Participación</th></tr></thead><tbody>
    ${data.diferencias.map(row => `<tr><td>${escapeHtml(row.etiqueta)}</td>${top.map(item => `<td class="${deltaClass(row.por_partido[item.clave])}">${signedPoints(row.por_partido[item.clave])}</td>`).join('')}<td><strong>${oneDecimal(row.indice)}</strong></td><td class="${deltaClass(row.participacion_pp)}">${signedPoints(row.participacion_pp)}</td></tr>`).join('')}
    </tbody></table></div>` : '';

  const s = data.secciones;
  const sections = s ? `
    <h4>Secciones donde cambia el ganador</h4>
    <p class="tp-note">De ${int(s.evaluadas)} secciones con al menos ${s.minimo_votos} votos, en ${int(s.iguales_en_todas)} ganó la misma opción registrada en todas las elecciones.</p>
    <div class="tp-table-wrap"><table class="tp-table tp-wrap"><thead><tr><th>Elección</th><th>Con otro ganador</th><th>Cambio más frecuente</th><th>Secciones grandes con cambio</th></tr></thead><tbody>
    ${s.cambios.map(item => { const flow = item.flujos[0]; return `<tr><td>${escapeHtml(item.etiqueta)}</td><td>${int(item.distintas)} de ${int(item.evaluadas)} (${pct(item.pct_distintas)})</td>
      <td>${flow ? `En ${int(flow.secciones)} secciones ganó ${escapeHtml(optionLabel(data, flow.en_referencia))} en ${ref} y ${escapeHtml(optionLabel(data, flow.en_otra))} aquí` : '—'}</td>
      <td>${item.ejemplos.map(e => `<button class="tp-link" type="button" data-open="seccion" data-value="${escapeHtml(e.seccion)}">${escapeHtml(e.seccion)}</button>`).join(', ') || '—'}</td></tr>`; }).join('')}
    </tbody></table></div>` : '';

  const history = data.historico ? `
    <h4>Evolución entre años</h4>
    <div class="tp-table-wrap"><table class="tp-table"><thead><tr><th>Elección</th><th>Año</th><th>Ganó</th><th>% del ganador</th><th>Participación</th></tr></thead><tbody>
    ${Object.values(data.historico).flatMap(h => h.anios.map(row => `<tr><td>${escapeHtml(h.etiqueta)}</td><td>${escapeHtml(row.anio)}</td><td>${escapeHtml(optionLabel(data, row.ganador))}</td><td>${pct(row.pct[row.ganador])}</td><td>${pct(row.participacion_pct)}</td></tr>`)).join('')}
    </tbody></table></div><p class="tp-note">Las coaliciones y la distritación pueden cambiar entre años: verifica que las opciones y territorios sean comparables.</p>`
    : `<h4>Evolución entre años</h4><p class="tp-note">La base solo tiene datos de ${escapeHtml(data.anio || 'un año')}. Cuando se carguen elecciones anteriores, la evolución aparecerá aquí y en el análisis escrito.</p>`;

  const notices = data.avisos.length ? `<p class="tp-quality" role="note"><strong>Nota sobre los datos:</strong> ${data.avisos.map(escapeHtml).join(' ')}</p>` : '';
  return `<article class="tp-card tp-analysis">
    <h3>Análisis del territorio</h3>
    <p class="tp-note">Cómo votó este territorio en cada elección${data.anio ? ` de ${escapeHtml(data.anio)}` : ''}. Cada partido se muestra por separado. Las coaliciones que existen en la base se conservan como opciones independientes.</p>
    ${notices}
    <h4>Voto por partido u opción registrada en cada elección</h4>
    <div id="tp-party-chart" class="tp-chart" role="img" aria-label="Porcentaje de voto por partido u opción registrada en cada elección"></div>
    ${delta}${sections}${history}
    <section class="tp-ai" aria-labelledby="tp-ai-title">
      <h4 id="tp-ai-title">Análisis escrito</h4>
      <p class="tp-note">Hallazgos, voto diferenciado y qué vigilar, redactados con estas mismas cifras. El texto se revisa contra los datos, pero una persona debe leerlo antes de usarlo.</p>
      <button id="tp-ai-btn" class="primary-button" type="button" data-tp="ai">Redactar análisis</button>
      <div id="tp-ai-out" aria-live="polite"></div>
    </section>
  </article>`;
}

function drawPartyChart(data) {
  const host = document.querySelector('#tp-party-chart');
  if (!host) return;
  if (!window.Plotly) { host.innerHTML = '<p class="weekly-note">No se pudo cargar la librería de gráficas. Verifica tu conexión a internet.</p>'; return; }
  const elections = [...data.elecciones].reverse();
  const traces = data.partidos.filter(item => elections.some(e => (e.pct[item.clave] || 0) > 0)).map(item => {
    const values = elections.map(e => e.pct[item.clave] || 0);
    return {
      type: 'bar', orientation: 'h', name: item.etiqueta, x: values, y: elections.map(e => e.etiqueta),
      marker: { color: item.color }, text: values.map(v => (v >= 8 ? `${Math.round(v)} %` : '')), textposition: 'inside',
      insidetextanchor: 'middle', textfont: { color: '#ffffff', size: 12 },
      hovertemplate: `${item.etiqueta}<br>%{y}: %{x:.1f} %<extra></extra>`,
    };
  });
  Plotly.react(host, traces, {
    barmode: 'stack', height: Math.max(230, 54 * elections.length + 110), margin: { l: 130, r: 16, t: 8, b: 70 },
    paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff', font: { family: 'Schibsted Grotesk, system-ui, sans-serif', color: '#364155', size: 12 },
    xaxis: { range: [0, 100], ticksuffix: ' %', gridcolor: '#eef1f5' }, yaxis: { automargin: true },
    legend: { orientation: 'h', y: -0.22 }, hoverlabel: { bgcolor: '#1b1b26', font: { color: 'white' } },
  }, { responsive: true, displaylogo: false, displayModeBar: false });
}

async function loadTerritoryAnalysis(profile) {
  const host = document.querySelector('#tp-analysis-host');
  if (!host) return;
  const token = ++analysisToken;
  lastAnalysisText = '';
  host.innerHTML = '<article class="tp-card"><h3>Análisis del territorio</h3><p class="tp-note" role="status">Calculando la comparación entre elecciones…</p></article>';
  try {
    const query = new URLSearchParams({ nivel: profile.nivel, unidad: profile.unidad, ...(profile.anio ? { anio: profile.anio } : {}) });
    const response = await fetch(`/api/territorio/comparativo?${query}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo calcular el análisis.');
    if (token !== analysisToken) return;
    host.innerHTML = renderAnalysisCard(data);
    drawPartyChart(data);
    if (AUTO_ANALYSIS) requestAnalysis(false);
  } catch (error) {
    if (token === analysisToken) host.innerHTML = `<article class="tp-card"><h3>Análisis del territorio</h3><p class="tp-note">${escapeHtml(error.message)}</p></article>`;
  }
}

function renderAnalysisText(data) {
  const check = data.verificacion;
  const verdict = check.no_respaldadas.length
    ? `<p class="tp-quality" role="note"><strong>Revisa estas cifras:</strong> ${check.no_respaldadas.map(item => `«${escapeHtml(item)}»`).join(', ')}. No coinciden con los datos calculados para este territorio.</p>`
    : check.respaldadas
      ? `<p class="tp-note">Las ${check.respaldadas} cifras verificables del texto coinciden con los datos calculados.</p>`
      : '<p class="tp-note">El texto no incluye cifras verificables.</p>';
  const when = data.generado ? new Date(data.generado).toLocaleString('es-MX', { dateStyle: 'medium', timeStyle: 'short' }) : '';
  return `<div class="tp-ai-text">${renderMarkdown(data.texto)}</div>${verdict}
    <p class="tp-note">${when ? `Redactado el ${escapeHtml(when)}${data.en_cache ? ' (guardado: las cifras no han cambiado)' : ''}.` : ''}</p>
    <div class="tp-actions"><button type="button" class="secondary-button" data-tp="ai-copy">Copiar texto</button><button type="button" class="secondary-button" data-tp="ai-retry">Volver a redactar</button></div>`;
}

async function requestAnalysis(regenerate = false) {
  const button = document.querySelector('#tp-ai-btn');
  const out = document.querySelector('#tp-ai-out');
  if (!currentProfile || !out) return;
  const profile = currentProfile;
  const token = analysisToken;
  if (button) { button.disabled = true; button.textContent = 'Redactando…'; }
  out.innerHTML = '<p class="tp-note" role="status">Redactando el análisis con las cifras de esta ficha. Tarda unos segundos.</p>';
  try {
    const response = await fetch('/api/territorio/analisis', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nivel: profile.nivel, unidad: String(profile.unidad), anio: profile.anio || null, regenerar: regenerate }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo redactar el análisis.');
    if (token !== analysisToken) return;
    lastAnalysisText = data.texto;
    out.innerHTML = renderAnalysisText(data);
    if (button) button.hidden = true;
  } catch (error) {
    if (token !== analysisToken) return;
    out.innerHTML = `<p class="tp-quality" role="alert">${escapeHtml(error.message)}</p>`;
    if (button) { button.disabled = false; button.textContent = 'Redactar análisis'; }
  }
}

// ------------------------------------------------------------------ eventos
territoryResult.addEventListener('click', async (event) => {
  const election = event.target.closest('[data-election]');
  const open = event.target.closest('[data-open]');
  const action = event.target.closest('[data-tp]')?.dataset.tp;
  if (election) {
    selectedElection = election.dataset.election;
    territoryResult.querySelectorAll('.tp-tab').forEach(tab => { const on = tab === election; tab.classList.toggle('active', on); tab.setAttribute('aria-selected', String(on)); });
    drawElection();
  } else if (open) {
    showProfile(open.dataset.open, open.dataset.value, currentProfile?.anio);
  } else if (action === 'ask') {
    activateTab('asistente');
    const input = document.querySelector('#question');
    input.value = `Resume los resultados de ${territoryName(currentProfile.nivel, currentProfile.unidad)} en ${currentProfile.anio || 'la última elección'} y dime dónde fue más competitivo.`;
    input.focus();
  } else if (action === 'speech') {
    window.openSpeechWithTerritory?.({ nivel: currentProfile.nivel, unidad: currentProfile.unidad, anio: currentProfile.anio, eleccion: selectedElection });
  } else if (action === 'ai' || action === 'ai-retry') {
    requestAnalysis(action === 'ai-retry');
  } else if (action === 'ai-copy') {
    try { await navigator.clipboard.writeText(lastAnalysisText); document.dispatchEvent(new CustomEvent('app:notice', { detail: 'Análisis copiado.' })); }
    catch { territoryStatus.textContent = 'No se pudo copiar. Selecciona el texto y cópialo manualmente.'; }
  } else if (action === 'link') {
    try { await navigator.clipboard.writeText(location.href); document.dispatchEvent(new CustomEvent('app:notice', { detail: 'Enlace copiado.' })); }
    catch { territoryStatus.textContent = 'No se pudo copiar. Copia la dirección desde la barra del navegador.'; }
  }
});

territoryResult.addEventListener('submit', async (event) => {
  if (event.target.id !== 'tp-goal-form') return;
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.target));
  const body = {
    titulo: values.titulo, indicador: values.indicador || '', objetivo: values.objetivo ? Number(values.objetivo) : null,
    fecha_limite: values.fecha_limite || null, territorio_tipo: currentProfile.nivel, territorio_valor: String(currentProfile.unidad),
  };
  try {
    const response = await fetch('/api/campania/metas', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo crear la meta.');
    document.dispatchEvent(new CustomEvent('app:notice', { detail: 'Meta creada. La verás también en Campaña y tareas.' }));
    showProfile(currentProfile.nivel, currentProfile.unidad, currentProfile.anio);
  } catch (error) { territoryStatus.textContent = error.message; }
});

territoryForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const unit = territoryUnit.value.trim();
  if (unit) showProfile(territoryLevel.value, unit, territoryYear.value || null);
});

// #territorio/municipio/MATEHUALA/2024
function routeTerritory() {
  const parts = decodeURIComponent(location.hash.slice(1)).split('/');
  if (parts[0] !== 'territorio') return;
  if (parts[1] && parts[2]) showProfile(parts[1], parts[2], parts[3] || null);
  else initTerritory();
}
document.addEventListener('tab:changed', (event) => { if (event.detail === 'territorio') initTerritory(); });
document.addEventListener('app:ready', routeTerritory);
window.addEventListener('hashchange', () => {
  const parts = decodeURIComponent(location.hash.slice(1)).split('/');
  const same = currentProfile && parts[1] === currentProfile.nivel && parts[2] === String(currentProfile.unidad);
  if (parts[0] === 'territorio' && !same) routeTerritory();
});

// Territorio activo: si cambia desde la barra (o desde otro módulo), el perfil lo sigue.
function followActiveTerritory() {
  const active = window.TerritorioActivo?.get();
  if (!active) return;
  const same = currentProfile && currentProfile.nivel === active.nivel && String(currentProfile.unidad) === String(active.unidad);
  if (!same) showProfile(active.nivel, active.unidad, active.anio || null);
}
document.addEventListener('territorio:activo', (event) => {
  if (event.detail && document.querySelector('.tab.active')?.dataset.tab === 'territorio') followActiveTerritory();
});
document.addEventListener('tab:changed', (event) => {
  if (event.detail === 'territorio' && !/^#territorio\/./.test(location.hash)) followActiveTerritory();
});
