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
    ${renderLinkedGoals(profile, linkedGoals, canWrite)}`;
  drawElection();
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
