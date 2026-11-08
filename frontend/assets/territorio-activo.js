// ---------------------------------------------------------------------------
// Territorio activo: se elige una vez y lo usan Perfil, Mapa, Discursos y Metas.
// Vive en sessionStorage (solo esta pestaña) para no mezclar personas en un equipo compartido.
// ---------------------------------------------------------------------------
(() => {
  const KEY = 'ie.territorioActivo';
  const bar = document.querySelector('#active-territory');
  if (!bar) return;
  const els = {
    current: bar.querySelector('#at-current'), form: bar.querySelector('#at-form'), level: bar.querySelector('#at-level'),
    unit: bar.querySelector('#at-unit'), units: bar.querySelector('#at-units'), hint: bar.querySelector('#at-hint'),
    change: bar.querySelector('#at-change'), clear: bar.querySelector('#at-clear'), open: bar.querySelector('#at-open'),
  };
  let active = null;
  try { active = JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch { active = null; }
  let catalog = null;

  const sameUnit = (a, b) => !!a && !!b && a.nivel === b.nivel && String(a.unidad) === String(b.unidad);
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(active)); } catch { /* sin almacenamiento: solo se pierde al recargar */ } };

  function render() {
    if (active) {
      const kind = ['entidad', 'municipio'].includes(active.nivel) ? ` <span class="at-kind">${escapeHtml(DIMENSION_LABELS[active.nivel] || '')}</span>` : '';
      els.current.innerHTML = `<strong>${escapeHtml(territoryName(active.nivel, active.unidad))}</strong>${kind}`;
    } else {
      els.current.textContent = 'Ninguno. Elige uno para que Perfil, Mapa, Discursos y Metas lo usen.';
    }
    els.open.hidden = !active;
    els.clear.hidden = !active;
    els.change.textContent = active ? 'Cambiar' : 'Elegir';
  }

  function set(selection, options = {}) {
    if (!selection?.nivel || selection.unidad === undefined || selection.unidad === null || selection.unidad === '') return;
    const next = { nivel: selection.nivel, unidad: String(selection.unidad), anio: selection.anio || null };
    const unchanged = sameUnit(active, next) && (active.anio || null) === next.anio;
    active = next;
    save();
    render();
    if (!unchanged) document.dispatchEvent(new CustomEvent('territorio:activo', { detail: { ...active, source: options.source || null } }));
  }

  function clear() {
    if (!active) return;
    active = null;
    save();
    render();
    document.dispatchEvent(new CustomEvent('territorio:activo', { detail: null }));
  }

  async function fillOptions() {
    try {
      catalog = catalog || await window.getTerritoryCatalog();
      if (!els.level.options.length) {
        els.level.innerHTML = catalog.niveles.map(l => `<option value="${escapeHtml(l.valor)}">${escapeHtml(l.etiqueta)}</option>`).join('');
      }
      const level = catalog.niveles.find(l => l.valor === els.level.value);
      els.units.innerHTML = (level?.unidades || []).map(u => `<option value="${escapeHtml(u)}"></option>`).join('');
    } catch (error) { els.hint.textContent = error.message; }
  }

  async function openForm() {
    els.form.hidden = false;
    els.hint.textContent = '';
    await fillOptions();
    if (active) { els.level.value = active.nivel; await fillOptions(); els.unit.value = active.unidad; }
    else if (!els.unit.value && catalog.niveles.some(l => l.valor === 'municipio')) { els.level.value = 'municipio'; await fillOptions(); }
    els.unit.focus();
  }
  function closeForm() { els.form.hidden = true; els.hint.textContent = ''; els.change.focus(); }

  els.change.addEventListener('click', () => (els.form.hidden ? openForm() : closeForm()));
  els.clear.addEventListener('click', clear);
  els.open.addEventListener('click', () => { if (active) activateTab('territorio'); });
  els.level.addEventListener('change', () => { els.unit.value = ''; fillOptions(); });
  els.form.addEventListener('keydown', event => { if (event.key === 'Escape') closeForm(); });
  els.form.addEventListener('submit', event => {
    event.preventDefault();
    const level = catalog?.niveles.find(l => l.valor === els.level.value);
    const typed = els.unit.value.trim().toLowerCase();
    const match = (level?.unidades || []).find(u => String(u).toLowerCase() === typed);
    if (match === undefined) { els.hint.textContent = 'No encontré ese territorio. Elige una opción de la lista.'; return; }
    set({ nivel: level.valor, unidad: match }, { source: 'barra' });
    closeForm();
  });

  window.TerritorioActivo = { get: () => (active ? { ...active } : null), set, clear };
  document.addEventListener('app:ready', () => { bar.hidden = false; render(); });
  render();
})();
