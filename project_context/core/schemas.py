import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    pass


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
        if self.is_text or self.is_inline_file:
            return None

        for attr_name in self.__dict__.keys():
            if attr_name.startswith("drive"):
                return getattr(self, attr_name).id

    @file_id.setter
    def file_id(self, val: str):
        """Permite modificar el ID de Drive de forma transparente."""
        for attr_name in self.__dict__.keys():
            if attr_name.startswith("drive"):
                setattr(self, attr_name, DriveDocument(id=val))
                break

    @property
    def is_file_reference(self) -> bool:
        """Determina si este bloque representa un recurso binario en Drive."""
        return False

    @property
    def type(self) -> str:
        return self.__class__.__name__

    @property
    def is_image(self) -> bool:
        return isinstance(self, ChunkImage)

    @property
    def is_text(self) -> bool:
        return isinstance(self, ChunkText)

    @property
    def is_document(self) -> bool:
        return isinstance(self, ChunkDocument)

    @property
    def is_inline_file(self) -> bool:
        """Es un recurso binario insertado en el chat, no vive en Drive."""
        return isinstance(self, ChunkInlineFile)


class DriveDocument(BaseModel):
    id: str


class PendingInputs(BaseModel):
    text: str
    role: Role = "user"


class ChunkText(BaseChunk):
    text: str
    tokenCount: Optional[int] = None
    finishReason: Optional[str] = None
    isThought: Optional[bool] = None
    thinkingBudget: Optional[int] = -1
    parts: Optional[list[Parts]] = None


class ChunkDocument(BaseChunk):
    driveDocument: DriveDocument
    tokenCount: Optional[int] = None


class ChunkImage(BaseChunk):
    driveImage: DriveDocument
    tokenCount: Optional[int] = None


class InlineFile(BaseModel):
    mimeType: str
    data: str


class ChunkInlineFile(BaseChunk):
    tokenCount: Optional[int] = None
    inlineFile: InlineFile
    createTime: str


Chunk = Union[ChunkText, ChunkDocument, ChunkImage, ChunkInlineFile]


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
        supports_thinking = "pro" in model_lower or "thinking" in model_lower

        if not supports_thinking:
            self.thinkingBudget = None
            self.thinkingLevel = None
            self.enableAgentThinkingSummariesControl = None
            self.enableAgentCollaborativePlanningControl = None

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

    def reset_context_document_tokencount(self):
        for chunk in self.chunkedPrompt.chunks:
            if isinstance(chunk, ChunkDocument):
                chunk.tokenCount = None  # type: ignore - Fuerza el recuento de tokens
                break


class LocalContextItems(BaseModel):
    files: List[str] = Field(default_factory=list)
    folders: List[str] = Field(default_factory=list)
    exclusions: List[str] = Field(default_factory=list)


class ProfileConfig(BaseModel):
    email: str
    associated_secret: str
    created_at: float = Field(default_factory=time.time)

    token_path: Path
    secret_path: Path


class Context(BaseModel):
    text: str
    token_count: int
    md5sum: str = Field(default_factory=str, alias="md5")

    @model_validator(mode="after")
    def compute_md5sum(self):
        from project_context.utils import compute_md5

        if bool(self.md5sum) is False:
            self.md5sum = compute_md5(self.text)
        return self


class ContextRemote(BaseModel):
    context: Context
    file_id: str


class ProjectState(BaseModel):
    # Nota: Nunca almacenar variables de flujos de estados en este schema. Solo valores persistentes.
    model_config = ConfigDict(extra="allow")

    chat_id: Optional[str] = None
    file_id: Optional[str] = None
    state_path: Path = Field(exclude=True)

    # file_md5: str = Field(default_factory=str)
    # No almacenar el md5sum. La api de drive ya ofrece el md5sum del file_id.
    # Cuando se genera un contexto, se calcula el md5sum del texto.
    # asi que tenemos dos fuentes de verdad. Almacenarlo, implica mantener un valor propenso a no actualizarse.

    context_items: LocalContextItems = Field(default_factory=LocalContextItems)

    last_modified: float = Field(default_factory=time.time)

    def __setattr__(self, name, value):
        super().__setattr__(name, value)

        if name != "last_modified":
            super().__setattr__("last_modified", time.time())

    def save(self):
        """
        Actualiza los parámetros críticos del estado en memoria y los persiste
        en un único ciclo de escritura en el disco local.
        """
        data = self.model_dump_json(indent=2)
        self.state_path.write_text(data, encoding="utf-8")
