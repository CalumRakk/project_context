(function () {
  console.log('%c[AI Studio Interceptor] 🚀 Inicializado (Filtros Precisos)', 'color: #8ab4f8; font-weight: bold; font-size: 13px;');

  function isTargetUrl(url) {
    if (!url || typeof url !== 'string') return false;
    // Interceptar estrictamente la generación de contenido (no conteos de tokens ni metadatos)
    return (
      url.includes('MakerSuiteService/GenerateContent') ||
      url.includes('GenerateContent') ||
      url.includes('streamGenerateContent')
    );
  }

  //
  // 1. HOOK PARA XMLHTTPREQUEST (XHR)
  //
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this._url = typeof url === 'string' ? url : (url?.toString() || '');
    this._method = method;
    return originalOpen.apply(this, [method, url, ...rest]);
  };

  XMLHttpRequest.prototype.send = function (...args) {
    if (this._url && isTargetUrl(this._url)) {
      const requestId = 'req_' + Date.now();
      let started = false;

      const handleProgress = (isComplete = false) => {
        try {
          if (this.responseText) {
            if (!started) {
              dispatchAppEvent('stream-start', { requestId, timestamp: Date.now() });
              started = true;
            }
            parseAndExtract(this.responseText, requestId, isComplete);
          }
        } catch (e) {
          console.error('[Interceptor] Error en streaming XHR:', e);
        }
      };

      this.addEventListener('progress', () => handleProgress(false));
      this.addEventListener('load', () => handleProgress(true));
    }

    return originalSend.apply(this, args);
  };

  //
  // 2. HOOK PARA FETCH
  //
  const originalFetch = window.fetch;
  window.fetch = async function (...args) {
    let url = '';
    if (typeof args[0] === 'string') {
      url = args[0];
    } else if (args[0] instanceof Request) {
      url = args[0].url;
    } else if (args[0] && typeof args[0] === 'object' && args[0].url) {
      url = args[0].url;
    }

    const response = await Reflect.apply(originalFetch, window, args);

    if (url && isTargetUrl(url)) {
      const requestId = 'req_' + Date.now();
      attachPassiveStreamSpy(response, requestId);
    }

    return response;
  };

  function attachPassiveStreamSpy(response, requestId) {
    if (!response?.body?.getReader) return;

    let started = false;
    const originalGetReader = response.body.getReader.bind(response.body);
    const decoder = new TextDecoder('utf-8');
    let fullRawText = '';

    response.body.getReader = function (...readerArgs) {
      const reader = originalGetReader(...readerArgs);
      const originalRead = reader.read.bind(reader);

      reader.read = async function () {
        const result = await originalRead();
        try {
          if (!started) {
            dispatchAppEvent('stream-start', { requestId, timestamp: Date.now() });
            started = true;
          }

          if (!result.done && result.value) {
            fullRawText += decoder.decode(result.value, { stream: true });
            parseAndExtract(fullRawText, requestId, false);
          } else if (result.done) {
            parseAndExtract(fullRawText, requestId, true);
          }
        } catch (err) {}
        return result;
      };

      return reader;
    };
  }

  //
  // 3. PARSER Y EXTRACTOR DE CONTENIDO SEGURO
  //
  function parseAndExtract(rawText, requestId, isComplete) {
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

    // Si no hay texto capturado aún y no es final, no inundar el panel
    if (!accumulatedText && !thoughtsText && !isBlocked) return;

    dispatchAppEvent(isComplete ? 'stream-complete' : 'stream-progress', {
      requestId,
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
      // Detección de texto JSPB: [null|number, "texto", ...]
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

      // Strings explícitos de finishReason (evitamos números arbitrarios)
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