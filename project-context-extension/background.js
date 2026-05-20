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
        findMatchingTabs(targetId, (tabs) => {
          tabs.forEach(tab => {
            console.log(`[Bridge] Recargando pestaña: ${tab.id}`);
            chrome.tabs.reload(tab.id);
          });
        });
      }
      else if (message.action === "query_empty_status") {
        findMatchingTabs(targetId, (tabs) => {
          if (tabs.length === 0) {
            sendResponseToCLI("reply_empty_status", targetId, true);
            return;
          }

          // Si hay varias pestañas del mismo chat, priorizamos la pestaña activa
          const activeTab = tabs.find(tab => tab.active);
          if (activeTab) {
            queryTabEmptyStatus(activeTab.id, targetId);
          } else {
            // Si ninguna está en foco, evaluamos todas de manera acumulativa
            queryAllTabsAccumulative(tabs, targetId);
          }
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

// Filtra las pestañas que tengan el chat_id en su URL
function findMatchingTabs(chatId, callback) {
  chrome.tabs.query({ url: "https://aistudio.google.com/*" }, (tabs) => {
    const matching = tabs.filter(tab => tab.url && tab.url.includes(chatId));
    callback(matching);
  });
}

function queryTabEmptyStatus(tabId, chatId) {
  chrome.tabs.sendMessage(tabId, { action: "check_input_status" }, (response) => {
    const isEmpty = response ? response.isEmpty : true;
    sendResponseToCLI("reply_empty_status", chatId, isEmpty);
  });
}

function queryAllTabsAccumulative(tabs, chatId) {
  const promises = tabs.map(tab => {
    return new Promise((resolve) => {
      chrome.tabs.sendMessage(tab.id, { action: "check_input_status" }, (response) => {
        resolve(response ? response.isEmpty : true);
      });
    });
  });

  Promise.all(promises).then((results) => {
    const allEmpty = results.every(status => status === true);
    sendResponseToCLI("reply_empty_status", chatId, allEmpty);
  });
}

function sendResponseToCLI(action, chatId, isEmpty) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ action: action, chat_id: chatId, isEmpty: isEmpty }));
  }
}

connect();
