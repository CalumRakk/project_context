from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field


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
    temperature: float = 1.0
    model: str = Field(
        default="models/gemini-3-flash-preview",
        description="Model name used in the chat.",
        examples=["models/gemini-2.5-flash", "models/gemini-2.5-pro"],
    )
    topP: float = 0.95
    topK: int = 64
    maxOutputTokens: int = 65536
    safetySettings: list[runSettings_safetySettings] = [
        runSettings_safetySettings(
            category="HARM_CATEGORY_HARASSMENT", threshold="OFF"
        ),
        runSettings_safetySettings(
            category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"
        ),
        runSettings_safetySettings(
            category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"
        ),
        runSettings_safetySettings(
            category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"
        ),
    ]
    responseMimeType: Optional[str] = None  # application/json
    enableCodeExecution: bool = False
    responseSchema: dict = {}
    enableSearchAsATool: bool = True
    enableBrowseAsATool: bool = False
    enableAutoFunctionResponse: bool = False
    thinkingBudget: int = 0
    mediaResolution: MediaResolution = "MEDIA_RESOLUTION_UNSPECIFIED"
    outputResolution: str = "1K"
    thinkingLevel: Optional[ThinkingLevel] = None
    enableImageSearch: bool = False
    enableGoogleMaps: bool = False
    enableAgentThinkingSummariesControl: bool = False
    enableAgentVisualizationControl: bool = False
    enableAgentCollaborativePlanningControl: bool = False


class SystemInstruction(BaseModel):
    text: Optional[str] = None


class ChatIAStudio(RunSettings):
    runSettings: RunSettings
    systemInstruction: SystemInstruction
    chunkedPrompt: ChunkedPrompt
