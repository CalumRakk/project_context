from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Literal, Optional, Union
from pydantic import BaseModel, Field, ConfigDict
from pydantic import BaseModel, Field

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


class ChunksText(BaseModel):
    text: str
    role: Role
    tokenCount: Optional[int] = None
    finishReason: Optional[str] = None  # STOP, PROHIBITED_CONTENT,
    isThought: Optional[bool] = None
    thinkingBudget: Optional[int] = -1
    parts: Optional[list[Parts]] = None


class DriveDocument(BaseModel):
    id: str


class ChunksDocument(BaseModel):
    driveDocument: DriveDocument
    role: Role
    tokenCount: int


class ChunksImage(BaseModel):
    driveImage: DriveDocument
    role: Role
    tokenCount: Optional[int] = None


class PendingInputs(BaseModel):
    text: str
    role: Role = "user"


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
        examples=["models/gemini-3.1-flash-lite", "gemini-3.1-pro-preview","gemini-flash-latest","models/gemini-2.5-flash"],
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

class SystemInstruction(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: Optional[str] = None

class ChatIAStudio(BaseModel):
    model_config = ConfigDict(extra="allow")

    runSettings: RunSettings
    systemInstruction: SystemInstruction
    chunkedPrompt: ChunkedPrompt
