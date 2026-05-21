const WEBSOCKET_URL = "ws://127.0.0.1:8765";
let socket = null;
let reconnectInterval = 3000;
let isConnected = false;

// Estado inicial: Desconectado
chrome.runtime.onInstalled.addListener(() => {
  updateBadge(false);
});

function updateBadge(connected) {
  isConnected = connected;
  const text = connected ? "ON" : "OFF";
  const color = connected ? "#4CAF50" : "#F44336"; // Verde si conecta, Rojo si no
  const title = connected
    ? "Conectado al CLI de project-context"
    : "Desconectado del CLI de project-context";

  chrome.action.setBadgeText({ text: text });
  chrome.action.setBadgeBackgroundColor({ color: color });
  chrome.action.setTitle({ title: title });

  // Notificar a las pestañas abiertas de AI Studio
  chrome.tabs.query({ url: "https://aistudio.google.com/*" }, (tabs) => {
    tabs.forEach(tab => {
      chrome.tabs.sendMessage(tab.id, { action: "connection_status", connected: connected }).catch(() => {
        // Silenciar errores en pestañas que no estén completamente listas
      });
    });
  });
}

function connect() {
  console.log(`[Bridge] Intentando conectar a ${WEBSOCKET_URL}...`);
  socket = new WebSocket(WEBSOCKET_URL);

  socket.onopen = () => {
    console.log("[Bridge] Conectado exitosamente al CLI de project-context.");
    updateBadge(true);
  };

  socket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      const targetId = message.chat_id;

      if (message.action === "reload") {
        findActiveMatchingTab(targetId, (tab) => {
          if (tab) {
            console.log(`[Bridge] Recargando pestaña enfocada: ${tab.id}`);
            chrome.tabs.reload(tab.id);
          } else {
            sendResponseToCLI("reply_tab_not_focused", targetId, null);
          }
        });
      }
      else if (message.action === "query_empty_status") {
        findActiveMatchingTab(targetId, (tab) => {
          if (!tab) {
            sendResponseToCLI("reply_empty_status", targetId, { isEmpty: true, focused: false });
            return;
          }
          chrome.tabs.sendMessage(tab.id, { action: "check_input_status" }, (response) => {
            const isEmpty = response ? response.isEmpty : true;
            sendResponseToCLI("reply_empty_status", targetId, { isEmpty: isEmpty, focused: true });
          });
        });
      }

      else if (message.action === "run") {
        findActiveMatchingTab(targetId, (tab) => {
          if (!tab) {
            sendResponseToCLI("reply_run_status", targetId, { success: false, message: "La pestaña no está enfocada o activa." });
            return;
          }
          chrome.tabs.sendMessage(tab.id, { action: "click_run" }, (response) => {
            const success = response ? response.success : false;
            const msg = response ? response.message : "Sin respuesta de la página web.";
            sendResponseToCLI("reply_run_status", targetId, { success: success, message: msg });
          });
        });
      }
    } catch (error) {
      console.error("[Bridge] Error al procesar mensaje:", error);
    }
  };

  socket.onclose = () => {
    socket = null;
    updateBadge(false);
    setTimeout(connect, reconnectInterval);
  };

  socket.onerror = () => {
    if (socket) {
      socket.close();
    }
  };
}

function findActiveMatchingTab(chatId, callback) {
  chrome.tabs.query({ active: true }, (tabs) => {
    const matchingTab = tabs.find(tab => tab.url && tab.url.includes(chatId));
    callback(matchingTab);
  });
}

function sendResponseToCLI(action, chatId, payload) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({
      action: action,
      chat_id: chatId,
      ...payload
    }));
  }
}

// Escuchar peticiones puntuales del script de contenido (por ejemplo, al refrescar la web)
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "query_bridge_connection") {
    sendResponse({ connected: isConnected });
  }
  return true;
});

connect();
