(function () {
  let captures = [];
  let chatCounters = {}; // { [chatId]: number }
  let isStorageLoaded = false;
  let currentChatId = getActiveChatId();
  let isPanelOpen = false;

  let deleteChatConfirmTimeout = null;
  let saveDebounceTimeout = null;

  // Estado de búsqueda
  let searchQuery = '';

  // Control de confirmación de eliminación por ID
  let deleteCaptureConfirmTimeout = null;
  let confirmingCaptureId = null;

  // Control de edición por ID
  let editingCaptureId = null;

  function resetCaptureDeleteState() {
    if (deleteCaptureConfirmTimeout) {
      clearTimeout(deleteCaptureConfirmTimeout);
      deleteCaptureConfirmTimeout = null;
    }
    confirmingCaptureId = null;
  }

  function resetCaptureEditState() {
    editingCaptureId = null;
  }

  function resetSearch() {
    searchQuery = '';
    const searchInput = document.getElementById('vault-search-input');
    const clearBtn = document.getElementById('vault-search-clear');
    const metaEl = document.getElementById('vault-search-meta');
    if (searchInput) searchInput.value = '';
    if (clearBtn) clearBtn.style.display = 'none';
    if (metaEl) {
      metaEl.textContent = '';
      metaEl.style.display = 'none';
    }
  }

  function getActiveChatId() {
    const path = window.location.pathname;
    const parts = path.split('/').filter(Boolean);
    const promptIdx = parts.findIndex(p => p === 'prompts' || p === 'prompt');
    if (promptIdx !== -1 && parts[promptIdx + 1]) {
      return parts[promptIdx + 1];
    }
    return 'new_chat';
  }

  
  // HELPERS DE BÚSQUEDA Y SANITIZACIÓN
  
  function escapeRegExp(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  // Filtra etiquetas de razonamiento/pensamiento incrustadas en el texto
  function filterThoughts(text) {
    if (!text) return '';
    return text
      .replace(/<thought>[\s\S]*?(?:<\/thought>|$)/gi, '')
      .replace(/<think>[\s\S]*?(?:<\/think>|$)/gi, '')
      .trim();
  }

  function countMatches(text, query) {
    const cleanText = filterThoughts(text);
    if (!cleanText || !query) return 0;
    const regex = new RegExp(escapeRegExp(query), 'gi');
    const matches = cleanText.match(regex);
    return matches ? matches.length : 0;
  }

  function highlightText(text, query) {
    const cleanText = filterThoughts(text);
    if (!cleanText) return '';
    const safeText = escapeHtml(cleanText);
    const trimmedQuery = (query || '').trim();
    if (!trimmedQuery) return safeText;

    const safeQuery = escapeHtml(trimmedQuery);
    const regex = new RegExp(`(${escapeRegExp(safeQuery)})`, 'gi');
    return safeText.replace(regex, '<mark class="vault-highlight">$1</mark>');
  }

  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  
  // ALMACENAMIENTO Y MIGRACIÓN TRANSPARENTE
  
  async function loadStorage() {
    try {
      const data = await chrome.storage.local.get(['ai_vault_captures', 'ai_vault_counters', 'ai_vault_records']);
      
      chatCounters = data.ai_vault_counters || {};

      if (data.ai_vault_captures) {
        captures = data.ai_vault_captures;
      } else if (Array.isArray(data.ai_vault_records) && data.ai_vault_records.length > 0) {
        console.log('[Vault] Migrando registros antiguos a formato de capturas planas...');
        captures = [];
        data.ai_vault_records.forEach(rec => {
          const chatId = rec.chatId || 'new_chat';
          if (Array.isArray(rec.attempts)) {
            rec.attempts.forEach(att => {
              chatCounters[chatId] = (chatCounters[chatId] || 0) + 1;
              captures.push({
                id: att.id || ('cap_' + att.timestamp + '_' + Math.random().toString(36).substring(2, 7)),
                chatId: chatId,
                version: chatCounters[chatId],
                timestamp: att.timestamp || rec.createdAt || Date.now(),
                text: att.text || '',
                thoughts: att.thoughts || '',
                finishReason: att.finishReason || 'STOP',
                isBlocked: !!att.isBlocked
              });
            });
          }
        });
        await chrome.storage.local.set({ ai_vault_captures: captures, ai_vault_counters: chatCounters });
        await chrome.storage.local.remove('ai_vault_records');
      } else {
        captures = [];
      }

      isStorageLoaded = true;
      updateBadge();
      if (isPanelOpen) renderCaptureList();
    } catch (e) {
      console.error('[Vault] Error cargando storage:', e);
    }
  }

  async function persistStorage() {
    if (!isStorageLoaded) return;
    try {
      await chrome.storage.local.set({
        ai_vault_captures: captures,
        ai_vault_counters: chatCounters
      });
      updateBadge();
    } catch (e) {
      console.error('[Vault] Error guardando storage:', e);
    }
  }

  function debouncedPersistStorage(delay = 400) {
    if (saveDebounceTimeout) clearTimeout(saveDebounceTimeout);
    saveDebounceTimeout = setTimeout(() => {
      persistStorage();
    }, delay);
  }

  loadStorage();

  // Monitor de cambio de URL en SPA
  setInterval(() => {
    const newChatId = getActiveChatId();
    if (newChatId !== currentChatId) {
      const oldChatId = currentChatId;
      currentChatId = newChatId;

      if (oldChatId === 'new_chat' && newChatId !== 'new_chat' && isStorageLoaded) {
        let modified = false;
        captures.forEach(c => {
          if (c.chatId === 'new_chat') {
            c.chatId = newChatId;
            modified = true;
          }
        });
        if (chatCounters['new_chat']) {
          chatCounters[newChatId] = Math.max(chatCounters[newChatId] || 0, chatCounters['new_chat']);
          delete chatCounters['new_chat'];
          modified = true;
        }
        if (modified) persistStorage();
      }

      resetCaptureDeleteState();
      resetCaptureEditState();
      resetSearch();
      updateChatHeader();
      updateBadge();
      if (isPanelOpen) renderCaptureList();
    }
  }, 1000);

  
  // RECEPCIÓN DE MENSAJES DEL INTERCEPTOR
  
  window.addEventListener('__AI_STUDIO_INTERCEPT__', (event) => {
    const { type, payload } = event.detail;
    currentChatId = getActiveChatId();
    const effectiveChatId = payload.chatId || currentChatId;

    let capture = captures.find(c => c.id === payload.requestId);

    if (!capture) {
      chatCounters[effectiveChatId] = (chatCounters[effectiveChatId] || 0) + 1;
      const nextVersion = chatCounters[effectiveChatId];

      capture = {
        id: payload.requestId,
        chatId: effectiveChatId,
        version: nextVersion,
        timestamp: payload.timestamp,
        text: '',
        thoughts: '',
        finishReason: 'GENERATING...',
        isBlocked: false
      };
      // Insertar al inicio de la lista global
      captures.unshift(capture);
    }

    if (capture.chatId === 'new_chat' && currentChatId !== 'new_chat') {
      capture.chatId = currentChatId;
    }

    if (type === 'stream-start') {
      debouncedPersistStorage(200);
      updateBadge();
      if (isPanelOpen) {
        renderCaptureList();
        // Mantener el scroll arriba al empezar una nueva respuesta
        const listEl = document.getElementById('ai-interceptor-list');
        if (listEl) listEl.scrollTop = 0;
      }
    } else if (type === 'stream-progress' || type === 'stream-complete') {
      capture.text = payload.accumulatedText || capture.text;
      capture.thoughts = payload.thoughtsText || capture.thoughts;
      capture.isBlocked = payload.isBlocked;
      capture.finishReason = payload.finishReason;
      capture.timestamp = payload.timestamp;

      if (type === 'stream-complete') {
        if (saveDebounceTimeout) clearTimeout(saveDebounceTimeout);
        persistStorage();
        if (payload.isBlocked) {
          showBlockedToast(payload);
        }
      } else {
        debouncedPersistStorage(500);
      }

      updateBadge();
      if (isPanelOpen) renderCaptureList();
    }
  });

  
  // ACCIONES SOBRE CAPTURAS (POR ID ÚNICO)
  
  function deleteCapture(id) {
    const index = captures.findIndex(c => c.id === id);
    if (index === -1) return;

    if (editingCaptureId === id) {
      resetCaptureEditState();
    }

    captures.splice(index, 1);

    persistStorage();
    updateBadge();
    renderCaptureList();
  }

  function saveEditedCapture(id, newText) {
    const capture = captures.find(c => c.id === id);
    if (!capture) return;

    capture.text = newText;
    capture.timestamp = Date.now();

    resetCaptureEditState();
    persistStorage();
    renderCaptureList();
  }

  // Devuelve las capturas del chat actual ordenadas: ÚLTIMA ARRIBA, MÁS VIEJA AL FINAL
  function getActiveChatCaptures() {
    return captures
      .filter(c => c.chatId === currentChatId)
      .sort((a, b) => {
        if (b.version !== undefined && a.version !== undefined && b.version !== a.version) {
          return b.version - a.version;
        }
        return (b.timestamp || 0) - (a.timestamp || 0);
      });
  }

  
  // CONSTRUCCIÓN DE LA INTERFAZ
  
  function initUI() {
    if (document.getElementById('ai-interceptor-root')) return;

    const root = document.createElement('div');
    root.id = 'ai-interceptor-root';
    root.innerHTML = `
      <button id="ai-interceptor-badge" title="Bóveda de Respuestas">
        <span class="badge-icon">🛡️</span>
        <span class="badge-text">Vault</span>
        <span id="ai-interceptor-count">0</span>
      </button>

      <div id="ai-interceptor-drawer" class="drawer-hidden">
        <div class="drawer-header">
          <div class="drawer-title">
            <span>🛡️ Response Vault</span>
            <small id="vault-current-scope">Chat: new_chat</small>
          </div>
          <div class="drawer-actions">
            <button id="ai-interceptor-export" title="Exportar capturas a Markdown">📥</button>
            <button id="ai-interceptor-clear" title="Limpiar capturas de este chat">
              <span class="clear-icon">🗑️</span>
              <span class="clear-text"></span>
            </button>
            <div class="action-divider"></div>
            <button id="ai-interceptor-close" title="Cerrar panel">✕</button>
          </div>
        </div>

        <div class="drawer-search-container">
          <div class="search-input-wrapper">
            <span class="search-icon">🔍</span>
            <input 
              type="text" 
              id="vault-search-input" 
              placeholder="Buscar en el texto capturado..." 
              autocomplete="off" 
              spellcheck="false" 
            />
            <button id="vault-search-clear" title="Limpiar búsqueda" style="display: none;">✕</button>
          </div>
          <div id="vault-search-meta" class="search-meta" style="display: none;"></div>
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

    setupSearchBar();
    setupSecureClearButton();
    updateChatHeader();
    updateBadge();

    document.addEventListener('click', (e) => {
      if (confirmingCaptureId && !e.target.closest('.delete-capture-btn')) {
        resetCaptureDeleteState();
        renderCaptureList();
      }
    });
  }

  function setupSearchBar() {
    const input = document.getElementById('vault-search-input');
    const clearBtn = document.getElementById('vault-search-clear');
    if (!input || !clearBtn) return;

    input.addEventListener('input', (e) => {
      searchQuery = e.target.value;
      clearBtn.style.display = searchQuery ? 'flex' : 'none';
      renderCaptureList();
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        resetSearch();
        renderCaptureList();
      }
    });

    clearBtn.addEventListener('click', () => {
      resetSearch();
      renderCaptureList();
      input.focus();
    });
  }

  function setupSecureClearButton() {
    const clearBtn = document.getElementById('ai-interceptor-clear');
    if (!clearBtn) return;

    function resetClearState() {
      if (deleteChatConfirmTimeout) {
        clearTimeout(deleteChatConfirmTimeout);
        deleteChatConfirmTimeout = null;
      }
      clearBtn.classList.remove('btn-confirm-delete');
      clearBtn.querySelector('.clear-icon').textContent = '🗑️';
      clearBtn.querySelector('.clear-text').textContent = '';
      clearBtn.title = 'Limpiar capturas de este chat';
    }

    clearBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const chatCaptures = getActiveChatCaptures();
      if (chatCaptures.length === 0) return;

      if (clearBtn.classList.contains('btn-confirm-delete')) {
        captures = captures.filter(c => c.chatId !== currentChatId);
        delete chatCounters[currentChatId];
        resetCaptureEditState();
        resetCaptureDeleteState();
        resetSearch();
        persistStorage();
        renderCaptureList();
        resetClearState();
      } else {
        clearBtn.classList.add('btn-confirm-delete');
        clearBtn.querySelector('.clear-icon').textContent = '⚠️';
        clearBtn.querySelector('.clear-text').textContent = '¿Borrar chat?';

        deleteChatConfirmTimeout = setTimeout(() => {
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
      renderCaptureList();
      // Asegurar que el scroll siempre empiece arriba para ver la última captura
      const listEl = document.getElementById('ai-interceptor-list');
      if (listEl) listEl.scrollTop = 0;
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

  function updateBadge() {
    const countEl = document.getElementById('ai-interceptor-count');
    const badgeEl = document.getElementById('ai-interceptor-badge');
    if (!countEl || !badgeEl) return;

    const chatCaptures = getActiveChatCaptures();
    countEl.textContent = chatCaptures.length;

    const hasBlocked = chatCaptures.some(c => c.isBlocked);
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
          <small>El texto generado ha sido rescatado en el panel.</small>
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

  
  // RENDERIZADO DE CAPTURAS
  
  function renderCaptureList() {
    const listEl = document.getElementById('ai-interceptor-list');
    const metaEl = document.getElementById('vault-search-meta');
    if (!listEl) return;

    const chatCaptures = getActiveChatCaptures();
    const query = searchQuery.trim();

    if (chatCaptures.length === 0) {
      listEl.innerHTML = '<div class="drawer-empty">No hay respuestas capturadas en este chat aún.</div>';
      if (metaEl) metaEl.style.display = 'none';
      return;
    }

    let displayCaptures = chatCaptures;
    let totalMatchesInChat = 0;

    if (query) {
      displayCaptures = chatCaptures.filter(cap => {
        const matches = countMatches(cap.text, query);
        if (matches > 0) {
          totalMatchesInChat += matches;
          return true;
        }
        return false;
      });

      if (metaEl) {
        metaEl.style.display = 'block';
        if (displayCaptures.length > 0) {
          metaEl.innerHTML = `<span>${totalMatchesInChat}</span> ${totalMatchesInChat === 1 ? 'coincidencia' : 'coincidencias'} en <span>${displayCaptures.length}</span> ${displayCaptures.length === 1 ? 'captura' : 'capturas'}`;
        } else {
          metaEl.innerHTML = `<span class="search-no-match">Sin coincidencias para "${escapeHtml(query)}"</span>`;
        }
      }
    } else {
      if (metaEl) {
        metaEl.textContent = '';
        metaEl.style.display = 'none';
      }
    }

    if (displayCaptures.length === 0) {
      listEl.innerHTML = `<div class="drawer-empty">No se encontraron respuestas que contengan "<strong>${escapeHtml(query)}</strong>".</div>`;
      return;
    }

    listEl.innerHTML = displayCaptures.map(cap => {
      const isEditing = (editingCaptureId === cap.id);
      const isConfirming = (confirmingCaptureId === cap.id);
      const time = new Date(cap.timestamp).toLocaleTimeString();
      const statusClass = cap.isBlocked ? 'status-blocked' : (cap.finishReason === 'STOP' ? 'status-ok' : 'status-generating');

      const cleanText = filterThoughts(cap.text);

      const textPreview = cleanText 
        ? highlightText(cleanText, query)
        : '<em style="color:#888;">(Sin texto generado)</em>';

      const deleteBtnText = isConfirming ? '⚠️ ¿Confirmar?' : `🗑️ Eliminar`;
      const deleteBtnClass = isConfirming ? 'delete-capture-btn btn-confirm-delete' : 'delete-capture-btn';

      const responseHtml = isEditing ? `
        <div class="response-edit-wrapper">
          <textarea class="response-edit-area" data-id="${cap.id}" placeholder="Edita el texto rescatado...">${escapeHtml(cleanText)}</textarea>
          <div class="response-edit-hint">Atajo: <code>Ctrl + Enter</code> para guardar | <code>Esc</code> para cancelar</div>
        </div>
      ` : `
        <div class="response-body">
          <pre>${textPreview}</pre>
        </div>
      `;

      const footerButtons = isEditing ? `
        <button class="save-capture-btn" data-id="${cap.id}">💾 Guardar</button>
        <button class="cancel-capture-btn" data-id="${cap.id}">✖️ Cancelar</button>
      ` : `
        <button class="edit-capture-btn" data-id="${cap.id}" title="Editar esta captura">✏️ Editar</button>
        <button class="copy-response-btn" data-id="${cap.id}">📋 Copiar</button>
        <button class="${deleteBtnClass}" data-id="${cap.id}" title="Eliminar captura v${cap.version}">
          ${deleteBtnText}
        </button>
      `;

      return `
        <div class="prompt-card ${cap.isBlocked ? 'item-blocked' : ''} ${isEditing ? 'item-editing' : ''}">
          <div class="prompt-card-header">
            <span class="prompt-turn-tag">Captura v${cap.version}</span>
            <div class="prompt-header-actions">
              <span class="item-time">${time}</span>
              <span class="item-status ${statusClass}">${cap.finishReason}</span>
            </div>
          </div>

          ${responseHtml}

          <div class="prompt-card-footer">
            <span class="char-count">${cleanText.length} chars</span>
            <div class="card-footer-buttons">
              ${footerButtons}
            </div>
          </div>
        </div>
      `;
    }).join('');

    attachCardEventListeners(listEl);
  }

  function attachCardEventListeners(listEl) {
    listEl.querySelectorAll('.edit-capture-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        resetCaptureDeleteState();
        editingCaptureId = btn.getAttribute('data-id');
        renderCaptureList();

        const textarea = listEl.querySelector(`.response-edit-area[data-id="${editingCaptureId}"]`);
        if (textarea) {
          textarea.focus();
          textarea.setSelectionRange(textarea.value.length, textarea.value.length);
        }
      });
    });

    listEl.querySelectorAll('.save-capture-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-id');
        const textarea = listEl.querySelector(`.response-edit-area[data-id="${id}"]`);
        if (textarea) {
          saveEditedCapture(id, textarea.value);
        }
      });
    });

    listEl.querySelectorAll('.cancel-capture-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        resetCaptureEditState();
        renderCaptureList();
      });
    });

    listEl.querySelectorAll('.response-edit-area').forEach(textarea => {
      textarea.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          const id = textarea.getAttribute('data-id');
          saveEditedCapture(id, textarea.value);
        } else if (e.key === 'Escape') {
          e.preventDefault();
          resetCaptureEditState();
          renderCaptureList();
        }
      });
    });

    // Copiar al portapapeles (texto limpio sin pensamientos)
    listEl.querySelectorAll('.copy-response-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const id = e.target.getAttribute('data-id');
        const cap = captures.find(c => c.id === id);
        const textToCopy = cap ? filterThoughts(cap.text) : '';
        if (textToCopy) {
          navigator.clipboard.writeText(textToCopy).then(() => {
            feedbackButton(e.target, '✅ Copiado');
          });
        }
      });
    });

    listEl.querySelectorAll('.delete-capture-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = btn.getAttribute('data-id');

        if (confirmingCaptureId === id) {
          resetCaptureDeleteState();
          deleteCapture(id);
        } else {
          resetCaptureDeleteState();
          confirmingCaptureId = id;
          renderCaptureList();

          deleteCaptureConfirmTimeout = setTimeout(() => {
            resetCaptureDeleteState();
            renderCaptureList();
          }, 3500);
        }
      });
    });
  }

  function feedbackButton(btn, text) {
    const original = btn.textContent;
    btn.textContent = text;
    setTimeout(() => { btn.textContent = original; }, 1800);
  }

  
  // EXPORTACIÓN A MARKDOWN (SIN PENSAMIENTOS)
  
  function exportChatToMarkdown() {
    const chatCaptures = getActiveChatCaptures();
    if (chatCaptures.length === 0) return;

    // Exportar ordenado cronológicamente (v1, v2...)
    const ordered = [...chatCaptures].sort((a, b) => a.version - b.version);

    let md = `# Historial Rescatado de AI Studio\n**Chat ID:** \`${currentChatId}\`\n**Fecha:** ${new Date().toLocaleString()}\n\n---\n\n`;

    ordered.forEach((cap) => {
      const cleanText = filterThoughts(cap.text);
      md += `## Captura v${cap.version} (${cap.finishReason} - ${new Date(cap.timestamp).toLocaleTimeString()})\n\n`;
      md += `\`\`\`\n${cleanText}\n\`\`\`\n\n---\n\n`;
    });

    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `AI_Studio_Vault_${currentChatId}_${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUI);
  } else {
    initUI();
  }
})();