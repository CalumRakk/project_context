const WEBSOCKET_URL = "ws://127.0.0.1:8765";
let socket = null;
let reconnectInterval = 3000;

function connect() {
  console.log(`[Bridge] Intentando conectar a ${WEBSOCKET_URL}...`);
  socket = new WebSocket(WEBSOCKET_URL);

  socket.onopen = () => {
    console.log("[Bridge] Conectado exitosamente al CLI de project-context.");
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
            // Informamos al CLI que la pestaña no está activa o no está enfocada
            sendResponseToCLI("reply_empty_status", targetId, { isEmpty: true, focused: false });
            return;
          }
          // Si está enfocada, consultamos al content script
          chrome.tabs.sendMessage(tab.id, { action: "check_input_status" }, (response) => {
            const isEmpty = response ? response.isEmpty : true;
            sendResponseToCLI("reply_empty_status", targetId, { isEmpty: isEmpty, focused: true });
          });
        });
      }
    } catch (error) {
      console.error("[Bridge] Error al procesar mensaje:", error);
    }
  };

  socket.onclose = () => {
    socket = null;
    setTimeout(connect, reconnectInterval);
  };

  socket.onerror = () => {
    socket.close();
  };
}

// Busca únicamente la pestaña activa que coincida con el chat_id en su URL
function findActiveMatchingTab(chatId, callback) {
  // Consultamos pestañas que estén activas (pueden ser de diferentes ventanas del navegador)
  chrome.tabs.query({ active: true }, (tabs) => {
    const matchingTab = tabs.find(tab => tab.url && tab.url.includes(chatId));
    callback(matchingTab); // Retorna la pestaña o undefined si ninguna coincide o está enfocada
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

connect();
