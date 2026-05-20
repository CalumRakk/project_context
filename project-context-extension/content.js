chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "check_input_status") {
    // Intentamos capturar el editor de texto de la interfaz
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
  return true; // Mantiene el canal de comunicación abierto para respuestas asíncronas
});
