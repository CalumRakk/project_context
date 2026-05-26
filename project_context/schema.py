from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from project_context.api_drive import AIStudioDriveManager
    from project_context.history import SnapshotManager


@dataclass
class SessionContext:
    api: "AIStudioDriveManager"
    state: dict
    project_path: Path
    monitor: "SnapshotManager"
    session_media_root: Optional[Path] = None

    def stop_monitor(self):
        self.monitor.stop_monitoring()

    def start_monitor(self):
        if self.state.get("monitor_active"):
            self.monitor.start_monitoring()


class FileDrive(BaseModel):
    id: str
    name: str
    mimeType: str
    modifiedTime: str
    is_folder: bool


class Parts(BaseModel):
    text: str


Role = Literal["user", "model"]


class BaseChunk(BaseModel):
    role: Role

    @property
    def file_id(self) -> Optional[str]:
        """Retorna el ID del archivo de Google Drive asociado si aplica."""
        return None

    @file_id.setter
    def file_id(self, val: str):
        """Permite modificar el ID de Drive de forma transparente."""
        pass

    @property
    def is_file_reference(self) -> bool:
        """Determina si este bloque representa un recurso binario en Drive."""
        return False

    @property
    def is_text(self) -> bool:
        """Determina si este bloque representa un bloque de texto."""
        return False


class DriveDocument(BaseModel):
    id: str


class PendingInputs(BaseModel):
    text: str
    role: Role = "user"


class ChunksText(BaseChunk):
    text: str
    tokenCount: Optional[int] = None
    finishReason: Optional[str] = None
    isThought: Optional[bool] = None
    thinkingBudget: Optional[int] = -1
    parts: Optional[list[Parts]] = None

    @property
    def is_text(self) -> bool:
        return True


class ChunksDocument(BaseChunk):
    driveDocument: DriveDocument
    tokenCount: int

    @property
    def is_text(self) -> bool:
        return False

    @property
    def file_id(self) -> Optional[str]:
        return self.driveDocument.id

    @file_id.setter
    def file_id(self, val: str):
        self.driveDocument.id = val

    @property
    def is_file_reference(self) -> bool:
        return True


class ChunksImage(BaseChunk):
    driveImage: DriveDocument
    tokenCount: Optional[int] = None

    @property
    def is_text(self) -> bool:
        return False

    @property
    def file_id(self) -> Optional[str]:
        return self.driveImage.id

    @file_id.setter
    def file_id(self, val: str):
        self.driveImage.id = val

    @property
    def is_file_reference(self) -> bool:
        return True


Chunk = Union[ChunksText, ChunksDocument, ChunksImage]


class ChunkedPrompt(BaseModel):
    chunks: List[Chunk] = Field(default_factory=list)
    pendingInputs: list[PendingInputs] = Field(default_factory=list)


class runSettings_safetySettings(BaseModel):
    category: str = Field(
        description="Category of the safety setting.",
        examples=[
            "HARM_CATEGORY_HARASSMENT",
            "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_DANGEROUS_CONTENT",
        ],
    )
    threshold: str = Field(
        description="Threshold level for the safety setting.",
        examples=[
            "BLOCK_NONE",
            "BLOCK_ONLY_HIGH",
            "BLOCK_MEDIUM_AND_ABOVE",
            "BLOCK_LOW_AND_ABOVE",
            "OFF",
        ],
    )


MediaResolution = Literal[
    "MEDIA_RESOLUTION_UNSPECIFIED",
    "MEDIA_RESOLUTION_LOW",
    "MEDIA_RESOLUTION_MEDIUM",
    "MEDIA_RESOLUTION_HIGH",
]
ThinkingLevel = Literal[
    "THINKING_MINIMAL", "THINKING_LOW", "THINKING_MEDIUM", "THINKING_HIGH"
]


class RunSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(
        default="models/gemini-3.5-flash",
        description="Model name used in the chat.",
        examples=[
            "models/gemini-3.1-flash-lite",
            "gemini-3.1-pro-preview",
            "gemini-flash-latest",
            "models/gemini-2.5-flash",
            "models/gemini-flash-lite-latest",
        ],
    )
    # TODO: los modelos "pequeños" de gemini-2* ahora son de pago.
    temperature: float = 1.0
    topP: float = 0.95
    topK: int = 64
    maxOutputTokens: int = 65536

    safetySettings: Optional[list[runSettings_safetySettings]] = None
    responseMimeType: Optional[str] = None
    enableCodeExecution: Optional[bool] = None
    responseSchema: Optional[dict] = None
    enableSearchAsATool: Optional[bool] = None
    enableBrowseAsATool: Optional[bool] = None
    enableAutoFunctionResponse: Optional[bool] = None
    thinkingBudget: Optional[int] = None
    mediaResolution: Optional[MediaResolution] = None
    outputResolution: Optional[str] = None
    thinkingLevel: Optional[ThinkingLevel] = None
    enableImageSearch: Optional[bool] = None
    enableGoogleMaps: Optional[bool] = None
    enableAgentThinkingSummariesControl: Optional[bool] = None
    enableAgentVisualizationControl: Optional[bool] = None
    enableAgentCollaborativePlanningControl: Optional[bool] = None

    environmentMode: Optional[str] = None
    googleSearch: Optional[dict] = None

    def sanitize(self):
        """
        Sanea la configuración actual basándose en el modelo seleccionado.
        Evita que parámetros avanzados queden como 'ruido' al cambiar a modelos más simples.
        """
        model_lower = self.model.lower()

        # Determinamos de manera general si el modelo soporta razonamiento avanzado (Thinking)
        # por lo común, las variantes 'pro' de las series 2.5 y modelos específicos de razonamiento
        supports_thinking = "pro" in model_lower or "thinking" in model_lower

        if not supports_thinking:
            # Al cambiar a un modelo estándar (como gemini-2.5-flash o gemini-3.5-flash),
            # limpiamos los campos de control de pensamiento para no generar un esquema corrupto.
            self.thinkingBudget = None
            self.thinkingLevel = None
            self.enableAgentThinkingSummariesControl = None
            self.enableAgentCollaborativePlanningControl = None

            # Ajuste de temperatura sugerida para modelos estándar rápidos
            if "flash" in model_lower:
                self.temperature = 1.0


class SystemInstruction(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: Optional[str] = None


class ChatIAStudio(BaseModel):
    model_config = ConfigDict(extra="allow")

    runSettings: RunSettings
    systemInstruction: SystemInstruction
    chunkedPrompt: ChunkedPrompt
