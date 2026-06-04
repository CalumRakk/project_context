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


class AssociatedSecretMissingError(ProjectContextError):
    """Se lanza de forma genérica si falta la asociación del secreto (retrocompatibilidad)."""

    pass


class SecretAssociationMissingError(ProjectContextError):
    """Se lanza cuando un perfil resuelto no tiene un secreto asociado en sus metadatos (Decisión 7)."""

    pass


class SecretFileMissingError(ProjectContextError):
    """Se lanza cuando el archivo de secretos físico (.json) no se encuentra en el disco (Decisión 8)."""

    pass


class ProfileConfigCorruptError(ProjectContextError):
    """Se lanza si el archivo de configuración del perfil se encuentra corrupto o ilegible."""

    pass


class ProfileConfigNotFoundError(ProjectContextError):
    """Se lanza cuando el perfil de usuario especificado o activo no existe."""

    pass


class ProfileNotFoundError(ProjectContextError):
    """Se lanza cuando se intenta acceder a un perfil inexistente."""

    pass


class ProfileActiveNotFoundError(ProjectContextError):
    """Se lanza cuando se intenta acceder a un perfil activo inexistente."""

    pass


class ProfileTokenNotFoundError(ProjectContextError):
    """Se lanza cuando se intenta acceder a un token de perfil inexistente."""

    pass


class FreshInstallRequiredError(ProjectContextError):
    """Se lanza cuando el entorno carece por completo de perfiles o credenciales de Drive (Decisión 4)."""

    pass


class AuthenticationFailedError(ProjectContextError):
    """Se lanza cuando el flujo de autenticación interactivo (OAuth) falla o es cancelado (Decisión 9)."""

    pass


class StateNotFoundError(ProjectContextError):
    """Se lanza cuando se intenta acceder a un estado inexistente."""

    pass


class StateJSONDecodeError(ProjectContextError):
    """Se lanza cuando el json de state.json no se puede decodificar."""

    pass
