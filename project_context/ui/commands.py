# Importa el registro e infraestructura para mantener la compatibilidad hacia atrás
from project_context.ui.registry import SessionContext, registry

# Importa los submódulos para forzar su registro en tiempo de importación
import project_context.ui.handlers
