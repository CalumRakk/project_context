(function () {
  let records = [];
  let currentChatId = getActiveChatId();
  let isPanelOpen = false;
  let selectedAttempts = {};
  let deleteConfirmTimeout = null;

  function getActiveChatId() {
    const path = window.location.pathname;
    const parts = path.split('/').filter(Boolean);
    if (parts.length >= 2 && parts[0] === 'prompts') {
      return parts[1];
    }
    return 'new_chat';
  }

  function simpleHash(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash) + str.charCodeAt(i);
      hash |= 0;
    }
    return Math.abs(hash).toString(36);
  }

  function getPromptKey(chatId, turnIndex, promptText) {
    return `${chatId}_t${turnIndex}_${simpleHash(promptText)}`;
  }

  async function loadStorage() {
    try {
      const data = await chrome.storage.local.get('ai_vault_records');
      records = data.ai_vault_records || [];
      updateBadge();
      if (isPanelOpen) renderPromptList();
    } catch (e) {
      console.error('[Vault] Error cargando storage:', e);
    }
  }

  async function persistStorage() {
    try {
      await chrome.storage.local.set({ ai_vault_records: records });
      updateBadge();
    } catch (e) {
      console.error('[Vault] Error guardando storage:', e);
    }
  }

  // Monitor de cambio de URL en SPA
  setInterval(() => {
    const newChatId = getActiveChatId();
    if (newChatId !== currentChatId) {
      const oldChatId = currentChatId;
      currentChatId = newChatId;

      // Migrar registros de new_chat al nuevo ID permanente de Drive
      if (oldChatId === 'new_chat' && newChatId !== 'new_chat') {
        records.forEach(r => {
          if (r.chatId === 'new_chat') {
            r.chatId = newChatId;
            r.key = getPromptKey(newChatId, r.turnIndex, r.promptText);
          }
        });
        persistStorage();
      }

      updateChatHeader();
      updateBadge();
      if (isPanelOpen) renderPromptList();
    }
  }, 1000);

  // ==========================================
  // RECEPCIÓN DE MENSAJES DEL INTERCEPTOR
  // ==========================================
  window.addEventListener('__AI_STUDIO_INTERCEPT__', (event) => {
    const { type, payload } = event.detail;
    currentChatId = getActiveChatId();

    // 1. Buscar si ya existe el registro asociado a esta petición exacta
    let promptRecord = records.find(r => r.attempts.some(a => a.id === payload.requestId));

    // 2. Si no, buscar por clave de prompt
    const key = getPromptKey(payload.chatId || currentChatId, payload.turnIndex, payload.promptText);
    if (!promptRecord) {
      promptRecord = records.find(r => r.key === key);
    }

    // 3. Crear el registro si es un inicio de stream o primera recepción
    if (!promptRecord) {
      promptRecord = {
        key,
        chatId: payload.chatId || currentChatId,
        turnIndex: payload.turnIndex,
        promptText: payload.promptText,
        createdAt: payload.timestamp,
        updatedAt: payload.timestamp,
        attempts: []
      };
      records.unshift(promptRecord);
    }

    // Actualizar chatId por si cambió de new_chat a Drive ID durante el envío
    if (promptRecord.chatId === 'new_chat' && currentChatId !== 'new_chat') {
      promptRecord.chatId = currentChatId;
      promptRecord.key = getPromptKey(currentChatId, promptRecord.turnIndex, promptRecord.promptText);
    }

    // Buscar o crear el intento (attempt)
    let attempt = promptRecord.attempts.find(a => a.id === payload.requestId);
    if (!attempt) {
      attempt = {
        id: payload.requestId,
        timestamp: payload.timestamp,
        text: '',
        thoughts: '',
        finishReason: 'GENERATING...',
        isBlocked: false
      };
      promptRecord.attempts.push(attempt);
    }

    if (type === 'stream-start') {
      promptRecord.updatedAt = payload.timestamp;
      selectedAttempts[promptRecord.key] = promptRecord.attempts.length - 1;
      updateBadge();
      if (isPanelOpen) renderPromptList();
    } 
    else if (type === 'stream-progress' || type === 'stream-complete') {
      attempt.text = payload.accumulatedText || attempt.text;
      attempt.thoughts = payload.thoughtsText || attempt.thoughts;
      attempt.isBlocked = payload.isBlocked;
      attempt.finishReason = payload.finishReason;
      promptRecord.updatedAt = payload.timestamp;

      if (type === 'stream-complete') {
        persistStorage();
        if (payload.isBlocked) {
          showBlockedToast(payload);
        }
      }

      updateBadge();
      if (isPanelOpen) renderPromptList();
    }
  });

  // ==========================================
  // CONSTRUCCIÓN DE LA INTERFAZ
  // ==========================================
  function initUI() {
    if (document.getElementById('ai-interceptor-root')) return;

    const root = document.createElement('div');
    root.id = 'ai-interceptor-root';
    root.innerHTML = `
      <button id="ai-interceptor-badge" title="Bóveda de Prompts y Respuestas">
        <span class="badge-icon">🛡️</span>
        <span class="badge-text">Vault</span>
        <span id="ai-interceptor-count">0</span>
      </button>

      <div id="ai-interceptor-drawer" class="drawer-hidden">
        <div class="drawer-header">
          <div class="drawer-title">
            <span>🛡️ Prompt & Response Vault</span>
            <small id="vault-current-scope">Chat: new_chat</small>
          </div>
          <div class="drawer-actions">
            <button id="ai-interceptor-export" title="Exportar chat a Markdown">📥</button>
            <button id="ai-interceptor-clear" title="Limpiar este chat">
              <span class="clear-icon">🗑️</span>
              <span class="clear-text"></span>
            </button>
            <div class="action-divider"></div>
            <button id="ai-interceptor-close" title="Cerrar panel">✕</button>
          </div>
        </div>
        <div id="ai-interceptor-list" class="drawer-content">
          <div class="drawer-empty">No hay respuestas capturadas en este chat aún.</div>
        </div>
      </div>

      <div id="ai-interceptor-toast" class="toast-hidden"></div>
    `;

    document.body.appendChild(root);

    document.getElementById('ai-interceptor-badge').addEventListener('click', toggleDrawer);
    document.getElementById('ai-interceptor-close').addEventListener('click', toggleDrawer);
    document.getElementById('ai-interceptor-export').addEventListener('click', exportChatToMarkdown);
    
    setupSecureClearButton();
    loadStorage();
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
      clearBtn.title = 'Limpiar este chat';
    }

    clearBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const chatRecords = getActiveChatRecords();
      if (chatRecords.length === 0) return;

      if (clearBtn.classList.contains('btn-confirm-delete')) {
        records = records.filter(r => r.chatId !== currentChatId);
        persistStorage();
        renderPromptList();
        resetClearState();
      } else {
        clearBtn.classList.add('btn-confirm-delete');
        clearBtn.querySelector('.clear-icon').textContent = '⚠️';
        clearBtn.querySelector('.clear-text').textContent = '¿Borrar chat?';

        deleteConfirmTimeout = setTimeout(() => {
          resetClearState();
        }, 3500);
      }
    });

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
      updateChatHeader();
      renderPromptList();
    } else {
      drawer.classList.add('drawer-hidden');
    }
  }

  function updateChatHeader() {
    const scopeEl = document.getElementById('vault-current-scope');
    if (scopeEl) {
      const label = currentChatId === 'new_chat' ? 'Chat Nuevo' : `ID: ${currentChatId.substring(0, 12)}...`;
      scopeEl.textContent = `Chat activo: ${label}`;
    }
  }

  function getActiveChatRecords() {
    return records.filter(r => r.chatId === currentChatId);
  }

  function updateBadge() {
    const countEl = document.getElementById('ai-interceptor-count');
    const badgeEl = document.getElementById('ai-interceptor-badge');
    if (!countEl || !badgeEl) return;

    const chatRecords = getActiveChatRecords();
    countEl.textContent = chatRecords.length;

    const hasBlocked = chatRecords.some(r => r.attempts.some(a => a.isBlocked));
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
          <strong>¡Respuesta Interrumpida por Filtro!</strong>
          <p>Motivo: <span class="tag-reason">${payload.finishReason}</span></p>
          <small>El texto generado ha sido rescatado en la tarjeta de este prompt.</small>
        </div>
      </div>
      <button id="toast-open-btn">Ver Rescate</button>
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

  function renderPromptList() {
    const listEl = document.getElementById('ai-interceptor-list');
    if (!listEl) return;

    const chatRecords = getActiveChatRecords();

    if (chatRecords.length === 0) {
      listEl.innerHTML = '<div class="drawer-empty">No hay prompts ni respuestas capturadas en este chat.</div>';
      return;
    }

    listEl.innerHTML = chatRecords.map(record => {
      const attemptsCount = record.attempts.length;
      const activeIndex = selectedAttempts[record.key] !== undefined 
        ? Math.min(selectedAttempts[record.key], attemptsCount - 1) 
        : attemptsCount - 1;

      const currentAttempt = record.attempts[activeIndex] || {
        text: '',
        thoughts: '',
        finishReason: 'VACÍO',
        isBlocked: false,
        timestamp: record.createdAt
      };

      const time = new Date(currentAttempt.timestamp).toLocaleTimeString();
      const statusClass = currentAttempt.isBlocked ? 'status-blocked' : (currentAttempt.finishReason === 'STOP' ? 'status-ok' : 'status-generating');
      const textPreview = currentAttempt.text ? escapeHtml(currentAttempt.text) : '<em style="color:#888;">(Sin texto generado)</em>';
      const thoughtsPreview = currentAttempt.thoughts ? `<details class="item-thoughts"><summary>💭 Pensamiento (${currentAttempt.thoughts.length} chars)</summary><pre>${escapeHtml(currentAttempt.thoughts)}</pre></details>` : '';

      const versionTabs = attemptsCount > 1 ? `
        <div class="attempts-selector">
          <span class="attempts-label">Versiones:</span>
          ${record.attempts.map((att, idx) => `
            <button class="attempt-tab ${idx === activeIndex ? 'active' : ''} ${att.isBlocked ? 'blocked' : ''}" data-key="${record.key}" data-index="${idx}">
              v${idx + 1} ${att.isBlocked ? '⚠️' : '✅'}
            </button>
          `).join('')}
        </div>
      ` : '';

      return `
        <div class="prompt-card ${currentAttempt.isBlocked ? 'item-blocked' : ''}">
          <div class="prompt-card-header">
            <span class="prompt-turn-tag">Turno #${record.turnIndex}</span>
            <div class="prompt-header-actions">
              <span class="item-time">${time}</span>
              <span class="item-status ${statusClass}">${currentAttempt.finishReason}</span>
            </div>
          </div>

          <div class="prompt-box">
            <strong>Prompt:</strong>
            <p>${escapeHtml(record.promptText)}</p>
          </div>

          ${versionTabs}
          ${thoughtsPreview}

          <div class="response-body">
            <pre>${textPreview}</pre>
          </div>

          <div class="prompt-card-footer">
            <span class="char-count">${currentAttempt.text.length} chars rescatados</span>
            <div class="card-footer-buttons">
              <button class="copy-prompt-btn" data-key="${record.key}">📋 Copiar Prompt</button>
              <button class="copy-response-btn" data-key="${record.key}" data-index="${activeIndex}">📋 Copiar Respuesta</button>
            </div>
          </div>
        </div>
      `;
    }).join('');

    listEl.querySelectorAll('.attempt-tab').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const key = e.target.getAttribute('data-key');
        const index = parseInt(e.target.getAttribute('data-index'), 10);
        selectedAttempts[key] = index;
        renderPromptList();
      });
    });

    listEl.querySelectorAll('.copy-prompt-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const key = e.target.getAttribute('data-key');
        const record = records.find(r => r.key === key);
        if (record) {
          navigator.clipboard.writeText(record.promptText).then(() => {
            feedbackButton(e.target, '✅ Prompt Copiado');
          });
        }
      });
    });

    listEl.querySelectorAll('.copy-response-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const key = e.target.getAttribute('data-key');
        const index = parseInt(e.target.getAttribute('data-index'), 10);
        const record = records.find(r => r.key === key);
        const attempt = record?.attempts[index];
        if (attempt && attempt.text) {
          navigator.clipboard.writeText(attempt.text).then(() => {
            feedbackButton(e.target, '✅ Respuesta Copiada');
          });
        }
      });
    });
  }

  function feedbackButton(btn, text) {
    const original = btn.textContent;
    btn.textContent = text;
    setTimeout(() => { btn.textContent = original; }, 1800);
  }

  function exportChatToMarkdown() {
    const chatRecords = getActiveChatRecords();
    if (chatRecords.length === 0) return;

    let md = `# Historial Rescatado de AI Studio\n**Chat ID:** \`${currentChatId}\`\n**Fecha:** ${new Date().toLocaleString()}\n\n---\n\n`;

    chatRecords.forEach((rec) => {
      md += `## Turno #${rec.turnIndex}: ${rec.promptText}\n\n`;
      rec.attempts.forEach((att, idx) => {
        md += `### Intento v${idx + 1} (${att.finishReason} - ${new Date(att.timestamp).toLocaleTimeString()})\n\n`;
        if (att.thoughts) {
          md += `> **Pensamiento:**\n> ${att.thoughts.replace(/\n/g, '\n> ')}\n\n`;
        }
        md += `\`\`\`\n${att.text}\n\`\`\`\n\n`;
      });
      md += `---\n\n`;
    });

    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `AI_Studio_Vault_${currentChatId}_${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function escapeHtml(text) {
    if (!text) return '';
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