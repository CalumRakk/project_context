class ProjectContextError(Exception):
    """Clase base para todos los errores de dominio de project_context."""
    pass


class ChatSessionError(ProjectContextError):
    """Se lanza cuando el chat en Google Drive no es accesible o está corrupto."""
    pass


class MissingStateError(ProjectContextError):
    """Se lanza cuando faltan parámetros de estado críticos (como chat_id o file_id)."""
    pass


class VanishModeActiveError(ProjectContextError):
    """Se lanza cuando se intenta ejecutar un comando no permitido en modo vanish."""
    pass


class BrowserBridgeError(ProjectContextError):
    """Se lanza ante fallos de comunicación con el puente del navegador."""
    pass


class InvalidCommandArgumentError(ProjectContextError):
    """Se lanza cuando los argumentos de un comando CLI fallan la validación."""
    pass
