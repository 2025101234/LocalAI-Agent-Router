"""SQLite 数据库连接与 ORM 定义。"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from storage.permissions import secure_directory, secure_file


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    """返回适合 SQLite 存储的 UTC naive 时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Session(Base):
    """对话会话表。"""

    __tablename__ = "sessions"

    id = Column(String(64), primary_key=True)
    title = Column(String(256), nullable=False, default="新会话")
    mode = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")
    usages = relationship("TokenUsage", back_populates="session", cascade="all, delete-orphan")


class Message(Base):
    """消息表。"""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey("sessions.id"), nullable=False, index=True)
    role = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    model = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    session = relationship("Session", back_populates="messages")


class TokenUsage(Base):
    """Token 消耗统计表。"""

    __tablename__ = "token_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey("sessions.id"), nullable=False, index=True)
    model = Column(String(128), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    cost = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=utc_now, nullable=False, index=True)

    session = relationship("Session", back_populates="usages")


class Database:
    """数据库管理器。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        secure_directory(db_path.parent)
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            future=True,
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        event.listen(self.engine, "connect", self._configure_sqlite)
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )

    def create_tables(self) -> None:
        Base.metadata.create_all(bind=self.engine)
        secure_file(self.db_path)
        secure_file(Path(f"{self.db_path}-wal"))
        secure_file(Path(f"{self.db_path}-shm"))
        logger.info(f"数据库表已创建/校验: {self.db_path}")

    def get_session(self) -> sessionmaker:
        return self.SessionLocal()

    @staticmethod
    def _configure_sqlite(dbapi_connection: object, connection_record: object) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def get_db(db: Database) -> Generator[sessionmaker, None, None]:
    session = db.get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
