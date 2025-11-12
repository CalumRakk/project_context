from typing import Literal, Optional, Union

from pydantic import BaseModel


class FileDrive(BaseModel):
    id: str
    name: str
    mimeType: str
    modifiedTime: str
    is_folder: bool


class systemInstruction(BaseModel):
    text: Optional[str] = None


class Parts(BaseModel):
    text: str


class chunks_text(BaseModel):
    text: str
    role: Literal["user", "model"]
    tokenCount: int
    finishReason: Optional[str] = None
    parts: Optional[list[Parts]] = None


class driveDocument(BaseModel):
    id: str


class chunks_file(BaseModel):
    driveDocument: driveDocument
    role: Literal["user", "model"]
    tokenCount: int


class chunkedPrompt(BaseModel):
    chunks: list[Union[chunks_text, chunks_file]]
    pendingInputs: list[dict]


class ChatIAStudio(BaseModel):
    runSettings: dict
    systemInstruction: systemInstruction
    chunkedPrompt: chunkedPrompt
