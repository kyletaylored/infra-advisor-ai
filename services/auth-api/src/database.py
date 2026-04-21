import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Text, create_engine, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DATABASE_URL: str = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ─── ORM Model ────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(Text, unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    is_service_account = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    reset_token_hash = Column(Text, nullable=True)
    reset_token_expires = Column(DateTime(timezone=True), nullable=True)


# ─── Schema bootstrap ─────────────────────────────────────────────────────────

def init_db() -> None:
    """Create the users table if it does not already exist, and migrate existing tables."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                is_service_account BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                reset_token_hash TEXT,
                reset_token_expires TIMESTAMPTZ
            )
        """))
        # Add reset columns to existing tables (idempotent)
        for col, typedef in [
            ("reset_token_hash", "TEXT"),
            ("reset_token_expires", "TIMESTAMPTZ"),
        ]:
            conn.execute(text(
                f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {typedef}"
            ))
        conn.commit()


# ─── CRUD helpers ─────────────────────────────────────────────────────────────

def _row_to_dict(row: UserRow) -> dict:
    return {
        "id": str(row.id),
        "email": row.email,
        "password_hash": row.password_hash,
        "is_admin": row.is_admin,
        "is_service_account": row.is_service_account,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def get_db() -> Session:
    return SessionLocal()


def create_user(
    email: str,
    password_hash: str,
    is_admin: bool = False,
    is_service_account: bool = False,
) -> dict:
    db: Session = get_db()
    try:
        user = UserRow(
            email=email,
            password_hash=password_hash,
            is_admin=is_admin,
            is_service_account=is_service_account,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return _row_to_dict(user)
    finally:
        db.close()


def get_user_by_email(email: str) -> dict | None:
    db: Session = get_db()
    try:
        row = db.query(UserRow).filter(UserRow.email == email).first()
        return _row_to_dict(row) if row else None
    finally:
        db.close()


def get_user_by_id(user_id: str) -> dict | None:
    db: Session = get_db()
    try:
        row = db.query(UserRow).filter(UserRow.id == user_id).first()
        return _row_to_dict(row) if row else None
    finally:
        db.close()


def list_users() -> list[dict]:
    db: Session = get_db()
    try:
        rows = db.query(UserRow).order_by(UserRow.created_at).all()
        return [_row_to_dict(r) for r in rows]
    finally:
        db.close()


def delete_user(user_id: str) -> bool:
    db: Session = get_db()
    try:
        row = db.query(UserRow).filter(UserRow.id == user_id).first()
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True
    finally:
        db.close()


def update_user(user_id: str, **fields) -> dict | None:
    """Update arbitrary columns on a user row. Returns updated user or None if not found."""
    db: Session = get_db()
    try:
        row = db.query(UserRow).filter(UserRow.id == user_id).first()
        if row is None:
            return None
        for key, value in fields.items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
        db.commit()
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        db.close()


def count_users() -> int:
    db: Session = get_db()
    try:
        return db.query(UserRow).count()
    finally:
        db.close()


def set_reset_token(user_id: str, token_hash: str, expires: datetime) -> bool:
    db: Session = get_db()
    try:
        row = db.query(UserRow).filter(UserRow.id == user_id).first()
        if row is None:
            return False
        row.reset_token_hash = token_hash
        row.reset_token_expires = expires
        db.commit()
        return True
    finally:
        db.close()


def get_user_by_reset_token(token_hash: str) -> dict | None:
    db: Session = get_db()
    try:
        row = db.query(UserRow).filter(UserRow.reset_token_hash == token_hash).first()
        return _row_to_dict(row) if row else None
    finally:
        db.close()


def clear_reset_token(user_id: str) -> None:
    db: Session = get_db()
    try:
        row = db.query(UserRow).filter(UserRow.id == user_id).first()
        if row:
            row.reset_token_hash = None
            row.reset_token_expires = None
            db.commit()
    finally:
        db.close()
