from pydantic import BaseModel, Field


class ChatMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ChatMessageResponse(BaseModel):
    status: str
    session_id: str
    msg_count: int
    bant_scheduled: bool
