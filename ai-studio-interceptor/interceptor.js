(function () {
  console.log('%c[AI Studio Interceptor] 🚀 Inicializado (Soporte Dual: XHR + Fetch)', 'color: #8ab4f8; font-weight: bold; font-size: 13px;');

  const PROTO_FINISH_REASONS = {
    1: 'STOP',
    2: 'MAX_TOKENS',
    3: 'SAFETY',
    4: 'RECITATION',
    5: 'OTHER',
    6: 'BLOCKLIST',
    7: 'PROHIBITED_CONTENT',
    8: 'SPII'
  };

  function isTargetUrl(url) {
    if (!url || typeof url !== 'string') return false;
    return (
      url.includes('MakerSuiteService/GenerateContent') ||
      url.includes('GenerateContent') ||
      url.includes('alkalimakersuite')
    );
  }

  // ==========================================
  // 1. HOOK PARA XMLHTTPREQUEST (XHR) - PRINCIPAL
  // ==========================================
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this._url = typeof url === 'string' ? url : (url?.toString() || '');
    this._method = method;
    return originalOpen.apply(this, [method, url, ...rest]);
  };

  XMLHttpRequest.prototype.send = function (...args) {
    if (this._url && isTargetUrl(this._url)) {
      console.log('%c[XHR DETECTADO] 🎯 Petición GenerateContent iniciada:', 'color: #81c995; font-weight: bold;', this._url);
      const requestId = 'xhr_' + Date.now();

      dispatchAppEvent('stream-start', { requestId, timestamp: Date.now() });

      const handleProgress = () => {
        try {
          if (this.responseText) {
            parseAndExtract(this.responseText, requestId, false);
          }
        } catch (e) {
          console.error('[XHR Interceptor] Error procesando texto en streaming:', e);
        }
      };

      this.addEventListener('progress', handleProgress);

      this.addEventListener('readystatechange', () => {
        if (this.readyState === 3) {
          handleProgress();
        } else if (this.readyState === 4) {
          console.log('%c[XHR FINALIZADO] ✅ Generación completa recibida', 'color: #81c995; font-weight: bold;');
          try {
            parseAndExtract(this.responseText, requestId, true);
          } catch (e) {
            console.error('[XHR Interceptor] Error en estado final:', e);
          }
        }
      });
    }

    return originalSend.apply(this, args);
  };

  // ==========================================
  // 2. HOOK PARA FETCH (RESPALDO)
  // ==========================================
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
      console.log('%c[FETCH DETECTADO] 🎯 Petición GenerateContent iniciada:', 'color: #81c995; font-weight: bold;', url);
      const requestId = 'fetch_' + Date.now();
      attachPassiveStreamSpy(response, requestId);
    }

    return response;
  };

  function attachPassiveStreamSpy(response, requestId) {
    if (!response?.body?.getReader) return;

    dispatchAppEvent('stream-start', { requestId, timestamp: Date.now() });

    const originalGetReader = response.body.getReader.bind(response.body);
    const decoder = new TextDecoder('utf-8');
    let fullRawText = '';

    response.body.getReader = function (...readerArgs) {
      const reader = originalGetReader(...readerArgs);
      const originalRead = reader.read.bind(reader);

      reader.read = async function () {
        const result = await originalRead();
        try {
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

  // ==========================================
  // 3. PARSER Y EXTRACTOR DE CONTENIDO
  // ==========================================
  function parseAndExtract(rawText, requestId, isComplete) {
    if (!rawText) return;

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
        }, (reasonCode, isSafety) => {
          finishReason = reasonCode;
          if (isSafety) isBlocked = true;
        });
      } catch (e) {
        // Bloque JSON parcial
      }
    }

    if (isBlocked && finishReason === 'STOP') {
      finishReason = 'SAFETY';
    }

    if (accumulatedText.length > 0) {
      console.log(`%c[CAPTURA] 📝 ${accumulatedText.length} caracteres extraídos | Estado: ${finishReason}`, 'color: #ff9800;');
    }

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
      if (node.finishReason) {
        const isSafe = ['SAFETY', 'PROHIBITED_CONTENT', 'BLOCKLIST', 'SPII'].includes(node.finishReason);
        onFinishReason(node.finishReason, isSafe);
      }
      for (const key in node) {
        extractFromJspb(node[key], onTextFound, onFinishReason);
      }
      return;
    }

    if (Array.isArray(node)) {
      if (node.length >= 2 && typeof node[1] === 'string' && (node[0] === null || typeof node[0] === 'number')) {
        const textCandidate = node[1];
        if (textCandidate.length > 0 && !textCandidate.startsWith('!FBel') && !textCandidate.startsWith('CAES')) {
          const isThought = node[2] === true || node[3] === true;
          onTextFound(textCandidate, isThought);
        }
      }

      for (const item of node) {
        if (typeof item === 'number' && PROTO_FINISH_REASONS[item]) {
          onFinishReason(PROTO_FINISH_REASONS[item], [3, 6, 7, 8].includes(item));
        } else if (typeof item === 'string' && ['SAFETY', 'PROHIBITED_CONTENT', 'STOP', 'MAX_TOKENS'].includes(item)) {
          onFinishReason(item, item !== 'STOP' && item !== 'MAX_TOKENS');
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