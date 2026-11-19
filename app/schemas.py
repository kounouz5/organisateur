from typing import Literal, Optional
from pydantic import BaseModel, Field

ItemType = Literal["task", "reminder", "event", "email_draft"]


class Item(BaseModel):
    type: ItemType
    title: str
    when: Optional[str] = Field(None, description="ISO 8601 datetime, local time")
    to: Optional[str] = Field(None, description="Email recipient name (email_draft only)")
    subject: Optional[str] = None
    body: Optional[str] = None


class SavedItem(Item):
    id: int
    done: bool = False
    created_at: str


class ItemUpdate(BaseModel):
    """Partial update: only the fields sent are changed."""
    type: Optional[ItemType] = None
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    when: Optional[str] = None
    to: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    done: Optional[bool] = None


class ParseRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    now: Optional[str] = Field(None, description="ISO datetime used as 'now' (tests / timezone)")
    day: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$",
                               description="Day picked in the calendar: items without a date go on this day")


class ParseResponse(BaseModel):
    items: list[Item]
    engine: Literal["llm", "rules"]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    now: Optional[str] = None
    day: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    conversation_id: Optional[str] = Field(None, max_length=64)


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    changed: bool
    engine: Literal["llm", "local", "rules"]


class MailConnect(BaseModel):
    address: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    password: str = Field(..., min_length=1, max_length=200)
    host: Optional[str] = Field(None, max_length=200, description="IMAP server, guessed from the address if empty")


class AddResponse(BaseModel):
    items: list[SavedItem]
    engine: Literal["llm", "rules"]
