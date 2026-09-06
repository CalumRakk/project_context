(function () {
  const sessionHistory = [];
  let isPanelOpen = false;
  let deleteConfirmTimeout = null;

  window.addEventListener('__AI_STUDIO_INTERCEPT__', (event) => {
    const { type, payload } = event.detail;

    if (type === 'stream-start') {
      const exists = sessionHistory.some(h => h.id === payload.requestId);
      if (!exists) {
        sessionHistory.unshift({
          id: payload.requestId,
          timestamp: payload.timestamp,
          text: '',
          thoughts: '',
          isBlocked: false,
          finishReason: 'GENERATING...'
        });
        updateBadge();
      }
    } 
    else if (type === 'stream-progress' || type === 'stream-complete') {
      let item = sessionHistory.find(h => h.id === payload.requestId);
      
      if (!item) {
        item = {
          id: payload.requestId,
          timestamp: payload.timestamp || Date.now(),
          text: '',
          thoughts: '',
          isBlocked: false,
          finishReason: 'GENERATING...'
        };
        sessionHistory.unshift(item);
      }

      item.text = payload.accumulatedText || item.text;
      item.thoughts = payload.thoughtsText || item.thoughts;
      item.isBlocked = payload.isBlocked;
      item.finishReason = payload.finishReason;

      if (type === 'stream-complete' && !item.text && !item.thoughts && !item.isBlocked) {
        const idx = sessionHistory.indexOf(item);
        if (idx !== -1) sessionHistory.splice(idx, 1);
      }

      updateBadge();
      if (isPanelOpen) renderHistoryList();

      if (type === 'stream-complete' && payload.isBlocked) {
        showBlockedToast(payload);
      }
    }
  });

  function initUI() {
    if (document.getElementById('ai-interceptor-root')) return;

    const root = document.createElement('div');
    root.id = 'ai-interceptor-root';
    root.innerHTML = `
      <button id="ai-interceptor-badge" title="Historial y rescate de respuestas">
        <span class="badge-icon">🛡️</span>
        <span class="badge-text">Logs</span>
        <span id="ai-interceptor-count">0</span>
      </button>

      <div id="ai-interceptor-drawer" class="drawer-hidden">
        <div class="drawer-header">
          <div class="drawer-title">
            <span>🛡️ Historial de Respuestas</span>
            <small>Memoria de sesión activa</small>
          </div>
          <div class="drawer-actions">
            <button id="ai-interceptor-clear" title="Limpiar historial">
              <span class="clear-icon">🗑️</span>
              <span class="clear-text"></span>
            </button>
            <div class="action-divider"></div>
            <button id="ai-interceptor-close" title="Cerrar panel">✕</button>
          </div>
        </div>
        <div id="ai-interceptor-list" class="drawer-content">
          <div class="drawer-empty">No hay respuestas capturadas aún en esta pestaña.</div>
        </div>
      </div>

      <div id="ai-interceptor-toast" class="toast-hidden"></div>
    `;

    document.body.appendChild(root);

    document.getElementById('ai-interceptor-badge').addEventListener('click', toggleDrawer);
    document.getElementById('ai-interceptor-close').addEventListener('click', toggleDrawer);
    
    // Configuración del botón de borrado seguro con confirmación
    setupSecureClearButton();
  }

  function setupSecureClearButton() {
    const clearBtn = document.getElementById('ai-interceptor-clear');
    if (!clearBtn) return;

    function resetClearState() {
      if (deleteConfirmTimeout) {
        clearTimeout(deleteConfirmTimeout);
        deleteConfirmTimeout = null;
      }
      clearBtn.classList.remove('btn-confirm-delete');
      clearBtn.querySelector('.clear-icon').textContent = '🗑️';
      clearBtn.querySelector('.clear-text').textContent = '';
      clearBtn.title = 'Limpiar historial';
    }

    clearBtn.addEventListener('click', (e) => {
      e.stopPropagation();

      if (sessionHistory.length === 0) return;

      // Segundo clic: El usuario confirmó
      if (clearBtn.classList.contains('btn-confirm-delete')) {
        sessionHistory.length = 0;
        updateBadge();
        renderHistoryList();
        resetClearState();
      } 
      // Primer clic: Pedir confirmación con animación
      else {
        clearBtn.classList.add('btn-confirm-delete');
        clearBtn.querySelector('.clear-icon').textContent = '⚠️';
        clearBtn.querySelector('.clear-text').textContent = '¿Confirmar?';
        clearBtn.title = 'Haz clic de nuevo para borrar todo';

        deleteConfirmTimeout = setTimeout(() => {
          resetClearState();
        }, 3500); // 3.5 segundos para confirmar
      }
    });

    // Si hace clic fuera del botón, cancela la confirmación
    document.addEventListener('click', (e) => {
      if (!clearBtn.contains(e.target) && clearBtn.classList.contains('btn-confirm-delete')) {
        resetClearState();
      }
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

    toast.innerHTML = `
      <div class="toast-content">
        <span class="toast-icon">⚠️</span>
        <div>
          <strong>¡Respuesta Interrumpida por Seguridad!</strong>
          <p>Motivo: <span class="tag-reason">${payload.finishReason}</span></p>
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

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUI);
  } else {
    initUI();
  }
})();