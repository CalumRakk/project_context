const WEBSOCKET_URL = "ws://127.0.0.1:8765";
let socket = null;
let reconnectInterval = 3000; // Intento de reconexión cada 3 segundos

function connect() {
  console.log(`[Bridge] Intentando conectar a ${WEBSOCKET_URL}...`);
  socket = new WebSocket(WEBSOCKET_URL);

  socket.onopen = () => {
    console.log("[Bridge] Conectado exitosamente al CLI de project-context.");
  };

  socket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.action === "reload") {
        console.log("[Bridge] Señal de recarga recibida. Buscando pestañas de AI Studio...");
        reloadAIStudioTabs();
      }
    } catch (error) {
      console.error("[Bridge] Error al procesar mensaje:", error);
    }
  };

  socket.onclose = () => {
    console.log("[Bridge] Conexión cerrada. Reintentando en breve...");
    socket = null;
    setTimeout(connect, reconnectInterval);
  };

  socket.onerror = (error) => {
    // Evitamos saturar la consola si el servidor no está encendido
    console.warn("[Bridge] Error en WebSocket o CLI no está corriendo.");
    socket.close();
  };
}

function reloadAIStudioTabs() {
  // Buscamos todas las pestañas que tengan abierta la URL de AI Studio
  chrome.tabs.query({ url: "https://aistudio.google.com/*" }, (tabs) => {
    if (tabs.length === 0) {
      console.log("[Bridge] No se encontraron pestañas de Google AI Studio abiertas.");
      return;
    }

    tabs.forEach((tab) => {
      console.log(`[Bridge] Recargando pestaña ID: ${tab.id} - ${tab.title}`);
      chrome.tabs.reload(tab.id);
    });
  });
}

// Iniciamos la primera conexión al cargar la extensión
connect();
