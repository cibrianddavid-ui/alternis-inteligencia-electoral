// ---------------------------------------------------------------------------
// Discursos v2: formatos, datos de un territorio, ajustes rápidos, verificación de
// cifras, historial persistente en el servidor y exportación a Word, PDF y TXT.
// ---------------------------------------------------------------------------
const speech = { id: null, options: null, messages: [], latest: '', verification: null, busy: false };
const speechEls = {
  messages: document.querySelector('#speech-messages'), status: document.querySelector('#speech-status'),
  form: document.querySelector('#speech-form'), prompt: document.querySelector('#speech-prompt'),
  format: document.querySelector('#speech-format'), useData: document.querySelector('#speech-use-data'),
  fields: document.querySelector('#speech-territory-fields'), level: document.querySelector('#speech-level'),
  unit: document.querySelector('#speech-unit'), units: document.querySelector('#speech-units'),
  election: document.querySelector('#speech-election'), quick: document.querySelector('#speech-quick'),
  verify: document.querySelector('#speech-verify'), meta: document.querySelector('#speech-meta'),
  history: document.querySelector('#speech-history-list'),
};
const SPEECH_INTRO = 'Cuéntame para qué ocasión necesitas el discurso, su tema y su tono. Elige un formato arriba y, si quieres cifras confiables, activa "Usar datos de un territorio": el sistema las calcula y revisa el borrador contra ellas.';

async function speechApi(path, options = {}) {
  const response = await fetch(`/api/discursos${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo completar la acción.');
  return data;
}
const jsonPost = (body) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

// ------------------------------------------------------------------ territorio elegido
function currentSpeechTerritory() {
  if (!speechEls.useData.checked) return null;
  const unidad = speechEls.unit.value.trim();
  if (!unidad) return null;
  return { nivel: speechEls.level.value, unidad, eleccion: speechEls.election.value || null };
}

async function fillSpeechTerritoryOptions() {
  try {
    const catalog = await window.getTerritoryCatalog();
    if (!speechEls.level.options.length) {
      speechEls.level.innerHTML = catalog.niveles.map(level => `<option value="${escapeHtml(level.valor)}">${escapeHtml(level.etiqueta)}</option>`).join('');
      speechEls.level.value = catalog.niveles.some(level => level.valor === 'id_distrito_local') ? 'id_distrito_local' : catalog.niveles[0].valor;
      speechEls.election.innerHTML = '<option value="">Elección principal</option>' + catalog.elecciones.map(item => `<option value="${escapeHtml(item.valor)}">${escapeHtml(item.etiqueta)}</option>`).join('');
    }
    const level = catalog.niveles.find(item => item.valor === speechEls.level.value);
    speechEls.units.innerHTML = (level?.unidades || []).map(unit => `<option value="${escapeHtml(unit)}"></option>`).join('');
  } catch (error) { speechEls.status.textContent = error.message; }
}
speechEls.useData.addEventListener('change', () => {
  speechEls.fields.hidden = !speechEls.useData.checked;
  if (speechEls.useData.checked) fillSpeechTerritoryOptions();
  renderQuickAdjustments();
});
speechEls.level.addEventListener('change', () => { speechEls.unit.value = ''; fillSpeechTerritoryOptions(); });
speechEls.unit.addEventListener('input', renderQuickAdjustments);

async function setSpeechTerritory(selection) {
  speechEls.useData.checked = !!selection;
  speechEls.fields.hidden = !selection;
  if (!selection) { speechEls.unit.value = ''; return; }
  await fillSpeechTerritoryOptions();
  speechEls.level.value = selection.nivel;
  await fillSpeechTerritoryOptions();
  speechEls.unit.value = selection.unidad;
  speechEls.election.value = selection.eleccion || '';
}

// ------------------------------------------------------------------ mensajes
// El texto se inserta con nodos de texto: nunca como HTML. Las cifras se resaltan por posición.
function draftNodes(text, verification) {
  const fragment = document.createDocumentFragment();
  const marks = [
    ...(verification?.respaldadas || []).map(item => ({ ...item, kind: 'ok', title: 'Coincide con los datos' })),
    ...(verification?.no_respaldadas || []).map(item => ({ ...item, kind: 'warn', title: 'No coincide con los datos ni con tu petición: verifícala' })),
  ].sort((a, b) => a.inicio - b.inicio);
  let cursor = 0;
  for (const mark of marks) {
    if (mark.inicio < cursor || mark.fin > text.length) continue;
    fragment.append(document.createTextNode(text.slice(cursor, mark.inicio)));
    const node = document.createElement('mark');
    node.className = `fig-${mark.kind}`;
    node.title = mark.title;
    node.textContent = text.slice(mark.inicio, mark.fin);
    fragment.append(node);
    cursor = mark.fin;
  }
  fragment.append(document.createTextNode(text.slice(cursor)));
  return fragment;
}

function addSpeechMessage(role, content, verification = null) {
  const node = document.createElement('div');
  node.className = `speech-message ${role}`;
  if (role === 'assistant' && verification) node.append(draftNodes(content, verification));
  else node.textContent = content;
  speechEls.messages.append(node);
  speechEls.messages.scrollTop = speechEls.messages.scrollHeight;
  return node;
}

function renderConversation() {
  speechEls.messages.innerHTML = '';
  if (!speech.messages.length) addSpeechMessage('assistant', SPEECH_INTRO);
  speech.messages.forEach(message => addSpeechMessage(message.role, message.content, message.verification));
  const last = [...speech.messages].reverse().find(message => message.role === 'assistant');
  speech.latest = last?.content || '';
  speech.verification = last?.verification || null;
  refreshSpeechPanel();
}

function speechDuration(words) {
  const seconds = Math.round(words / 130 * 60);  // ritmo de lectura en voz alta: ~130 palabras por minuto
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${String(seconds % 60).padStart(2, '0')} s`;
}

function refreshSpeechPanel() {
  const hasDraft = !!speech.latest;
  document.querySelectorAll('[data-speech-action]').forEach(button => { button.disabled = !hasDraft; });
  const words = hasDraft ? (speech.latest.match(/\S+/g) || []).length : 0;
  speechEls.meta.textContent = hasDraft ? `${words} palabras · ≈ ${speechDuration(words)} en voz alta` : '';
  renderVerification();
  renderQuickAdjustments();
}

function renderVerification() {
  const v = speech.verification;
  const box = speechEls.verify;
  if (!speech.latest || !v || v.estado === 'sin_cifras') { box.hidden = true; return; }
  const doubtful = v.no_respaldadas.map(item => item.texto);
  const source = v.aplicada ? `los datos de ${escapeHtml(v.territorio)}` : 'lo que escribiste en tu petición';
  box.hidden = false;
  box.className = `speech-verify ${v.estado === 'ok' ? 'is-ok' : 'is-warn'}`;
  if (v.estado === 'ok') {
    box.innerHTML = `<strong>✓ Cifras verificadas.</strong> ${v.respaldadas.length} ${v.respaldadas.length === 1 ? 'cifra coincide' : 'cifras coinciden'} con ${source}.`;
  } else {
    box.innerHTML = `<strong>⚠ ${doubtful.length} ${doubtful.length === 1 ? 'cifra sin respaldo' : 'cifras sin respaldo'}:</strong> ${doubtful.map(text => `<mark class="fig-warn">${escapeHtml(text)}</mark>`).join(' ')}. `
      + (v.aplicada ? 'No coinciden con los datos del territorio ni con lo que escribiste.' : 'No provienen de datos verificados ni de tu petición: elige un territorio o confírmalas antes de usarlas.')
      + ` <button type="button" class="text-button" data-fix-figures>Corregir cifras</button>`;
  }
}

function renderQuickAdjustments() {
  const options = speech.options;
  if (!options) return;
  const hasDraft = !!speech.latest;
  speechEls.quick.hidden = !hasDraft;
  if (!hasDraft) return;
  const hasData = !!currentSpeechTerritory();
  const hasDoubtful = !!speech.verification?.no_respaldadas?.length;
  speechEls.quick.innerHTML = '<span class="quick-label">Ajustar:</span>' + options.ajustes
    .filter(item => item.id !== 'corregir_cifras' || hasDoubtful)
    .map(item => `<button type="button" class="quick-chip" data-adjust="${escapeHtml(item.id)}" ${item.requiere_datos && !hasData ? 'disabled title="Activa «Usar datos de un territorio»"' : ''}>${escapeHtml(item.etiqueta)}</button>`).join('');
}

// ------------------------------------------------------------------ envío
async function sendSpeech({ mensaje = null, ajuste = null }) {
  if (speech.busy) return;
  speech.busy = true;
  speechEls.status.textContent = '';
  speechEls.form.querySelector('button').disabled = true;
  document.querySelectorAll('.quick-chip').forEach(chip => { chip.disabled = true; });
  const label = ajuste ? `Ajuste: ${speech.options.ajustes.find(item => item.id === ajuste)?.etiqueta || ajuste}` : mensaje;
  addSpeechMessage('user', label);
  const waiting = addSpeechMessage('assistant', 'Preparando borrador…');
  try {
    const body = { formato: speechEls.format.value, territorio: currentSpeechTerritory() };
    if (speech.id) body.discurso_id = speech.id;
    if (mensaje) body.mensaje = mensaje;
    if (ajuste) body.ajuste = ajuste;
    const data = await speechApi('/chat', jsonPost(body));
    speech.id = data.discurso_id;
    speech.messages.push({ role: 'user', content: label }, { role: 'assistant', content: data.respuesta, verification: data.verificacion });
    renderConversation();
    speechEls.status.textContent = data.verificacion.estado === 'revisar' ? 'Revisa las cifras resaltadas antes de usar el texto.' : 'Puedes pedir ajustes, descargar el borrador o compartirlo.';
    loadSpeechHistory();
  } catch (error) {
    waiting.remove();
    speechEls.messages.lastElementChild?.classList.contains('user') && speechEls.messages.lastElementChild.remove();
    speechEls.status.textContent = error.message;
    if (mensaje) speechEls.prompt.value = mensaje;
  } finally {
    speech.busy = false;
    speechEls.form.querySelector('button').disabled = false;
    renderQuickAdjustments();
    speechEls.prompt.focus();
  }
}

speechEls.form.addEventListener('submit', (event) => {
  event.preventDefault();
  const text = speechEls.prompt.value.trim();
  if (!text) return;
  speechEls.prompt.value = '';
  sendSpeech({ mensaje: text });
});
speechEls.quick.addEventListener('click', (event) => {
  const chip = event.target.closest('[data-adjust]');
  if (chip && !chip.disabled) sendSpeech({ ajuste: chip.dataset.adjust });
});
speechEls.verify.addEventListener('click', (event) => { if (event.target.closest('[data-fix-figures]')) sendSpeech({ ajuste: 'corregir_cifras' }); });

// ------------------------------------------------------------------ historial
function relativeDate(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('es-MX', { day: 'numeric', month: 'short' });
}

async function loadSpeechHistory() {
  try {
    const items = await speechApi('/historial');
    speechEls.history.innerHTML = items.map(item => `<li class="${item.id === speech.id ? 'active' : ''}"><button type="button" class="history-open" data-open="${item.id}"><strong>${escapeHtml(item.titulo)}</strong><small>${escapeHtml(relativeDate(item.actualizado))} · ${Math.floor(item.mensajes / 2)} ${Math.floor(item.mensajes / 2) === 1 ? 'versión' : 'versiones'}</small></button><button type="button" class="history-delete" data-delete="${item.id}" aria-label="Eliminar «${escapeHtml(item.titulo)}»">×</button></li>`).join('')
      || '<li class="history-empty">Aquí aparecerán tus discursos. Se guardan automáticamente.</li>';
  } catch { /* el historial es un extra: si falla, el resto sigue funcionando */ }
}

async function openConversation(id) {
  try {
    const data = await speechApi(`/historial/${id}`);
    speech.id = data.id;
    speech.messages = data.mensajes.map(item => ({ role: item.role, content: item.content, verification: item.verificacion }));
    speechEls.format.value = data.formato;
    await setSpeechTerritory(data.territorio);
    renderConversation();
    speechEls.status.textContent = `Conversación «${data.titulo}» recuperada.`;
    loadSpeechHistory();
  } catch (error) { speechEls.status.textContent = error.message; }
}

function newSpeech() {
  speech.id = null; speech.messages = []; speech.latest = ''; speech.verification = null;
  speechEls.prompt.value = '';
  renderConversation();
  speechEls.status.textContent = 'Nuevo discurso listo.';
  loadSpeechHistory();
}

speechEls.history.addEventListener('click', async (event) => {
  const open = event.target.closest('[data-open]');
  const remove = event.target.closest('[data-delete]');
  if (open) openConversation(Number(open.dataset.open));
  if (remove && confirm('¿Eliminar este discurso y todas sus versiones?')) {
    try {
      await speechApi(`/historial/${remove.dataset.delete}`, { method: 'DELETE' });
      if (Number(remove.dataset.delete) === speech.id) newSpeech(); else loadSpeechHistory();
    } catch (error) { speechEls.status.textContent = error.message; }
  }
});
document.querySelector('#speech-clear').addEventListener('click', newSpeech);

// Desde el perfil territorial: nuevo discurso con ese territorio ya elegido.
window.openSpeechWithTerritory = async (selection) => {
  if (!activateTab('discursos')) return;
  if (speech.id || speech.messages.length) newSpeech();
  await setSpeechTerritory(selection);
  renderQuickAdjustments();
  speechEls.prompt.value = `Redacta un discurso para presentar la situación electoral de ${territoryName(selection.nivel, selection.unidad)} con cifras claras y un llamado a la participación.`;
  speechEls.prompt.focus();
  speechEls.status.textContent = 'El sistema usará únicamente las cifras verificadas de este territorio.';
};

// ------------------------------------------------------------------ exportación
document.querySelectorAll('[data-speech-action]').forEach(button => button.addEventListener('click', async () => {
  if (!speech.latest) return;
  const action = button.dataset.speechAction;
  try {
    if (action === 'copy') {
      await navigator.clipboard.writeText(speech.latest);
      speechEls.status.textContent = 'Texto copiado.';
    } else if (action === 'share') {
      if (navigator.share) await navigator.share({ title: 'Discurso', text: speech.latest });
      else { await navigator.clipboard.writeText(speech.latest); speechEls.status.textContent = 'Tu navegador no ofrece compartir. Se copió el texto para que puedas pegarlo.'; }
    } else {
      button.disabled = true;
      const response = await fetch(`/api/discursos/exportar/${action}`, jsonPost({ texto: speech.latest, discurso_id: speech.id }));
      if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || 'Error al descargar.'); }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = `discurso.${action}`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      speechEls.status.textContent = action === 'docx' ? 'Documento de Word descargado.' : 'Descarga lista.';
    }
  } catch (error) { if (error.name !== 'AbortError') speechEls.status.textContent = error.message; }
  finally { button.disabled = !speech.latest; }
}));

// ------------------------------------------------------------------ arranque
document.addEventListener('app:ready', async () => {
  if (!hasRole('coordinador')) return;
  renderConversation();
  try {
    speech.options = await speechApi('/opciones');
    speechEls.format.innerHTML = speech.options.formatos.map(item => `<option value="${escapeHtml(item.id)}" title="${escapeHtml(item.descripcion)}">${escapeHtml(item.nombre)}</option>`).join('');
    renderQuickAdjustments();
  } catch (error) { speechEls.status.textContent = error.message; }
  loadSpeechHistory();
});
