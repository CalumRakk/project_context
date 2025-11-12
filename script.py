from project_context.browser import Browser
from project_context.browser.browser import Browser
from project_context.utils import generate_context, save_context

if __name__ == '__main__':
    cookies_path = r"aistudio.google.com_cookies.txt"
    browser = Browser(cookies_path=cookies_path)
    browser.chat.select_model("Gemini 2.5 Flash")

    prompt = """Eres un ingeniero de software senior y experto en análisis de código completo.

    A continuación te paso **todo el código fuente de mi proyecto** en formato texto plano optimizado para LLMs (generado con Gitingest). 

    Formato del resumen:
    - Las rutas de archivo aparecen entre ``` (tres acentos graves) seguidas del path completo.
    - Luego viene el contenido completo del archivo.
    - Los directorios vacíos o archivos ignorados (.gitignore, node_modules, binarios, etc.) están excluidos.
    - Todo el proyecto está aquí, no hay archivos externos ni dependencias que no se vean.

    INSTRUCCIONES OBLIGATORIAS:
    1. Analiza TODA la estructura del proyecto antes de responder.
    2. Recuerda el contenido de cada archivo importante (no lo olvides en respuestas siguientes).
    3. Si necesitas ver algún archivo de nuevo, puedes pedírmelo por su ruta exacta.
    4. Cuando hagas sugerencias de código, respeta la arquitectura actual y el estilo del proyecto.

    ¿Entendido? Confirma con "Listo, proyecto cargado" y dime brevemente de qué va el proyecto según lo que ves.
    """

    project_path = r"D:\github Leo\servercontrol"
    content = generate_context(project_path)
    path_context = save_context(project_path, content)

    browser.chat.attach_file(path_context)
    response, chat_id = browser.chat.write_prompt(prompt, thinking_mode=False)

    print(response)
