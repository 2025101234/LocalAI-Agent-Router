"""数据库与历史记录测试。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from storage.history import ConversationHistory


def test_create_and_get_session(history: ConversationHistory):
    sid = history.create_session("测试会话", mode="coder")
    session = history.get_session(sid)
    assert session is not None
    assert session.title == "测试会话"
    assert session.mode == "coder"


def test_add_and_get_messages(history: ConversationHistory):
    sid = history.create_session()
    history.add_message(sid, "user", "你好")
    history.add_message(sid, "assistant", "你好！", model="deepseek")
    msgs = history.get_messages(sid)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[1].model == "deepseek"


def test_search_sessions(history: ConversationHistory):
    sid = history.create_session("搜索测试")
    history.add_message(sid, "user", "关键词 AlphaBeta")
    results = history.search_sessions("AlphaBeta")
    assert len(results) == 1


def test_record_usage_and_report(history: ConversationHistory):
    sid = history.create_session()
    history.record_usage(sid, "deepseek", 100, 50, 0.001)
    report = history.daily_report()
    assert report["total_input"] == 100
    assert report["total_output"] == 50
    assert report["total_calls"] == 1
    assert report["date"] == datetime.now().astimezone().date().isoformat()


def test_first_user_message_updates_title(history: ConversationHistory) -> None:
    sid = history.create_session()
    history.add_message(sid, "user", "这是自动生成的会话标题")

    assert history.get_session(sid).title == "这是自动生成的会话标题"


def test_history_export_json_and_markdown(history: ConversationHistory, temp_dir) -> None:
    sid = history.create_session("导出测试")
    history.add_message(sid, "user", "hello")
    json_path = temp_dir / "exports" / "session.json"
    md_path = temp_dir / "exports" / "session.md"

    history.export_session_to_json(sid, json_path)
    history.export_session_to_markdown(sid, md_path)

    assert json.loads(json_path.read_text(encoding="utf-8"))["messages"][0]["content"] == "hello"
    assert "# 会话" in md_path.read_text(encoding="utf-8")


def test_daily_report_uses_local_calendar_boundaries(history: ConversationHistory) -> None:
    local_tz = datetime.now().astimezone().tzinfo
    target = date(2024, 1, 15)
    sid = history.create_session()
    usage = history.record_usage(sid, "local-model", 10, 5, 0.0)
    usage.created_at = datetime(2024, 1, 15, 0, 30, tzinfo=local_tz).astimezone(
        UTC
    ).replace(tzinfo=None)
    history.db.commit()

    report = history.daily_report(target)

    assert report["total_calls"] == 1
