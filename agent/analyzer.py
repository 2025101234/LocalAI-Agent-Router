"""任务分析器：基于关键词与规则判断任务类型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import yaml
from loguru import logger


@dataclass
class TaskRule:
    """用户自定义规则。"""

    name: str
    keywords: list[str]
    model: str


class TaskAnalyzer:
    """分析用户输入，输出任务能力标签与匹配规则。"""

    DEFAULT_TAGS: ClassVar[dict[str, list[str]]] = {
        "coding": [
            "代码", "编程", "c++", "cpp", "python", "java", "javascript",
            "算法", "leetcode", "acm", "debug", "bug", "编译", "函数", "类",
            "递归", "动态规划", "数据结构",
        ],
        "math": [
            "数学", "微积分", "线性代数", "概率", "统计", "证明", "计算",
            "方程", "矩阵", "导数", "积分",
        ],
        "translation": [
            "翻译", "translate", "英文", "中文", "中英", "日文", "韩文",
        ],
        "writing": [
            "写作", "写文章", "润色", "文案", "邮件", "博客", "总结",
        ],
        "document": [
            "pdf", "论文", "文献", "abstract", "总结", "摘要", "报告", "文档",
        ],
    }

    def __init__(self, rules_path: Path) -> None:
        self.rules_path = rules_path
        self.rules: list[TaskRule] = []
        self._load_rules()

    def _load_rules(self) -> None:
        if not self.rules_path.exists():
            self.rules = []
            return
        try:
            with self.rules_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self.rules = [
                TaskRule(
                    name=r["name"],
                    keywords=[str(k).lower() for k in r.get("keywords", [])],
                    model=r["model"],
                )
                for r in data.get("rules", [])
            ]
            logger.debug(f"加载 {len(self.rules)} 条用户规则")
        except (OSError, yaml.YAMLError, KeyError, TypeError) as exc:
            logger.error(f"加载规则失败: {exc}")
            self.rules = []

    def reload(self) -> None:
        """热加载规则文件。"""
        self._load_rules()

    def analyze(self, text: str) -> dict[str, Any]:
        """分析输入文本，返回标签、匹配规则和置信信息。"""
        lowered = text.lower()
        tags: set[str] = set()

        for tag, keywords in self.DEFAULT_TAGS.items():
            for kw in keywords:
                if kw.lower() in lowered:
                    tags.add(tag)
                    break

        matched_rule: TaskRule | None = None
        for rule in self.rules:
            for kw in rule.keywords:
                if kw in lowered:
                    matched_rule = rule
                    break
            if matched_rule:
                break

        if matched_rule and matched_rule.model:
            tags.add(matched_rule.model)

        logger.debug(f"任务分析结果: tags={tags}, rule={matched_rule.name if matched_rule else None}")
        return {
            "tags": sorted(tags),
            "matched_rule": matched_rule,
            "primary_tag": min(tags) if tags else "general",
        }

    def get_rule_model(self, text: str) -> str | None:
        """仅返回命中的用户规则模型。"""
        result = self.analyze(text)
        rule = result.get("matched_rule")
        return rule.model if rule else None
