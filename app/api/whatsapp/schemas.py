"""
GreenAPI webhook payload Pydantic modelleri.

GreenAPI sunucuları her gelen WhatsApp mesajı için POST isteği gönderir.
Bu modeller gelen JSON'u parse eder ve yardımcı metodlar sağlar.
"""
from pydantic import BaseModel
from typing import Optional


class InstanceData(BaseModel):
    idInstance: int
    wid: str
    typeInstance: str


class SenderData(BaseModel):
    chatId: str           # "905551234567@c.us" veya "120363XXXXX@g.us" (grup)
    sender: str
    chatName: Optional[str] = ""
    senderName: Optional[str] = ""
    senderContactName: Optional[str] = None


class TextMessageData(BaseModel):
    textMessage: str


class ExtendedTextMessageData(BaseModel):
    text: str


class MessageData(BaseModel):
    typeMessage: str
    textMessageData: Optional[TextMessageData] = None
    extendedTextMessageData: Optional[ExtendedTextMessageData] = None


class GreenAPIWebhookPayload(BaseModel):
    typeWebhook: str
    instanceData: InstanceData
    timestamp: int
    idMessage: str
    senderData: Optional[SenderData] = None
    messageData: Optional[MessageData] = None

    def extract_phone(self) -> Optional[str]:
        """
        chatId'den telefon numarasını çıkarır.
        Grup mesajı (@g.us) veya senderData yoksa None döner.
        Örnek: "905551234567@c.us" → "905551234567"
        """
        if not self.senderData:
            return None
        chat_id = self.senderData.chatId
        if "@g.us" in chat_id:
            return None  # Grup mesajı — yoksay
        return chat_id.split("@")[0]

    def extract_text(self) -> Optional[str]:
        """
        textMessage ve extendedTextMessage tipindeki mesajlar için metin döner.
        Resim, ses, belge vb. için None döner.
        """
        if not self.messageData:
            return None
        msg_type = self.messageData.typeMessage
        if msg_type == "textMessage":
            if not self.messageData.textMessageData:
                return None
            return self.messageData.textMessageData.textMessage
        if msg_type == "extendedTextMessage":
            if not self.messageData.extendedTextMessageData:
                return None
            return self.messageData.extendedTextMessageData.text
        return None
