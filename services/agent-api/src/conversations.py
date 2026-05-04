import logging
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Text, create_engine, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker
from sqlalchemy.types import JSON

logger = logging.getLogger(__name__)

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    _engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
else:
    _engine = None
    _SessionLocal = None


class Base(DeclarativeBase):
    pass


class ConversationRow(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Text, nullable=False)
    title = Column(Text, nullable=False, default="New Conversation")
    model = Column(Text, nullable=True)
    backend = Column(Text, nullable=True, default="python")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    messages = relationship(
        "MessageRow",
        back_populates="conversation",
        order_by="MessageRow.created_at",
        cascade="all, delete-orphan",
        lazy="select",
    )


class MessageRow(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    sources = Column(JSON, nullable=False, default=list)
    trace_id = Column(Text, nullable=True)
    span_id = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    conversation = relationship("ConversationRow", back_populates="messages")


def init_db() -> None:
    """Create conversations/messages tables if they don't exist. No-op if DATABASE_URL unset."""
    if not _engine:
        logger.info("DATABASE_URL not set — conversation persistence disabled")
        return
    with _engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS conversations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'New Conversation',
                model TEXT,
                backend TEXT DEFAULT 'python',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources JSONB NOT NULL DEFAULT '[]',
                trace_id TEXT,
                span_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id)"
        ))
        conn.commit()
    logger.info("conversation DB schema ready")


def _get_db() -> Session | None:
    if not _SessionLocal:
        return None
    return _SessionLocal()


def _conv_to_summary(row: ConversationRow, *, include_count: bool = True) -> dict:
    return {
        "id": str(row.id),
        "user_id": row.user_id,
        "title": row.title,
        "model": row.model,
        "backend": row.backend,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "message_count": len(row.messages) if include_count else 0,
    }


def _msg_to_dict(row: MessageRow) -> dict:
    return {
        "id": str(row.id),
        "conversation_id": str(row.conversation_id),
        "role": row.role,
        "content": row.content,
        "sources": row.sources or [],
        "trace_id": row.trace_id,
        "span_id": row.span_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ─── CRUD ─────────────────────────────────────────────────────────────────────

def create_conversation(
    user_id: str,
    title: str = "New Conversation",
    model: str | None = None,
    backend: str = "python",
) -> dict | None:
    db = _get_db()
    if not db:
        return None
    try:
        row = ConversationRow(user_id=user_id, title=title, model=model, backend=backend)
        db.add(row)
        db.commit()
        db.refresh(row)
        return _conv_to_summary(row, include_count=False)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_conversations(user_id: str) -> list[dict]:
    db = _get_db()
    if not db:
        return []
    try:
        rows = (
            db.query(ConversationRow)
            .filter(ConversationRow.user_id == user_id)
            .order_by(ConversationRow.updated_at.desc())
            .all()
        )
        return [_conv_to_summary(r) for r in rows]
    finally:
        db.close()


def get_conversation(conv_id: str, user_id: str) -> dict | None:
    db = _get_db()
    if not db:
        return None
    try:
        row = (
            db.query(ConversationRow)
            .filter(ConversationRow.id == conv_id, ConversationRow.user_id == user_id)
            .first()
        )
        if not row:
            return None
        result = _conv_to_summary(row)
        result["messages"] = [_msg_to_dict(m) for m in row.messages]
        return result
    finally:
        db.close()


def delete_conversation(conv_id: str, user_id: str) -> bool:
    db = _get_db()
    if not db:
        return False
    try:
        row = (
            db.query(ConversationRow)
            .filter(ConversationRow.id == conv_id, ConversationRow.user_id == user_id)
            .first()
        )
        if not row:
            return False
        db.delete(row)
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def save_messages(
    conv_id: str,
    user_query: str,
    ai_answer: str,
    sources: list[str],
    trace_id: str | None,
    span_id: str | None,
) -> None:
    """Save a user+assistant exchange to the messages table (non-fatal)."""
    db = _get_db()
    if not db:
        return
    try:
        db.add(MessageRow(
            conversation_id=uuid.UUID(conv_id),
            role="user",
            content=user_query,
            sources=[],
        ))
        db.add(MessageRow(
            conversation_id=uuid.UUID(conv_id),
            role="assistant",
            content=ai_answer,
            sources=sources,
            trace_id=trace_id,
            span_id=span_id,
        ))
        db.execute(text(f"UPDATE conversations SET updated_at = NOW() WHERE id = '{conv_id}'"))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("save_messages failed for conv_id=%s: %s", conv_id, exc)
    finally:
        db.close()
