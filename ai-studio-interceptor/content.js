(function () {
  // Historial en memoria de la sesión activa
  const sessionHistory = [];
  let isPanelOpen = false;

  // Escuchar eventos del interceptor en MAIN world
  window.addEventListener('__AI_STUDIO_INTERCEPT__', (event) => {
    const { type, payload } = event.detail;
    console.log('%c[CONTENT SCRIPT] 📥 Recibido:', 'color: #ff9800; font-weight: bold;', type, payload);

    if (type === 'stream-start') {
      sessionHistory.unshift({
        id: payload.requestId,
        timestamp: payload.timestamp,
        text: '',
        thoughts: '',
        isBlocked: false,
        finishReason: 'GENERATING...',
        safetyRatings: []
      });
      updateBadge();
    } 
    else if (type === 'stream-progress') {
      const item = sessionHistory.find(h => h.id === payload.requestId);
      if (item) {
        item.text = payload.accumulatedText;
        item.thoughts = payload.thoughtsText;
        item.isBlocked = payload.isBlocked;
        item.finishReason = payload.finishReason;
        item.safetyRatings = payload.safetyRatings;
        if (isPanelOpen) renderHistoryList();
      }
    } 
    else if (type === 'stream-complete') {
      const item = sessionHistory.find(h => h.id === payload.requestId);
      if (item) {
        item.text = payload.accumulatedText;
        item.thoughts = payload.thoughtsText;
        item.isBlocked = payload.isBlocked;
        item.finishReason = payload.finishReason;
        item.safetyRatings = payload.safetyRatings;
        item.promptFeedback = payload.promptFeedback;
      }

      updateBadge();
      if (isPanelOpen) renderHistoryList();

      if (payload.isBlocked) {
        showBlockedToast(payload);
      }
    }
  });

  // Inyectar UI cuando cargue el DOM
  function initUI() {
    if (document.getElementById('ai-interceptor-root')) return;

    const root = document.createElement('div');
    root.id = 'ai-interceptor-root';
    root.innerHTML = `
      <!-- Botón Flotante -->
      <button id="ai-interceptor-badge" title="Historial y rescate de respuestas">
        <span class="badge-icon">🛡️</span>
        <span class="badge-text">Logs</span>
        <span id="ai-interceptor-count">0</span>
      </button>

      <!-- Panel Lateral Deslizable -->
      <div id="ai-interceptor-drawer" class="drawer-hidden">
        <div class="drawer-header">
          <div class="drawer-title">
            <span>🛡️ Historial de Respuestas</span>
            <small>Memoria de sesión activa</small>
          </div>
          <div class="drawer-actions">
            <button id="ai-interceptor-clear" title="Limpiar historial en memoria">🗑️</button>
            <button id="ai-interceptor-close">✕</button>
          </div>
        </div>
        <div id="ai-interceptor-list" class="drawer-content">
          <div class="drawer-empty">No hay respuestas capturadas aún en esta pestaña.</div>
        </div>
      </div>

      <!-- Notificación Toast -->
      <div id="ai-interceptor-toast" class="toast-hidden"></div>
    `;

    document.body.appendChild(root);

    // Event listeners
    document.getElementById('ai-interceptor-badge').addEventListener('click', toggleDrawer);
    document.getElementById('ai-interceptor-close').addEventListener('click', toggleDrawer);
    document.getElementById('ai-interceptor-clear').addEventListener('click', () => {
      sessionHistory.length = 0;
      updateBadge();
      renderHistoryList();
    });
  }

  function toggleDrawer() {
    isPanelOpen = !isPanelOpen;
    const drawer = document.getElementById('ai-interceptor-drawer');
    if (isPanelOpen) {
      drawer.classList.remove('drawer-hidden');
      renderHistoryList();
    } else {
      drawer.classList.add('drawer-hidden');
    }
  }

  function updateBadge() {
    const countEl = document.getElementById('ai-interceptor-count');
    const badgeEl = document.getElementById('ai-interceptor-badge');
    if (!countEl || !badgeEl) return;

    countEl.textContent = sessionHistory.length;
    
    const hasBlocked = sessionHistory.some(h => h.isBlocked);
    if (hasBlocked) {
      badgeEl.classList.add('has-blocked');
    } else {
      badgeEl.classList.remove('has-blocked');
    }
  }

  function showBlockedToast(payload) {
    const toast = document.getElementById('ai-interceptor-toast');
    if (!toast) return;

    const reasons = (payload.safetyRatings || [])
      .filter(r => r.blocked || r.probability === 'HIGH')
      .map(r => r.category.replace('HARM_CATEGORY_', ''))
      .join(', ') || payload.finishReason;

    toast.innerHTML = `
      <div class="toast-content">
        <span class="toast-icon">⚠️</span>
        <div>
          <strong>¡Respuesta Interrumpida por Seguridad!</strong>
          <p>Motivo: <span class="tag-reason">${reasons}</span></p>
          <small>El texto generado hasta el corte ha sido rescatado en el panel.</small>
        </div>
      </div>
      <button id="toast-open-btn">Ver Texto</button>
    `;

    toast.classList.remove('toast-hidden');

    document.getElementById('toast-open-btn')?.addEventListener('click', () => {
      if (!isPanelOpen) toggleDrawer();
      toast.classList.add('toast-hidden');
    });

    setTimeout(() => {
      toast.classList.add('toast-hidden');
    }, 7000);
  }

  function renderHistoryList() {
    const listEl = document.getElementById('ai-interceptor-list');
    if (!listEl) return;

    if (sessionHistory.length === 0) {
      listEl.innerHTML = '<div class="drawer-empty">No hay respuestas capturadas aún.</div>';
      return;
    }

    listEl.innerHTML = sessionHistory.map((item, idx) => {
      const time = new Date(item.timestamp).toLocaleTimeString();
      const statusClass = item.isBlocked ? 'status-blocked' : (item.finishReason === 'STOP' ? 'status-ok' : 'status-generating');
      const textPreview = item.text ? escapeHtml(item.text) : '<em style="color:#888;">(Sin texto generado)</em>';
      const thoughtsPreview = item.thoughts ? `<details class="item-thoughts"><summary>Ver Pensamiento (${item.thoughts.length} chars)</summary><pre>${escapeHtml(item.thoughts)}</pre></details>` : '';

      return `
        <div class="history-item ${item.isBlocked ? 'item-blocked' : ''}">
          <div class="item-meta">
            <span class="item-time">${time}</span>
            <span class="item-status ${statusClass}">${item.finishReason}</span>
          </div>
          ${thoughtsPreview}
          <div class="item-body">
            <pre>${textPreview}</pre>
          </div>
          <div class="item-footer">
            <span class="char-count">${item.text.length} caracteres rescatados</span>
            <button class="copy-btn" data-index="${idx}">📋 Copiar Texto</button>
          </div>
        </div>
      `;
    }).join('');

    // Handlers de copia
    listEl.querySelectorAll('.copy-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const index = e.target.getAttribute('data-index');
        const item = sessionHistory[index];
        if (item && item.text) {
          navigator.clipboard.writeText(item.text).then(() => {
            const originalText = e.target.textContent;
            e.target.textContent = '✅ ¡Copiado!';
            setTimeout(() => { e.target.textContent = originalText; }, 1800);
          });
        }
      });
    });
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // Inicializar UI tras cargar el DOM
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUI);
  } else {
    initUI();
  }
})();