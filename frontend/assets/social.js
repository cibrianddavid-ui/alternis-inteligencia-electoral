// ---------------------------------------------------------------------------
// Social Listening — frontend module
// Llama a POST /api/social/analizar, renderiza sentimiento, n-gramas y resumen.
// ---------------------------------------------------------------------------

(function () {
  'use strict';

  // ── Elementos del DOM ────────────────────────────────────────────────────
  const form = document.getElementById('social-form');
  const urlInput = document.getElementById('social-url');
  const postsSlider = document.getElementById('social-posts');
  const postsVal = document.getElementById('social-posts-val');
  const commentsSlider = document.getElementById('social-comments');
  const commentsVal = document.getElementById('social-comments-val');
  const submitBtn = document.getElementById('social-submit');
  const forceRefreshInput = document.getElementById('social-force-refresh');
  const progressEl = document.getElementById('social-progress');
  const errorEl = document.getElementById('social-error');
  const resultsEl = document.getElementById('social-results');

  // ── Elementos del Selector de Modo ────────────────────────────────────────
  const tabProfileBtn = document.getElementById('social-tab-profile-btn');
  const tabPostsBtn = document.getElementById('social-tab-posts-btn');
  const profileModeEl = document.getElementById('social-profile-mode');
  const postsModeEl = document.getElementById('social-posts-mode');

  // ── Elementos del Modo Manual de Posts ───────────────────────────────────
  const postsForm = document.getElementById('social-posts-form');
  const postsUrlsInput = document.getElementById('social-posts-urls');
  const postsCommentsSlider = document.getElementById('social-posts-comments');
  const postsCommentsVal = document.getElementById('social-posts-comments-val');
  const postsForceRefreshInput = document.getElementById('social-posts-force-refresh');
  const postsSubmitBtn = document.getElementById('social-posts-submit');
  const postsProgressEl = document.getElementById('social-posts-progress');
  const postsErrorEl = document.getElementById('social-posts-error');
  const postsResultsEl = document.getElementById('social-posts-results');

  const postsStep1El = document.getElementById('social-posts-step-1');
  const postsStep2El = document.getElementById('social-posts-step-2');
  const postsStep3El = document.getElementById('social-posts-step-3');
  const postsStep4El = document.getElementById('social-posts-step-4');

  const step1El = document.getElementById('social-step-1');
  const step2El = document.getElementById('social-step-2');
  const step3El = document.getElementById('social-step-3');
  const step4El = document.getElementById('social-step-4');

  const metaEl = document.getElementById('social-meta');
  const sentimentEl = document.getElementById('social-sentiment-bars');
  const ngramsEl = document.getElementById('social-ngrams');
  const summaryEl = document.getElementById('social-summary');
  const methodBadge = document.getElementById('social-method-badge');

  const ngramModalEl = document.getElementById('social-ngram-modal');
  const ngramModalCloseBtn = document.getElementById('modal-ngram-close');
  const ngramModalTitle = document.getElementById('modal-ngram-title');
  const ngramModalBadge = document.getElementById('modal-ngram-badge');
  const ngramModalDesc = document.getElementById('modal-ngram-desc');
  const ngramModalStats = document.getElementById('modal-ngram-stats');
  const ngramModalComments = document.getElementById('modal-ngram-comments');

  if (!form) return; // guard: el tab no existe en este contexto

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // ── Sliders — actualizar etiqueta en tiempo real ─────────────────────────
  postsSlider.addEventListener('input', () => {
    postsVal.textContent = postsSlider.value;
  });
  commentsSlider.addEventListener('input', () => {
    commentsVal.textContent = commentsSlider.value;
  });

  // ── Helpers de UI ────────────────────────────────────────────────────────
  function setStep(n) {
    [step1El, step2El, step3El, step4El].forEach((el, i) => {
      if (!el) return;
      el.style.opacity = i < n ? '1' : '0.4';
      el.textContent = el.textContent.replace('⏳', i < n ? '✅' : '⏳');
    });
  }

  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.hidden = false;
    progressEl.hidden = true;
    submitBtn.disabled = false;
    submitBtn.textContent = 'Analizar';
  }

  function hideError() {
    errorEl.textContent = '';
    errorEl.hidden = true;
  }

  function resetSteps() {
    const steps = [step1El, step2El, step3El, step4El];
    const labels = ['⏳ Extrayendo posts…', '⏳ Obteniendo comentarios…',
      '⏳ Analizando sentimiento…', '⏳ Generando resumen…'];
    steps.forEach((el, i) => {
      if (el) {
        el.textContent = labels[i];
        el.style.opacity = i === 0 ? '1' : '0.4';
      }
    });
  }

  // ── Helper de Exportación a PDF para Social Listening ─────────────────────
  async function descargarPdfSocial(tipo, data, btn) {
    if (!data) {
      alert('No hay datos disponibles para exportar.');
      return;
    }
    const textoOriginal = btn ? btn.innerHTML : '';
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span>⏳</span> Generando PDF…';
    }

    try {
      const res = await fetch('/api/social/exportar/pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tipo: tipo, data: data }),
        credentials: 'same-origin',
        signal: AbortSignal.timeout(60_000),
      });

      if (!res.ok) {
        const errDetail = await res.json().catch(() => ({}));
        throw new Error(errDetail.detail || `Error al generar PDF (${res.status})`);
      }

      const blob = await res.blob();
      const contentDisposition = res.headers.get('Content-Disposition') || '';
      const filenameMatch = /filename="?([^";]+)"?/.exec(contentDisposition);
      const filename = filenameMatch ? filenameMatch[1] : `social_${tipo}.pdf`;

      const downloadUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = downloadUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        document.body.removeChild(a);
        URL.revokeObjectURL(downloadUrl);
      }, 2000);

    } catch (err) {
      console.error('Error descargando PDF:', err);
      alert(err.message || 'Ocurrió un error al generar o descargar el reporte en PDF.');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = textoOriginal;
      }
    }
  }

  // ── Renderizado de resultados ─────────────────────────────────────────────

  const commentsGridEl = document.getElementById('social-comments-grid');
  const filterTabsContainer = document.getElementById('social-filter-tabs');

  // ── Renderizado de resultados ─────────────────────────────────────────────

  function renderSentiment(sentimiento, distribucion, totalComentarios, postsAnalizados, aprobacionPct) {
    metaEl.textContent =
      `${postsAnalizados} posts analizados · ${totalComentarios} comentarios procesados`;

    const nps = aprobacionPct !== undefined ? aprobacionPct : Math.round((sentimiento.positivo || 0) - (sentimiento.negativo || 0));
    let npsColor = '#16a34a';
    let npsBadge = 'Aprobación Neta Favorable';
    let npsBg = '#dcfce7';

    if (nps < -15) {
      npsColor = '#dc2626';
      npsBadge = 'Alerta de Rechazo / Crisis de Imagen';
      npsBg = '#fee2e2';
    } else if (nps <= 10) {
      npsColor = '#d97706';
      npsBadge = 'Opinión Dividida / En Disputa';
      npsBg = '#fef3c7';
    }

    const npsHtml = `
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px 16px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">
        <div>
          <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.06em;color:#64748b;text-transform:uppercase">Índice de Aprobación Neta (% Positivo - % Negativo)</div>
          <div style="font-size:1.75rem;font-weight:800;color:${npsColor};line-height:1.2;margin-top:2px">${nps > 0 ? '+' : ''}${nps}%</div>
        </div>
        <span style="background:${npsBg};color:${npsColor};font-weight:700;font-size:0.8rem;padding:5px 12px;border-radius:20px;border:1px solid ${npsColor}33">${npsBadge}</span>
      </div>`;

    const colores = {
      positivo: { bg: '#dcfce7', bar: '#16a34a', label: 'Positivo 😊' },
      neutro: { bg: '#f3f4f6', bar: '#6b7280', label: 'Neutro 😐' },
      negativo: { bg: '#fee2e2', bar: '#dc2626', label: 'Negativo 😟' },
    };

    const order = ['positivo', 'neutro', 'negativo'];
    const barsHtml = order.map(key => {
      const pct = sentimiento[key] ?? 0;
      const n = distribucion[key] ?? 0;
      const c = colores[key];
      return `
        <div style="margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span style="font-weight:600;font-size:0.88rem">${c.label}</span>
            <span style="font-size:0.88rem;color:#374151">${pct}% <span style="color:#9ca3af;font-size:0.8rem">(${n} comentarios)</span></span>
          </div>
          <div style="background:${c.bg};border-radius:6px;overflow:hidden;height:16px">
            <div style="width:${pct}%;background:${c.bar};height:100%;border-radius:6px;
                        transition:width 0.6s ease"></div>
          </div>
        </div>`;
    }).join('');

    sentimentEl.innerHTML = npsHtml + barsHtml;
  }

  // ── Modal de detalle de n-grama / frase clave ────────────────────────────
  if (ngramModalCloseBtn) {
    ngramModalCloseBtn.addEventListener('click', () => {
      if (ngramModalEl) ngramModalEl.hidden = true;
    });
  }
  if (ngramModalEl) {
    ngramModalEl.addEventListener('click', (e) => {
      if (e.target === ngramModalEl) ngramModalEl.hidden = true;
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && ngramModalEl && !ngramModalEl.hidden) {
      ngramModalEl.hidden = true;
    }
  });

  function openNgramModal(ng) {
    if (!ngramModalEl || !ng) return;
    const badgeTono = {
      positivo: { bg: '#dcfce7', color: '#15803d', label: 'Positivo 😊' },
      negativo: { bg: '#fee2e2', color: '#b91c1c', label: 'Crítica / Reclamo 😟' },
      neutro: { bg: '#f3f4f6', color: '#4b5563', label: 'Neutro 😐' },
    };
    const tInfo = badgeTono[ng.tono] || badgeTono.neutro;

    if (ngramModalTitle) ngramModalTitle.textContent = ng.texto || 'Tema';
    if (ngramModalBadge) {
      ngramModalBadge.textContent = tInfo.label;
      ngramModalBadge.style.background = tInfo.bg;
      ngramModalBadge.style.color = tInfo.color;
    }
    if (ngramModalDesc) ngramModalDesc.textContent = ng.explicacion || 'Eje temático identificado en la conversación.';

    const d = ng.desglose || {};
    const nCrit = d.negativo || 0;
    const nApo = d.positivo || 0;
    const nNeu = d.neutro || 0;

    if (ngramModalStats) {
      ngramModalStats.innerHTML = `
        <span style="font-size:0.8rem;background:#f1f5f9;color:#334155;padding:4px 10px;border-radius:12px;font-weight:600">📊 ${ng.frecuencia || 0} mención${(ng.frecuencia || 0) > 1 ? 'es' : ''} asociadas</span>
        ${nCrit > 0 ? `<span style="font-size:0.8rem;background:#fee2e2;color:#b91c1c;padding:4px 10px;border-radius:12px;font-weight:600">🔴 ${nCrit} crítica${nCrit > 1 ? 's' : ''}</span>` : ''}
        ${nApo > 0 ? `<span style="font-size:0.8rem;background:#dcfce7;color:#15803d;padding:4px 10px;border-radius:12px;font-weight:600">🟢 ${nApo} apoyo${nApo > 1 ? 's' : ''}</span>` : ''}
        ${nNeu > 0 ? `<span style="font-size:0.8rem;background:#f3f4f6;color:#4b5563;padding:4px 10px;border-radius:12px;font-weight:600">⚪ ${nNeu} neutro${nNeu > 1 ? 's' : ''}</span>` : ''}
      `;
    }

    const comments = ng.comentarios || [];
    if (ngramModalComments) {
      if (comments.length === 0) {
        ngramModalComments.innerHTML = `
          <div style="padding:24px;text-align:center;color:#94a3b8;background:#f8fafc;border-radius:8px;font-size:0.9rem">
            No hay comentarios registrados directamente para este eje temático.
          </div>`;
      } else {
        ngramModalComments.innerHTML = comments.map(c => {
          const cT = badgeTono[c.tono] || badgeTono.neutro;
          const autor = escapeHtml(c.autor_nombre || 'Usuario de Facebook');
          const inicial = autor.charAt(0).toUpperCase() || 'U';
          const urlFB = c.comentario_url || c.autor_url;
          return `
            <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;padding:12px;box-shadow:0 1px 2px rgba(0,0,0,0.03)">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;gap:8px">
                <div style="display:flex;align-items:center;gap:8px;min-width:0">
                  <div style="width:28px;height:28px;border-radius:50%;background:#e2e8f0;color:#334155;font-weight:700;display:grid;place-items:center;font-size:0.8rem;flex-shrink:0">
                    ${inicial}
                  </div>
                  <strong style="font-weight:700;font-size:0.88rem;color:#0f172a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                    ${autor}
                  </strong>
                  <span style="background:${cT.bg};color:${cT.color};font-size:0.68rem;font-weight:600;padding:2px 7px;border-radius:10px">
                    ${cT.label}
                  </span>
                </div>
                ${c.likes > 0 ? `<span style="font-size:0.72rem;font-weight:700;background:#fef3c7;color:#b45309;padding:2px 7px;border-radius:10px">👍 ${c.likes}</span>` : ''}
              </div>
              <p style="margin:0 0 8px 0;font-size:0.88rem;line-height:1.45;color:#1e293b;background:#f8fafc;padding:8px 10px;border-radius:6px;word-break:break-word">
                «${escapeHtml(c.texto || '')}»
              </p>
              ${urlFB ? `
                <div style="text-align:right">
                  <a href="${escapeHtml(urlFB)}" target="_blank" rel="noopener noreferrer" style="display:inline-flex;align-items:center;gap:4px;color:#1d4ed8;font-size:0.75rem;font-weight:600;text-decoration:none">
                    <span>Contactar / Ver en FB</span> ↗
                  </a>
                </div>` : ''}
            </div>
          `;
        }).join('');
      }
    }

    ngramModalEl.hidden = false;
  }

  function renderNgrams(ngramas) {
    if (!ngramas || ngramas.length === 0) {
      ngramsEl.innerHTML = '<p style="color:#9ca3af;font-size:0.9rem">No se encontraron frases o temas clave.</p>';
      return;
    }
    const maxFreq = Math.max(...ngramas.map(ng => ng.frecuencia || 1), 1);

    const badgeTono = {
      positivo: { bg: '#dcfce7', color: '#15803d', label: 'Positivo 😊' },
      negativo: { bg: '#fee2e2', color: '#b91c1c', label: 'Crítica / Negativo 😟' },
      neutro: { bg: '#f3f4f6', color: '#4b5563', label: 'Neutro 😐' },
    };

    ngramsEl.innerHTML = ngramas.map((ng, i) => {
      const pct = Math.round(((ng.frecuencia || 1) / maxFreq) * 100);
      const tonoInfo = badgeTono[ng.tono] || badgeTono.neutro;
      const barColor = ng.tono === 'negativo' ? '#ef4444' : ng.tono === 'positivo' ? '#22c55e' : '#3b82f6';
      const explicacionHtml = ng.explicacion
        ? `<div style="font-size:0.83rem;color:#4b5563;margin-top:4px;line-height:1.4">${escapeHtml(ng.explicacion)}</div>`
        : '';

      const d = ng.desglose || {};
      const breakdownPills = [];
      if ((d.negativo || 0) > 0) breakdownPills.push(`<span style="color:#b91c1c;font-weight:600">🔴 ${d.negativo} crítica${d.negativo > 1 ? 's' : ''}</span>`);
      if ((d.positivo || 0) > 0) breakdownPills.push(`<span style="color:#15803d;font-weight:600">🟢 ${d.positivo} apoyo${d.positivo > 1 ? 's' : ''}</span>`);
      if ((d.neutro || 0) > 0) breakdownPills.push(`<span style="color:#64748b;font-weight:600">⚪ ${d.neutro} neutro${d.neutro > 1 ? 's' : ''}</span>`);
      const breakdownHtml = breakdownPills.length > 0
        ? `<div style="display:flex;gap:8px;font-size:0.75rem;margin-top:6px;flex-wrap:wrap">${breakdownPills.join(' · ')}</div>`
        : '';

      const numComentarios = (ng.comentarios || []).length;

      return `
        <div class="ngram-card" data-ngram-idx="${i}" style="background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;padding:12px 14px;margin-bottom:10px;box-shadow:0 1px 2px rgba(0,0,0,0.02);cursor:pointer;transition:border-color 0.2s, box-shadow 0.2s">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:4px;flex-wrap:wrap">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
              <span style="font-weight:700;font-size:0.85rem;color:#9ca3af">#${i + 1}</span>
              <strong style="font-size:0.95rem;color:#111827">${escapeHtml(ng.texto)}</strong>
              <span style="background:${tonoInfo.bg};color:${tonoInfo.color};font-size:0.72rem;font-weight:600;padding:2px 8px;border-radius:12px">${tonoInfo.label}</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-size:0.82rem;font-weight:600;color:#6b7280">${ng.frecuencia} mención${ng.frecuencia > 1 ? 'es' : ''}</span>
              <button type="button" class="btn-ver-ngram-comments" style="font-size:0.75rem;background:#eff6ff;color:#2563eb;border:1px solid #bfdbfe;padding:2px 8px;border-radius:6px;font-weight:600;cursor:pointer">Ver comentarios (${numComentarios}) 🔍</button>
            </div>
          </div>
          <div style="background:#f1f5f9;border-radius:4px;overflow:hidden;height:6px;margin:6px 0">
            <div style="width:${pct}%;background:${barColor};height:100%;border-radius:4px"></div>
          </div>
          ${explicacionHtml}
          ${breakdownHtml}
        </div>`;
    }).join('');

    ngramsEl.querySelectorAll('.ngram-card').forEach(card => {
      card.addEventListener('click', () => {
        const idx = parseInt(card.getAttribute('data-ngram-idx'), 10);
        if (ngramas[idx]) {
          openNgramModal(ngramas[idx]);
        }
      });
      card.addEventListener('mouseenter', () => {
        card.style.borderColor = '#93c5fd';
        card.style.boxShadow = '0 4px 6px -1px rgba(0,0,0,0.06)';
      });
      card.addEventListener('mouseleave', () => {
        card.style.borderColor = '#e5e7eb';
        card.style.boxShadow = '0 1px 2px rgba(0,0,0,0.02)';
      });
    });
  }

  // ── Bandeja de comentarios para seguimiento de campaña ──────────────────
  let comentariosGlobales = [];
  let filtroActual = 'todos';

  function actualizarFiltrosBotones(conteoPorTono) {
    if (!filterTabsContainer) return;
    filterTabsContainer.querySelectorAll('button').forEach(btn => {
      const f = btn.getAttribute('data-filter');
      btn.classList.toggle('active', f === filtroActual);
      btn.style.background = f === filtroActual ? '#1e293b' : '#ffffff';
      btn.style.color = f === filtroActual ? '#ffffff' : '#334155';
      btn.style.border = '1px solid #cbd5e1';
      btn.style.borderRadius = '8px';
      btn.style.padding = '6px 14px';
      btn.style.fontSize = '0.82rem';
      btn.style.fontWeight = '600';
      btn.style.cursor = 'pointer';

      let count = 0;
      if (f === 'todos') count = comentariosGlobales.length;
      else count = conteoPorTono[f] || 0;

      if (f === 'todos') btn.textContent = `Todos (${count})`;
      else if (f === 'negativo') btn.textContent = `🔴 Críticas (${count})`;
      else if (f === 'positivo') btn.textContent = `🟢 Apoyos (${count})`;
      else if (f === 'neutro') btn.textContent = `⚪ Dudas / Neutros (${count})`;
    });
  }

  function renderizarListaComentarios() {
    if (!commentsGridEl) return;
    const filtrados = filtroActual === 'todos'
      ? comentariosGlobales
      : comentariosGlobales.filter(c => c.tono === filtroActual);

    if (filtrados.length === 0) {
      commentsGridEl.innerHTML = `
        <div style="grid-column: 1 / -1; padding: 24px; text-align: center; color: #94a3b8; background: #f8fafc; border-radius: 8px">
          No hay comentarios en esta categoría.
        </div>`;
      return;
    }

    const badgeTono = {
      positivo: { bg: '#dcfce7', color: '#15803d', label: 'Apoyo / Positivo 😊', border: '#86efac' },
      negativo: { bg: '#fee2e2', color: '#b91c1c', label: 'Crítica / Reclamo 😟', border: '#fca5a5' },
      neutro: { bg: '#f1f5f9', color: '#475569', label: 'Consulta / Neutro 😐', border: '#cbd5e1' },
    };

    const accionSugerida = {
      negativo: '🚨 <strong>Acción recomendada:</strong> Contactar para aclarar con propuestas o datos antes de que escale.',
      positivo: '⭐ <strong>Acción recomendada:</strong> Agradecer respaldo y canalizar a red de promotores / brigadas.',
      neutro: '💬 <strong>Acción recomendada:</strong> Resolver duda o brindar canal de atención ciudadana.',
    };

    commentsGridEl.innerHTML = filtrados.map(c => {
      const tonoInfo = badgeTono[c.tono] || badgeTono.neutro;
      const accion = accionSugerida[c.tono] || '';
      const inicial = (c.autor_nombre || 'U').charAt(0).toUpperCase();
      const urlFB = c.comentario_url || c.autor_url;

      return `
        <div style="background:#ffffff;border:1px solid ${tonoInfo.border};border-radius:10px;padding:14px;display:flex;flex-direction:column;justify-content:space-between;box-shadow:0 2px 4px rgba(0,0,0,0.02)">
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;gap:8px">
              <div style="display:flex;align-items:center;gap:10px;min-width:0">
                <div style="width:34px;height:34px;border-radius:50%;background:#e2e8f0;color:#334155;font-weight:700;display:grid;place-items:center;font-size:0.9rem;flex-shrink:0">
                  ${inicial}
                </div>
                <div style="min-width:0">
                  <div style="font-weight:700;font-size:0.92rem;color:#0f172a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                    ${c.autor_nombre || 'Usuario de Facebook'}
                  </div>
                  <span style="display:inline-block;background:${tonoInfo.bg};color:${tonoInfo.color};font-size:0.72rem;font-weight:600;padding:2px 8px;border-radius:12px;margin-top:2px">
                    ${tonoInfo.label}
                  </span>
                </div>
              </div>
              ${c.likes > 0 ? `<span style="font-size:0.75rem;font-weight:700;background:#fef3c7;color:#b45309;padding:3px 8px;border-radius:12px;white-space:nowrap">👍 ${c.likes}</span>` : ''}
            </div>

            <p style="font-size:0.9rem;line-height:1.5;color:#1e293b;margin:10px 0;word-break:break-word;background:#f8fafc;padding:10px;border-radius:8px">
              «${c.texto}»
            </p>
          </div>

          <div style="margin-top:12px;padding-top:10px;border-top:1px dashed #e2e8f0">
            <div style="font-size:0.78rem;color:#475569;margin-bottom:8px;line-height:1.4">
              ${accion}
            </div>
            ${urlFB ? `
              <a href="${urlFB}" target="_blank" rel="noopener noreferrer" style="display:inline-flex;align-items:center;gap:6px;background:#1d4ed8;color:#ffffff;font-size:0.8rem;font-weight:600;padding:6px 12px;border-radius:6px;text-decoration:none">
                <span>Contactar / Ver en FB</span> ↗
              </a>` : `
              <span style="font-size:0.75rem;color:#94a3b8">Enlace no disponible</span>`}
          </div>
        </div>`;
    }).join('');
  }

  function initFiltrosComentarios() {
    if (!filterTabsContainer) return;
    filterTabsContainer.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', () => {
        filtroActual = btn.getAttribute('data-filter') || 'todos';
        const conteos = {
          negativo: comentariosGlobales.filter(c => c.tono === 'negativo').length,
          positivo: comentariosGlobales.filter(c => c.tono === 'positivo').length,
          neutro: comentariosGlobales.filter(c => c.tono === 'neutro').length,
        };
        actualizarFiltrosBotones(conteos);
        renderizarListaComentarios();
      });
    });
  }

  function renderClassifiedComments(comentarios) {
    comentariosGlobales = comentarios || [];
    filtroActual = 'todos';

    const conteos = {
      negativo: comentariosGlobales.filter(c => c.tono === 'negativo').length,
      positivo: comentariosGlobales.filter(c => c.tono === 'positivo').length,
      neutro: comentariosGlobales.filter(c => c.tono === 'neutro').length,
    };

    actualizarFiltrosBotones(conteos);
    initFiltrosComentarios();
    renderizarListaComentarios();
  }

  let currentProfileData = null;

  function renderResults(data) {
    currentProfileData = data;
    renderSentiment(data.sentimiento, data.distribucion, data.total_comentarios, data.posts_analizados, data.aprobacion_pct);
    renderNgrams(data.ngramas);
    renderClassifiedComments(data.comentarios_clasificados || []);
    summaryEl.textContent = data.resumen || '— Sin resumen disponible —';
    methodBadge.textContent = data.metodo_resumen && data.metodo_resumen.includes('modelo') ? '🤖 IA Semántica' : '📝 Extractivo';
    resultsEl.hidden = false;
  }

  const profileExportPdfBtn = document.getElementById('social-profile-export-pdf');
  if (profileExportPdfBtn) {
    profileExportPdfBtn.addEventListener('click', () => {
      descargarPdfSocial('perfil', currentProfileData, profileExportPdfBtn);
    });
  }

  // ── Animación de progreso simulada ───────────────────────────────────────
  // El backend procesa en un solo request; animamos pasos para retroalimentación visual.
  let progressTimer = null;

  function startProgressAnimation() {
    let step = 1;
    setStep(step);
    progressTimer = setInterval(() => {
      step = Math.min(step + 1, 3); // avanzamos hasta step 3; el 4 lo marcamos al recibir respuesta
      setStep(step);
    }, 18000); // cada ~18s avanzamos un paso visual (el scraping real tarda 30–120s)
  }

  function stopProgressAnimation() {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
    setStep(4); // marcar todos como completados
  }

  // ── Submit principal ──────────────────────────────────────────────────────
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideError();
    resultsEl.hidden = true;

    const url = urlInput.value.trim();
    if (!url) return;

    submitBtn.disabled = true;
    submitBtn.textContent = 'Analizando…';
    resetSteps();
    progressEl.hidden = false;
    startProgressAnimation();

    try {
      const body = {
        url_perfil: url,
        max_posts: parseInt(postsSlider.value, 10),
        max_comentarios: parseInt(commentsSlider.value, 10),
        forzar_refresco: forceRefreshInput ? forceRefreshInput.checked : false,
      };

      const res = await fetch('/api/social/analizar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        credentials: 'same-origin',
        // timeout generoso: el scraping puede tardar hasta 3 minutos en casos pesados
        signal: AbortSignal.timeout(300_000),
      });

      stopProgressAnimation();
      progressEl.hidden = true;

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `Error ${res.status}`);
      }

      const data = await res.json();

      if (data.error) {
        showError(`⚠️ ${data.error}`);
        return;
      }

      renderResults(data);

    } catch (err) {
      stopProgressAnimation();
      progressEl.hidden = true;
      if (err.name === 'TimeoutError' || err.name === 'AbortError') {
        showError('La operación tardó demasiado. Intenta con menos posts o comentarios.');
      } else {
        showError(err.message || 'Error de conexión. Intenta de nuevo.');
      }
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Analizar';
    }
  });


  // ===========================================================================
  // MODO 2: Posts Específicos y Comparativa Inter-Posts
  // ===========================================================================

  // ── Switcher de pestañas Perfil vs Posts ─────────────────────────────────
  if (tabProfileBtn && tabPostsBtn) {
    tabProfileBtn.addEventListener('click', () => {
      if (typeof window.cambiarModoSocial === 'function') {
        window.cambiarModoSocial('perfil');
      }
    });

    tabPostsBtn.addEventListener('click', () => {
      if (typeof window.cambiarModoSocial === 'function') {
        window.cambiarModoSocial('posts');
      }
    });
  }

  // ── Slider de comentarios por post en modo manual ────────────────────────
  if (postsCommentsSlider && postsCommentsVal) {
    postsCommentsSlider.addEventListener('input', () => {
      postsCommentsVal.textContent = postsCommentsSlider.value;
    });
  }

  // ── Animación y helpers de progreso en modo manual ────────────────────────
  let postsProgressTimer = null;

  function setPostsStep(n) {
    [postsStep1El, postsStep2El, postsStep3El, postsStep4El].forEach((el, i) => {
      if (!el) return;
      el.style.opacity = i < n ? '1' : '0.4';
      el.textContent = el.textContent.replace('⏳', i < n ? '✅' : '⏳');
    });
  }

  function resetPostsSteps() {
    const steps = [postsStep1El, postsStep2El, postsStep3El, postsStep4El];
    const labels = [
      '⏳ Sincronizando publicaciones…',
      '⏳ Obteniendo comentarios…',
      '⏳ Analizando postura y temas…',
      '⏳ Generando comparativa…',
    ];
    steps.forEach((el, i) => {
      if (el) {
        el.textContent = labels[i];
        el.style.opacity = i === 0 ? '1' : '0.4';
      }
    });
  }

  function startPostsProgressAnimation() {
    let step = 1;
    setPostsStep(step);
    postsProgressTimer = setInterval(() => {
      step = Math.min(step + 1, 3);
      setPostsStep(step);
    }, 18000);
  }

  function stopPostsProgressAnimation() {
    if (postsProgressTimer) {
      clearInterval(postsProgressTimer);
      postsProgressTimer = null;
    }
    setPostsStep(4);
  }

  function showPostsError(msg) {
    if (postsErrorEl) {
      postsErrorEl.textContent = msg;
      postsErrorEl.hidden = false;
    }
    if (postsProgressEl) postsProgressEl.hidden = true;
    if (postsSubmitBtn) {
      postsSubmitBtn.disabled = false;
      postsSubmitBtn.textContent = 'Analizar publicaciones';
    }
  }

  function hidePostsError() {
    if (postsErrorEl) {
      postsErrorEl.textContent = '';
      postsErrorEl.hidden = true;
    }
  }


  // ── Renderizado de Resultados de Modo Posts Manual ────────────────────────
  let currentManualPostsData = [];
  let currentPostsResultData = null;

  function renderPostsResults(data) {
    if (!postsResultsEl) return;
    currentPostsResultData = data;
    currentManualPostsData = data.posts || [];
    postsResultsEl.innerHTML = '';
    postsResultsEl.hidden = false;

    let html = '';

    // 1. Advertencias de fallos parciales
    if (data.posts_con_error && data.posts_con_error.length > 0) {
      html += `
        <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:12px;padding:14px 18px;margin-bottom:20px;color:#92400e;font-size:0.9rem">
          <strong>⚠️ Atención:</strong> No fue posible descargar los datos de ${data.posts_con_error.length} publicación(es):
          <ul style="margin:8px 0 0 20px;padding:0">
            ${data.posts_con_error.map(err => `
              <li style="margin-bottom:4px">
                <a href="${escapeHtml(err.post_url)}" target="_blank" rel="noopener noreferrer" style="color:#b45309;font-weight:600">${escapeHtml(err.post_url)}</a>:
                <span>${escapeHtml(err.motivo)}</span>
              </li>
            `).join('')}
          </ul>
        </div>
      `;
    }

    // 2. Si no hay publicaciones válidas
    if (!data.posts || data.posts.length === 0) {
      html += `
        <div class="dashboard-card" style="text-align:center;padding:32px;color:#64748b">
          <p style="font-size:1.05rem;font-weight:600;margin:0 0 8px 0">No se obtuvieron publicaciones para analizar.</p>
          <p style="font-size:0.88rem;margin:0">Verifica que los enlaces correspondan a publicaciones públicas accesibles en Facebook.</p>
        </div>
      `;
      postsResultsEl.innerHTML = html;
      return;
    }

    // Barra de acciones / Descarga de PDF
    if (data.comparacion) {
      html += `
        <div style="display:flex;justify-content:flex-end;gap:10px;margin-bottom:16px;flex-wrap:wrap">
          <button type="button" id="social-posts-export-comp-pdf" class="primary-button" style="display:inline-flex;align-items:center;gap:6px;font-size:0.85rem;padding:6px 14px">
            <span>📥</span> Descargar comparativa en PDF
          </button>
          <button type="button" id="social-posts-export-detail-pdf" class="secondary-button" style="display:inline-flex;align-items:center;gap:6px;font-size:0.85rem;padding:6px 14px">
            <span>📄</span> Descargar detalle de posts en PDF
          </button>
        </div>
      `;
    } else {
      html += `
        <div style="display:flex;justify-content:flex-end;margin-bottom:16px">
          <button type="button" id="social-posts-export-detail-pdf" class="secondary-button" style="display:inline-flex;align-items:center;gap:6px;font-size:0.85rem;padding:6px 14px">
            <span>📥</span> Descargar análisis de post en PDF
          </button>
        </div>
      `;
    }

    // 3. Comparativa Inter-Posts (si data.comparacion existe)
    if (data.comparacion) {
      const comp = data.comparacion;
      const kpis = comp.metrica_resumen || {};

      // Resumen ejecutivo comparativo
      html += `
        <div class="dashboard-card" style="margin-bottom:20px">
          <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:12px">
            <h2 style="margin:0">Resumen ejecutivo comparativo</h2>
            <span style="font-size:0.75rem;background:#e0e7ff;color:#3730a3;border-radius:12px;padding:3px 12px;font-weight:700">
              Comparativa de ${comp.total_posts} publicaciones · ${comp.total_comentarios} comentarios
            </span>
          </div>
          <div style="line-height:1.75;font-size:0.95rem;color:#1e293b;white-space:pre-line">
            ${escapeHtml(comp.resumen_comparativo)}
          </div>
        </div>
      `;

      // Tarjetas de Métricas Clave y Balance
      html += `
        <div class="dashboard-card" style="margin-bottom:20px">
          <h2>Métricas destacadas de la comparativa</h2>
          <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(210px, 1fr));gap:12px;margin-bottom:18px">
            <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px 14px">
              <div style="font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase">Mayor Interacción</div>
              <div style="font-size:1.05rem;font-weight:800;color:#0f172a;margin-top:4px">${escapeHtml(kpis.post_mayor_interaccion || 'N/A')}</div>
            </div>
            <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px 14px">
              <div style="font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase">Menor Interacción</div>
              <div style="font-size:1.05rem;font-weight:800;color:#0f172a;margin-top:4px">${escapeHtml(kpis.post_menor_interaccion || 'N/A')}</div>
            </div>
            <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:12px 14px">
              <div style="font-size:0.72rem;font-weight:700;color:#15803d;text-transform:uppercase">Mayor Favorabilidad</div>
              <div style="font-size:1.05rem;font-weight:800;color:#16a34a;margin-top:4px">${escapeHtml(kpis.post_mayor_favorabilidad || 'N/A')}</div>
            </div>
            <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:10px;padding:12px 14px">
              <div style="font-size:0.72rem;font-weight:700;color:#b91c1c;text-transform:uppercase">Mayor Crítica / Rechazo</div>
              <div style="font-size:1.05rem;font-weight:800;color:#dc2626;margin-top:4px">${escapeHtml(kpis.post_mayor_critica || 'N/A')}</div>
            </div>
          </div>

          ${comp.diferencias_sentimiento && comp.diferencias_sentimiento.length > 0 ? `
            <h3 style="font-size:0.95rem;color:#334155;margin:16px 0 8px">Contrastes de postura observados:</h3>
            <div style="display:flex;flex-direction:column;gap:8px">
              ${comp.diferencias_sentimiento.map(diff => `
                <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 14px;font-size:0.88rem;color:#1e293b;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
                  <span>${escapeHtml(diff.descripcion)}</span>
                  <div style="display:flex;gap:6px">
                    <span style="background:#e2e8f0;color:#334155;font-weight:600;font-size:0.75rem;padding:3px 8px;border-radius:10px">Positivo: ${diff.diferencia_positivo_pp > 0 ? '+' : ''}${diff.diferencia_positivo_pp} pp (${diff.magnitud_diferencia_positiva})</span>
                    <span style="background:#fee2e2;color:#b91c1c;font-weight:600;font-size:0.75rem;padding:3px 8px;border-radius:10px">Negativo: ${diff.diferencia_negativo_pp > 0 ? '+' : ''}${diff.diferencia_negativo_pp} pp (${diff.magnitud_diferencia_negativa})</span>
                  </div>
                </div>
              `).join('')}
            </div>
          ` : ''}
        </div>
      `;

      // Ejes temáticos comparativos
      html += `
        <div class="dashboard-card" style="margin-bottom:20px">
          <h2>Ejes temáticos en la conversación inter-posts</h2>
          <div class="dashboard-grid" style="gap:16px;margin-top:12px">
            <!-- Temas compartidos -->
            <div>
              <h3 style="font-size:0.95rem;font-weight:700;color:#1e293b;margin-bottom:10px">
                🔹 Temas compartidos (${(comp.temas_compartidos || []).length})
              </h3>
              ${(comp.temas_compartidos || []).length === 0 ? `
                <p style="font-size:0.85rem;color:#94a3b8">No se identificaron temas coincidentes entre las publicaciones analizadas.</p>
              ` : `
                <div style="display:flex;flex-direction:column;gap:8px">
                  ${comp.temas_compartidos.map(t => `
                    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px">
                      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                        <strong style="font-size:0.9rem;color:#0f172a">${escapeHtml(t.tema)}</strong>
                        <span style="font-size:0.75rem;background:#e2e8f0;color:#334155;padding:2px 8px;border-radius:10px;font-weight:600">${t.total_menciones} menciones</span>
                      </div>
                      <div style="display:flex;gap:6px;flex-wrap:wrap">
                        ${(t.posts || []).map(p => {
        const badgeColor = p.tono_predominante === 'positivo' ? '#15803d' : (p.tono_predominante === 'negativo' ? '#b91c1c' : '#4b5563');
        const badgeBg = p.tono_predominante === 'positivo' ? '#dcfce7' : (p.tono_predominante === 'negativo' ? '#fee2e2' : '#f3f4f6');
        return `
                            <span style="font-size:0.75rem;background:${badgeBg};color:${badgeColor};padding:3px 8px;border-radius:6px;font-weight:600">
                              ${escapeHtml(p.alias)}: ${p.porcentaje}% (${p.tono_predominante})
                            </span>
                          `;
      }).join('')}
                      </div>
                    </div>
                  `).join('')}
                </div>
              `}
            </div>

            <!-- Temas distintivos -->
            <div>
              <h3 style="font-size:0.95rem;font-weight:700;color:#1e293b;margin-bottom:10px">
                🔸 Temas distintivos por publicación (${(comp.temas_distintivos || []).length})
              </h3>
              ${(comp.temas_distintivos || []).length === 0 ? `
                <p style="font-size:0.85rem;color:#94a3b8">Todos los temas identificados fueron compartidos entre publicaciones.</p>
              ` : `
                <div style="display:flex;flex-direction:column;gap:8px">
                  ${comp.temas_distintivos.map(t => {
        const p = (t.posts && t.posts[0]) || {};
        const badgeColor = p.tono_predominante === 'positivo' ? '#15803d' : (p.tono_predominante === 'negativo' ? '#b91c1c' : '#4b5563');
        const badgeBg = p.tono_predominante === 'positivo' ? '#dcfce7' : (p.tono_predominante === 'negativo' ? '#fee2e2' : '#f3f4f6');
        return `
                      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
                        <div>
                          <strong style="font-size:0.9rem;color:#0f172a">${escapeHtml(t.tema)}</strong>
                          <span style="font-size:0.75rem;color:#64748b;margin-left:6px">(${t.total_menciones} menciones)</span>
                        </div>
                        <span style="font-size:0.75rem;background:${badgeBg};color:${badgeColor};padding:3px 8px;border-radius:6px;font-weight:600">
                          Solo en ${escapeHtml(p.alias || 'Post')} (${p.porcentaje || 0}%)
                        </span>
                      </div>
                    `;
      }).join('')}
                </div>
              `}
            </div>
          </div>
        </div>
      `;
    }

    // 4. Detalle individual de publicaciones
    html += `
      <div style="margin-bottom:16px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
        <h2 style="margin:0;font-size:1.25rem">Publicaciones analizadas (${data.posts.length})</h2>
        ${data.posts.length > 1 ? `
          <div id="posts-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap">
            <button type="button" class="filter-chip active" data-post-filter="all">Todas</button>
            ${data.posts.map(p => `
              <button type="button" class="filter-chip" data-post-filter="${escapeHtml(p.alias)}">${escapeHtml(p.alias)}</button>
            `).join('')}
          </div>
        ` : ''}
      </div>
    `;

    // Tarjetas de cada post
    data.posts.forEach((post, postIdx) => {
      const bal = post.balance_postura !== undefined ? post.balance_postura : 0.0;
      let balColor = '#16a34a';
      let balBadge = 'Aprobación Neta Favorable';
      let balBg = '#dcfce7';

      if (bal < -15) {
        balColor = '#dc2626';
        balBadge = 'Alerta de Rechazo / Crítica';
        balBg = '#fee2e2';
      } else if (bal <= 10) {
        balColor = '#d97706';
        balBadge = 'Opinión Dividida';
        balBg = '#fef3c7';
      }

      html += `
        <div class="dashboard-card post-card-item" data-alias="${escapeHtml(post.alias)}" style="margin-bottom:20px;border-left:4px solid #1e293b">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:12px">
            <div>
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                <span style="background:#1e293b;color:#ffffff;font-size:0.8rem;font-weight:700;padding:2px 8px;border-radius:6px">
                  ${escapeHtml(post.alias)}
                </span>
                <strong style="font-size:1.05rem;color:#0f172a">
                  ${escapeHtml(post.perfil_nombre || 'Facebook')}
                </strong>
              </div>
              <div style="font-size:0.8rem;color:#64748b">
                ${post.fecha_post ? `Publicado el ${escapeHtml(post.fecha_post)} · ` : ''}
                ${post.total_comentarios} comentarios (${post.cobertura_pct}% analizados) ·
                👍 ${post.post_likes} likes
              </div>
            </div>
            <a href="${escapeHtml(post.post_url)}" target="_blank" rel="noopener noreferrer" style="display:inline-flex;align-items:center;gap:4px;color:#1d4ed8;font-size:0.82rem;font-weight:600;text-decoration:none">
              <span>Ver Publicación en Facebook</span> ↗
            </a>
          </div>

          ${post.texto_post ? `
            <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:0.88rem;color:#334155;line-height:1.45;max-height:110px;overflow-y:auto;white-space:pre-line">
              «${escapeHtml(post.texto_post)}»
            </div>
          ` : ''}

          <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;padding:12px 14px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">
            <div>
              <div style="font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase">Balance Neto (% Positivo - % Negativo)</div>
              <div style="font-size:1.6rem;font-weight:800;color:${balColor};line-height:1.2;margin-top:2px">
                ${bal > 0 ? '+' : ''}${bal}%
              </div>
            </div>
            <span style="background:${balBg};color:${balColor};font-weight:700;font-size:0.8rem;padding:4px 12px;border-radius:20px;border:1px solid ${balColor}33">
              ${balBadge}
            </span>
          </div>

          <div style="margin-bottom:16px">
            <div style="display:grid;grid-template-columns:repeat(3, 1fr);gap:10px;margin-bottom:8px">
              <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:8px 12px">
                <div style="font-size:0.75rem;font-weight:600;color:#16a34a">🟢 Positivo</div>
                <div style="font-size:1.1rem;font-weight:800;color:#15803d">${(post.porcentajes && post.porcentajes.positivo) || 0}% <span style="font-size:0.75rem;font-weight:500;color:#64748b">(${(post.distribucion && post.distribucion.positivo) || 0})</span></div>
              </div>
              <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:8px 12px">
                <div style="font-size:0.75rem;font-weight:600;color:#64748b">⚪ Neutro</div>
                <div style="font-size:1.1rem;font-weight:800;color:#334155">${(post.porcentajes && post.porcentajes.neutro) || 0}% <span style="font-size:0.75rem;font-weight:500;color:#64748b">(${(post.distribucion && post.distribucion.neutro) || 0})</span></div>
              </div>
              <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:8px 12px">
                <div style="font-size:0.75rem;font-weight:600;color:#dc2626">🔴 Negativo</div>
                <div style="font-size:1.1rem;font-weight:800;color:#b91c1c">${(post.porcentajes && post.porcentajes.negativo) || 0}% <span style="font-size:0.75rem;font-weight:500;color:#64748b">(${(post.distribucion && post.distribucion.negativo) || 0})</span></div>
              </div>
            </div>
          </div>

          <div style="margin-bottom:14px">
            <h4 style="font-size:0.85rem;font-weight:700;color:#475569;text-transform:uppercase;margin:0 0 6px 0">Resumen de la conversación</h4>
            <p style="margin:0;font-size:0.9rem;line-height:1.55;color:#1e293b">${escapeHtml(post.resumen)}</p>
          </div>

          ${post.temas && post.temas.length > 0 ? `
            <div style="margin-bottom:14px">
              <h4 style="font-size:0.85rem;font-weight:700;color:#475569;text-transform:uppercase;margin:0 0 8px 0">Temas destacados (haz clic para ver comentarios)</h4>
              <div style="display:flex;gap:6px;flex-wrap:wrap">
                ${post.temas.map((t, tIdx) => `
                  <button type="button" class="post-theme-chip" data-post-idx="${postIdx}" data-theme-idx="${tIdx}" style="border:1px solid #cbd5e1;background:#ffffff;border-radius:20px;padding:4px 12px;font-size:0.8rem;cursor:pointer;display:inline-flex;align-items:center;gap:6px;transition:all 0.15s">
                    <span style="font-weight:600;color:#0f172a">${escapeHtml(t.texto)}</span>
                    <span style="background:#f1f5f9;color:#475569;font-size:0.72rem;font-weight:700;padding:1px 6px;border-radius:10px">${t.frecuencia}</span>
                  </button>
                `).join('')}
              </div>
            </div>
          ` : ''}

          ${post.comentarios_representativos && post.comentarios_representativos.length > 0 ? `
            <div>
              <h4 style="font-size:0.85rem;font-weight:700;color:#475569;text-transform:uppercase;margin:0 0 8px 0">Comentarios representativos</h4>
              <div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));gap:10px">
                ${post.comentarios_representativos.map(c => {
        const tonoBadge = c.tono === 'positivo' ? { bg: '#dcfce7', color: '#15803d', label: 'Positivo 😊' } : (c.tono === 'negativo' ? { bg: '#fee2e2', color: '#b91c1c', label: 'Crítica 😟' } : { bg: '#f3f4f6', color: '#4b5563', label: 'Neutro 😐' });
        const urlFB = c.comentario_url || c.autor_url;
        return `
                    <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;padding:10px;font-size:0.85rem">
                      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
                        <strong style="color:#0f172a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:160px">${escapeHtml(c.autor_nombre || 'Usuario')}</strong>
                        <span style="background:${tonoBadge.bg};color:${tonoBadge.color};font-size:0.68rem;font-weight:600;padding:2px 6px;border-radius:10px">${tonoBadge.label}</span>
                      </div>
                      <p style="margin:0 0 6px 0;color:#334155;line-height:1.4;background:#f8fafc;padding:6px 8px;border-radius:6px;word-break:break-word">«${escapeHtml(c.texto)}»</p>
                      ${urlFB ? `<div style="text-align:right"><a href="${escapeHtml(urlFB)}" target="_blank" rel="noopener noreferrer" style="color:#1d4ed8;font-size:0.75rem;font-weight:600;text-decoration:none">Ver en FB ↗</a></div>` : ''}
                    </div>
                  `;
      }).join('')}
              </div>
            </div>
          ` : ''}
        </div>
      `;
    });

    postsResultsEl.innerHTML = html;

    // Conectar eventos de exportación a PDF
    const compPdfBtn = postsResultsEl.querySelector('#social-posts-export-comp-pdf');
    if (compPdfBtn) {
      compPdfBtn.addEventListener('click', () => {
        descargarPdfSocial('comparativa', currentPostsResultData, compPdfBtn);
      });
    }
    const postsPdfBtn = postsResultsEl.querySelector('#social-posts-export-detail-pdf');
    if (postsPdfBtn) {
      postsPdfBtn.addEventListener('click', () => {
        descargarPdfSocial('posts', currentPostsResultData, postsPdfBtn);
      });
    }

    // Conectar eventos a los chips de temas para abrir el modal
    postsResultsEl.querySelectorAll('.post-theme-chip').forEach(btn => {
      btn.addEventListener('click', () => {
        const pIdx = parseInt(btn.dataset.postIdx, 10);
        const tIdx = parseInt(btn.dataset.themeIdx, 10);
        const post = currentManualPostsData[pIdx];
        if (post && post.temas && post.temas[tIdx]) {
          openNgramModal(post.temas[tIdx]);
        }
      });
    });

    // Conectar filtro de publicaciones individuales (todas vs alias)
    const filterContainer = postsResultsEl.querySelector('#posts-filter-chips');
    if (filterContainer) {
      filterContainer.querySelectorAll('button').forEach(btn => {
        btn.addEventListener('click', () => {
          filterContainer.querySelectorAll('button').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          const filter = btn.dataset.postFilter;
          postsResultsEl.querySelectorAll('.post-card-item').forEach(card => {
            if (filter === 'all' || card.dataset.alias === filter) {
              card.hidden = false;
            } else {
              card.hidden = true;
            }
          });
        });
      });
    }
  }

  // ── Submit de formulario de posts manuales ────────────────────────────────
  if (postsForm) {
    postsForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      hidePostsError();
      if (postsResultsEl) postsResultsEl.hidden = true;

      const raw = (postsUrlsInput ? postsUrlsInput.value : '').trim();
      if (!raw) {
        showPostsError('Ingresa al menos una URL de publicación de Facebook.');
        return;
      }

      const urls = raw
        .split('\n')
        .map(u => u.trim())
        .filter(u => u.length > 0 && !u.startsWith('#'));

      if (urls.length === 0) {
        showPostsError('Ingresa al menos una URL de publicación válida.');
        return;
      }
      if (urls.length > 20) {
        showPostsError(`Máximo 20 publicaciones por consulta. Has ingresado ${urls.length}.`);
        return;
      }

      if (postsSubmitBtn) {
        postsSubmitBtn.disabled = true;
        postsSubmitBtn.textContent = 'Analizando publicaciones…';
      }
      resetPostsSteps();
      if (postsProgressEl) postsProgressEl.hidden = false;
      startPostsProgressAnimation();

      try {
        const body = {
          post_urls: urls,
          max_comentarios_por_post: parseInt(postsCommentsSlider ? postsCommentsSlider.value : 50, 10),
          forzar_refresco: postsForceRefreshInput ? postsForceRefreshInput.checked : false,
        };

        const res = await fetch('/api/social/posts/analizar', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          credentials: 'same-origin',
          signal: AbortSignal.timeout(360_000),
        });

        stopPostsProgressAnimation();
        if (postsProgressEl) postsProgressEl.hidden = true;

        if (!res.ok) {
          const detail = await res.json().catch(() => ({}));
          throw new Error(detail.detail || `Error HTTP ${res.status}`);
        }

        const data = await res.json();
        renderPostsResults(data);

      } catch (err) {
        stopPostsProgressAnimation();
        if (postsProgressEl) postsProgressEl.hidden = true;
        if (err.name === 'TimeoutError' || err.name === 'AbortError') {
          showPostsError('La operación tardó demasiado. Intenta con menos publicaciones o menos comentarios por post.');
        } else {
          showPostsError(err.message || 'Error de conexión al analizar publicaciones.');
        }
      } finally {
        if (postsSubmitBtn) {
          postsSubmitBtn.disabled = false;
          postsSubmitBtn.textContent = 'Analizar publicaciones';
        }
      }
    });
  }


  // =========================================================================
  // MODO 3: Histórico por Perfil (100% BD Local, 0 Llamadas a Apify)
  // =========================================================================


  const historicoForm = document.getElementById('social-historico-form');
  const historicoUrlInput = document.getElementById('social-historico-url');
  const historicoSubmitBtn = document.getElementById('social-historico-submit');
  const historicoErrorEl = document.getElementById('social-historico-error');
  const historicoResultsEl = document.getElementById('social-historico-results');
  const historicoPillsEl = document.getElementById('social-historico-pills');
  const histAgrSemana = document.getElementById('hist-agr-semana');
  const histAgrMes = document.getElementById('hist-agr-mes');
  const histAgrSemanaLabel = document.getElementById('hist-agr-semana-label');
  const histAgrMesLabel = document.getElementById('hist-agr-mes-label');

  let currentHistoricoData = null;

  function showHistoricoError(msg) {
    if (!historicoErrorEl) return;
    historicoErrorEl.textContent = msg;
    historicoErrorEl.hidden = false;
  }

  function hideHistoricoError() {
    if (!historicoErrorEl) return;
    historicoErrorEl.hidden = true;
    historicoErrorEl.textContent = '';
  }

  function setHistoricoAgrupacionUI(agrupacion) {
    if (!histAgrSemana || !histAgrMes || !histAgrSemanaLabel || !histAgrMesLabel) return;
    if (agrupacion === 'semana') {
      histAgrSemana.checked = true;
      histAgrSemanaLabel.style.background = '#ffffff';
      histAgrSemanaLabel.style.color = '#0f172a';
      histAgrSemanaLabel.style.fontWeight = '700';
      histAgrSemanaLabel.style.boxShadow = '0 1px 2px rgba(0,0,0,0.06)';

      histAgrMesLabel.style.background = 'transparent';
      histAgrMesLabel.style.color = '#475569';
      histAgrMesLabel.style.fontWeight = '600';
      histAgrMesLabel.style.boxShadow = 'none';
    } else {
      histAgrMes.checked = true;
      histAgrMesLabel.style.background = '#ffffff';
      histAgrMesLabel.style.color = '#0f172a';
      histAgrMesLabel.style.fontWeight = '700';
      histAgrMesLabel.style.boxShadow = '0 1px 2px rgba(0,0,0,0.06)';

      histAgrSemanaLabel.style.background = 'transparent';
      histAgrSemanaLabel.style.color = '#475569';
      histAgrSemanaLabel.style.fontWeight = '600';
      histAgrSemanaLabel.style.boxShadow = 'none';
    }
  }

  if (histAgrSemana && histAgrMes) {
    histAgrSemana.addEventListener('change', () => {
      setHistoricoAgrupacionUI('semana');
      if (historicoUrlInput && historicoUrlInput.value.trim() && currentHistoricoData) {
        ejecutarConsultaHistorico('semana');
      }
    });
    histAgrMes.addEventListener('change', () => {
      setHistoricoAgrupacionUI('mes');
      if (historicoUrlInput && historicoUrlInput.value.trim() && currentHistoricoData) {
        ejecutarConsultaHistorico('mes');
      }
    });
  }

  async function cargarPerfilesHistoricos() {
    if (!historicoPillsEl) return;
    try {
      const res = await fetch('/api/social/historico/perfiles', {
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
      });
      if (!res.ok) return;
      const perfiles = await res.json();
      if (!Array.isArray(perfiles) || perfiles.length === 0) {
        historicoPillsEl.innerHTML = '<span style="font-size:0.78rem;color:#94a3b8">Aún no hay publicaciones guardadas en la base de datos local. Ejecuta un análisis de perfil o posts para sembrar datos.</span>';
        return;
      }

      historicoPillsEl.innerHTML = perfiles.map(p => {
        const fechasRango = p.fecha_min && p.fecha_max ? ` · ${p.fecha_min} al ${p.fecha_max}` : '';
        return `
          <button type="button" class="history-profile-chip" data-url="${escapeHtml(p.url_perfil)}" style="display:inline-flex;align-items:center;gap:6px;background:#ffffff;border:1px solid #cbd5e1;border-radius:18px;padding:4px 12px;font-size:0.78rem;cursor:pointer;transition:all 0.15s">
            <span style="font-weight:700;color:#0f172a">👤 ${escapeHtml(p.perfil_nombre || 'Perfil')}</span>
            <span style="color:#64748b">(${p.total_posts} posts · ${p.total_comentarios} com.${fechasRango})</span>
          </button>
        `;
      }).join('');

      historicoPillsEl.querySelectorAll('.history-profile-chip').forEach(btn => {
        btn.addEventListener('click', () => {
          const u = btn.dataset.url;
          if (historicoUrlInput) {
            historicoUrlInput.value = u;
          }
          ejecutarConsultaHistorico();
        });
      });
    } catch (e) {
      console.warn('Error cargando perfiles históricos:', e);
    }
  }

  window.cargarPerfilesHistoricos = cargarPerfilesHistoricos;

  async function ejecutarConsultaHistorico(forzarAgrupacion) {
    hideHistoricoError();
    const url = (historicoUrlInput ? historicoUrlInput.value : '').trim();
    if (!url) {
      showHistoricoError('Ingresa la URL pública de la página o perfil de Facebook.');
      return;
    }

    let agrupacion = forzarAgrupacion;
    if (!agrupacion) {
      agrupacion = histAgrSemana && histAgrSemana.checked ? 'semana' : 'mes';
    }
    setHistoricoAgrupacionUI(agrupacion);

    if (historicoSubmitBtn) {
      historicoSubmitBtn.disabled = true;
      historicoSubmitBtn.textContent = 'Calculando serie histórica…';
    }

    try {
      const q = `/api/social/historico?url_perfil=${encodeURIComponent(url)}&agrupacion=${encodeURIComponent(agrupacion)}`;
      const res = await fetch(q, {
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
      });

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `Error HTTP ${res.status}`);
      }

      const data = await res.json();
      currentHistoricoData = data;
      renderHistoricoResults(data);

    } catch (err) {
      showHistoricoError(err.message || 'Error al obtener la serie histórica del perfil.');
    } finally {
      if (historicoSubmitBtn) {
        historicoSubmitBtn.disabled = false;
        historicoSubmitBtn.textContent = 'Cargar serie histórica';
      }
    }
  }

  if (historicoForm) {
    historicoForm.addEventListener('submit', (e) => {
      e.preventDefault();
      ejecutarConsultaHistorico();
    });
  }

  function renderHistoricoResults(data) {
    if (!historicoResultsEl) return;
    historicoResultsEl.hidden = false;

    if (!data.serie_temporal || data.serie_temporal.length === 0) {
      historicoResultsEl.innerHTML = `
        <div class="dashboard-card" style="border-left:4px solid #f59e0b">
          <h3 style="margin:0 0 6px 0;color:#0f172a">Sin publicaciones con fecha para «${escapeHtml(data.perfil_nombre || data.url_perfil)}»</h3>
          <p style="margin:0;font-size:0.9rem;color:#64748b">
            No se encontraron publicaciones con fecha válida registradas en la base de datos local para este perfil.
            Prueba ejecutando un análisis desde la pestaña <strong>"Por Perfil de Facebook"</strong> para recopilar publicaciones recientes.
          </p>
        </div>
      `;
      return;
    }

    const serie = data.serie_temporal;
    const esSemana = data.agrupacion === 'semana';

    let html = `
      <div style="display:flex;justify-content:flex-end;margin-bottom:16px">
        <button type="button" id="social-historico-export-pdf" class="secondary-button" style="display:inline-flex;align-items:center;gap:6px;font-size:0.85rem;padding:6px 14px">
          <span>📥</span> Descargar reporte histórico en PDF
        </button>
      </div>
    `;

    if (data.resumen_ejecutivo) {
      html += `
        <div class="dashboard-card" style="margin-bottom:20px;border-left:4px solid #3b82f6">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
            <span style="font-size:0.75rem;background:#e0e7ff;color:#3730a3;padding:2px 8px;border-radius:10px;font-weight:700">SÍNTESIS ESTRATÉGICA</span>
            <h3 style="margin:0;font-size:1.1rem;color:#0f172a">Resumen Ejecutivo de Tendencias</h3>
          </div>
          <p style="margin:0;line-height:1.75;color:#334155;white-space:pre-line">${escapeHtml(data.resumen_ejecutivo)}</p>
        </div>
      `;
    }

    html += `
      <div class="dashboard-card" style="margin-bottom:20px;border-left:4px solid #4f46e5">
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:16px">
          <div>
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-size:0.75rem;background:#e0e7ff;color:#3730a3;padding:2px 8px;border-radius:10px;font-weight:700">HISTÓRICO BD</span>
              <h2 style="margin:0;font-size:1.25rem;color:#0f172a">${escapeHtml(data.perfil_nombre || 'Perfil')}</h2>
            </div>
            <p class="composer-hint" style="margin:4px 0 0 0">
              ${data.total_posts} publicaciones totales registradas · ${data.total_comentarios} comentarios recopilados (${data.total_clasificados} con postura clasificada)
            </p>
          </div>

          <!-- Selector rápido de vista dentro de los resultados -->
          <div style="display:flex;align-items:center;gap:6px;background:#f1f5f9;padding:4px;border-radius:8px">
            <button type="button" id="btn-quick-agr-semana" class="secondary-button" style="padding:4px 12px;font-size:0.8rem;border:none;background:${esSemana ? '#ffffff' : 'transparent'};color:${esSemana ? '#0f172a' : '#64748b'};font-weight:${esSemana ? '700' : '600'};box-shadow:${esSemana ? '0 1px 2px rgba(0,0,0,0.06)' : 'none'}">
              Semanas (${serie.length})
            </button>
            <button type="button" id="btn-quick-agr-mes" class="secondary-button" style="padding:4px 12px;font-size:0.8rem;border:none;background:${!esSemana ? '#ffffff' : 'transparent'};color:${!esSemana ? '#0f172a' : '#64748b'};font-weight:${!esSemana ? '700' : '600'};box-shadow:${!esSemana ? '0 1px 2px rgba(0,0,0,0.06)' : 'none'}">
              Meses
            </button>
          </div>
        </div>

        <!-- Contenedor de la gráfica de líneas -->
        <div style="position:relative;width:100%;min-height:430px;background:#ffffff;border-radius:8px;padding:8px">
          <div id="social-historico-plotly-chart" style="width:100%;height:430px"></div>
        </div>
      </div>

      <!-- Tabla de desglose temporal y deltas -->
      <div class="dashboard-card">
        <h3 style="margin:0 0 8px 0;font-size:1.1rem;color:#0f172a">Matriz de Evolución Temporal y Variaciones</h3>
        <p class="composer-hint" style="margin:0 0 14px 0">Detalle de porcentaje de comentarios por tono, balance neto y variación en puntos porcentuales (pp) respecto al periodo anterior.</p>

        <div style="overflow-x:auto">
          <table class="admin-table" style="width:100%;font-size:0.88rem;border-collapse:collapse">
            <thead>
              <tr style="background:#f8fafc;border-bottom:2px solid #e2e8f0;text-align:left">
                <th style="padding:10px 14px">Periodo</th>
                <th style="padding:10px 14px">Rango de fechas</th>
                <th style="padding:10px 14px;text-align:center">Posts</th>
                <th style="padding:10px 14px;text-align:center">Comentarios</th>
                <th style="padding:10px 14px;text-align:center">🟢 Positivo</th>
                <th style="padding:10px 14px;text-align:center">⚪ Neutro</th>
                <th style="padding:10px 14px;text-align:center">🔴 Negativo</th>
                <th style="padding:10px 14px;text-align:center">⚖️ Balance neto</th>
                <th style="padding:10px 14px;text-align:center">Variación vs anterior</th>
              </tr>
            </thead>
            <tbody>
              ${serie.map((p, idx) => {
      const bal = p.balance_postura;
      const balColor = bal > 10 ? '#16a34a' : (bal < -15 ? '#b91c1c' : '#b45309');
      const balBg = bal > 10 ? '#dcfce7' : (bal < -15 ? '#fee2e2' : '#fef3c7');
      const rangoStr = p.fecha_inicio && p.fecha_fin ? `${p.fecha_inicio} al ${p.fecha_fin}` : '—';

      let deltaHtml = '<span style="color:#94a3b8">—</span>';
      if (p.diferencia_balance !== null && p.diferencia_balance !== undefined) {
        const d = p.diferencia_balance;
        const dSign = d > 0 ? '+' : '';
        const dColor = d > 0 ? '#15803d' : (d < 0 ? '#b91c1c' : '#64748b');
        const dArrow = d > 0 ? '▲' : (d < 0 ? '▼' : '▬');
        deltaHtml = `<span style="font-weight:700;color:${dColor}">${dArrow} ${dSign}${d} pp</span>`;
      }

      return `
                  <tr style="border-bottom:1px solid #f1f5f9;background:${idx % 2 === 0 ? '#ffffff' : '#f8fafc'}">
                    <td style="padding:10px 14px;font-weight:700;color:#0f172a">${escapeHtml(p.etiqueta || p.periodo)}</td>
                    <td style="padding:10px 14px;color:#64748b;font-size:0.82rem">${escapeHtml(rangoStr)}</td>
                    <td style="padding:10px 14px;text-align:center;font-weight:600;color:#334155">${p.posts}</td>
                    <td style="padding:10px 14px;text-align:center;color:#334155">${p.comentarios}</td>
                    <td style="padding:10px 14px;text-align:center;font-weight:700;color:#15803d">${p.positivo}%</td>
                    <td style="padding:10px 14px;text-align:center;color:#64748b">${p.neutro}%</td>
                    <td style="padding:10px 14px;text-align:center;font-weight:700;color:#b91c1c">${p.negativo}%</td>
                    <td style="padding:10px 14px;text-align:center">
                      <span style="background:${balBg};color:${balColor};padding:3px 10px;border-radius:12px;font-weight:700;font-size:0.82rem">
                        ${bal > 0 ? '+' : ''}${bal}%
                      </span>
                    </td>
                    <td style="padding:10px 14px;text-align:center;font-size:0.82rem">
                      ${deltaHtml}
                    </td>
                  </tr>
                `;
    }).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;

    historicoResultsEl.innerHTML = html;

    // Conectar botones rápidos de agrupación dentro de la card
    const quickSemana = historicoResultsEl.querySelector('#btn-quick-agr-semana');
    const quickMes = historicoResultsEl.querySelector('#btn-quick-agr-mes');
    if (quickSemana) {
      quickSemana.addEventListener('click', () => ejecutarConsultaHistorico('semana'));
    }
    if (quickMes) {
      quickMes.addEventListener('click', () => ejecutarConsultaHistorico('mes'));
    }

    // Renderizar gráfica con Plotly
    renderPlotlyHistoricoChart(serie, data.agrupacion);

    // Conectar botón de exportación a PDF
    const histPdfBtn = historicoResultsEl.querySelector('#social-historico-export-pdf');
    if (histPdfBtn) {
      histPdfBtn.addEventListener('click', () => {
        descargarPdfSocial('historico', currentHistoricoData, histPdfBtn);
      });
    }
  }

  function renderPlotlyHistoricoChart(serie, agrupacion) {
    const chartDiv = document.getElementById('social-historico-plotly-chart');
    if (!chartDiv || typeof Plotly === 'undefined') return;

    const xVals = serie.map(p => p.etiqueta || p.periodo);

    // Preparar customdata para hover individual detallado
    const customdata = serie.map(p => ({
      periodo: p.etiqueta || p.periodo,
      pos: p.positivo,
      neg: p.negativo,
      neu: p.neutro,
      posts: p.posts,
      comms: p.comentarios,
      bal: p.balance_postura,
    }));

    const hoverTemplatePos =
      '<b>%{customdata.periodo}</b><br>' +
      '🟢 <b>Positivo: %{customdata.pos:.1f}%</b><br>' +
      '🔴 Negativo: %{customdata.neg:.1f}%<br>' +
      '⚪ Neutro: %{customdata.neu:.1f}%<br>' +
      '📄 Publicaciones: %{customdata.posts}<br>' +
      '💬 Comentarios: %{customdata.comms}<br>' +
      '⚖️ Balance de postura: %{customdata.bal:+.1f}%<extra></extra>';

    const hoverTemplateNeg =
      '<b>%{customdata.periodo}</b><br>' +
      '🟢 Positivo: %{customdata.pos:.1f}%<br>' +
      '🔴 <b>Negativo: %{customdata.neg:.1f}%</b><br>' +
      '⚪ Neutro: %{customdata.neu:.1f}%<br>' +
      '📄 Publicaciones: %{customdata.posts}<br>' +
      '💬 Comentarios: %{customdata.comms}<br>' +
      '⚖️ Balance de postura: %{customdata.bal:+.1f}%<extra></extra>';

    const hoverTemplateNeu =
      '<b>%{customdata.periodo}</b><br>' +
      '🟢 Positivo: %{customdata.pos:.1f}%<br>' +
      '🔴 Negativo: %{customdata.neg:.1f}%<br>' +
      '⚪ <b>Neutro: %{customdata.neu:.1f}%</b><br>' +
      '📄 Publicaciones: %{customdata.posts}<br>' +
      '💬 Comentarios: %{customdata.comms}<br>' +
      '⚖️ Balance de postura: %{customdata.bal:+.1f}%<extra></extra>';

    const hoverTemplateBal =
      '<b>%{customdata.periodo}</b><br>' +
      '⚖️ <b>Balance de postura: %{customdata.bal:+.1f}%</b><br>' +
      '🟢 Positivo: %{customdata.pos:.1f}%<br>' +
      '🔴 Negativo: %{customdata.neg:.1f}%<br>' +
      '⚪ Neutro: %{customdata.neu:.1f}%<br>' +
      '📄 Publicaciones: %{customdata.posts}<br>' +
      '💬 Comentarios: %{customdata.comms}<extra></extra>';

    const tracePositivo = {
      x: xVals,
      y: serie.map(p => p.positivo),
      name: '🟢 Positivo',
      type: 'scatter',
      mode: 'lines+markers',
      line: { color: '#16a34a', width: 3, shape: 'spline' },
      marker: { size: 9, color: '#16a34a', symbol: 'circle' },
      customdata: customdata,
      hovertemplate: hoverTemplatePos,
    };

    const traceNegativo = {
      x: xVals,
      y: serie.map(p => p.negativo),
      name: '🔴 Negativo',
      type: 'scatter',
      mode: 'lines+markers',
      line: { color: '#dc2626', width: 3, shape: 'spline' },
      marker: { size: 9, color: '#dc2626', symbol: 'circle' },
      customdata: customdata,
      hovertemplate: hoverTemplateNeg,
    };

    const traceNeutro = {
      x: xVals,
      y: serie.map(p => p.neutro),
      name: '⚪ Neutro',
      type: 'scatter',
      mode: 'lines+markers',
      line: { color: '#64748b', width: 2, dash: 'dot', shape: 'spline' },
      marker: { size: 8, color: '#64748b', symbol: 'diamond' },
      customdata: customdata,
      hovertemplate: hoverTemplateNeu,
    };

    const traceBalance = {
      x: xVals,
      y: serie.map(p => p.balance_postura),
      name: '⚖️ Balance de postura (Eje der.)',
      type: 'scatter',
      mode: 'lines+markers',
      yaxis: 'y2',
      line: { color: '#4f46e5', width: 2, dash: 'dash', shape: 'spline' },
      marker: { size: 7, color: '#4f46e5', symbol: 'square' },
      customdata: customdata,
      hovertemplate: hoverTemplateBal,
    };

    const layout = {
      title: {
        text: '<b>Evolución de la postura en comentarios</b>',
        font: { size: 16, color: '#0f172a', family: 'system-ui, -apple-system, sans-serif' },
        x: 0.02,
        y: 0.98,
      },
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
      xaxis: {
        title: {
          text: agrupacion === 'semana' ? 'Semanas de publicación' : 'Meses de publicación',
          font: { size: 12, color: '#64748b' },
        },
        tickfont: { size: 11, color: '#334155' },
        showgrid: true,
        gridcolor: '#f1f5f9',
      },
      yaxis: {
        title: {
          text: 'Porcentaje de comentarios (%)',
          font: { size: 12, color: '#334155' },
        },
        range: [0, 105],
        ticksuffix: '%',
        showgrid: true,
        gridcolor: '#e2e8f0',
        zeroline: false,
      },
      yaxis2: {
        title: {
          text: 'Balance neto de postura (pp)',
          font: { size: 11, color: '#4f46e5' },
        },
        overlaying: 'y',
        side: 'right',
        range: [-105, 105],
        ticksuffix: '%',
        showgrid: false,
        zeroline: true,
        zerolinecolor: '#cbd5e1',
        zerolinewidth: 1.5,
      },
      legend: {
        orientation: 'h',
        x: 0.5,
        xanchor: 'center',
        y: -0.22,
        font: { size: 12, color: '#1e293b' },
      },
      margin: { l: 55, r: 55, t: 50, b: 70 },
      hovermode: 'closest',
    };

    const config = {
      responsive: true,
      displayModeBar: false,
    };

    Plotly.newPlot(chartDiv, [tracePositivo, traceNegativo, traceNeutro, traceBalance], layout, config);
  }

})();
