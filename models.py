from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, Text, DateTime
import datetime


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    last_seen: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

    # Связь с сообщениями
    messages = relationship("Message", back_populates="sender")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    # Связь с отправителем
    sender = relationship("User", back_populates="messages")