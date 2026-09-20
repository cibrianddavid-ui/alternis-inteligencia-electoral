// ---------------------------------------------------------------------------
// Administración: usuarios y modo demostración.
// El aviso de datos de ejemplo lo ve cualquier usuario; la gestión, solo el administrador.
// ---------------------------------------------------------------------------
const adminStatus = document.querySelector('#admin-status');
const userRows = document.querySelector('#user-rows');
const demoBanner = document.querySelector('#demo-banner');

async function adminApi(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo completar la acción.');
  return data;
}
const adminJson = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function lastAccess(iso) {
  if (!iso) return 'Nunca';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('es-MX', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

async function loadUsers() {
  try {
    const users = await adminApi('/api/usuarios');
    userRows.innerHTML = users.map(user => `<tr class="${user.activo ? '' : 'is-off'}" data-user="${user.id}">
      <td><strong>${escapeHtml(user.usuario)}</strong></td><td>${escapeHtml(user.nombre)}</td>
      <td><select data-field="rol" aria-label="Rol de ${escapeHtml(user.usuario)}">${Object.entries(ROLE_LABEL).map(([value, label]) => `<option value="${value}" ${value === user.rol ? 'selected' : ''}>${label}</option>`).join('')}</select></td>
      <td>${escapeHtml(lastAccess(user.ultimo_acceso))}</td>
      <td>${user.activo ? 'Activo' : 'Desactivado'}</td>
      <td class="admin-row-actions"><button type="button" class="text-button" data-act="password">Restablecer contraseña</button><button type="button" class="text-button" data-act="toggle">${user.activo ? 'Desactivar' : 'Reactivar'}</button></td></tr>`).join('');
  } catch (error) { adminStatus.textContent = error.message; }
}

userRows.addEventListener('change', async (event) => {
  const select = event.target.closest('[data-field="rol"]');
  if (!select) return;
  const id = select.closest('[data-user]').dataset.user;
  try {
    await adminApi(`/api/usuarios/${id}`, adminJson('PATCH', { rol: select.value }));
    adminStatus.textContent = 'Rol actualizado. Se cerraron las sesiones abiertas de esa persona.';
  } catch (error) { adminStatus.textContent = error.message; }
  loadUsers();
});

userRows.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-act]');
  if (!button) return;
  const row = button.closest('[data-user]');
  const name = row.querySelector('strong').textContent;
  try {
    if (button.dataset.act === 'password') {
      const clave = window.prompt(`Nueva contraseña para ${name} (mínimo 8 caracteres):`);
      if (!clave) return;
      await adminApi(`/api/usuarios/${row.dataset.user}`, adminJson('PATCH', { clave }));
      adminStatus.textContent = `Contraseña de ${name} restablecida. Sus sesiones abiertas se cerraron.`;
    } else {
      const active = !row.classList.contains('is-off');
      if (active && !window.confirm(`¿Desactivar a ${name}? Ya no podrá iniciar sesión.`)) return;
      await adminApi(`/api/usuarios/${row.dataset.user}`, adminJson('PATCH', { activo: !active }));
      adminStatus.textContent = active ? `${name} fue desactivado.` : `${name} fue reactivado.`;
    }
  } catch (error) { adminStatus.textContent = error.message; }
  loadUsers();
});

document.querySelector('#user-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const user = await adminApi('/api/usuarios', adminJson('POST', Object.fromEntries(new FormData(form))));
    form.reset();
    adminStatus.textContent = `Usuario ${user.usuario} creado. Entrégale su contraseña inicial por un canal seguro.`;
    loadUsers();
  } catch (error) { adminStatus.textContent = error.message; }
});

// ------------------------------------------------------------------ modo demostración
async function refreshDemo() {
  let loaded = false;
  try { loaded = (await adminApi('/api/demo')).cargado; } catch { return; }
  const admin = hasRole('admin');
  demoBanner.hidden = !loaded;
  demoBanner.innerHTML = loaded
    ? `<span><strong>Datos de demostración.</strong> Lo que ves en Campaña y tareas es de ejemplo.</span>${admin ? '<button type="button" class="demo-remove-inline">Quitar datos de ejemplo</button>' : ''}` : '';
  document.body.classList.toggle('has-demo-banner', loaded);
  const text = document.querySelector('#demo-text');
  if (text) {
    text.textContent = loaded
      ? 'Hay datos de ejemplo cargados (áreas, personas, metas y tareas ligadas a territorios reales). Puedes quitarlos sin afectar tus datos reales.'
      : 'Carga una campaña de ejemplo ligada a territorios reales de la base para mostrar la plataforma. Todo lo que se crea queda marcado y se retira con un clic.';
    document.querySelector('#demo-load').disabled = loaded;
    document.querySelector('#demo-remove').disabled = !loaded;
  }
}

async function changeDemo(method) {
  try {
    const data = await adminApi('/api/demo', { method });
    adminStatus.textContent = method === 'POST' ? 'Datos de ejemplo cargados. Revisa Campaña y tareas.' : `Se eliminaron ${data.eliminados} registros de ejemplo.`;
    document.dispatchEvent(new CustomEvent('demo:changed'));
  } catch (error) { adminStatus.textContent = error.message; }
  refreshDemo();
}
document.querySelector('#demo-load').addEventListener('click', () => changeDemo('POST'));
document.querySelector('#demo-remove').addEventListener('click', () => { if (window.confirm('¿Quitar todos los datos de ejemplo?')) changeDemo('DELETE'); });
demoBanner.addEventListener('click', (event) => {
  if (event.target.closest('.demo-remove-inline') && window.confirm('¿Quitar todos los datos de ejemplo?')) changeDemo('DELETE');
});

document.addEventListener('app:ready', () => {
  refreshDemo();
  if (hasRole('admin')) loadUsers();
});
document.addEventListener('tab:changed', (event) => { if (event.detail === 'admin') { loadUsers(); refreshDemo(); } });
