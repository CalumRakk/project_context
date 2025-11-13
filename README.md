🚧🔨👷‍♂️



## Configuración Inicial

Antes de usar la herramienta, necesitas obtener tus credenciales de la API de Google Drive:

1.  Ve a la [Google Cloud Console](https://console.cloud.google.com/).
2.  Crea un nuevo proyecto o selecciona uno existente.
3.  Activa la API de Google Drive.
4.  Crea credenciales de tipo "OAuth client ID" para una "Desktop app".
5.  Descarga el archivo JSON de las credenciales.
6.  Renombra el archivo a `client_secrets.json` y colócalo en la siguiente ruta, dependiendo de tu sistema operativo:
    *   **Windows:** `C:\Users\<tu_usuario>\AppData\Roaming\project_context\client_secrets.json`
    *   **macOS:** `/Users/<tu_usuario>/Library/Application Support/project_context/client_secrets.json`
    *   **Linux:** `/home/<tu_usuario>/.config/project_context/client_secrets.json`

La primera vez que ejecutes `project_context`, se abrirá un navegador para que autorices el acceso a tu cuenta de Google Drive. Después de eso, se creará un archivo `token.json` en el mismo directorio de configuración para futuras sesiones.