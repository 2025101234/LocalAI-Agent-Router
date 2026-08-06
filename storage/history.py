"""对话历史与 Token 统计操作封装。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from loguru import logger
from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DbSession

from storage.database import AgentThread, Message, Session, TokenUsage, utc_now
from storage.permissions import atomic_write_text


def _local_day_bounds(target: date) -> tuple[datetime, datetime]:
    """把本地自然日边界转换成 SQLite 使用的 UTC naive 时间。"""
    local_tz = datetime.now().astimezone().tzinfo
    local_start = datetime.combine(target, datetime.min.time(), tzinfo=local_tz)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(UTC).replace(tzinfo=None),
        local_end.astimezone(UTC).replace(tzinfo=None),
    )


class ConversationHistory:
    """管理会话、消息与 Token 统计。"""

    def __init__(self, db_session: DbSession) -> None:
        self.db = db_session

    def create_session(self, title: str = "新会话", mode: str | None = None) -> str:
        session_id = uuid.uuid4().hex
        session = Session(id=session_id, title=title, mode=mode)
        self.db.add(session)
        self.db.commit()
        logger.debug(f"创建会话: {session_id}")
        return session_id

    def get_session(self, session_id: str) -> Session | None:
        return self.db.query(Session).filter(Session.id == session_id).first()

    def list_sessions(self, limit: int = 50) -> list[Session]:
        return (
            self.db.query(Session)
            .order_by(Session.updated_at.desc())
            .limit(limit)
            .all()
        )

    def search_sessions(
        self,
        keyword: str = "",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        model: str | None = None,
    ) -> list[Session]:
        query = self.db.query(Session).join(Message)
        if keyword:
            query = query.filter(
                or_(Session.title.contains(keyword), Message.content.contains(keyword))
            )
        if start_time:
            query = query.filter(Session.created_at >= start_time)
        if end_time:
            query = query.filter(Session.created_at < end_time)
        if model:
            query = query.filter(Message.model == model)
        return query.distinct().order_by(Session.updated_at.desc()).all()

    def update_session_mode(self, session_id: str, mode: str) -> None:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"会话不存在: {session_id}")
        session.mode = mode
        session.updated_at = utc_now()
        self.db.commit()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        model: str | None = None,
    ) -> Message:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"会话不存在: {session_id}")
        msg = Message(session_id=session_id, role=role, content=content, model=model)
        self.db.add(msg)
        if role == "user" and session.title == "新会话":
            title = " ".join(content.strip().split())
            session.title = title[:50] + ("…" if len(title) > 50 else "") or "新会话"
        session.updated_at = utc_now()
        self.db.commit()
        logger.debug(f"保存消息 [{role}] 到会话 {session_id}")
        return msg

    def get_messages(self, session_id: str) -> list[Message]:
        return (
            self.db.query(Message)
            .filter(Message.session_id == session_id)
            .order_by(Message.created_at.asc())
            .all()
        )

    def get_agent_thread(self, session_id: str, runtime: str) -> AgentThread | None:
        return (
            self.db.query(AgentThread)
            .filter(
                AgentThread.session_id == session_id,
                AgentThread.runtime == runtime,
            )
            .first()
        )

    def save_agent_thread(
        self,
        session_id: str,
        runtime: str,
        remote_session_id: str,
        model: str,
        last_message_id: int,
    ) -> AgentThread:
        thread = self.get_agent_thread(session_id, runtime)
        if thread is None:
            thread = AgentThread(
                session_id=session_id,
                runtime=runtime,
                remote_session_id=remote_session_id,
            )
            self.db.add(thread)
        thread.remote_session_id = remote_session_id
        thread.model = model
        thread.last_message_id = last_message_id
        thread.updated_at = utc_now()
        self.db.commit()
        return thread

    def messages_after(self, session_id: str, message_id: int | None) -> list[Message]:
        query = self.db.query(Message).filter(Message.session_id == session_id)
        if message_id is not None:
            query = query.filter(Message.id > message_id)
        return query.order_by(Message.id.asc()).all()

    def record_usage(
        self,
        session_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> TokenUsage:
        usage = TokenUsage(
            session_id=session_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )
        self.db.add(usage)
        self.db.commit()
        logger.debug(f"记录用量: {model} in={input_tokens} out={output_tokens}")
        return usage

    def daily_report(self, target_date: date | None = None) -> dict:
        target = target_date or datetime.now().astimezone().date()
        start, end = _local_day_bounds(target)
        result = (
            self.db.query(
                TokenUsage.model,
                func.sum(TokenUsage.input_tokens).label("total_input"),
                func.sum(TokenUsage.output_tokens).label("total_output"),
                func.sum(TokenUsage.cost).label("total_cost"),
                func.count(TokenUsage.id).label("calls"),
            )
            .filter(TokenUsage.created_at >= start, TokenUsage.created_at < end)
            .group_by(TokenUsage.model)
            .all()
        )
        rows = [
            {
                "model": row.model,
                "input_tokens": row.total_input or 0,
                "output_tokens": row.total_output or 0,
                "cost": round(row.total_cost or 0, 6),
                "calls": row.calls,
            }
            for row in result
        ]
        summary = {
            "date": target.isoformat(),
            "models": rows,
            "total_input": sum(r["input_tokens"] for r in rows),
            "total_output": sum(r["output_tokens"] for r in rows),
            "total_cost": round(sum(r["cost"] for r in rows), 6),
            "total_calls": sum(r["calls"] for r in rows),
        }
        return summary

    def monthly_report(self, year: int | None = None, month: int | None = None) -> dict:
        now = datetime.now().astimezone()
        target_year = year or now.year
        target_month = month or now.month
        local_tz = now.tzinfo
        local_start = datetime(target_year, target_month, 1, tzinfo=local_tz)
        if target_month == 12:
            local_end = datetime(target_year + 1, 1, 1, tzinfo=local_tz)
        else:
            local_end = datetime(target_year, target_month + 1, 1, tzinfo=local_tz)
        start = local_start.astimezone(UTC).replace(tzinfo=None)
        end = local_end.astimezone(UTC).replace(tzinfo=None)
        result = (
            self.db.query(
                TokenUsage.model,
                func.sum(TokenUsage.input_tokens).label("total_input"),
                func.sum(TokenUsage.output_tokens).label("total_output"),
                func.sum(TokenUsage.cost).label("total_cost"),
                func.count(TokenUsage.id).label("calls"),
            )
            .filter(TokenUsage.created_at >= start, TokenUsage.created_at < end)
            .group_by(TokenUsage.model)
            .all()
        )
        rows = [
            {
                "model": row.model,
                "input_tokens": row.total_input or 0,
                "output_tokens": row.total_output or 0,
                "cost": round(row.total_cost or 0, 6),
                "calls": row.calls,
            }
            for row in result
        ]
        summary = {
            "year": target_year,
            "month": target_month,
            "models": rows,
            "total_input": sum(r["input_tokens"] for r in rows),
            "total_output": sum(r["output_tokens"] for r in rows),
            "total_cost": round(sum(r["cost"] for r in rows), 6),
            "total_calls": sum(r["calls"] for r in rows),
        }
        return summary

    def export_session_to_json(self, session_id: str, output_path: Path) -> None:
        if self.get_session(session_id) is None:
            raise ValueError(f"会话不存在: {session_id}")
        messages = self.get_messages(session_id)
        data = {
            "session_id": session_id,
            "exported_at": utc_now().isoformat(),
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "model": m.model,
                    "created_at": m.created_at.isoformat(),
                }
                for m in messages
            ],
        }
        atomic_write_text(
            output_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
        logger.info(f"会话 {session_id} 已导出到 {output_path}")

    def export_session_to_markdown(self, session_id: str, output_path: Path) -> None:
        if self.get_session(session_id) is None:
            raise ValueError(f"会话不存在: {session_id}")
        messages = self.get_messages(session_id)
        lines = [f"# 会话 {session_id}\n\n"]
        for m in messages:
            lines.append(f"## {m.role} ({m.created_at.isoformat()})")
            if m.model:
                lines.append(f"_model: {m.model}_")
            lines.append("")
            lines.append(m.content)
            lines.append("")
        atomic_write_text(output_path, "\n".join(lines) + "\n")
        logger.info(f"会话 {session_id} 已导出到 {output_path}")
