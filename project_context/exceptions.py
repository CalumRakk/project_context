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


class ProfileConfigNotFoundError(ProjectContextError):
    """Se lanza cuando el perfil de usuario especificado no existe."""

    pass


class AssociatedSecretMissingError(ProjectContextError):
    """Se lanza cuando falta el archivo físico de secretos necesario para re-autenticar la sesión."""

    pass


class ProfileConfigurationCorruptError(ProjectContextError):
    """Se lanza si el archivo de configuración del perfil se encuentra corrupto o ilegible."""

    pass


class FreshInstallRequiredError(ProjectContextError):
    """Se lanza cuando el entorno carece por completo de perfiles o credenciales de Drive instaladas."""

    pass
