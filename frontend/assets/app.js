const state = {
  sessionId: localStorage.getItem("electoral_session_id") || null,
  loading: false,
  graphics: {
    catalog: null,
    mode: "temporal",
    chartType: "bar",
    rows: [],
    response: null,
    initialized: false,
  },
};

const messages = document.querySelector("#messages");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#question");
const sendButton = document.querySelector("#send-button");
const tableContainer = document.querySelector("#data-table");
const rowCount = document.querySelector("#row-count");

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((tab) => {
      const selected = tab === button;
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", String(selected));
    });
    document.querySelectorAll(".panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === button.dataset.tab);
    });
    if (button.dataset.tab === "graficos") initializeGraphics();
    if (button.dataset.tab === "campania") loadCampaign();
  });
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatText(text) {
  return escapeHtml(text).replaceAll("\n", "<br>");
}

function addMessage(text, role) {
  const article = document.createElement("article");
  article.className = `message ${role}-message`;
  article.innerHTML = role === "assistant"
    ? `<div class="avatar">IE</div><div class="bubble"><p>${formatText(text)}</p></div>`
    : `<div class="bubble"><p>${formatText(text)}</p></div>`;
  messages.appendChild(article);
  messages.scrollTop = messages.scrollHeight;
  return article;
}

function addLoading() {
  const node = document.createElement("article");
  node.className = "message assistant-message loading-message";
  node.innerHTML = `<div class="avatar">IE</div><div class="bubble typing"><i></i><i></i><i></i></div>`;
  messages.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function renderTable(rows) {
  if (!rows || rows.length === 0) {
    rowCount.textContent = "Sin filas";
    tableContainer.className = "table-wrap empty-table";
    tableContainer.innerHTML = `<div class="empty-state"><span>⌁</span><p>La consulta no produjo una tabla de datos.</p></div>`;
    return;
  }
  const columns = Object.keys(rows[0]);
  rowCount.textContent = `${rows.length} ${rows.length === 1 ? "fila" : "filas"}`;
  tableContainer.className = "table-wrap";
  tableContainer.innerHTML = `
    <table>
      <thead><tr>${columns.map((column) => `<th>${escapeHtml(column.replaceAll("_", " "))}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(row[column])}</td>`).join("")}</tr>`).join("")}</tbody>
    </table>`;
}

async function sendQuestion(question) {
  if (state.loading || !question.trim()) return;
  state.loading = true;
  sendButton.disabled = true;
  input.disabled = true;
  addMessage(question, "user");
  const loader = addLoading();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pregunta: question, session_id: state.sessionId }),
    });
    const data = await response.json();
    loader.remove();
    if (!response.ok) throw new Error(data.detail || "No fue posible completar la consulta.");
    state.sessionId = data.session_id;
    localStorage.setItem("electoral_session_id", state.sessionId);
    addMessage(data.respuesta, "assistant");
    renderTable(data.datos);
  } catch (error) {
    loader.remove();
    addMessage(error.message || "Ocurrió un error inesperado.", "assistant");
  } finally {
    state.loading = false;
    sendButton.disabled = false;
    input.disabled = false;
    input.value = "";
    input.style.height = "auto";
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  sendQuestion(input.value);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
});

document.querySelectorAll(".suggestions button").forEach((button) => {
  button.addEventListener("click", () => sendQuestion(button.textContent));
});

document.querySelector("#clear-chat").addEventListener("click", async () => {
  if (state.sessionId) {
    await fetch(`/api/chat/${encodeURIComponent(state.sessionId)}`, { method: "DELETE" }).catch(() => {});
  }
  state.sessionId = null;
  localStorage.removeItem("electoral_session_id");
  messages.innerHTML = `<article class="message assistant-message"><div class="avatar">IE</div><div class="bubble"><p>Nueva conversación iniciada. ¿Qué deseas consultar?</p></div></article>`;
  renderTable([]);
  input.focus();
});

fetch("/api/health")
  .then((response) => response.json())
  .then((health) => {
    const dot = document.querySelector("#status-dot");
    const text = document.querySelector("#status-text");
    dot.classList.toggle("online", health.ok);
    text.textContent = health.ok ? `${health.filas.toLocaleString("es-MX")} registros disponibles` : "Configuración pendiente";
  })
  .catch(() => { document.querySelector("#status-text").textContent = "Backend sin conexión"; });

// ---------------------------------------------------------------------------
// Resultados graficos
// ---------------------------------------------------------------------------

const graphicsForm = document.querySelector("#graphics-form");
const levelFilter = document.querySelector("#level-filter");
const unitFilter = document.querySelector("#unit-filter");
const yearOptions = document.querySelector("#year-options");
const electionOptions = document.querySelector("#election-options");
const partyOptions = document.querySelector("#party-options");
const partySearch = document.querySelector("#party-search");
const individualPartyField = document.querySelector("#individual-party-field");
const coalitionBuilder = document.querySelector("#coalition-builder");
const coalitionList = document.querySelector("#coalition-list");
const graphicsError = document.querySelector("#graphics-error");
const generateButton = document.querySelector("#generate-chart");

function checkboxList(container, values, name, selected = []) {
  const selectedSet = new Set(selected.map(String));
  container.innerHTML = values.map((value) => `
    <label class="check-option" title="${escapeHtml(value)}">
      <input type="checkbox" name="${escapeHtml(name)}" value="${escapeHtml(value)}" ${selectedSet.has(String(value)) ? "checked" : ""}>
      <span>${escapeHtml(value.replaceAll("_", " "))}</span>
    </label>`).join("");
}

function checkedValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((node) => node.value);
}

function updateUnits() {
  const level = state.graphics.catalog?.niveles.find((item) => item.valor === levelFilter.value);
  unitFilter.innerHTML = (level?.unidades || []).map((value) =>
    `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`
  ).join("");
}

function setGraphicsDefaults(catalog) {
  levelFilter.innerHTML = catalog.niveles.map((level) =>
    `<option value="${escapeHtml(level.valor)}">${escapeHtml(level.etiqueta)}</option>`
  ).join("");
  const municipality = catalog.niveles.find((level) => level.valor === "municipio");
  if (municipality) levelFilter.value = "municipio";
  updateUnits();

  const defaultYear = catalog.anios.length ? [catalog.anios.at(-1)] : [];
  checkboxList(yearOptions, catalog.anios, "graphics-year", defaultYear);
  checkboxList(electionOptions, catalog.elecciones, "graphics-election", catalog.elecciones.slice(0, 1));
  const preferred = ["PAN", "PRI", "MORENA", "PVEM"].filter((p) => catalog.partidos.includes(p));
  checkboxList(partyOptions, catalog.partidos, "graphics-party", preferred.slice(0, 3));
}

async function initializeGraphics() {
  if (state.graphics.initialized) return;
  state.graphics.initialized = true;
  graphicsError.textContent = "Cargando filtros…";
  try {
    const response = await fetch("/api/graficos/catalogo");
    const catalog = await response.json();
    if (!response.ok) throw new Error(catalog.detail || "No fue posible cargar los filtros.");
    state.graphics.catalog = catalog;
    setGraphicsDefaults(catalog);
    graphicsError.textContent = "";
  } catch (error) {
    state.graphics.initialized = false;
    graphicsError.textContent = error.message;
  }
}

levelFilter.addEventListener("change", updateUnits);

partySearch.addEventListener("input", () => {
  const query = partySearch.value.trim().toLocaleLowerCase("es");
  partyOptions.querySelectorAll(".check-option").forEach((option) => {
    option.hidden = !option.textContent.toLocaleLowerCase("es").includes(query);
  });
});

document.querySelectorAll(".mode-button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".mode-button").forEach((item) => item.classList.toggle("active", item === button));
    state.graphics.mode = button.dataset.mode;
    const coalitionMode = state.graphics.mode === "coaliciones";
    coalitionBuilder.hidden = !coalitionMode;
    individualPartyField.hidden = coalitionMode;
    if (coalitionMode && coalitionList.children.length === 0) addCoalition();
  });
});

function addCoalition() {
  const index = coalitionList.children.length + 1;
  const row = document.createElement("div");
  row.className = "coalition-row";
  row.innerHTML = `
    <div class="coalition-row-head">
      <input class="coalition-name" type="text" maxlength="80" value="Coalición ${index}" aria-label="Nombre de coalición">
      <button class="remove-coalition" type="button" aria-label="Eliminar coalición">×</button>
    </div>
    <div class="check-options coalition-party-options" role="group" aria-label="Partidos de la coalición">
      ${(state.graphics.catalog?.partidos_coalicion || []).map((party) => `
        <label class="check-option" title="${escapeHtml(party)}">
          <input class="coalition-party" type="checkbox" value="${escapeHtml(party)}">
          <span>${escapeHtml(party.replaceAll("_", " "))}</span>
        </label>`).join("")}
    </div>
    <span class="coalition-counter">0 partidos seleccionados</span>`;
  row.querySelector(".remove-coalition").addEventListener("click", () => {
    row.remove();
    syncCoalitionParties();
  });
  row.querySelectorAll(".coalition-party").forEach((checkbox) => {
    checkbox.addEventListener("change", syncCoalitionParties);
  });
  coalitionList.appendChild(row);
  syncCoalitionParties();
}

document.querySelector("#add-coalition").addEventListener("click", addCoalition);

function getCoalitions() {
  return [...coalitionList.querySelectorAll(".coalition-row")].map((row) => ({
    nombre: row.querySelector(".coalition-name").value.trim(),
    partidos: [...row.querySelectorAll(".coalition-party:checked")].map((checkbox) => checkbox.value),
  })).filter((group) => group.nombre || group.partidos.length);
}

function syncCoalitionParties() {
  const rows = [...coalitionList.querySelectorAll(".coalition-row")];
  rows.forEach((currentRow) => {
    const selectedElsewhere = new Set(
      rows.filter((row) => row !== currentRow)
        .flatMap((row) => [...row.querySelectorAll(".coalition-party:checked")].map((checkbox) => checkbox.value))
    );

    currentRow.querySelectorAll(".coalition-party").forEach((checkbox) => {
      const unavailable = selectedElsewhere.has(checkbox.value) && !checkbox.checked;
      checkbox.disabled = unavailable;
      checkbox.closest(".check-option").classList.toggle("disabled", unavailable);
    });

    const count = currentRow.querySelectorAll(".coalition-party:checked").length;
    currentRow.querySelector(".coalition-counter").textContent =
      `${count} ${count === 1 ? "partido seleccionado" : "partidos seleccionados"}`;
  });
}

function buildPayload() {
  return {
    modo: state.graphics.mode,
    nivel: levelFilter.value,
    unidades: [unitFilter.value],
    anios: checkedValues("graphics-year"),
    elecciones: checkedValues("graphics-election"),
    partidos: state.graphics.mode === "coaliciones" ? [] : checkedValues("graphics-party"),
    grupos: state.graphics.mode === "coaliciones" ? getCoalitions() : [],
    metrica: document.querySelector("#metric-filter").value,
  };
}

graphicsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  graphicsError.textContent = "";
  generateButton.disabled = true;
  generateButton.textContent = "Construyendo…";
  try {
    const response = await fetch("/api/graficos/analizar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload()),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "No fue posible generar la comparación.");
    if (!data.filas.length) throw new Error(data.avisos?.[0] || "La selección no produjo resultados.");
    state.graphics.rows = data.filas;
    state.graphics.response = data;
    renderGraphics();
  } catch (error) {
    graphicsError.textContent = error.message;
  } finally {
    generateButton.disabled = false;
    generateButton.textContent = "Generar comparación";
  }
});

function formatNumber(value, decimals = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("es-MX", { maximumFractionDigits: decimals, minimumFractionDigits: decimals });
}

function traceGrouping(row, xField) {
  const details = [row.serie, row.unidad];
  if (xField !== "tipo_eleccion") details.push(row.tipo_eleccion?.replaceAll("_", " "));
  if (xField !== "anio" && row.anio !== undefined) details.push(row.anio);
  return details.filter(Boolean).join(" · ");
}

function chartConfig() {
  const mode = state.graphics.mode;
  const rows = state.graphics.rows;
  if (mode === "temporal" && rows.some((row) => row.anio !== undefined)) return { xField: "anio", xTitle: "Año" };
  if (mode === "diferenciado") return { xField: "tipo_eleccion", xTitle: "Tipo de elección" };
  const years = new Set(rows.map((row) => row.anio).filter(Boolean));
  return years.size > 1 ? { xField: "anio", xTitle: "Año" } : { xField: "tipo_eleccion", xTitle: "Tipo de elección" };
}

function renderPlot() {
  if (!window.Plotly) {
    graphicsError.textContent = "No se pudo cargar la librería de gráficas. Verifica tu conexión a internet.";
    return;
  }
  const metric = document.querySelector("#metric-filter").value;
  const { xField, xTitle } = chartConfig();
  const grouped = new Map();
  state.graphics.rows.forEach((row) => {
    const key = traceGrouping(row, xField);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(row);
  });

  const traces = [...grouped.entries()].map(([name, rows]) => {
    rows.sort((a, b) => String(a[xField]).localeCompare(String(b[xField]), "es", { numeric: true }));
    const common = {
      name,
      x: rows.map((row) => String(row[xField]).replaceAll("_", " ")),
      y: rows.map((row) => Number(row[metric] || 0)),
      customdata: rows.map((row) => [row.votos, row.porcentaje, row.variacion_votos, row.variacion_pp]),
      hovertemplate: `<b>%{fullData.name}</b><br>${xTitle}: %{x}<br>Votos: %{customdata[0]:,.0f}<br>Porcentaje: %{customdata[1]:.2f}%<br>Variación votos: %{customdata[2]:+,.0f}<br>Variación pp: %{customdata[3]:+.2f}<extra></extra>`,
    };
    if (state.graphics.chartType === "line") return { ...common, type: "scatter", mode: "lines+markers", line: { width: 3 }, marker: { size: 8 } };
    return { ...common, type: "bar" };
  });

  Plotly.react("electoral-chart", traces, {
    autosize: true,
    barmode: "group",
    margin: { l: 65, r: 25, t: 30, b: 80 },
    paper_bgcolor: "#ffffff",
    plot_bgcolor: "#ffffff",
    font: { family: "Inter, system-ui, sans-serif", color: "#364155", size: 12 },
    colorway: ["#1a2940", "#d5a84b", "#16845b", "#c84646", "#677ca6", "#9a6b9e", "#d47742"],
    xaxis: { title: xTitle, tickangle: -25, gridcolor: "#eef1f5" },
    yaxis: { title: metric === "votos" ? "Votos" : "Porcentaje (%)", ticksuffix: metric === "porcentaje" ? "%" : "", gridcolor: "#e7ebf0", rangemode: "tozero" },
    legend: { orientation: "h", y: -0.28, x: 0 },
    hoverlabel: { bgcolor: "#111b2b", font: { color: "white" } },
  }, { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d"] });
}

function renderSummary() {
  const response = state.graphics.response;
  const rows = state.graphics.rows;
  const latest = [...rows].sort((a, b) => String(b.anio || "").localeCompare(String(a.anio || ""), "es", { numeric: true }));
  const leader = latest.reduce((best, row) => !best || Number(row.votos) > Number(best.votos) ? row : best, null);
  const maxGrowth = rows.filter((row) => row.variacion_pp !== null).reduce((best, row) => !best || Number(row.variacion_pp) > Number(best.variacion_pp) ? row : best, null);
  const cards = [
    ["Votos representados", formatNumber(response.resumen.votos_representados)],
    ["Series comparadas", formatNumber(response.resumen.series)],
    ["Mayor votación", leader ? leader.serie : "—"],
    ["Mayor avance", maxGrowth ? `${maxGrowth.serie} (${Number(maxGrowth.variacion_pp) >= 0 ? "+" : ""}${formatNumber(maxGrowth.variacion_pp, 2)} pp)` : "Sin periodo anterior"],
  ];
  document.querySelector("#summary-cards").innerHTML = cards.map(([label, value]) =>
    `<div class="summary-card"><span>${escapeHtml(label)}</span><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong></div>`
  ).join("");
}

function renderGraphicsTable() {
  const rows = state.graphics.rows;
  const columns = ["anio", "unidad", "tipo_eleccion", "serie", "votos", "porcentaje", "variacion_votos", "variacion_pp"].filter((column) => rows.some((row) => row[column] !== undefined));
  document.querySelector("#graphics-table").innerHTML = `
    <table><thead><tr>${columns.map((column) => `<th>${escapeHtml(column.replaceAll("_", " "))}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((row) => `<tr>${columns.map((column) => {
      const value = ["porcentaje", "variacion_pp"].includes(column) && row[column] !== null ? `${formatNumber(row[column], 2)}%` : row[column];
      return `<td>${escapeHtml(value ?? "—")}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table>`;
}

function renderGraphics() {
  document.querySelector("#graphics-intro").hidden = true;
  document.querySelector("#graphics-results").hidden = false;
  document.querySelector("#download-chart").disabled = false;
  const payload = buildPayload();
  document.querySelector("#chart-title").textContent = `${unitFilter.value} · ${payload.metrica === "votos" ? "Votos" : "Porcentaje de votación"}`;
  document.querySelector("#chart-eyebrow").textContent = state.graphics.mode === "temporal" ? "EVOLUCIÓN TEMPORAL" : state.graphics.mode === "diferenciado" ? "VOTO DIFERENCIADO" : "SIMULACIÓN DE COALICIONES";
  renderSummary();
  renderPlot();
  renderGraphicsTable();
  document.querySelector("#chart-warnings").innerHTML = (state.graphics.response.avisos || []).map((warning) => `<div class="chart-warning">${escapeHtml(warning)}</div>`).join("");
}

document.querySelectorAll(".chart-type").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".chart-type").forEach((item) => item.classList.toggle("active", item === button));
    state.graphics.chartType = button.dataset.chart;
    if (state.graphics.rows.length) renderPlot();
  });
});

document.querySelector("#download-chart").addEventListener("click", () => {
  if (!window.Plotly || !state.graphics.rows.length) return;
  Plotly.downloadImage("electoral-chart", { format: "png", filename: "comparacion_electoral", width: 1400, height: 800, scale: 2 });
});

document.querySelector("#download-data").addEventListener("click", () => {
  if (!state.graphics.rows.length) return;
  const columns = Object.keys(state.graphics.rows[0]);
  const quote = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const csv = [columns.map(quote).join(","), ...state.graphics.rows.map((row) => columns.map((column) => quote(row[column])).join(","))].join("\n");
  const blob = new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "resultados_electorales.csv";
  link.click();
  URL.revokeObjectURL(link.href);
});

// Noticias y ficha informativa territorial
let recentNews = [];

const newsForm = document.querySelector('#news-form');

function renderWordCloud(items) {
  if (!items.length) return '<p>No se identificaron palabras o frases repetidas en al menos dos notas.</p>';
  const frequencies = items.map(item => Number(item.noticias) || 1);
  const minimum = Math.min(...frequencies);
  const maximum = Math.max(...frequencies);
  const colors = ['#174b37', '#9a6b20', '#315d72', '#743f45', '#52705c'];
  return `<div class="word-cloud" role="img" aria-label="Nube de palabras y frases asociadas">${items.map((item, index) => {
    const relative = maximum === minimum ? 0.5 : (item.noticias - minimum) / (maximum - minimum);
    const fontSize = 16 + Math.round(relative * 28);
    const weight = 550 + Math.round(relative * 250);
    return `<span style="font-size:${fontSize}px;font-weight:${weight};color:${colors[index % colors.length]}" title="Aparece en ${item.noticias} notas de ${item.fuentes} fuentes">${escapeHtml(item.frase)}<small>${item.noticias}</small></span>`;
  }).join('')}</div><p class="cloud-help">El tamaño representa el número de notas distintas en las que aparece cada expresión. El número pequeño muestra esa frecuencia.</p>`;
}

function renderToneTrafficLight(semaforo) {
  const categories = [
    ['negativo', 'Negativas o críticas', '#b63a3a'],
    ['neutro', 'Neutras', '#c49a2c'],
    ['positivo', 'Positivas', '#2f855a'],
  ];
  if (!semaforo || !semaforo.total) return '<p>No hay suficientes notas para calcular el tono de la cobertura.</p>';
  return `<div class="tone-grid">${categories.map(([key, label, color]) => {
    const item = semaforo[key] || { porcentaje: 0, notas: 0 };
    return `<article class="tone-card"><span class="tone-light" style="background:${color}"></span><div><strong>${Number(item.porcentaje).toFixed(1)}%</strong><p>${label}</p><small>${item.notas} ${item.notas === 1 ? 'nota' : 'notas'}</small></div></article>`;
  }).join('')}</div><div class="tone-bar" aria-label="Distribución del tono">${categories.map(([key,,color]) => `<span style="width:${semaforo[key]?.porcentaje || 0}%;background:${color}"></span>`).join('')}</div><p class="tone-note">${escapeHtml(semaforo.aviso || '')}</p>`;
}

function renderCoverageByTone(analysis) {
  const tone = analysis?.por_tono;
  if (!tone) return '';
  const rows = [
    ['negativo', 'Qué dicen las notas negativas o críticas'],
    ['neutro', 'Qué dicen las notas neutras'],
    ['positivo', 'Qué dicen las notas positivas'],
  ];
  return `<div class="tone-summaries">${rows.map(([key, title]) => `<article class="tone-summary ${key}"><h3>${title}</h3><p>${escapeHtml(tone[key] || 'No hay evidencia suficiente en esta categoría.')}</p></article>`).join('')}</div>`;
}

document.querySelector('#news-to').value = new Date().toISOString().slice(0, 10);
document.querySelector('#news-from').value = `${new Date().getFullYear()}-01-01`;

newsForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const status = document.querySelector('#news-status');
  const target = document.querySelector('#news-results');
  const button = newsForm.querySelector('button');
  button.disabled = true;
  status.textContent = 'Buscando noticias y leyendo artículos. Esto puede tardar un momento…';
  try {
    const response = await fetch('/api/posicionamiento/analizar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nombre: document.querySelector('#news-name').value,
        fecha_inicio: document.querySelector('#news-from').value,
        fecha_fin: document.querySelector('#news-to').value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Error al buscar noticias.');
    recentNews = data.noticias;
    status.textContent = `${recentNews.length} notas encontradas · ${data.asociaciones.length} expresiones presentes en al menos dos notas. ${data.aviso}`;
    const analysis = data.analisis || {};
    target.innerHTML = `<section class="media-analysis"><p class="eyebrow">SÍNTESIS GENERAL</p><h2>Resumen de toda la cobertura</h2><p class="coverage-summary">${escapeHtml(analysis.resumen || 'No hay resumen disponible.')}</p>${renderCoverageByTone(analysis)}<h3>¿Qué representa esta cobertura?</h3><p>${escapeHtml(analysis.lectura || 'La cobertura disponible no permite una interpretación suficiente.')}</p><small>Alcance analizado: ${escapeHtml(analysis.alcance || `${recentNews.length} notas`)} · Síntesis: ${escapeHtml(analysis.metodo_resumen || 'automática')}</small></section>
      <section class="tone-section"><p class="eyebrow">TONO DE LA COBERTURA</p><h2>Semáforo de notas</h2>${renderToneTrafficLight(data.semaforo)}</section>
      <section class="cloud-section"><p class="eyebrow">ASOCIACIONES RECURRENTES</p><h2>Palabras y frases más mencionadas</h2>${renderWordCloud(data.asociaciones || [])}</section>
      <h2>Notas consultadas</h2><ul class="evidence-list">${recentNews.map(n => `<li><span class="tone-badge ${escapeHtml(n.tono || 'neutro')}">${escapeHtml(n.tono || 'neutro')}</span> <a href="${escapeHtml(n.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(n.titulo)}</a><br><small>${escapeHtml(n.fuente)} · ${escapeHtml(n.fecha_buscador || 'Fecha no indicada')} · ${n.texto_disponible ? 'Texto extraído' : 'Solo título y resumen'}</small></li>`).join('')}</ul>`;
  } catch (error) { status.textContent = error.message; target.innerHTML = ''; recentNews = []; }
  finally { button.disabled = false; }
});


// Conversación de discursos: el último borrador queda disponible para exportar.
let speechHistory = [];
let latestSpeech = '';
const speechMessages = document.querySelector('#speech-messages');
const speechStatus = document.querySelector('#speech-status');
function speechMessage(value, role) {
  const node = document.createElement('div');
  node.className = `speech-message ${role}`;
  node.textContent = value;
  speechMessages.appendChild(node);
  speechMessages.scrollTop = speechMessages.scrollHeight;
  return node;
}
document.querySelector('#speech-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const prompt = document.querySelector('#speech-prompt');
  const mensaje = prompt.value.trim();
  if (!mensaje) return;
  const button = form.querySelector('button');
  button.disabled = true;
  speechMessage(mensaje, 'user');
  prompt.value = '';
  const waiting = speechMessage('Preparando borrador…', 'assistant');
  speechStatus.textContent = '';
  try {
    const response = await fetch('/api/discursos/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mensaje, historial: speechHistory }) });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo generar el discurso.');
    waiting.textContent = data.respuesta;
    speechHistory.push({ role: 'user', content: mensaje }, { role: 'assistant', content: data.respuesta });
    latestSpeech = data.respuesta;
    document.querySelectorAll('[data-speech-action]').forEach(el => { el.disabled = false; });
    speechStatus.textContent = 'Puedes pedir ajustes, descargar el borrador o compartirlo.';
  } catch (error) {
    waiting.remove();
    speechStatus.textContent = error.message;
    prompt.value = mensaje;
  } finally { button.disabled = false; prompt.focus(); }
});
document.querySelector('#speech-clear').addEventListener('click', () => {
  speechHistory = []; latestSpeech = '';
  speechMessages.innerHTML = '';
  speechMessage('Cuéntame para qué ocasión necesitas el discurso, su tema y duración.', 'assistant');
  document.querySelectorAll('[data-speech-action]').forEach(el => { el.disabled = true; });
  speechStatus.textContent = 'Nueva conversación lista.';
});
document.querySelectorAll('[data-speech-action]').forEach(button => button.addEventListener('click', async () => {
  if (!latestSpeech) return;
  const action = button.dataset.speechAction;
  try {
    if (action === 'copy') {
      await navigator.clipboard.writeText(latestSpeech);
      speechStatus.textContent = 'Texto copiado.';
    } else if (action === 'share' && navigator.share) {
      await navigator.share({ title: 'Discurso', text: latestSpeech });
    } else if (action === 'share') {
      await navigator.clipboard.writeText(latestSpeech);
      speechStatus.textContent = 'Tu navegador no ofrece compartir. Se copió el texto para que puedas pegarlo.';
    } else {
      const response = await fetch(`/api/discursos/exportar/${action}`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ texto: latestSpeech }) });
      if (!response.ok) { const data = await response.json(); throw new Error(data.detail || 'Error al descargar.'); }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = `discurso.${action}`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  } catch (error) { if (error.name !== 'AbortError') speechStatus.textContent = error.message; }
}));

// Tablero de campaña persistido en SQLite mediante la API.
let campaign = { areas: [], metas: [], personas: [], tareas: [] };
const campaignStatus = document.querySelector('#campaign-status');
async function campaignRequest(path, options = {}) {
  const response = await fetch(`/api/campania${path}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo guardar el cambio.');
  return data;
}
async function loadCampaign() {
  try { campaign = await campaignRequest(''); renderCampaign(); campaignStatus.textContent = ''; }
  catch (error) { campaignStatus.textContent = error.message; }
}
function campaignOption(select, rows, label) {
  const previous = select.value;
  select.innerHTML = `<option value="">${label}</option>`;
  rows.forEach(row => select.add(new Option(row.titulo || row.nombre, row.id)));
  select.value = previous;
}
function renderCampaign() {
  campaignOption(document.querySelector('#task-goal'), campaign.metas, 'Sin meta');
  campaignOption(document.querySelector('#task-person'), campaign.personas, 'Sin responsable');
  campaignOption(document.querySelector('#person-area'), campaign.areas, 'Sin área');
  campaignOption(document.querySelector('#goal-area'), campaign.areas, 'Sin área');
  campaignOption(document.querySelector('#goal-person'), campaign.personas, 'Sin responsable');
  for (const [key, target, label] of [['areas', '#area-list', 'nombre'], ['metas', '#goal-list', 'titulo'], ['personas', '#person-list', 'nombre']]) {
    document.querySelector(target).innerHTML = campaign[key].map(row =>
      `<span class="campaign-chip">${escapeHtml(row[label])}<button type="button" data-delete="${key}" data-id="${row.id}" aria-label="Eliminar ${escapeHtml(row[label])}">×</button></span>`).join('') || '<small>Sin registros todavía</small>';
  }
  const labels = { por_hacer: 'Por hacer', en_proceso: 'En proceso', finalizada: 'Finalizada' };
  document.querySelector('#task-board').innerHTML = Object.entries(labels).map(([status, title]) => {
    const rows = campaign.tareas.filter(t => t.estado === status);
    return `<section class="task-column" data-column="${status}"><h2>${title} <span>${rows.length}</span></h2><div class="task-stack">${rows.map(t => {
      const goal = campaign.metas.find(m => m.id === t.meta_id);
      const person = campaign.personas.find(p => p.id === t.persona_id);
      return `<article class="task-card priority-${t.prioridad || 'media'}" draggable="true" data-task="${t.id}"><div class="task-card-heading"><strong>${escapeHtml(t.titulo)}</strong><span>${escapeHtml(t.prioridad || 'media')} · ${t.peso || 3} pts</span></div><p>${escapeHtml(t.descripcion || '')}</p><small>${goal ? 'Meta: ' + escapeHtml(goal.titulo) : 'Sin meta'} · ${person ? 'Responsable: ' + escapeHtml(person.nombre) : 'Sin responsable'}${t.fecha_limite ? ' · Hasta: ' + escapeHtml(t.fecha_limite) : ''}${t.evidencia ? ' · Evidencia: ' + escapeHtml(t.evidencia) : ''}</small><div class="task-controls"><select data-state="${t.id}" aria-label="Estado de ${escapeHtml(t.titulo)}">${Object.entries(labels).map(([code, name]) => `<option value="${code}" ${code === status ? 'selected' : ''}>${name}</option>`).join('')}</select><button class="text-button" data-delete="tareas" data-id="${t.id}" aria-label="Eliminar tarea">Eliminar</button></div></article>`;
    }).join('') || '<p class="task-empty">Suelta una tarea aquí</p>'}</div></section>`;
  }).join('');
  renderCampaignDashboard();
}
function metricCard(label, value, tone = '') { return `<article class="campaign-kpi ${tone}"><span>${label}</span><strong>${value}</strong></article>`; }
function progressRow(name, done, total, extra = '') {
  const percent = total ? Math.round(done * 100 / total) : 0;
  return `<div class="progress-row"><div><strong>${escapeHtml(name)}</strong><span>${done}/${total} finalizadas ${extra}</span></div><b>${percent}%</b><div class="progress-track"><i style="width:${percent}%"></i></div></div>`;
}
function renderCampaignDashboard() {
  const tasks = campaign.tareas; const today = new Date().toISOString().slice(0, 10);
  const count = state => tasks.filter(t => t.estado === state).length;
  const overdue = tasks.filter(t => t.estado !== 'finalizada' && t.fecha_limite && t.fecha_limite < today).length;
  document.querySelector('#campaign-kpis').innerHTML = metricCard('Metas activas', campaign.metas.length) + metricCard('Por empezar', count('por_hacer')) + metricCard('En proceso', count('en_proceso'), 'blue') + metricCard('Finalizadas', count('finalizada'), 'green') + metricCard('Vencidas', overdue, overdue ? 'red' : '');
  document.querySelector('#goal-progress').innerHTML = campaign.metas.map(g => { const rows = tasks.filter(t => t.meta_id === g.id); return progressRow(g.titulo, rows.filter(t => t.estado === 'finalizada').length, rows.length, g.fecha_limite ? `· vence ${g.fecha_limite}` : ''); }).join('') || '<p class="empty-dashboard">Aún no hay metas.</p>';
  document.querySelector('#person-performance').innerHTML = campaign.personas.map(p => { const rows = tasks.filter(t => t.persona_id === p.id); const done = rows.filter(t => t.estado === 'finalizada'); const points = done.reduce((sum, t) => sum + Number(t.peso || 3), 0); return progressRow(p.nombre, done.length, rows.length, `· ${points} puntos`); }).join('') || '<p class="empty-dashboard">Aún no hay personas.</p>';
  document.querySelector('#area-performance').innerHTML = campaign.areas.map(a => { const people = campaign.personas.filter(p => p.area_id === a.id).map(p => p.id); const goals = campaign.metas.filter(g => g.area_id === a.id).map(g => g.id); const rows = tasks.filter(t => people.includes(t.persona_id) || goals.includes(t.meta_id)); return progressRow(a.nombre, rows.filter(t => t.estado === 'finalizada').length, rows.length); }).join('') || '<p class="empty-dashboard">Crea áreas para comparar equipos.</p>';
  const alerts = [];
  tasks.filter(t => t.estado !== 'finalizada' && t.fecha_limite && t.fecha_limite < today).forEach(t => alerts.push(`Tarea vencida: ${t.titulo}`));
  tasks.filter(t => !t.persona_id).forEach(t => alerts.push(`Sin responsable: ${t.titulo}`));
  campaign.metas.filter(g => !tasks.some(t => t.meta_id === g.id)).forEach(g => alerts.push(`Meta sin tareas: ${g.titulo}`));
  document.querySelector('#campaign-alerts').innerHTML = alerts.slice(0, 10).map(a => `<p class="campaign-alert">${escapeHtml(a)}</p>`).join('') || '<p class="campaign-ok">No hay alertas operativas.</p>';
}
for (const [id, key] of [['#area-form', 'areas'], ['#goal-form', 'metas'], ['#person-form', 'personas'], ['#task-form', 'tareas']]) {
  document.querySelector(id).addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    ['meta_id', 'persona_id', 'responsable_id', 'area_id'].forEach(field => { if (field in values) values[field] = values[field] ? Number(values[field]) : null; });
    if (key === 'metas') { values.objetivo = values.objetivo ? Number(values.objetivo) : null; values.fecha_limite ||= null; }
    if (key === 'personas') values.area_id = values.area_id ? Number(values.area_id) : null;
    if (key === 'tareas') {
      values.fecha_limite ||= null;
      values.peso = Number(values.peso);
    }
    try { await campaignRequest(`/${key}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
      form.reset(); await loadCampaign(); campaignStatus.textContent = 'Guardado.';
    } catch (error) { campaignStatus.textContent = error.message; }
  });
}
document.querySelectorAll('[data-campaign-view]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-campaign-view]').forEach(item => item.classList.toggle('active', item === button));
  document.querySelector('#campaign-dashboard').hidden = button.dataset.campaignView !== 'dashboard';
  document.querySelector('#campaign-work').hidden = button.dataset.campaignView !== 'work';
}));
document.querySelector('#campania').addEventListener('click', async event => {
  const button = event.target.closest('[data-delete]');
  if (!button || !confirm('¿Eliminar este registro?')) return;
  try { await campaignRequest(`/${button.dataset.delete}/${button.dataset.id}`, { method: 'DELETE' });
    await loadCampaign(); campaignStatus.textContent = 'Eliminado.';
  } catch (error) { campaignStatus.textContent = error.message; }
});
async function updateTask(id, estado) {
  try { await campaignRequest(`/tareas/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ estado }) });
    await loadCampaign(); campaignStatus.textContent = 'Estado actualizado.';
  } catch (error) { campaignStatus.textContent = error.message; await loadCampaign(); }
}
document.querySelector('#task-board').addEventListener('change', event => {
  if (event.target.matches('[data-state]')) updateTask(event.target.dataset.state, event.target.value);
});
document.querySelector('#task-board').addEventListener('dragstart', event => {
  const card = event.target.closest('[data-task]');
  if (card) event.dataTransfer.setData('text/plain', card.dataset.task);
});
document.querySelector('#task-board').addEventListener('dragover', event => {
  if (event.target.closest('[data-column]')) event.preventDefault();
});
document.querySelector('#task-board').addEventListener('drop', event => {
  const col = event.target.closest('[data-column]');
  const id = event.dataTransfer.getData('text/plain');
  if (col && id) { event.preventDefault(); updateTask(id, col.dataset.column); }
});
