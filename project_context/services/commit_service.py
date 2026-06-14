from typing import Optional

from project_context.services.git_service import GitService
from project_context.utils import COMMIT_TASK_MARKER


class CommitService:
    """Servicio para gestionar la lógica de generación de sugerencias de commit."""

    def __init__(self, git_service: GitService):
        self.git_service = git_service

    def generate_prompt(self) -> Optional[str]:
        """Genera el prompt completo con el diff de Git para enviarlo al modelo."""
        diff_content = self.git_service.get_diff_message()

        if not diff_content:
            return None

        return (
            f"{COMMIT_TASK_MARKER}\n\n"
            "Actúa como un desarrollador senior con amplia experiencia en la redacción de "
            "mensajes de commit siguiendo las mejores prácticas Conventional Commits. "
            "Tienes adjunto a este chat el contexto del proyecto para que entiendas la "
            "arquitectura general.\n\n"
            "He realizado los siguientes cambios (git diff --cached):\n\n"
            "```diff\n"
            f"{diff_content}\n"
            "```\n\n"
            "Con base en esos cambios, sugiéreme un único mensaje de commit conciso, "
            "en español, que resuma de forma clara y profesional los puntos más relevantes. "
            "No me des explicaciones, solo devuélveme el mensaje final listo para copiar y pegar.\n"
            "Formato deseado: <tipo>(<alcance>): <descripción>"
        )
