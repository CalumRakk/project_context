(function () {
  console.log('%c[AI Studio Vault] 🚀 Interceptor Híbrido Activo (Modo Capturas)', 'color: #8ab4f8; font-weight: bold; font-size: 13px;');

  function isTargetUrl(url) {
    if (!url || typeof url !== 'string') return false;
    return (
      url.includes('MakerSuiteService/GenerateContent') ||
      url.includes('GenerateContent') ||
      url.includes('streamGenerateContent')
    );
  }

  // Obtiene el ID del chat actual de la URL
  function getActiveChatId() {
    const path = window.location.pathname;
    const parts = path.split('/').filter(Boolean);
    const promptIdx = parts.findIndex(p => p === 'prompts' || p === 'prompt');
    if (promptIdx !== -1 && parts[promptIdx + 1]) {
      return parts[promptIdx + 1];
    }
    return 'new_chat';
  }

  function generateCaptureId() {
    return 'cap_' + Date.now() + '_' + Math.random().toString(36).substring(2, 8);
  }

  
  // 1. HOOK PARA XMLHTTPREQUEST
  
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this._url = typeof url === 'string' ? url : (url?.toString() || '');
    return originalOpen.apply(this, [method, url, ...rest]);
  };

  XMLHttpRequest.prototype.send = function (body) {
    if (this._url && isTargetUrl(this._url)) {
      const requestId = generateCaptureId();
      const chatId = getActiveChatId();
      let started = false;

      const handleProgress = (isComplete = false) => {
        try {
          if (this.responseText) {
            if (!started) {
              dispatchAppEvent('stream-start', {
                requestId,
                chatId,
                timestamp: Date.now()
              });
              started = true;
            }
            parseAndExtract(this.responseText, requestId, chatId, isComplete);
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

  
  // 2. HOOK PARA FETCH
  
  const originalFetch = window.fetch;
  window.fetch = async function (...args) {
    let url = '';
    if (typeof args[0] === 'string') {
      url = args[0];
    } else if (args[0] instanceof Request) {
      url = args[0].url;
    } else if (args[0] && typeof args[0] === 'object') {
      url = args[0].url || '';
    }

    const response = await Reflect.apply(originalFetch, window, args);

    if (url && isTargetUrl(url)) {
      const requestId = generateCaptureId();
      const chatId = getActiveChatId();
      attachFetchStreamSpy(response, requestId, chatId);
    }

    return response;
  };

  function attachFetchStreamSpy(response, requestId, chatId) {
    if (!response?.body?.getReader) return;

    let started = false;
    const originalGetReader = response.body.getReader.bind(response.body);
    const decoder = new TextDecoder('utf-8');
    let fullRawText = '';

    response.body.getReader = function (...readerArgs) {
      const reader = originalGetReader(...readerArgs);
      const originalRead = reader.read.bind(reader);

      const originalCancel = reader.cancel ? reader.cancel.bind(reader) : null;
      if (originalCancel) {
        reader.cancel = async function (...cancelArgs) {
          try {
            if (fullRawText) {
              parseAndExtract(fullRawText, requestId, chatId, true);
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
          try {
            if (fullRawText) {
              parseAndExtract(fullRawText, requestId, chatId, true);
            }
          } catch (e) {}
          throw streamErr;
        }

        try {
          if (!started) {
            dispatchAppEvent('stream-start', {
              requestId,
              chatId,
              timestamp: Date.now()
            });
            started = true;
          }

          if (!result.done && result.value) {
            fullRawText += decoder.decode(result.value, { stream: true });
            parseAndExtract(fullRawText, requestId, chatId, false);
          } else if (result.done) {
            parseAndExtract(fullRawText, requestId, chatId, true);
          }
        } catch (err) {}

        return result;
      };

      return reader;
    };
  }

  
  // 3. PARSER Y DETECTOR DE RESPUESTAS
  
  function parseAndExtract(rawText, requestId, chatId, isComplete) {
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
      chatId,
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