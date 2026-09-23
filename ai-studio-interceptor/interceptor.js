(function () {
  console.log('%c[AI Studio Vault] 🚀 Interceptor Híbrido (XHR + Fetch) Activo', 'color: #8ab4f8; font-weight: bold; font-size: 13px;');

  function isTargetUrl(url) {
    if (!url || typeof url !== 'string') return false;
    return (
      url.includes('MakerSuiteService/GenerateContent') ||
      url.includes('GenerateContent') ||
      url.includes('streamGenerateContent')
    );
  }

  // Soporta /prompts/ID, /prompt/ID, /u/0/prompts/ID, etc.
  function getActiveChatId() {
    const path = window.location.pathname;
    const parts = path.split('/').filter(Boolean);
    const promptIdx = parts.findIndex(p => p === 'prompts' || p === 'prompt');
    if (promptIdx !== -1 && parts[promptIdx + 1]) {
      return parts[promptIdx + 1];
    }
    return 'new_chat';
  }

  function extractPromptMetadata(bodyData) {
    let promptText = '';
    let turnIndex = 1;

    try {
      let parsed = null;
      if (typeof bodyData === 'string') {
        parsed = JSON.parse(bodyData);
      } else if (bodyData instanceof ArrayBuffer || bodyData instanceof Uint8Array) {
        const text = new TextDecoder().decode(bodyData);
        parsed = JSON.parse(text);
      } else if (typeof bodyData === 'object' && bodyData !== null) {
        parsed = bodyData;
      }

      if (parsed) {
        const contents = parsed.contents || (parsed[1] && parsed[1].contents);
        if (Array.isArray(contents)) {
          turnIndex = contents.length;
          for (let i = contents.length - 1; i >= 0; i--) {
            const item = contents[i];
            const role = item.role || (Array.isArray(item) ? item[1] : null);
            if (role === 'user' || !role) {
              const parts = item.parts || (Array.isArray(item) ? item[0] : null);
              if (Array.isArray(parts)) {
                promptText = parts.map(p => (typeof p === 'string' ? p : p.text || '')).filter(Boolean).join('\n');
              } else if (typeof item === 'string') {
                promptText = item;
              }
              if (promptText) break;
            }
          }
        }
      }
    } catch (e) {}

    return {
      promptText: promptText.trim() || '(Prompt interceptado)',
      turnIndex: turnIndex || 1,
      chatId: getActiveChatId()
    };
  }

  
  // 1. HOOK PARA XMLHTTPREQUEST (USADO POR AI STUDIO)
  
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this._url = typeof url === 'string' ? url : (url?.toString() || '');
    return originalOpen.apply(this, [method, url, ...rest]);
  };

  XMLHttpRequest.prototype.send = function (body) {
    if (this._url && isTargetUrl(this._url)) {
      const requestId = 'req_' + Date.now();
      const meta = extractPromptMetadata(body);
      let started = false;

      const handleProgress = (isComplete = false) => {
        try {
          if (this.responseText) {
            if (!started) {
              dispatchAppEvent('stream-start', {
                requestId,
                chatId: meta.chatId,
                turnIndex: meta.turnIndex,
                promptText: meta.promptText,
                timestamp: Date.now()
              });
              started = true;
            }
            parseAndExtract(this.responseText, requestId, meta, isComplete);
          }
        } catch (e) {
          console.error('[Vault] Error en streaming XHR:', e);
        }
      };

      this.addEventListener('progress', () => handleProgress(false));
      this.addEventListener('load', () => handleProgress(true));
      this.addEventListener('error', () => handleProgress(true));
      this.addEventListener('abort', () => handleProgress(true));
    }

    return originalSend.apply(this, arguments);
  };

  
  // 2. HOOK PARA FETCH (RESPALDO Y STREAMING)
  
  const originalFetch = window.fetch;
  window.fetch = async function (...args) {
    let url = '';
    let bodyData = null;

    if (typeof args[0] === 'string') {
      url = args[0];
      bodyData = args[1]?.body;
    } else if (args[0] instanceof Request) {
      url = args[0].url;
      try { bodyData = await args[0].clone().text(); } catch (e) {}
    } else if (args[0] && typeof args[0] === 'object') {
      url = args[0].url || '';
      bodyData = args[0].body;
    }

    const response = await Reflect.apply(originalFetch, window, args);

    if (url && isTargetUrl(url)) {
      const requestId = 'req_' + Date.now();
      const meta = extractPromptMetadata(bodyData);
      attachFetchStreamSpy(response, requestId, meta);
    }

    return response;
  };

  function attachFetchStreamSpy(response, requestId, meta) {
    if (!response?.body?.getReader) return;

    let started = false;
    const originalGetReader = response.body.getReader.bind(response.body);
    const decoder = new TextDecoder('utf-8');
    let fullRawText = '';

    response.body.getReader = function (...readerArgs) {
      const reader = originalGetReader(...readerArgs);
      const originalRead = reader.read.bind(reader);

      // Captura si el cliente o el filtro cancelan el stream deliberadamente
      const originalCancel = reader.cancel ? reader.cancel.bind(reader) : null;
      if (originalCancel) {
        reader.cancel = async function (...cancelArgs) {
          try {
            if (fullRawText) {
              parseAndExtract(fullRawText, requestId, meta, true);
            }
          } catch (e) {}
          return originalCancel(...cancelArgs);
        };
      }

      reader.read = async function () {
        let result;
        try {
          result = await originalRead();
        } catch (streamErr) {
          // Si el stream es abortado con un AbortError (típico de filtros de seguridad)
          try {
            if (fullRawText) {
              parseAndExtract(fullRawText, requestId, meta, true);
            }
          } catch (e) {}
          throw streamErr;
        }

        try {
          if (!started) {
            dispatchAppEvent('stream-start', {
              requestId,
              chatId: meta.chatId,
              turnIndex: meta.turnIndex,
              promptText: meta.promptText,
              timestamp: Date.now()
            });
            started = true;
          }

          if (!result.done && result.value) {
            fullRawText += decoder.decode(result.value, { stream: true });
            parseAndExtract(fullRawText, requestId, meta, false);
          } else if (result.done) {
            parseAndExtract(fullRawText, requestId, meta, true);
          }
        } catch (err) {}

        return result;
      };

      return reader;
    };
  }

  
  // 3. PARSER Y DETECTOR DE RESPUESTAS
  
  function parseAndExtract(rawText, requestId, meta, isComplete) {
    if (!rawText || rawText.trim().length === 0) return;

    let accumulatedText = '';
    let thoughtsText = '';
    let isBlocked = false;
    let finishReason = isComplete ? 'STOP' : 'GENERATING...';

    const jsonMatches = extractJsonArrays(rawText);

    for (const jsonStr of jsonMatches) {
      try {
        const parsed = JSON.parse(jsonStr);
        extractFromJspb(parsed, (text, isThought) => {
          if (isThought) {
            thoughtsText += text;
          } else {
            accumulatedText += text;
          }
        }, (reason, safetyFlag) => {
          finishReason = reason;
          if (safetyFlag) isBlocked = true;
        });
      } catch (e) {}
    }

    if (!accumulatedText && !thoughtsText && !isBlocked) return;

    dispatchAppEvent(isComplete ? 'stream-complete' : 'stream-progress', {
      requestId,
      chatId: meta.chatId,
      turnIndex: meta.turnIndex,
      promptText: meta.promptText,
      accumulatedText,
      thoughtsText,
      isBlocked,
      finishReason,
      timestamp: Date.now()
    });
  }

  function extractJsonArrays(text) {
    const results = [];
    let startIdx = -1;
    let depth = 0;

    for (let i = 0; i < text.length; i++) {
      if (text[i] === '[') {
        if (depth === 0) startIdx = i;
        depth++;
      } else if (text[i] === ']') {
        depth--;
        if (depth === 0 && startIdx !== -1) {
          results.push(text.substring(startIdx, i + 1));
          startIdx = -1;
        }
      }
    }
    return results;
  }

  function extractFromJspb(node, onTextFound, onFinishReason) {
    if (!node) return;

    if (typeof node === 'object' && !Array.isArray(node)) {
      if (node.text && typeof node.text === 'string') {
        onTextFound(node.text, !!node.thought);
      }
      if (node.finishReason && typeof node.finishReason === 'string') {
        const isSafety = ['SAFETY', 'PROHIBITED_CONTENT', 'BLOCKLIST', 'SPII'].includes(node.finishReason);
        onFinishReason(node.finishReason, isSafety);
      }
      if (Array.isArray(node.safetyRatings)) {
        const hasBlocked = node.safetyRatings.some(r => r.blocked || r.probability === 'HIGH');
        if (hasBlocked) onFinishReason('SAFETY', true);
      }
      for (const key in node) {
        extractFromJspb(node[key], onTextFound, onFinishReason);
      }
      return;
    }

    if (Array.isArray(node)) {
      if (node.length >= 2 && typeof node[1] === 'string' && (node[0] === null || typeof node[0] === 'number')) {
        const textCandidate = node[1];
        if (
          textCandidate.length > 0 &&
          !textCandidate.startsWith('!FBel') &&
          !textCandidate.startsWith('CAES') &&
          !textCandidate.startsWith('generic::')
        ) {
          const isThought = node[2] === true || node[3] === true;
          onTextFound(textCandidate, isThought);
        }
      }

      for (const item of node) {
        if (typeof item === 'string') {
          if (['SAFETY', 'PROHIBITED_CONTENT', 'BLOCKLIST', 'SPII'].includes(item)) {
            onFinishReason(item, true);
          } else if (['STOP', 'MAX_TOKENS', 'RECITATION'].includes(item)) {
            onFinishReason(item, false);
          }
        }
      }

      for (const child of node) {
        if (typeof child === 'object' && child !== null) {
          extractFromJspb(child, onTextFound, onFinishReason);
        }
      }
    }
  }

  function dispatchAppEvent(type, payload) {
    window.dispatchEvent(new CustomEvent('__AI_STUDIO_INTERCEPT__', {
      detail: { type, payload }
    }));
  }
})();