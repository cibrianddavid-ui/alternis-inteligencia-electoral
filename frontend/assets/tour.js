// ---------------------------------------------------------------------------
// Recorrido guiado: presenta cada módulo en un minuto. Solo muestra lo que el
// rol de la persona puede usar. Se ofrece una vez por usuario y siempre se puede repetir.
// ---------------------------------------------------------------------------
const TOUR_STEPS = [
  { tab: null, title: 'Bienvenido', text: 'En un minuto verás cómo esta plataforma pasa de los datos a la acción: consultas resultados, ves el perfil de un territorio, redactas con cifras verificadas y das seguimiento a lo que se planeó.' },
  { tab: 'asistente', title: 'Asistente electoral', text: 'Pregunta en lenguaje natural, por ejemplo «¿quién ganó en Matehuala?». Debajo de cada respuesta verás cómo se interpretó tu consulta, y todas las cifras las calcula el sistema, no el modelo.' },
  { tab: 'territorio', title: 'Perfil territorial', text: 'La ficha de cualquier estado, municipio, distrito o sección: ganadores, margen, participación y secciones más competidas. Desde aquí creas metas y redactas discursos con esos datos.' },
  { tab: 'graficos', title: 'Resultados gráficos', text: 'Compara partidos, elecciones y coaliciones. Cada partido conserva su color, y puedes descargar la gráfica y sus datos.' },
  { tab: 'semaforo', title: 'Semáforo de posicionamiento', text: 'Analiza la cobertura de prensa de una persona, o compara dos lado a lado: tono, cobertura por semana y un reporte en PDF.', minRole: 'coordinador' },
  { tab: 'discursos', title: 'Discursos', text: 'Elige un formato, activa los datos de un territorio y ajusta con un clic. El sistema revisa cada cifra del borrador contra los datos y marca las que no coinciden. Todo se guarda en tu historial y se exporta a Word.', minRole: 'coordinador' },
  { tab: 'campania', title: 'Campaña y tareas', text: 'Metas con avance medible ligadas a un territorio, tareas por persona y alertas de vencimientos. Lo que planeas en el perfil territorial aparece aquí.' },
  { tab: null, title: 'Listo', text: 'Puedes repetir este recorrido cuando quieras desde «Recorrido», abajo a la izquierda.' },
];

let tourIndex = -1;
let tourSteps = [];
const tourLayer = document.createElement('div');
tourLayer.className = 'tour-layer';
tourLayer.hidden = true;
tourLayer.innerHTML = '<div class="tour-backdrop"></div><section class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title"><p class="tour-count"></p><h2 id="tour-title"></h2><p class="tour-text"></p><div class="tour-actions"><button type="button" class="text-button" data-tour="skip">Saltar</button><span></span><button type="button" class="secondary-button" data-tour="prev">Anterior</button><button type="button" class="primary-button" data-tour="next">Siguiente</button></div></section>';
document.body.append(tourLayer);

function clearTourHighlight() {
  document.querySelectorAll('.tour-highlight').forEach(node => node.classList.remove('tour-highlight'));
}

function showTourStep(index) {
  tourIndex = index;
  const step = tourSteps[index];
  clearTourHighlight();
  if (step.tab) {
    activateTab(step.tab);
    document.querySelector(`.tab[data-tab="${step.tab}"]`)?.classList.add('tour-highlight');
  }
  tourLayer.querySelector('.tour-count').textContent = `Paso ${index + 1} de ${tourSteps.length}`;
  tourLayer.querySelector('#tour-title').textContent = step.title;
  tourLayer.querySelector('.tour-text').textContent = step.text;
  tourLayer.querySelector('[data-tour="prev"]').hidden = index === 0;
  const last = index === tourSteps.length - 1;
  tourLayer.querySelector('[data-tour="next"]').textContent = last ? 'Terminar' : 'Siguiente';
  tourLayer.querySelector('[data-tour="skip"]').hidden = last;
}

function endTour() {
  tourLayer.hidden = true;
  tourIndex = -1;
  clearTourHighlight();
  if (window.currentUser) localStorage.setItem(`tour_done_${window.currentUser.id}`, '1');
}

function startTour() {
  tourSteps = TOUR_STEPS.filter(step => !step.minRole || hasRole(step.minRole));
  tourLayer.hidden = false;
  showTourStep(0);
  tourLayer.querySelector('[data-tour="next"]').focus();
}

tourLayer.addEventListener('click', (event) => {
  const action = event.target.closest('[data-tour]')?.dataset.tour;
  if (action === 'next') { if (tourIndex >= tourSteps.length - 1) endTour(); else showTourStep(tourIndex + 1); }
  else if (action === 'prev' && tourIndex > 0) showTourStep(tourIndex - 1);
  else if (action === 'skip') endTour();
});
document.addEventListener('keydown', (event) => {
  if (tourLayer.hidden) return;
  if (event.key === 'Escape') endTour();
  if (event.key === 'ArrowRight' && tourIndex < tourSteps.length - 1) showTourStep(tourIndex + 1);
  if (event.key === 'ArrowLeft' && tourIndex > 0) showTourStep(tourIndex - 1);
});
document.querySelector('#tour-start').addEventListener('click', startTour);

// Se ofrece automáticamente una sola vez por usuario en este navegador.
document.addEventListener('app:ready', (event) => {
  if (!localStorage.getItem(`tour_done_${event.detail.id}`)) setTimeout(startTour, 400);
});
