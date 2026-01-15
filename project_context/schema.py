from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


class FileDrive(BaseModel):
    id: str
    name: str
    mimeType: str
    modifiedTime: str
    is_folder: bool


class Parts(BaseModel):
    text: str


class ChunksText(BaseModel):
    text: str
    role: Literal["user", "model"]
    tokenCount: Optional[int] = None
    finishReason: Optional[str] = None
    parts: Optional[list[Parts]] = None
    isThought: Optional[bool] = None
    thinkingBudget: Optional[int] = None


class DriveDocument(BaseModel):
    id: str


class ChunksDocument(BaseModel):
    driveDocument: DriveDocument
    role: Literal["user", "model"]
    tokenCount: int


class ChunksImage(BaseModel):
    driveImage: DriveDocument
    role: Literal["user", "model"]
    tokenCount: Optional[int] = None


class PendingInputs(BaseModel):
    text: str
    role: Literal["user", "model"] = "user"


class ChunkedPrompt(BaseModel):
    chunks: list[Union[ChunksText, ChunksDocument, ChunksImage]] = []
    pendingInputs: list[PendingInputs] = []


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
        examples=["BLOCK_NONE", "BLOCK_LOW", "BLOCK_MEDIUM", "BLOCK_HIGH", "OFF"],
    )


class RunSettings(BaseModel):
    temperature: float = 1.0
    model: str = Field(
        default="models/gemini-2.5-pro",
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
    enableCodeExecution: bool = False
    enableSearchAsATool: bool = True
    enableBrowseAsATool: bool = False
    enableAutoFunctionResponses: bool = False
    thinkingBudget: int = -1
    googleSearch: dict = {}
    outputResolution: str = "1K"


class SystemInstruction(BaseModel):
    text: Optional[str] = None


class ChatIAStudio(RunSettings):
    runSettings: RunSettings
    systemInstruction: SystemInstruction
    chunkedPrompt: ChunkedPrompt
