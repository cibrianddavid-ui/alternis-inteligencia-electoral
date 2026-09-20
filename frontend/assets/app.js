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
    groups: [],
    order: [],
  },
};

const messages = document.querySelector("#messages");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#question");
const sendButton = document.querySelector("#send-button");
const tableContainer = document.querySelector("#data-table");
const rowCount = document.querySelector("#row-count");

function activateTab(tabId, updateHash = true) {
  const button = document.querySelector(`.tab[data-tab="${tabId}"]`);
  if (!button || !hasRole(button.dataset.minRole || "consulta")) return false;
  document.querySelectorAll(".tab").forEach((tab) => {
    const selected = tab === button;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === tabId);
  });
  if (tabId === "graficos") initializeGraphics();
  if (tabId === "campania") loadCampaign();
  if (updateHash && !location.hash.startsWith(`#${tabId}`)) history.replaceState(null, "", `#${tabId}`);
  document.dispatchEvent(new CustomEvent("tab:changed", { detail: tabId }));
  return true;
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
});

// Enlaces directos: #territorio/municipio/MATEHUALA, #discursos, #campania...
function routeFromHash() {
  const [tabId] = decodeURIComponent(location.hash.slice(1)).split("/");
  const active = document.querySelector(".tab.active")?.dataset.tab;
  if (tabId && tabId !== active) activateTab(tabId, false);
}
window.addEventListener("hashchange", routeFromHash);

function toast(message) {
  const node = document.createElement("div");
  node.className = "toast";
  node.setAttribute("role", "status");
  node.textContent = message;
  document.body.append(node);
  setTimeout(() => node.remove(), 3800);
}
document.addEventListener("app:notice", (event) => toast(event.detail));

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

// ---------------------------------------------------------------------------
// Markdown seguro para las respuestas del asistente
// Todo el texto se escapa ANTES de interpretar el formato; las etiquetas que
// aparecen en el resultado las genera este código, nunca el modelo.
// ---------------------------------------------------------------------------

function inlineMarkdown(escaped) {
  const codes = [];
  return escaped
    .replace(/`([^`\n]+)`/g, (_, code) => `\u0000${codes.push(code) - 1}\u0000`)
    .replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?![*\w])/g, "$1<em>$2</em>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/&lt;br\s*\/?&gt;/gi, "<br>")
    .replace(/\u0000(\d+)\u0000/g, (_, index) => `<code>${codes[Number(index)]}</code>`);
}

function renderMarkdown(source) {
  const lines = String(source ?? "").replaceAll("\u0000", "").replace(/\r\n?/g, "\n").split("\n");
  const inline = (text) => inlineMarkdown(escapeHtml(text));
  const html = [];
  let paragraph = [];
  let list = null;
  const flushParagraph = () => {
    if (paragraph.length) html.push(`<p>${paragraph.join("<br>")}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (list) html.push(`<${list.type}>${list.items.map((item) => `<li>${item}</li>`).join("")}</${list.type}>`);
    list = null;
  };
  const flush = () => { flushParagraph(); flushList(); };
  const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  const isSeparator = (line) => line.includes("|") && line.includes("-")
    && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush();
      const code = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) { code.push(lines[i]); i += 1; }
      html.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flush(); html.push("<hr>"); continue; }

    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.*)$/);
    if (heading) {
      flush();
      const level = heading[1].length <= 2 ? 3 : 4;
      html.push(`<h${level}>${inline(heading[2].replace(/\s+#+\s*$/, ""))}</h${level}>`);
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && isSeparator(lines[i + 1])) {
      flush();
      const head = cells(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) { rows.push(cells(lines[i])); i += 1; }
      i -= 1;
      html.push(`<div class="md-table"><table><thead><tr>${head.map((cell) => `<th>${inline(cell)}</th>`).join("")}</tr></thead>`
        + `<tbody>${rows.map((row) => `<tr>${head.map((_, k) => `<td>${inline(row[k] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }

    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d{1,2}[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      const type = bullet ? "ul" : "ol";
      if (list && list.type !== type) flushList();
      list = list || { type, items: [] };
      list.items.push(inline((bullet || numbered)[1]));
      continue;
    }

    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) { flush(); html.push(`<blockquote>${inline(quote[1])}</blockquote>`); continue; }

    flushList();
    paragraph.push(inline(line.trim()));
  }
  flush();
  return html.join("");
}

// ---------------------------------------------------------------------------
// Colores por partido y chips de interpretación
// ---------------------------------------------------------------------------

// Colores de uso común en la cobertura electoral de México. ML y CP no tienen un
// color de referencia establecido: son provisionales y pueden ajustarse aquí.
const PARTY_COLORS = {
  PAN: "#0b5fa5", PRI: "#e4002b", PRD: "#f4c20d", PT: "#b5121b", MORENA: "#7e2a4f",
  PVEM: "#3ba935", MC: "#ff8200", PES: "#5b3f9e", NA: "#00a7b5", ML: "#6b8e9b", CP: "#8a6d3b",
  NULOS: "#9aa3af", NO_REGISTRADAS: "#c7ccd4",
};
const FALLBACK_COLORS = ["#5c6b7a", "#8d6e63", "#3f8f8f", "#b0a04a", "#7986cb", "#a1887f"];
const LINE_DASHES = ["solid", "dash", "dot", "dashdot", "longdash"];
const BAR_OPACITY = [1, 0.72, 0.5, 0.34];

// Devuelve los colores de una serie. Una coalición (PAN_PRI_PRD, o un grupo
// creado por la persona) usa los colores de sus partidos integrantes.
function seriesStyle(serie) {
  const name = String(serie);
  if (PARTY_COLORS[name]) return { colors: [PARTY_COLORS[name]], combo: false };
  const custom = state.graphics.groups.find((group) => group.nombre === name);
  const members = custom ? custom.partidos : name.split("_");
  const colors = members.map((party) => PARTY_COLORS[party]).filter(Boolean);
  if (colors.length && (custom || colors.length === members.length)) {
    return { colors, combo: members.length > 1 };
  }
  const index = Math.max(0, state.graphics.order.indexOf(name));
  return { colors: [FALLBACK_COLORS[index % FALLBACK_COLORS.length]], combo: false };
}

function swatchHtml(serie) {
  const { colors, combo } = seriesStyle(serie);
  const background = combo && colors[1] ? `linear-gradient(135deg, ${colors[0]} 50%, ${colors[1]} 50%)` : colors[0];
  return `<span class="swatch" style="background:${background}" aria-hidden="true"></span>`;
}

const DIMENSION_LABELS = {
  entidad: "Estado", municipio: "Municipio", seccion: "Sección", id_distrito_local: "Distrito local",
  id_distrito_federal: "Distrito federal", tipo_eleccion: "Elección", partido: "Partido", anio: "Año",
};
const ELECTION_LABELS = {
  DIPUTACION_LOC: "Diputación local", DIP_FEDERAL: "Diputación federal", AYUNTAMIENTO: "Ayuntamiento",
  PRESIDENCIA: "Presidencia", SENADO: "Senado",
};
const METRIC_LABELS = {
  total_votos: "Total de votos", ranking: "Ranking", ganador: "Ganador", comparacion: "Comparación",
  margen: "Margen de victoria", competitividad: "Competitividad", participacion: "Participación",
  detalle: "Detalle", resumen: "Resumen",
};
const ORDER_LABELS = {
  votos_desc: "Más votos primero", votos_asc: "Menos votos primero",
  margen_asc: "Más competidos primero", margen_desc: "Mayor ventaja primero",
};
const LOWERCASE_WORDS = new Set(["de", "del", "la", "las", "los", "el", "y", "en"]);

function titleCase(text) {
  return text.toLocaleLowerCase("es").split(" ").map((word, index) =>
    index > 0 && LOWERCASE_WORDS.has(word) ? word : word.charAt(0).toLocaleUpperCase("es") + word.slice(1)
  ).join(" ");
}

function friendlyValue(dimension, value) {
  const text = String(value ?? "");
  if (dimension === "tipo_eleccion") return ELECTION_LABELS[text] || text.replaceAll("_", " ");
  if (dimension === "partido") return text.replaceAll("_", "-");
  if (dimension === "entidad" || dimension === "municipio") return titleCase(text);
  return text.replaceAll("_", " ");
}

// Convierte el plan validado por el backend en chips que explican qué se consultó.
function interpretationChips(plan) {
  if (!plan || typeof plan !== "object") return "";
  const chips = [];
  const chip = (label, text, color) => chips.push(
    `<span class="chip">${color ? `<i class="dot" style="background:${color}"></i>` : ""}`
    + `<span class="chip-key">${escapeHtml(label)}</span><span class="chip-value">${escapeHtml(text)}</span></span>`
  );
  if (plan.metrica) chip("Consulta", METRIC_LABELS[plan.metrica] || plan.metrica);
  for (const [dimension, values] of Object.entries(plan.filtros || {})) {
    for (const value of Array.isArray(values) ? values : [values]) {
      chip(DIMENSION_LABELS[dimension] || dimension, friendlyValue(dimension, value),
        dimension === "partido" ? seriesStyle(value).colors[0] : null);
    }
  }
  const groups = (plan.agrupar_por || []).map((item) => DIMENSION_LABELS[item] || item);
  if (groups.length) chip("Desglose", groups.join(", "));
  if (plan.orden && plan.orden !== "votos_desc") chip("Orden", ORDER_LABELS[plan.orden] || plan.orden);
  if (plan.limite && Number(plan.limite) !== 20) chip("Máximo", `${plan.limite} resultados`);
  if (!chips.length) return "";
  return `<div class="interp-chips" aria-label="Cómo se interpretó la consulta"><span class="interp-label">Entendí:</span>${chips.join("")}</div>`;
}

function addMessage(text, role, plan = null) {
  const article = document.createElement("article");
  article.className = `message ${role}-message`;
  article.innerHTML = role === "assistant"
    ? `<div class="avatar">IE</div><div class="bubble"><div class="md">${renderMarkdown(text)}</div>${interpretationChips(plan)}</div>`
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
    addMessage(data.respuesta, "assistant", data.plan);
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

function loadHealth() {
  fetch("/api/health")
    .then((response) => response.json())
    .then((health) => {
      const dot = document.querySelector("#status-dot");
      const text = document.querySelector("#status-text");
      dot.classList.toggle("online", health.ok);
      text.textContent = health.ok ? `${health.filas.toLocaleString("es-MX")} registros disponibles` : (health.error || "Configuración pendiente");
    })
    .catch(() => { document.querySelector("#status-text").textContent = "Backend sin conexión"; });
}
document.addEventListener("app:ready", () => { loadHealth(); routeFromHash(); });

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
    const payload = buildPayload();
    const response = await fetch("/api/graficos/analizar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "No fue posible generar la comparación.");
    if (!data.filas.length) throw new Error(data.avisos?.[0] || "La selección no produjo resultados.");
    state.graphics.groups = payload.grupos;
    state.graphics.order = [...new Set(data.filas.map((row) => row.serie))];
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

  const variantCount = new Map();
  const traces = [...grouped.entries()].map(([name, rows]) => {
    rows.sort((a, b) => String(a[xField]).localeCompare(String(b[xField]), "es", { numeric: true }));
    const common = {
      name,
      x: rows.map((row) => String(row[xField]).replaceAll("_", " ")),
      y: rows.map((row) => Number(row[metric] || 0)),
      customdata: rows.map((row) => [row.votos, row.porcentaje, row.variacion_votos, row.variacion_pp]),
      hovertemplate: `<b>%{fullData.name}</b><br>${xTitle}: %{x}<br>Votos: %{customdata[0]:,.0f}<br>Porcentaje: %{customdata[1]:.2f}%<br>Variación votos: %{customdata[2]:+,.0f}<br>Variación pp: %{customdata[3]:+.2f}<extra></extra>`,
    };
    // Cada partido conserva su color; si una misma serie aparece en varias
    // elecciones o años, se distingue con trazo (líneas) u opacidad (barras).
    const serie = rows[0].serie;
    const variant = variantCount.get(serie) ?? 0;
    variantCount.set(serie, variant + 1);
    const style = seriesStyle(serie);
    const [primary, secondary] = style.colors;
    if (state.graphics.chartType === "line") {
      const dash = LINE_DASHES[(variant + (style.combo ? 1 : 0)) % LINE_DASHES.length];
      return { ...common, type: "scatter", mode: "lines+markers", line: { width: 3, color: primary, dash }, marker: { size: 8, color: primary } };
    }
    const opacity = BAR_OPACITY[variant % BAR_OPACITY.length];
    const marker = style.combo
      ? { color: primary, opacity, pattern: { shape: "/", size: 8, solidity: 0.55, fgcolor: secondary || "#ffffff", bgcolor: primary } }
      : { color: primary, opacity };
    return { ...common, type: "bar", marker };
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
      if (column === "serie") return `<td>${swatchHtml(row.serie)}${escapeHtml(row.serie)}</td>`;
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

// ---------------------------------------------------------------------------
// Semáforo de posicionamiento: una persona o la comparación de dos.
// Cada persona es un panel independiente con sus propios filtros.
// ---------------------------------------------------------------------------
const TONES = [
  { key: 'negativo', label: 'Negativas o críticas', short: 'Negativas', color: '#b63a3a' },
  { key: 'neutro', label: 'Neutras', short: 'Neutras', color: '#c49a2c' },
  { key: 'positivo', label: 'Positivas', short: 'Positivas', color: '#2f855a' },
];
const MONTHS_ES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
const DAY_MS = 86400000;

let newsReports = [];   // paneles vigentes, para exportarlos a PDF
const newsForm = document.querySelector('#news-form');
const newsStatus = document.querySelector('#news-status');
const newsTarget = document.querySelector('#news-results');
const newsToolbar = document.querySelector('#news-toolbar');
const newsExport = document.querySelector('#news-export');

function formatIsoDate(iso) {
  const [year, month, day] = String(iso).slice(0, 10).split('-').map(Number);
  return year && month && day ? `${day} ${MONTHS_ES[month - 1]} ${year}` : String(iso);
}

function noteDate(note) {
  return note.fecha ? formatIsoDate(note.fecha) : (note.fecha_buscador || 'Fecha no indicada');
}

// Plotly interpreta las fechas sin zona horaria: "AAAA-MM-DD HH:MM".
function plotlyDate(ms) {
  return new Date(ms).toISOString().slice(0, 16).replace('T', ' ');
}

function renderWordCloud(items) {
  if (!items.length) return '<p>No se identificaron palabras o frases repetidas en al menos dos notas.</p>';
  const frequencies = items.map(item => Number(item.noticias) || 1);
  const minimum = Math.min(...frequencies);
  const maximum = Math.max(...frequencies);
  const colors = ['#174b37', '#9a6b20', '#315d72', '#743f45', '#52705c'];
  return `<div class="word-cloud" role="group" aria-label="Nube de palabras y frases asociadas">${items.map((item, index) => {
    const relative = maximum === minimum ? 0.5 : (item.noticias - minimum) / (maximum - minimum);
    const fontSize = 16 + Math.round(relative * 28);
    const weight = 550 + Math.round(relative * 250);
    return `<button type="button" class="cloud-word" data-word="${index}" aria-pressed="false" style="font-size:${fontSize}px;font-weight:${weight};color:${colors[index % colors.length]}" title="Aparece en ${item.noticias} notas de ${item.fuentes} fuentes. Clic para ver esas notas.">${escapeHtml(item.frase)}<small>${item.noticias}</small></button>`;
  }).join('')}</div><p class="cloud-help">El tamaño representa el número de notas distintas en las que aparece cada expresión. Haz clic en una para ver solo esas notas.</p>`;
}

function renderToneTrafficLight(semaforo) {
  if (!semaforo || !semaforo.total) return '<p>No hay suficientes notas para calcular el tono de la cobertura.</p>';
  return `<div class="tone-grid">${TONES.map(({ key, label, color }) => {
    const item = semaforo[key] || { porcentaje: 0, notas: 0 };
    return `<button type="button" class="tone-card tone-filter" data-tone="${key}" aria-pressed="false"><span class="tone-light" style="background:${color}"></span><div><strong>${Number(item.porcentaje).toFixed(1)}%</strong><p>${label}</p><small>${item.notas} ${item.notas === 1 ? 'nota' : 'notas'}</small></div></button>`;
  }).join('')}</div><div class="tone-bar" aria-label="Distribución del tono">${TONES.map(({ key, color }) => `<span style="width:${semaforo[key]?.porcentaje || 0}%;background:${color}"></span>`).join('')}</div><p class="tone-note">${escapeHtml(semaforo.aviso || '')} Haz clic en una categoría para filtrar las notas.</p>`;
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

function drawWeeklyChart(plot, cobertura, yMax) {
  const weeks = cobertura.semanas || [];
  const x = weeks.map(week => week.semana);
  const traces = TONES.map(({ key, short, color }) => ({
    type: 'bar', name: short, x, y: weeks.map(week => week[key]),
    customdata: weeks.map(week => [week.semana, key]), marker: { color },
    hovertemplate: `Semana del %{x|%d/%m/%Y}<br>${short}: %{y}<extra></extra>`,
  }));
  Plotly.react(plot, traces, {
    barmode: 'stack', height: 300, margin: { l: 44, r: 12, t: 10, b: 46 },
    paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff',
    font: { family: 'Inter, system-ui, sans-serif', color: '#364155', size: 12 },
    xaxis: { type: 'date', tickformat: '%d/%m', gridcolor: '#eef1f5', title: { text: 'Semana (inicio en lunes)', standoff: 8 } },
    yaxis: { title: 'Notas', rangemode: 'tozero', range: [0, yMax], tickformat: 'd', gridcolor: '#e7ebf0' },
    legend: { orientation: 'h', y: -0.3, x: 0 }, shapes: [], bargap: 0.25,
    hoverlabel: { bgcolor: '#111b2b', font: { color: 'white' } },
  }, { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'] });
}

// Construye el panel completo de una persona y devuelve { element, plot, data }.
function buildPersonPanel(data, { yMax = 1 } = {}) {
  const notes = data.noticias || [];
  const associations = data.asociaciones || [];
  const cobertura = data.cobertura_semanal || { semanas: [], sin_fecha: notes.length, con_fecha: 0 };
  const analysis = data.analisis || {};
  const sources = new Set(notes.map(note => note.fuente).filter(Boolean)).size;
  const filters = { tone: null, word: null, week: null };

  const element = document.createElement('article');
  element.className = 'person-panel';
  const hasWeekly = cobertura.con_fecha > 0;
  element.innerHTML = `
    <header class="person-head"><h2>${escapeHtml(data.nombre)}</h2><span class="person-meta">${notes.length} ${notes.length === 1 ? 'nota' : 'notas'} · ${sources} ${sources === 1 ? 'fuente' : 'fuentes'}</span></header>
    <section class="media-analysis"><p class="eyebrow">SÍNTESIS GENERAL</p><h2>Resumen de toda la cobertura</h2><p class="coverage-summary">${escapeHtml(analysis.resumen || 'No hay resumen disponible.')}</p>${renderCoverageByTone(analysis)}<h3>¿Qué representa esta cobertura?</h3><p>${escapeHtml(analysis.lectura || 'La cobertura disponible no permite una interpretación suficiente.')}</p><small>Alcance analizado: ${escapeHtml(analysis.alcance || `${notes.length} notas`)} · Síntesis: ${escapeHtml(analysis.metodo_resumen || 'automática')}</small></section>
    <section class="tone-section"><p class="eyebrow">TONO DE LA COBERTURA</p><h2>Semáforo de notas</h2>${renderToneTrafficLight(data.semaforo)}</section>
    <section class="weekly-section"><p class="eyebrow">COBERTURA POR SEMANA</p><h2>¿Cuándo se publicó?</h2>
      ${hasWeekly ? '<div class="weekly-plot"></div><p class="weekly-note">Haz clic en una barra para ver las notas de esa semana y tono.</p>' : '<p class="weekly-note">Ninguna nota tiene una fecha de publicación identificable dentro del periodo.</p>'}
      ${cobertura.sin_fecha && hasWeekly ? `<p class="weekly-note">${cobertura.sin_fecha} ${cobertura.sin_fecha === 1 ? 'nota no aparece' : 'notas no aparecen'} en la gráfica porque no se pudo identificar su fecha de publicación.</p>` : ''}
    </section>
    <section class="cloud-section"><p class="eyebrow">ASOCIACIONES RECURRENTES</p><h2>Palabras y frases más mencionadas</h2>${renderWordCloud(associations)}</section>
    <section class="notes-section"><h2>Notas consultadas</h2><div class="filter-bar" aria-live="polite"></div><ul class="evidence-list"></ul></section>`;

  const filterBar = element.querySelector('.filter-bar');
  const list = element.querySelector('.evidence-list');
  const plot = element.querySelector('.weekly-plot');

  function visibleNotes() {
    const urls = filters.word !== null
      ? new Set(associations[filters.word]?.notas_urls || associations[filters.word]?.enlaces || [])
      : null;
    return notes.filter(note =>
      (!filters.tone || (note.tono || 'neutro') === filters.tone)
      && (!urls || urls.has(note.url))
      && (!filters.week || note.semana === filters.week));
  }

  function renderNotes() {
    const shown = visibleNotes();
    const active = [];
    if (filters.tone) active.push(['tone', `Tono: ${TONES.find(t => t.key === filters.tone).short.toLowerCase()}`]);
    if (filters.week) active.push(['week', `Semana del ${formatIsoDate(filters.week)}`]);
    if (filters.word !== null) active.push(['word', `Tema: ${associations[filters.word]?.frase || ''}`]);
    const counter = active.length ? `Mostrando <strong>${shown.length}</strong> de ${notes.length} notas` : `${notes.length} ${notes.length === 1 ? 'nota' : 'notas'}`;
    filterBar.innerHTML = `<span>${counter}</span>${active.map(([key, text]) => `<span class="filter-chip">${escapeHtml(text)}<button type="button" data-clear="${key}" aria-label="Quitar filtro: ${escapeHtml(text)}">×</button></span>`).join('')}${active.length > 1 ? '<button type="button" class="text-button" data-clear="all">Quitar todos</button>' : ''}`;
    list.innerHTML = shown.map(note => `<li><span class="tone-badge ${escapeHtml(note.tono || 'neutro')}">${escapeHtml(note.tono || 'neutro')}</span> <a href="${escapeHtml(note.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(note.titulo)}</a><br><small>${escapeHtml(note.fuente)} · ${escapeHtml(noteDate(note))} · ${note.texto_disponible ? 'Texto extraído' : 'Solo título y resumen'}</small></li>`).join('')
      || '<li class="filter-empty">Ninguna nota coincide con los filtros. Quita alguno para ver más.</li>';
  }

  function syncFilters() {
    element.querySelectorAll('[data-tone]').forEach(node => node.setAttribute('aria-pressed', String(node.dataset.tone === filters.tone)));
    element.querySelectorAll('[data-word]').forEach(node => node.setAttribute('aria-pressed', String(Number(node.dataset.word) === filters.word)));
    if (plot && window.Plotly && plot.data) {
      const start = filters.week ? new Date(`${filters.week}T00:00:00Z`).getTime() : null;
      Plotly.relayout(plot, {
        shapes: start === null ? [] : [{
          type: 'rect', xref: 'x', yref: 'paper', x0: plotlyDate(start - 3.5 * DAY_MS), x1: plotlyDate(start + 3.5 * DAY_MS),
          y0: 0, y1: 1, fillcolor: 'rgba(213,168,75,0.22)', line: { width: 0 }, layer: 'below',
        }],
      });
    }
    renderNotes();
  }

  element.addEventListener('click', event => {
    const toneButton = event.target.closest('[data-tone]');
    const wordButton = event.target.closest('[data-word]');
    const clearButton = event.target.closest('[data-clear]');
    if (toneButton) filters.tone = filters.tone === toneButton.dataset.tone ? null : toneButton.dataset.tone;
    else if (wordButton) filters.word = filters.word === Number(wordButton.dataset.word) ? null : Number(wordButton.dataset.word);
    else if (clearButton) {
      const key = clearButton.dataset.clear;
      if (key === 'all') { filters.tone = null; filters.word = null; filters.week = null; } else filters[key] = null;
    } else return;
    syncFilters();
  });

  if (plot) {
    if (window.Plotly) {
      drawWeeklyChart(plot, cobertura, yMax);
      plot.on('plotly_click', event => {
        const [week, tone] = event.points?.[0]?.customdata || [];
        if (!week) return;
        const same = filters.week === week && filters.tone === tone;
        filters.week = same ? null : week;
        filters.tone = same ? null : tone;
        syncFilters();
      });
    } else {
      plot.innerHTML = '<p class="weekly-note">No se pudo cargar la librería de gráficas. Verifica tu conexión a internet.</p>';
    }
  }
  renderNotes();
  return { element, plot: plot && window.Plotly ? plot : null, data };
}

function comparisonTable(reports) {
  const share = (data, key) => `${Number(data.semaforo?.[key]?.porcentaje || 0).toFixed(1)} %`;
  const peak = data => {
    const best = (data.cobertura_semanal?.semanas || []).reduce((top, week) => (!top || week.total > top.total ? week : top), null);
    return best && best.total ? `Semana del ${formatIsoDate(best.semana)} (${best.total} ${best.total === 1 ? 'nota' : 'notas'})` : '—';
  };
  const rows = [
    ['Notas', data => data.noticias.length],
    ['Fuentes distintas', data => new Set(data.noticias.map(note => note.fuente).filter(Boolean)).size],
    ['Negativas o críticas', data => share(data, 'negativo')],
    ['Neutras', data => share(data, 'neutro')],
    ['Positivas', data => share(data, 'positivo')],
    ['Semana con más notas', peak],
    ['Expresiones principales', data => (data.asociaciones || []).slice(0, 3).map(item => item.frase).join(', ') || '—'],
  ];
  const wrapper = document.createElement('div');
  wrapper.className = 'compare-table-wrap';
  wrapper.innerHTML = `<table class="compare-table"><thead><tr><th></th>${reports.map(data => `<th>${escapeHtml(data.nombre)}</th>`).join('')}</tr></thead><tbody>${rows.map(([label, getter]) => `<tr><th scope="row">${label}</th>${reports.map(data => `<td>${escapeHtml(getter(data))}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  return wrapper;
}

function newsErrorPanel(name, message) {
  const node = document.createElement('article');
  node.className = 'person-panel person-error';
  node.innerHTML = `<h2>${escapeHtml(name)}</h2><p>No se pudo completar la búsqueda: ${escapeHtml(message)}</p>`;
  return node;
}

async function fetchNews(name) {
  const response = await fetch('/api/posicionamiento/analizar', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      nombre: name,
      fecha_inicio: document.querySelector('#news-from').value,
      fecha_fin: document.querySelector('#news-to').value,
    }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Error al buscar noticias.');
  return data;
}

document.querySelector('#news-to').value = new Date().toISOString().slice(0, 10);
document.querySelector('#news-from').value = `${new Date().getFullYear()}-01-01`;

newsForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const first = document.querySelector('#news-name').value.trim();
  const second = document.querySelector('#news-name-2').value.trim();
  if (second && first.toLocaleLowerCase('es') === second.toLocaleLowerCase('es')) {
    newsStatus.textContent = 'Escribe dos nombres distintos para compararlos.';
    return;
  }
  const names = [first, second].filter(Boolean);
  const button = newsForm.querySelector('button[type="submit"]');
  button.disabled = true;
  newsToolbar.hidden = true;
  newsReports = [];
  newsTarget.innerHTML = '';
  newsStatus.textContent = names.length > 1
    ? `Buscando notas de ${first} y ${second} al mismo tiempo. Esto puede tardar un par de minutos…`
    : 'Buscando noticias y leyendo artículos. Esto puede tardar un momento…';
  try {
    const settled = await Promise.allSettled(names.map(fetchNews));
    const okData = settled.filter(result => result.status === 'fulfilled').map(result => result.value);
    const yMax = Math.max(1, Math.ceil(Math.max(0, ...okData.flatMap(data => (data.cobertura_semanal?.semanas || []).map(week => week.total))) * 1.15));

    document.querySelector('#semaforo .feature-card').classList.toggle('is-comparing', names.length > 1);
    if (okData.length > 1) newsTarget.append(comparisonTable(okData));
    const grid = document.createElement('div');
    grid.className = `person-grid${names.length > 1 ? ' compare' : ''}`;
    settled.forEach((result, index) => {
      if (result.status === 'fulfilled') {
        const panel = buildPersonPanel(result.value, { yMax });
        newsReports.push(panel);
        grid.append(panel.element);
      } else {
        grid.append(newsErrorPanel(names[index], result.reason?.message || 'Error desconocido.'));
      }
    });
    newsTarget.append(grid);

    const failed = settled.length - okData.length;
    newsStatus.textContent = okData.length === 1 && names.length === 1
      ? `${okData[0].noticias.length} notas encontradas · ${okData[0].asociaciones.length} expresiones presentes en al menos dos notas. ${okData[0].aviso}`
      : `${okData.map(data => `${data.nombre}: ${data.noticias.length} notas`).join(' · ')}${failed ? ` · ${failed} ${failed === 1 ? 'búsqueda falló' : 'búsquedas fallaron'}.` : '.'} ${okData[0]?.aviso || ''}`;
    newsToolbar.hidden = !newsReports.length;
  } catch (error) {
    newsStatus.textContent = error.message || 'Error al buscar noticias.';
  } finally { button.disabled = false; }
});

// Exporta el reporte completo (sin filtros) a PDF. La gráfica semanal se envía
// como imagen generada en el navegador, sin marcar la semana seleccionada.
newsExport.addEventListener('click', async () => {
  if (!newsReports.length) return;
  const label = newsExport.textContent;
  newsExport.disabled = true;
  newsExport.textContent = 'Preparando PDF…';
  try {
    const reportes = [];
    for (const { data, plot } of newsReports) {
      let grafica = null;
      if (plot && window.Plotly && plot.data) {
        try {
          grafica = await Plotly.toImage(
            { data: plot.data, layout: { ...plot.layout, shapes: [], width: 1000, height: 340, autosize: false } },
            { format: 'png', width: 1000, height: 340, scale: 1.5 });
        } catch { grafica = null; }
      }
      reportes.push({
        nombre: data.nombre, fecha_inicio: data.fecha_inicio, fecha_fin: data.fecha_fin,
        noticias: data.noticias, asociaciones: data.asociaciones, analisis: data.analisis,
        semaforo: data.semaforo, cobertura_semanal: data.cobertura_semanal, grafica_semanal: grafica,
      });
    }
    const response = await fetch('/api/posicionamiento/exportar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reportes }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(typeof error.detail === 'string' ? error.detail : 'No se pudo generar el PDF.');
    }
    const filename = /filename="?([^";]+)"?/.exec(response.headers.get('Content-Disposition') || '')?.[1] || 'posicionamiento.pdf';
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url; link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    newsStatus.textContent = 'PDF descargado.';
  } catch (error) {
    newsStatus.textContent = error.message || 'No se pudo generar el PDF.';
  } finally {
    newsExport.disabled = false;
    newsExport.textContent = label;
  }
});


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
  const canEdit = hasRole('coordinador');
  for (const [key, target, label] of [['areas', '#area-list', 'nombre'], ['metas', '#goal-list', 'titulo'], ['personas', '#person-list', 'nombre']]) {
    document.querySelector(target).innerHTML = campaign[key].map(row =>
      `<span class="campaign-chip">${escapeHtml(row[label])}${canEdit ? `<button type="button" data-delete="${key}" data-id="${row.id}" aria-label="Eliminar ${escapeHtml(row[label])}">×</button>` : ''}</span>`).join('') || '<small>Sin registros todavía</small>';
  }
  renderGoalCards();
  const labels = { por_hacer: 'Por hacer', en_proceso: 'En proceso', finalizada: 'Finalizada' };
  document.querySelector('#task-board').innerHTML = Object.entries(labels).map(([status, title]) => {
    const rows = campaign.tareas.filter(t => t.estado === status);
    return `<section class="task-column" data-column="${status}"><h2>${title} <span>${rows.length}</span></h2><div class="task-stack">${rows.map(t => {
      const goal = campaign.metas.find(m => m.id === t.meta_id);
      const person = campaign.personas.find(p => p.id === t.persona_id);
      return `<article class="task-card priority-${t.prioridad || 'media'}" ${canEdit ? 'draggable="true"' : ''} data-task="${t.id}"><div class="task-card-heading"><strong>${escapeHtml(t.titulo)}</strong><span>${escapeHtml(t.prioridad || 'media')} · ${t.peso || 3} pts</span></div><p>${escapeHtml(t.descripcion || '')}</p><small>${goal ? 'Meta: ' + escapeHtml(goal.titulo) : 'Sin meta'} · ${person ? 'Responsable: ' + escapeHtml(person.nombre) : 'Sin responsable'}${t.fecha_limite ? ' · Hasta: ' + escapeHtml(t.fecha_limite) : ''}${t.evidencia ? ' · Evidencia: ' + escapeHtml(t.evidencia) : ''}</small>${canEdit ? `<div class="task-controls"><select data-state="${t.id}" aria-label="Estado de ${escapeHtml(t.titulo)}">${Object.entries(labels).map(([code, name]) => `<option value="${code}" ${code === status ? 'selected' : ''}>${name}</option>`).join('')}</select><button class="text-button" data-delete="tareas" data-id="${t.id}" aria-label="Eliminar tarea">Eliminar</button></div>` : ''}</article>`;
    }).join('') || '<p class="task-empty">Suelta una tarea aquí</p>'}</div></section>`;
  }).join('');
  renderCampaignDashboard();
}
const formatCount = value => Number(value || 0).toLocaleString('es-MX', { maximumFractionDigits: 1 });
// Una meta con objetivo se mide por su indicador (avance/objetivo); si no, por sus tareas finalizadas.
function goalProgress(goal) {
  if (Number(goal.objetivo) > 0) {
    const percent = Math.min(100, Math.round(Number(goal.avance || 0) * 100 / Number(goal.objetivo)));
    return { percent, label: `${formatCount(goal.avance)} de ${formatCount(goal.objetivo)} ${goal.indicador || ''}`.trim() };
  }
  const rows = campaign.tareas.filter(t => t.meta_id === goal.id);
  const done = rows.filter(t => t.estado === 'finalizada').length;
  return { percent: rows.length ? Math.round(done * 100 / rows.length) : 0, label: `${done}/${rows.length} tareas finalizadas` };
}
function goalTerritoryLink(goal) {
  if (!goal.territorio_tipo) return '';
  const label = `${DIMENSION_LABELS[goal.territorio_tipo] || goal.territorio_tipo}: ${friendlyValue(goal.territorio_tipo, goal.territorio_valor)}`;
  return `<a class="goal-territory" href="#territorio/${goal.territorio_tipo}/${encodeURIComponent(goal.territorio_valor)}">${escapeHtml(label)}</a>`;
}
function renderGoalCards() {
  const canEdit = hasRole('coordinador');
  document.querySelector('#goal-cards').innerHTML = campaign.metas.map(goal => {
    const p = goalProgress(goal);
    const person = campaign.personas.find(x => x.id === goal.responsable_id);
    const editor = canEdit && Number(goal.objetivo) > 0
      ? `<form class="goal-progress-form" data-goal="${goal.id}"><label>Avance <input type="number" min="0" step="any" value="${goal.avance || 0}" aria-label="Avance de ${escapeHtml(goal.titulo)}" /></label><button class="secondary-button" type="submit">Actualizar</button></form>` : '';
    return `<article class="goal-card${goal.demo ? ' is-demo' : ''}"><div class="goal-head"><strong>${escapeHtml(goal.titulo)}</strong>${goalTerritoryLink(goal)}</div><div class="progress-row"><div><span>${escapeHtml(p.label)}${goal.fecha_limite ? ` · vence ${escapeHtml(goal.fecha_limite)}` : ''}${person ? ` · ${escapeHtml(person.nombre)}` : ''}</span></div><b>${p.percent}%</b><div class="progress-track"><i style="width:${p.percent}%"></i></div></div>${editor}</article>`;
  }).join('') || '<p class="empty-dashboard">Aún no hay metas.</p>';
}
document.querySelector('#goal-cards').addEventListener('submit', async event => {
  const form = event.target.closest('[data-goal]');
  if (!form) return;
  event.preventDefault();
  try {
    await campaignRequest(`/metas/${form.dataset.goal}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ avance: Number(form.querySelector('input').value || 0) }) });
    await loadCampaign(); campaignStatus.textContent = 'Avance actualizado.';
  } catch (error) { campaignStatus.textContent = error.message; }
});
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
  document.querySelector('#goal-progress').innerHTML = campaign.metas.map(g => {
    const p = goalProgress(g);
    return `<div class="progress-row"><div><strong>${escapeHtml(g.titulo)}</strong><span>${escapeHtml(p.label)}${g.fecha_limite ? ` · vence ${escapeHtml(g.fecha_limite)}` : ''}</span></div><b>${p.percent}%</b><div class="progress-track"><i style="width:${p.percent}%"></i></div></div>`;
  }).join('') || '<p class="empty-dashboard">Aún no hay metas.</p>';
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
    if (key === 'metas') {
      values.objetivo = values.objetivo ? Number(values.objetivo) : null; values.fecha_limite ||= null;
      values.territorio_valor = (values.territorio_valor || '').trim() || null;
      values.territorio_tipo ||= null;
      if (!values.territorio_valor && !values.territorio_tipo) { values.territorio_valor = values.territorio_tipo = null; }
    }
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

// Metas ligadas a un territorio: el tipo llena la lista de sugerencias del valor.
(function setupGoalTerritory() {
  const type = document.querySelector('#goal-territory-type');
  const list = document.querySelector('#goal-territory-units');
  Object.entries(DIMENSION_LABELS).filter(([key]) => ['entidad', 'municipio', 'id_distrito_local', 'id_distrito_federal', 'seccion'].includes(key))
    .forEach(([key, label]) => type.add(new Option(label, key)));
  type.addEventListener('change', async () => {
    list.innerHTML = '';
    if (!type.value || !window.getTerritoryCatalog) return;
    try {
      const catalog = await window.getTerritoryCatalog();
      const level = catalog.niveles.find(item => item.valor === type.value);
      list.innerHTML = (level?.unidades || []).map(unit => `<option value="${escapeHtml(unit)}"></option>`).join('');
    } catch { /* las sugerencias son opcionales */ }
  });
})();
document.addEventListener('demo:changed', () => loadCampaign());
