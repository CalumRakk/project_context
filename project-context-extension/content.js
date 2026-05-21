chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "check_input_status") {
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
    return true;
  }

  else if (request.action === "connection_status") {
    updateUIStatus(request.connected);
  }
  return true;
});

// Función para renderizar el indicador visual discreto en la web
function updateUIStatus(connected) {
  let indicator = document.getElementById("project-context-status-indicator");

  if (!indicator) {
    indicator = document.createElement("div");
    indicator.id = "project-context-status-indicator";

    // Estilos elegantes que flotan en la esquina inferior derecha
    Object.assign(indicator.style, {
      position: "fixed",
      bottom: "16px",
      right: "16px",
      zIndex: "10000",
      backgroundColor: "#1e1e2e",
      color: "#cdd6f4",
      padding: "6px 12px",
      borderRadius: "20px",
      fontSize: "11px",
      fontWeight: "500",
      fontFamily: "Google Sans, Roboto, sans-serif",
      display: "flex",
      alignItems: "center",
      gap: "8px",
      boxShadow: "0 4px 12px rgba(0, 0, 0, 0.3)",
      border: "1px solid #313244",
      pointerEvents: "none",
      transition: "opacity 0.3s ease, transform 0.3s ease"
    });

    const dot = document.createElement("span");
    dot.id = "project-context-status-dot";
    Object.assign(dot.style, {
      width: "8px",
      height: "8px",
      borderRadius: "50%",
      display: "inline-block"
    });

    const textNode = document.createElement("span");
    textNode.id = "project-context-status-text";

    indicator.appendChild(dot);
    indicator.appendChild(textNode);
    document.body.appendChild(indicator);
  }

  const dot = indicator.querySelector("#project-context-status-dot");
  const textNode = indicator.querySelector("#project-context-status-text");

  if (connected) {
    dot.style.backgroundColor = "#4CAF50"; // Verde
    textNode.textContent = "CLI Conectado";
    indicator.style.opacity = "0.85";
  } else {
    dot.style.backgroundColor = "#F44336"; // Rojo
    textNode.textContent = "CLI Desconectado";
    indicator.style.opacity = "0.5";
  }
}

// Consultar el estado de la conexión de forma periódica para evitar la suspensión del Service Worker en MV3
function checkConnection() {
  chrome.runtime.sendMessage({ action: "query_bridge_connection" }, (response) => {
    if (chrome.runtime.lastError) {
      // Si el Service Worker se suspendió, el envío de este mensaje lo forzará a despertar
      // y a ejecutar de nuevo el bloque de inicialización de conexión WebSocket.
      updateUIStatus(false);
      return;
    }
    if (response && response.connected !== undefined) {
      updateUIStatus(response.connected);
    }
  });
}

// Consulta inicial rápida
checkConnection();

// Heartbeat de ciclo corto (cada 5 segundos) para asegurar persistencia y reactivación inmediata
setInterval(checkConnection, 5000);

function attemptClickRun(sendResponse) {
  let attempts = 0;
  const maxAttempts = 30;

  const interval = setInterval(() => {
    attempts++;
    const chatTurns = document.querySelectorAll('ms-chat-turn, ms-chat-chunk, .chat-turn, .chat-chunk');

    if (chatTurns.length > 0) {
      const lastTurn = chatTurns[chatTurns.length - 1];
      triggerHover(lastTurn);
      const runButton = findRunButtonInBlock(lastTurn);

      if (runButton) {
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
