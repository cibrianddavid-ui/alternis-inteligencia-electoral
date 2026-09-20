// ---------------------------------------------------------------------------
// Acceso: inicio de sesión, primer administrador, permisos por rol y cierre de sesión.
// Se carga antes que el resto: envuelve fetch para detectar sesiones vencidas.
// ---------------------------------------------------------------------------
const ROLE_LEVEL = { consulta: 0, coordinador: 1, admin: 2 };
const ROLE_LABEL = { consulta: 'Consulta', coordinador: 'Coordinación', admin: 'Administración' };
window.currentUser = null;
let sessionStarted = false;

function hasRole(minimum) {
  const user = window.currentUser;
  return !!user && ROLE_LEVEL[user.rol] >= ROLE_LEVEL[minimum];
}

const nativeFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const response = await nativeFetch(input, init);
  const url = typeof input === 'string' ? input : (input && input.url) || '';
  if (response.status === 401 && url.startsWith('/api/') && !url.startsWith('/api/auth/') && sessionStarted) {
    showAuth('login', 'Tu sesión venció. Vuelve a iniciar sesión.');
  }
  return response;
};

const authScreen = document.querySelector('#auth-screen');
const loginForm = document.querySelector('#login-form');
const setupForm = document.querySelector('#setup-form');
const authError = document.querySelector('#auth-error');

function showAuth(mode, message = '') {
  document.body.classList.add('locked');
  authScreen.hidden = false;
  const setup = mode === 'setup';
  loginForm.hidden = setup;
  setupForm.hidden = !setup;
  document.querySelector('#auth-title').textContent = setup ? 'Crea el primer administrador' : 'Inicia sesión';
  document.querySelector('#auth-sub').textContent = setup
    ? 'Es la primera vez que se abre la plataforma. Esta persona podrá crear los demás usuarios.'
    : 'Inteligencia Electoral · San Luis Potosí';
  authError.textContent = message;
  (setup ? document.querySelector('#setup-user') : document.querySelector('#login-user')).focus();
}

async function postJson(url, body) {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(describeError(response, data));
  return data;
}

// Siempre explica qué pasó: texto del servidor, error de validación o, como último recurso, el código HTTP.
function describeError(response, data) {
  if (typeof data.detail === 'string') return data.detail;
  const first = Array.isArray(data.detail) ? data.detail[0] : null;
  if (first && first.msg) {
    const field = first.loc && first.loc[first.loc.length - 1];
    return `${field ? `${field}: ` : ''}${first.msg}`;
  }
  return `No se pudo completar la acción (error ${response.status}). Revisa la consola donde corre la aplicación.`;
}

function enterApp(user) {
  if (sessionStarted) { location.reload(); return; }  // sesión renovada: se reinicia todo limpio
  sessionStarted = true;
  window.currentUser = user;
  document.body.dataset.role = user.rol;
  document.body.classList.remove('locked');
  authScreen.hidden = true;
  document.querySelector('#user-box').hidden = false;
  document.querySelector('#user-name').textContent = user.nombre || user.usuario;
  document.querySelector('#user-role').textContent = ROLE_LABEL[user.rol] || user.rol;
  document.querySelector('#user-avatar').textContent = (user.nombre || user.usuario).trim().charAt(0).toUpperCase();
  document.dispatchEvent(new CustomEvent('app:ready', { detail: user }));
}

loginForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = loginForm.querySelector('button');
  button.disabled = true;
  authError.textContent = '';
  try {
    const data = await postJson('/api/auth/login', {
      usuario: document.querySelector('#login-user').value.trim(),
      clave: document.querySelector('#login-pass').value,
    });
    document.querySelector('#login-pass').value = '';
    enterApp(data.usuario);
  } catch (error) { authError.textContent = error.message; } finally { button.disabled = false; }
});

setupForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = setupForm.querySelector('button');
  authError.textContent = '';
  if (document.querySelector('#setup-pass').value !== document.querySelector('#setup-pass2').value) {
    authError.textContent = 'Las contraseñas no coinciden.';
    return;
  }
  button.disabled = true;
  try {
    const data = await postJson('/api/auth/setup', {
      usuario: document.querySelector('#setup-user').value.trim(),
      nombre: document.querySelector('#setup-name').value.trim(),
      clave: document.querySelector('#setup-pass').value,
    });
    enterApp(data.usuario);
  } catch (error) { authError.textContent = error.message; } finally { button.disabled = false; }
});

document.querySelector('#logout-button').addEventListener('click', async () => {
  await nativeFetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
  localStorage.removeItem('electoral_session_id');
  location.hash = '';
  location.reload();
});

// Cambio de contraseña propio
const passwordModal = document.querySelector('#password-modal');
document.querySelector('#password-open').addEventListener('click', () => {
  document.querySelector('#password-form').reset();
  document.querySelector('#password-error').textContent = '';
  passwordModal.hidden = false;
  document.querySelector('#pass-current').focus();
});
document.querySelector('#password-cancel').addEventListener('click', () => { passwordModal.hidden = true; });
document.querySelector('#password-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await postJson('/api/auth/clave', {
      actual: document.querySelector('#pass-current').value,
      nueva: document.querySelector('#pass-new').value,
    });
    passwordModal.hidden = true;
    document.dispatchEvent(new CustomEvent('app:notice', { detail: 'Contraseña actualizada.' }));
  } catch (error) { document.querySelector('#password-error').textContent = error.message; }
});
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') passwordModal.hidden = true; });

(async function boot() {
  try {
    const response = await nativeFetch('/api/auth/estado');
    const data = await response.json();
    if (!data.hay_usuarios) showAuth('setup');
    else if (!data.usuario) showAuth('login');
    else enterApp(data.usuario);
  } catch {
    showAuth('login', 'No se pudo conectar con el servidor.');
  }
})();
