#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ProblemBank - 题库抽象接口

题库的具体实现（JSON 文件、数据库、向量检索等）由外部提供。
RecommendManager 只依赖此接口，与存储格式完全解耦。

实现示例：
    class JsonProblemBank(ProblemBankBase):
        def __init__(self, path: str): ...
        async def query(self, q: ProblemQuery) -> list[ProblemContext]: ...
        async def get_by_id(self, pid: str) -> ProblemContext | None: ...
        async def get_prerequisites(self, concept_id: str) -> list[ProblemContext]: ...

注册到 RecommendManager：
    bank = JsonProblemBank("data/problems.json")
    manager = RecommendManager(registry, memory_manager, problem_bank=bank, ...)
"""

from abc import ABC, abstractmethod

from agent.tutor.data_structures import ProblemContext
from .data_structures import ProblemQuery


class ProblemBankBase(ABC):
    """
    题库查询接口。

    所有方法均为 async，以支持数据库 / 向量检索等 IO 操作。
    实现类无需是 BaseAgent，不依赖 LLM。
    """

    @abstractmethod
    async def query(self, query: ProblemQuery) -> list[ProblemContext]:
        """
        按条件查询题目。

        Args:
            query: 查询条件（tags、difficulty、chapter、exclude_ids、limit）

        Returns:
            匹配的题目列表（可以为空，由调用方处理降级逻辑）
        """
        ...

    @abstractmethod
    async def get_by_id(self, problem_id: str) -> ProblemContext | None:
        """按 ID 精确获取题目"""
        ...

    @abstractmethod
    async def get_prerequisites(self, concept_id: str) -> list[ProblemContext]:
        """
        获取针对某知识点前置概念的练习题。

        用于 EASIER_PROBLEM 推荐：当学生在某 concept 上持续出错时，
        推荐其 prerequisite_ids 对应的基础题。
        """
        ...


class NullProblemBank(ProblemBankBase):
    """
    空实现（占位用）。

    题库未接入时使用，所有查询返回空列表。
    RecommendManager 会自动降级到 REVIEW_CONCEPT 或 REST。
    """

    async def query(self, query: ProblemQuery) -> list[ProblemContext]:
        return []

    async def get_by_id(self, problem_id: str) -> ProblemContext | None:
        return None

    async def get_prerequisites(self, concept_id: str) -> list[ProblemContext]:
        return []
