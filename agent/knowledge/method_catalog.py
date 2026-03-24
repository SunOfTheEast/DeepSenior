#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Method catalog loader for RAG v2."""

from __future__ import annotations

from pathlib import Path

import yaml

from agent.infra.logging import get_logger

from .data_structures import MethodCatalogTopic, MethodSlot, TopicResolveResult


class MethodCatalog:
    """Loads topic catalogs and question-to-topic mappings from YAML files."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else Path(__file__).resolve().parents[2] / "content" / "method_catalog"
        self.logger = get_logger("Knowledge.MethodCatalog")
        self._topic_cache: dict[tuple[str, str], MethodCatalogTopic] = {}
        self._slot_cache: dict[str, MethodSlot] = {}
        self._question_map_cache: dict[str, dict] | None = None
        self._cross_topic_cache: MethodCatalogTopic | None = None

    def resolve_topic(
        self,
        *,
        question_id: str | None,
        chapter: str,
        requested_topic: str | None = None,
    ) -> TopicResolveResult:
        if requested_topic:
            return TopicResolveResult(
                question_id=question_id,
                chapter=chapter,
                topic=requested_topic,
                source="request",
            )

        question_map = self._load_question_topics()
        if question_id and question_id in question_map:
            item = question_map[question_id]
            return TopicResolveResult(
                question_id=question_id,
                chapter=chapter,
                topic=item.get("primary_topic"),
                fallback_topics=list(item.get("fallback_topics", [])),
                source="question_map",
            )

        default_topic = self._guess_default_topic(chapter)
        if default_topic:
            return TopicResolveResult(
                question_id=question_id,
                chapter=chapter,
                topic=default_topic,
                source="default",
            )

        return TopicResolveResult(
            question_id=question_id,
            chapter=chapter,
            topic=None,
            source="missing",
        )

    def get_topic_catalog(self, *, chapter: str, topic: str) -> MethodCatalogTopic | None:
        key = (chapter, topic)
        if key in self._topic_cache:
            return self._topic_cache[key]

        path = self.root / chapter / f"{topic}.yaml"
        if not path.exists():
            return None

        catalog = self._load_catalog_file(path)
        self._topic_cache[key] = catalog
        for slot in catalog.methods:
            self._slot_cache[slot.slot_id] = slot
        return catalog

    def get_cross_topic_catalog(self) -> MethodCatalogTopic:
        if self._cross_topic_cache is not None:
            return self._cross_topic_cache

        path = self.root / "_cross_topic.yaml"
        if path.exists():
            catalog = self._load_catalog_file(path)
        else:
            catalog = MethodCatalogTopic(chapter="_cross_topic", topic="公共方法")
        self._cross_topic_cache = catalog
        for slot in catalog.methods:
            self._slot_cache[slot.slot_id] = slot
        return catalog

    def get_slot(self, slot_id: str) -> MethodSlot | None:
        if slot_id in self._slot_cache:
            return self._slot_cache[slot_id]
        self.get_cross_topic_catalog()
        return self._slot_cache.get(slot_id)

    def _load_question_topics(self) -> dict[str, dict]:
        if self._question_map_cache is not None:
            return self._question_map_cache

        path = self.root / "_question_topics.yaml"
        if not path.exists():
            self._question_map_cache = {}
            return self._question_map_cache

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        result: dict[str, dict] = {}
        for item in data.get("questions", []):
            qid = str(item.get("question_id", "")).strip()
            if qid:
                result[qid] = item
        self._question_map_cache = result
        return result

    def _guess_default_topic(self, chapter: str) -> str | None:
        chapter_dir = self.root / chapter
        if not chapter_dir.exists() or not chapter_dir.is_dir():
            return None
        topic_files = sorted(p for p in chapter_dir.glob("*.yaml") if p.is_file())
        if len(topic_files) == 1:
            return topic_files[0].stem
        return None

    def _load_catalog_file(self, path: Path) -> MethodCatalogTopic:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        methods = [
            MethodSlot(
                slot_id=str(item.get("slot_id", "")).strip(),
                name=str(item.get("name", "")).strip(),
                trigger=str(item.get("trigger", "")).strip(),
                card_ids=[str(cid) for cid in item.get("card_ids", []) if str(cid).strip()],
                cross_ref=[str(cid) for cid in item.get("cross_ref", []) if str(cid).strip()],
                status=str(item.get("status", "active") or "active"),
                notes=item.get("notes"),
            )
            for item in data.get("methods", [])
            if str(item.get("slot_id", "")).strip()
        ]
        return MethodCatalogTopic(
            chapter=str(data.get("chapter", "")).strip() or path.parent.name,
            topic=str(data.get("topic", "")).strip() or path.stem,
            version=str(data.get("version", "")).strip(),
            status=str(data.get("status", "active") or "active"),
            methods=methods,
        )

