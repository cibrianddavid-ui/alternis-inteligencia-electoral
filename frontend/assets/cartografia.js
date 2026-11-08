// Cartografía electoral por sección. Los cálculos provienen del backend; Leaflet sólo representa datos.
(() => {
  const form = document.querySelector('#cartography-form');
  if (!form) return;
  const year = document.querySelector('#cartography-year');
  const election = document.querySelector('#cartography-election');
  const level = document.querySelector('#cartography-level');
  const unit = document.querySelector('#cartography-unit');
  const variable = document.querySelector('#cartography-variable');
  const yearField = document.querySelector('#cartography-year-field');
  const status = document.querySelector('#cartography-status');
  const legend = document.querySelector('#cartography-legend');
  const detail = document.querySelector('#cartography-detail');

  const COLORS = {
    PAN: '#0b5fa5', PRI: '#e4002b', PRD: '#f4c20d', PT: '#b5121b', MORENA: '#7e2a4f',
    PVEM: '#3ba935', MC: '#ff8200', PES: '#5b3f9e', NA: '#00a7b5', NULOS: '#9aa3af'
  };
  const FALLBACK = ['#5c6b7a', '#8d6e63', '#3f8f8f', '#b0a04a', '#7986cb', '#a1887f'];
  let catalog = null;
  let map = null;
  let layer = null;
  let casillasLayer = null;
  let casillasCache = null;
  let ready = false;
  let selectedSection = null;

  const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const fmt = value => value === null || value === undefined ? '—' : Number(value).toLocaleString('es-MX');
  const pct = value => value === null || value === undefined ? '—' : `${Number(value).toLocaleString('es-MX', {maximumFractionDigits: 1})} %`;
  const partyColor = name => {
    if (COLORS[name]) return COLORS[name];
    const parts = String(name || '').split('_');
    if (parts.length > 1 && COLORS[parts[0]]) return COLORS[parts[0]];
    let n = 0; for (const c of String(name || '')) n += c.charCodeAt(0);
    return FALLBACK[n % FALLBACK.length];
  };

  function mixWithWhite(hex, strength) {
    const clean = String(hex || '#9aa3af').replace('#', '');
    const base = clean.length === 3 ? clean.split('').map(c => c + c).join('') : clean;
    const rgb = [0, 2, 4].map(i => parseInt(base.slice(i, i + 2), 16));
    const t = Math.max(0, Math.min(1, Number(strength)));
    const mixed = rgb.map(c => Math.round(255 + (c - 255) * t));
    return `#${mixed.map(c => c.toString(16).padStart(2, '0')).join('')}`;
  }

  function winnerColor(props) {
    const base = partyColor(props.ganador?.partido);
    const pctWinner = Number(props.ganador?.porcentaje);
    if (!Number.isFinite(pctWinner)) return '#d9dee5';
    // Una victoria con <30% queda muy clara; desde 60% se acerca al color pleno del partido.
    const strength = pctWinner < 30 ? 0.28 : pctWinner < 40 ? 0.43 : pctWinner < 50 ? 0.60 : pctWinner < 60 ? 0.77 : 0.94;
    return mixWithWhite(base, strength);
  }

  function marginColor(props) {
    const base = partyColor(props.ganador?.partido);
    const margin = Number(props.margen_pp);
    if (!Number.isFinite(margin)) return '#d9dee5';
    // El tono identifica al ganador; la intensidad representa la ventaja sobre el segundo lugar.
    const strength = margin < 3 ? 0.28 : margin < 8 ? 0.43 : margin < 15 ? 0.65 : 0.92;
    return mixWithWhite(base, strength);
  }

  function scaleColor(value, kind) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '#d9dee5';
    const v = Number(value);
    if (kind === 'margen') return v < 3 ? '#f1eef6' : v < 8 ? '#bdc9e1' : v < 15 ? '#74a9cf' : '#0570b0';
    if (kind === 'participacion') return v < 40 ? '#f1eef6' : v < 50 ? '#bdc9e1' : v < 60 ? '#74a9cf' : '#0570b0';
    return v < 15 ? '#f1eef6' : v < 30 ? '#bdc9e1' : v < 45 ? '#74a9cf' : '#0570b0';
  }

  function featureColor(props) {
    if (variable.value === 'ganador') return winnerColor(props);
    if (variable.value === 'margen') return marginColor(props);
    if (variable.value === 'participacion') return scaleColor(props.participacion_pct, 'participacion');
    return '#d9dee5';
  }

  function renderLegend(data) {
    if (variable.value === 'ganador') {
      const parties = [...new Set(data.features.map(f => f.properties.ganador?.partido).filter(Boolean))].sort();
      const stops = [0.28, 0.43, 0.60, 0.77, 0.94];
      legend.innerHTML = `<strong>Ganador por sección</strong><small>Color = fuerza ganadora · Intensidad = % de voto</small>${parties.map(p => {
        const gradient = stops.map((strength, i) => `${mixWithWhite(partyColor(p), strength)} ${i * 25}%`).join(',');
        return `<span><i class="winner-gradient" style="background:linear-gradient(90deg,${gradient})"></i>${esc(p.replaceAll('_','-'))}</span>`;
      }).join('')}<div class="winner-scale"><span>&lt;30%</span><span>30–40%</span><span>40–50%</span><span>50–60%</span><span>≥60%</span></div>`;
      return;
    }
    if (variable.value === 'margen') {
      const parties = [...new Set(data.features.map(f => f.properties.ganador?.partido).filter(Boolean))].sort();
      const stops = [0.28, 0.43, 0.65, 0.92];
      legend.innerHTML = `<strong>Margen 1.º vs 2.º</strong><small>Color = fuerza ganadora · Intensidad = tamaño del margen</small>${parties.map(p => {
        const gradient = stops.map((strength, i) => `${mixWithWhite(partyColor(p), strength)} ${i * (100 / (stops.length - 1))}%`).join(',');
        return `<span><i class="winner-gradient" style="background:linear-gradient(90deg,${gradient})"></i>${esc(p.replaceAll('_','-'))}</span>`;
      }).join('')}<div class="winner-scale margin-scale"><span>&lt;3 pp</span><span>3–8 pp</span><span>8–15 pp</span><span>≥15 pp</span></div>`;
      return;
    }
    const specs = [['< 40%','#f1eef6'],['40–50%','#bdc9e1'],['50–60%','#74a9cf'],['≥ 60%','#0570b0']];
    legend.innerHTML = `<strong>Participación</strong>${specs.map(([t,c]) => `<span><i style="background:${c}"></i>${t}</span>`).join('')}`;
  }

  function renderCasillasPanel() {
    const target = document.querySelector('#map-casillas-detail');
    if (!target || selectedSection === null) return;
    const rows = (casillasCache?.registros || casillasCache?.features?.map(f => f.properties) || [])
      .filter(r => Number(r.seccion) === Number(selectedSection));
    if (!casillasCache) {
      target.innerHTML = '<p>Consultando domicilios de casillas…</p>';
      return;
    }
    if (!rows.length) {
      target.innerHTML = `<p>No se encontraron domicilios para la sección ${esc(selectedSection)}. ${esc(casillasCache.advertencia || '')} ${esc(casillasCache.diagnostico || '')}</p>`;
      return;
    }
    target.innerHTML = `<h3>Casillas de la sección (${rows.length})</h3>` + rows.map(r => {
      return `<div class="map-casilla-entry" style="border-top:1px solid #d6dce5;padding:10px 0">
        <strong>Casilla ${esc(r.casilla || '—')}</strong><div>${esc(r.inmueble || 'Inmueble no especificado')}</div>
        <div><strong>Domicilio:</strong> ${esc(r.domicilio || 'No disponible')}</div>
        ${r.referencia ? `<div><strong>Referencia:</strong> ${esc(r.referencia)}</div>` : ''}
        ${r.localidad_nombre ? `<small>Localidad: ${esc(r.localidad_nombre)}</small>` : ''}
      </div>`;
    }).join('');
  }

  async function loadCasillasData() {
    if (casillasCache) return casillasCache;
    const response = await fetch('/api/cartografia/casillas', {cache: 'no-store'});
    if (!response.ok) throw new Error(`No se pudieron consultar las casillas (HTTP ${response.status})`);
    casillasCache = await response.json();
    renderCasillasPanel();
    return casillasCache;
  }

  function renderDetail(props) {
    selectedSection = props.seccion;
    const first = props.ganador || {};
    const second = props.segundo || {};
    detail.innerHTML = `
      <p class="eyebrow">SECCIÓN ${esc(props.seccion)}</p>
      <h2>${first.partido ? esc(first.partido.replaceAll('_','-')) : 'Sin resultado'}</h2>
      <div class="map-detail-grid">
        <div><span>1.º lugar</span><strong>${esc(first.partido?.replaceAll('_','-') || '—')}</strong><small>${fmt(first.votos)} votos · ${pct(first.porcentaje)}</small></div>
        <div><span>2.º lugar</span><strong>${esc(second.partido?.replaceAll('_','-') || '—')}</strong><small>${fmt(second.votos)} votos · ${pct(second.porcentaje)}</small></div>
        <div><span>Margen</span><strong>${fmt(props.margen_votos)} votos</strong><small>${pct(props.margen_pp)}</small></div>
        <div><span>Participación</span><strong>${pct(props.participacion_pct)}</strong><small>Lista nominal: ${fmt(props.lista_nominal)}</small></div>
      </div>
      <div id="map-casillas-detail" style="margin-top:16px"></div>
      <button type="button" class="secondary-button" id="map-open-profile">Ver perfil completo</button>`;
    renderCasillasPanel();
    loadCasillasData().catch(error => {
      const el = document.querySelector('#map-casillas-detail');
      if (el) el.textContent = `No fue posible cargar domicilios: ${error.message}`;
    });
    document.querySelector('#map-open-profile')?.addEventListener('click', async () => {
      activateTab('territorio');
      await window.showTerritoryProfile?.('seccion', String(props.seccion), year.value || null);
    });
  }

  function initMap() {
    if (map || typeof L === 'undefined') return;
    map = L.map('cartography-map', { zoomControl: true, minZoom: 6 }).setView([22.15, -100.95], 8);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);
  }

  async function syncCasillas() {
    const toggle = document.querySelector('#cartography-show-casillas');
    if (!map) return;
    if (casillasLayer) { map.removeLayer(casillasLayer); casillasLayer = null; }
    if (!toggle.checked) return;
    try {
      await loadCasillasData();
      casillasLayer = L.geoJSON(casillasCache, {
        pointToLayer: (feature, latlng) => L.circleMarker(latlng, {radius: 5, color: '#173a69', fillColor: '#38bdf8', fillOpacity: 0.85, weight: 1}),
        onEachFeature: (feature, marker) => {
          const p = feature.properties;
          marker.bindPopup(`<strong>Casilla ${esc(p.casilla)}</strong><br>Sección ${esc(p.seccion)} · ${esc(p.municipio_nombre)}<br>${esc(p.inmueble)}<br>${esc(p.domicilio)}<br>${esc(p.referencia)}`);
        }
      }).addTo(map);
      if (casillasCache.advertencia) status.textContent = casillasCache.advertencia + ' ' + (casillasCache.diagnostico || '');
      else status.textContent = `${casillasCache.total || 0} casillas ubicadas (${casillasCache.origen || 'fuente desconocida'}).`;
      if (casillasCache.total > 0 && !layer?.getLayers()?.length) map.fitBounds(casillasLayer.getBounds());
    } catch (error) { status.textContent = error.message; }
  }
  document.querySelector('#cartography-show-casillas').addEventListener('change', syncCasillas);

  async function loadMap() {
    initMap();
    if (!map) { status.textContent = 'No se pudo iniciar el visor cartográfico.'; return; }
    status.textContent = 'Cargando secciones y resultados…';
    const q = new URLSearchParams({ eleccion: election.value, variable: variable.value });
    if (year.value) q.set('anio', year.value);
    if (level.value) q.set('nivel', level.value);
    if (unit.value) q.set('unidad', unit.value);
    try {
      const response = await fetch(`/api/cartografia/mapa?${q}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo cargar el mapa.');
      if (layer) layer.remove();
      layer = L.geoJSON(data, {
        style: feature => ({ color: '#ffffff', weight: 0.8, opacity: 0.9, fillColor: featureColor(feature.properties), fillOpacity: 0.78 }),
        onEachFeature: (feature, polygon) => {
          const p = feature.properties;
          polygon.bindTooltip(`<strong>Sección ${esc(p.seccion)}</strong><br>${esc(p.ganador?.partido?.replaceAll('_','-') || 'Sin resultado')} · ${pct(p.ganador?.porcentaje)}<br>Margen: ${pct(p.margen_pp)}`, { sticky: true });
          polygon.on({
            click: () => renderDetail(p),
            mouseover: e => e.target.setStyle({ weight: 2, color: '#26364a', fillOpacity: 0.92 }),
            mouseout: e => layer.resetStyle(e.target),
          });
        }
      }).addTo(map);
      if (data.features.length) map.fitBounds(layer.getBounds(), { padding: [18, 18] });
      renderLegend(data);
      await syncCasillas();
      if (!document.querySelector('#cartography-show-casillas')?.checked || !casillasCache?.advertencia) {
        status.textContent = `${data.features.length.toLocaleString('es-MX')} secciones con datos · ${data.meta.eleccion_etiqueta}${data.meta.anio ? ` · ${data.meta.anio}` : ''}${casillasCache && document.querySelector('#cartography-show-casillas')?.checked ? ` · ${casillasCache.total} casillas` : ''}`;
      }
      setTimeout(() => map.invalidateSize(), 0);
    } catch (error) { status.textContent = error.message; }
  }

  async function init() {
    if (ready) { setTimeout(() => map?.invalidateSize(), 0); return; }
    status.textContent = 'Preparando cartografía…';
    try {
      const response = await fetch('/api/cartografia/catalogo');
      catalog = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof catalog.detail === 'string' ? catalog.detail : 'No se pudo cargar el catálogo cartográfico.');
      year.innerHTML = (catalog.anios || []).map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
      if (catalog.anios?.length) year.value = catalog.anios.at(-1);
      yearField.hidden = (catalog.anios || []).length < 2;
      election.innerHTML = (catalog.elecciones || []).map(x => `<option value="${esc(x.valor)}">${esc(x.etiqueta)}</option>`).join('');
      if ((catalog.elecciones || []).some(x => x.valor === 'AYUNTAMIENTO')) election.value = 'AYUNTAMIENTO';
      level.innerHTML = (catalog.niveles || []).map(x => `<option value="${esc(x.valor)}">${esc(x.etiqueta)}</option>`).join('');
      if ((catalog.niveles || []).some(x => x.valor === 'municipio')) level.value = 'municipio';
      syncTerritoryUnits();
      applyActiveTerritory();
      variable.innerHTML = (catalog.variables || []).map(x => `<option value="${esc(x.valor)}">${esc(x.etiqueta)}</option>`).join('');
      ready = true;
      await loadMap();
    } catch (error) { status.textContent = error.message; }
  }

  function syncTerritoryUnits() {
    const selected = (catalog?.niveles || []).find(x => x.valor === level.value);
    const label = selected?.etiqueta || 'Unidad';
    const allLabel = level.value === 'seccion' ? 'Todas las secciones' : `Todos los ${label.toLowerCase()}s`;
    unit.innerHTML = `<option value="">${esc(allLabel)}</option>` + (selected?.unidades || []).map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
  }

  // Territorio activo: el mapa se enfoca ahí cuando cambia, sin pisar lo que la persona eligió a mano.
  let appliedActiveKey = null;
  function applyActiveTerritory() {
    const active = window.TerritorioActivo?.get();
    if (!active || !catalog) return false;
    const key = `${active.nivel}|${active.unidad}`;
    if (key === appliedActiveKey) return false;
    const target = (catalog.niveles || []).find(x => x.valor === active.nivel);
    const value = (target?.unidades || []).map(String).find(x => x === String(active.unidad));
    if (!target || value === undefined) return false;
    level.value = active.nivel;
    syncTerritoryUnits();
    unit.value = value;
    appliedActiveKey = key;
    return true;
  }
  const onCartographyTab = () => document.querySelector('.tab.active')?.dataset.tab === 'cartografia';
  document.addEventListener('territorio:activo', () => { if (ready && onCartographyTab() && applyActiveTerritory()) loadMap(); });

  level.addEventListener('change', syncTerritoryUnits);
  form.addEventListener('submit', event => { event.preventDefault(); loadMap(); });
  document.addEventListener('tab:changed', async event => { if (event.detail === 'cartografia') { await init(); if (ready && applyActiveTerritory()) loadMap(); } });
  if (document.querySelector('.tab.active')?.dataset.tab === 'cartografia') init();
})();
