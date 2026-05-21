chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "check_input_status") {
    // Verificación de seguridad para evitar recargas si hay un borrador activo en la caja de texto
    const editor = document.querySelector('div[contenteditable="true"], textarea, ms-prompt-input');
    let isEmpty = true;
    if (editor) {
      const text = editor.tagName.toLowerCase() === 'textarea'
        ? editor.value
        : editor.textContent;
      isEmpty = !text || text.trim() === "";
    }
    sendResponse({ isEmpty: isEmpty });
  }

  else if (request.action === "click_run") {
    attemptClickRun(sendResponse);
    return true; // Mantiene el canal de comunicación abierto para la respuesta asíncrona
  }
  return true;
});

function attemptClickRun(sendResponse) {
  let attempts = 0;
  const maxAttempts = 30; // Espera máxima de 15 segundos (intervalos de 500ms)

  const interval = setInterval(() => {
    attempts++;

    // Localizar los contenedores de turnos/mensajes del chat en la UI
    // AI Studio utiliza etiquetas personalizadas como ms-chat-turn o ms-chat-chunk
    const chatTurns = document.querySelectorAll('ms-chat-turn, ms-chat-chunk, .chat-turn, .chat-chunk');

    if (chatTurns.length > 0) {
      // Obtenemos el último bloque de la lista (el mensaje de usuario recién inyectado)
      const lastTurn = chatTurns[chatTurns.length - 1];

      // imular el evento hover (mouseenter/mouseover) para forzar al DOM a mostrar los controles rápidos
      triggerHover(lastTurn);

      // Buscar el botón de ejecución/re-run dentro de este bloque específico
      const runButton = findRunButtonInBlock(lastTurn);

      if (runButton) {
        // Verificar si el botón está desactivado temporalmente por el sistema
        const isDisabled = runButton.hasAttribute('disabled') ||
                           runButton.classList.contains('disabled') ||
                           runButton.getAttribute('aria-disabled') === 'true';

        if (!isDisabled) {
          runButton.click();
          clearInterval(interval);
          sendResponse({ success: true, message: "Ejecución remota iniciada sobre el último mensaje." });
          return;
        }
      }
    }

    if (attempts >= maxAttempts) {
      clearInterval(interval);
      sendResponse({
        success: false,
        message: "Tiempo de espera agotado. No se encontró o no se activó el botón de ejecución del último mensaje."
      });
    }
  }, 500);
}

function triggerHover(element) {
  const opts = { bubbles: true, cancelable: true, view: window };
  element.dispatchEvent(new MouseEvent('mouseenter', opts));
  element.dispatchEvent(new MouseEvent('mouseover', opts));
  element.dispatchEvent(new MouseEvent('mousemove', opts));
}


function findRunButtonInBlock(block) {
  const candidates = block.querySelectorAll('button, [role="button"]');
  for (const el of candidates) {
    const text = el.textContent ? el.textContent.trim().toLowerCase() : "";

    if (text === 'run' || text === 'ejecutar') {
      return el;
    }

    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) {
      const labelLower = ariaLabel.toLowerCase();
      if (labelLower.includes('run') || labelLower.includes('ejecutar') || labelLower.includes('play')) {
        return el;
      }
    }
  }
  return null;
}
