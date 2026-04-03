#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DraftStore — staging area for auto-generated knowledge artifacts.

Manages ``content/drafts/<book_name>/`` with save/load/approve/reject/promote
lifecycle.  All YAML I/O uses ``yaml.safe_load`` / ``yaml.dump``.

Directory layout::

    content/drafts/<book_name>/
      outline.yaml
      state.yaml
      analyses/<section_id>.yaml
      cards/<chapter>/<card_id>.yaml
      catalogs/<chapter>/<topic>.yaml
      concepts/<chapter>/<topic>.yaml
      thoughts/<thought_id>.yaml
"""

from __future__ import annotations

import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from agent.infra.logging import get_logger

from .data_structures import (
    BookOutline,
    DraftCard,
    DraftQuestion,
    ExtractedFigure,
    FoundationConcept,
    PipelineState,
    PreprocessState,
    SectionAnalysis,
    SectionContent,
)
from ..data_structures import ThoughtEntity


_logger = get_logger("Knowledge.DraftStore")


def _dump_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _load_yaml(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class DraftStore:
    """Manages the content/drafts/<book_name>/ staging area."""

    def __init__(self, book_name: str, drafts_root: Path | None = None):
        self.book_name = book_name
        root = drafts_root or Path(__file__).resolve().parents[3] / "content" / "drafts"
        self.base = root / book_name
        self.base.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Outline
    # ------------------------------------------------------------------

    def save_outline(self, outline: BookOutline) -> Path:
        path = self.base / "outline.yaml"
        _dump_yaml(path, asdict(outline))
        return path

    def load_outline(self) -> BookOutline | None:
        data = _load_yaml(self.base / "outline.yaml")
        if not data:
            return None
        from .data_structures import SectionOutline

        def _build_section(d: dict) -> SectionOutline:
            children = [_build_section(c) for c in d.get("children", [])]
            page_start = d["page_start"]
            page_end = d["page_end"]
            # Auto-fix inverted page ranges from LLM generation errors
            if page_end < page_start:
                page_end = page_start
            return SectionOutline(
                section_id=d["section_id"],
                title=d["title"],
                level=d["level"],
                page_start=page_start,
                page_end=page_end,
                knowledge_types=d.get("knowledge_types", []),
                section_type=d.get("section_type", "content"),
                children=children,
            )

        chapters = [_build_section(c) for c in data.get("chapters", [])]
        return BookOutline(
            book_name=data["book_name"],
            chapters=chapters,
            total_pages=data.get("total_pages", 0),
            extraction_model=data.get("extraction_model", ""),
            extraction_timestamp=data.get("extraction_timestamp", ""),
        )

    # ------------------------------------------------------------------
    # Analyses
    # ------------------------------------------------------------------

    def save_analysis(self, section_id: str, analysis: SectionAnalysis) -> Path:
        path = self.base / "analyses" / f"{section_id}.yaml"
        _dump_yaml(path, asdict(analysis))
        return path

    def load_analysis(self, section_id: str) -> SectionAnalysis | None:
        data = _load_yaml(self.base / "analyses" / f"{section_id}.yaml")
        if not data:
            return None
        from .data_structures import KnowledgeAtom, AtomDependency, HintIdea
        return SectionAnalysis(
            section_id=data["section_id"],
            knowledge_atoms=[KnowledgeAtom(**a) for a in data.get("knowledge_atoms", [])],
            student_pain_points=data.get("student_pain_points", []),
            hidden_traps=data.get("hidden_traps", []),
            dependencies=[AtomDependency(**d) for d in data.get("dependencies", [])],
            hint_ideas=[HintIdea(**h) for h in data.get("hint_ideas", [])],
            raw_content_summary=data.get("raw_content_summary", ""),
            teaches_concepts=data.get("teaches_concepts", []),
            requires_concepts=data.get("requires_concepts", []),
            aliases=data.get("aliases", []),
            formulae_spoken=data.get("formulae_spoken", []),
            formulae_raw=data.get("formulae_raw", []),
        )

    # ------------------------------------------------------------------
    # Draft cards
    # ------------------------------------------------------------------

    def save_cards(self, cards: list[DraftCard]) -> list[Path]:
        paths = []
        for card in cards:
            path = self.base / "cards" / card.chapter / f"{card.card_id}.yaml"
            _dump_yaml(path, asdict(card))
            paths.append(path)
        return paths

    def load_card(self, card_id: str) -> DraftCard | None:
        for path in (self.base / "cards").rglob(f"{card_id}.yaml"):
            data = _load_yaml(path)
            if data:
                return DraftCard(**{k: v for k, v in data.items() if k in DraftCard.__dataclass_fields__})
        return None

    def list_draft_cards(self, status: str | None = None) -> list[DraftCard]:
        cards_dir = self.base / "cards"
        if not cards_dir.exists():
            return []
        result = []
        for path in sorted(cards_dir.rglob("*.yaml")):
            data = _load_yaml(path)
            if not data or not data.get("card_id"):
                continue
            card = DraftCard(**{k: v for k, v in data.items() if k in DraftCard.__dataclass_fields__})
            if status is None or card.review_status == status:
                result.append(card)
        return result

    def approve_card(self, card_id: str) -> bool:
        return self._set_card_status(card_id, "approved")

    def reject_card(self, card_id: str) -> bool:
        return self._set_card_status(card_id, "rejected")

    def _set_card_status(self, card_id: str, new_status: str) -> bool:
        for path in (self.base / "cards").rglob(f"{card_id}.yaml"):
            data = _load_yaml(path)
            if data:
                data["review_status"] = new_status
                _dump_yaml(path, data)
                return True
        return False

    # ------------------------------------------------------------------
    # Catalogs
    # ------------------------------------------------------------------

    def save_catalogs(self, catalogs: dict[str, dict]) -> list[Path]:
        """Save method catalog dicts keyed by '<chapter>/<topic>'."""
        paths = []
        for key, catalog_data in catalogs.items():
            path = self.base / "catalogs" / f"{key}.yaml"
            _dump_yaml(path, catalog_data)
            paths.append(path)
        return paths

    # ------------------------------------------------------------------
    # Concepts
    # ------------------------------------------------------------------

    def save_concepts(self, concepts: dict[str, dict]) -> list[Path]:
        """Save concept dicts keyed by '<chapter>/<topic>'."""
        paths = []
        for key, concept_data in concepts.items():
            path = self.base / "concepts" / f"{key}.yaml"
            _dump_yaml(path, concept_data)
            paths.append(path)
        return paths

    # ------------------------------------------------------------------
    # Concept groupings (Pass 3 output, consumed by Pass 4)
    # ------------------------------------------------------------------

    def save_concept_groupings(self, groupings: list[dict]) -> Path:
        """Persist raw concept groupings from Pass 3."""
        path = self.base / "concept_groupings.yaml"
        _dump_yaml(path, groupings)
        return path

    def load_concept_groupings(self) -> list[dict]:
        """Load concept groupings saved by Pass 3."""
        path = self.base / "concept_groupings.yaml"
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------
    # Foundation concepts (Pass 3 — book-external prerequisites)
    # ------------------------------------------------------------------

    def save_foundation_concepts(self, concepts: list[FoundationConcept]) -> Path:
        """Persist foundation concepts detected in Pass 3."""
        path = self.base / "foundation_concepts.yaml"
        _dump_yaml(path, [asdict(c) for c in concepts])
        return path

    def load_foundation_concepts(self) -> list[dict]:
        """Load foundation concepts saved by Pass 3."""
        path = self.base / "foundation_concepts.yaml"
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------
    # Questions (Pass 2c output)
    # ------------------------------------------------------------------

    def save_questions(self, questions: list[DraftQuestion]) -> list[Path]:
        """Persist draft questions extracted by Pass 2c."""
        paths = []
        for q in questions:
            path = self.base / "questions" / q.chapter / f"{q.question_id}.yaml"
            _dump_yaml(path, asdict(q))
            paths.append(path)
        return paths

    def list_draft_questions(self) -> list[DraftQuestion]:
        """Load all draft questions."""
        questions_dir = self.base / "questions"
        if not questions_dir.exists():
            return []
        result = []
        for path in sorted(questions_dir.rglob("*.yaml")):
            data = _load_yaml(path)
            if data and data.get("question_id"):
                result.append(DraftQuestion(**{
                    k: v for k, v in data.items()
                    if k in DraftQuestion.__dataclass_fields__
                }))
        return result

    # ------------------------------------------------------------------
    # Tag clusters (Pass 4 output)
    # ------------------------------------------------------------------

    def save_tag_clusters(self, clusters: dict) -> Path:
        """Persist tag cluster mappings from Pass 4."""
        path = self.base / "tag_clusters.yaml"
        _dump_yaml(path, clusters)
        return path

    def load_tag_clusters(self) -> dict | None:
        """Load tag cluster mappings."""
        return _load_yaml(self.base / "tag_clusters.yaml")

    # ------------------------------------------------------------------
    # Thoughts
    # ------------------------------------------------------------------

    def save_thoughts(self, thoughts: list[ThoughtEntity]) -> list[Path]:
        paths = []
        for thought in thoughts:
            path = self.base / "thoughts" / f"{thought.thought_id}.yaml"
            _dump_yaml(path, asdict(thought))
            paths.append(path)
        return paths

    # ------------------------------------------------------------------
    # Pipeline state (resumability)
    # ------------------------------------------------------------------

    def save_state(self, state: PipelineState) -> Path:
        path = self.base / "state.yaml"
        state_dict = asdict(state)
        # BookOutline can be large; store reference instead of full inline
        if state.outline:
            state_dict["outline"] = {"book_name": state.outline.book_name, "_ref": "outline.yaml"}
        _dump_yaml(path, state_dict)
        return path

    def load_state(self) -> PipelineState | None:
        data = _load_yaml(self.base / "state.yaml")
        if not data:
            return None
        outline = self.load_outline() if data.get("outline") else None
        return PipelineState(
            book_name=data["book_name"],
            current_pass=data.get("current_pass", "init"),
            completed_sections=data.get("completed_sections", []),
            outline=outline,
            total_draft_cards=data.get("total_draft_cards", 0),
            errors=data.get("errors", []),
        )

    # ------------------------------------------------------------------
    # Figures (image archive)
    # ------------------------------------------------------------------

    def figures_dir(self) -> Path:
        d = self.base / "figures"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_figure(self, figure: ExtractedFigure, image_data: bytes) -> Path:
        """Save a figure image and its metadata YAML."""
        img_path = self.figures_dir() / figure.section_id / figure.image_filename
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(image_data)
        meta_path = self.figures_dir() / figure.section_id / f"{figure.figure_id}.yaml"
        _dump_yaml(meta_path, asdict(figure))
        return img_path

    def update_figure_meta(self, figure: ExtractedFigure) -> Path:
        """Update only the metadata YAML for a figure (does not touch image file)."""
        meta_path = self.figures_dir() / figure.section_id / f"{figure.figure_id}.yaml"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        _dump_yaml(meta_path, asdict(figure))
        return meta_path

    def load_figure_meta(self, figure_id: str) -> ExtractedFigure | None:
        for path in self.figures_dir().rglob(f"{figure_id}.yaml"):
            data = _load_yaml(path)
            if data and data.get("figure_id"):
                return ExtractedFigure(**{k: v for k, v in data.items() if k in ExtractedFigure.__dataclass_fields__})
        return None

    def list_figures(self, section_id: str | None = None) -> list[ExtractedFigure]:
        result = []
        search_dir = self.figures_dir() / section_id if section_id else self.figures_dir()
        if not search_dir.exists():
            return result
        for path in sorted(search_dir.rglob("*.yaml")):
            data = _load_yaml(path)
            if data and data.get("figure_id"):
                result.append(ExtractedFigure(**{k: v for k, v in data.items() if k in ExtractedFigure.__dataclass_fields__}))
        return result

    # ------------------------------------------------------------------
    # PDF Cache (multimodal extraction cache)
    # ------------------------------------------------------------------

    def _pdf_cache_dir(self) -> Path:
        d = self.base / "pdf_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_section_content(self, content: SectionContent) -> Path:
        path = self._pdf_cache_dir() / "sections" / f"{content.section_id}.yaml"
        data = asdict(content)
        # ExtractedFigure list may be large; store figure_ids only in cache
        data["figures"] = [asdict(f) for f in content.figures]
        _dump_yaml(path, data)
        return path

    def load_section_content(self, section_id: str) -> SectionContent | None:
        data = _load_yaml(self._pdf_cache_dir() / "sections" / f"{section_id}.yaml")
        if not data:
            return None
        figs = [
            ExtractedFigure(**{k: v for k, v in f.items() if k in ExtractedFigure.__dataclass_fields__})
            for f in data.get("figures", [])
            if isinstance(f, dict) and f.get("figure_id")
        ]
        return SectionContent(
            section_id=data["section_id"],
            text=data.get("text", ""),
            figures=figs,
            page_count=data.get("page_count", 0),
            extraction_method=data.get("extraction_method", ""),
            extraction_model=data.get("extraction_model", ""),
            vision_page_count=data.get("vision_page_count", 0),
        )

    def save_toc_cache(self, toc_text: str, method: str) -> Path:
        d = self._pdf_cache_dir()
        (d / "toc_raw.txt").write_text(toc_text, encoding="utf-8")
        _dump_yaml(d / "toc_meta.yaml", {"method": method})
        return d / "toc_raw.txt"

    def load_toc_cache(self) -> tuple[str, str] | None:
        """Returns (toc_text, method) or None."""
        txt_path = self._pdf_cache_dir() / "toc_raw.txt"
        meta_path = self._pdf_cache_dir() / "toc_meta.yaml"
        if not txt_path.exists():
            return None
        toc_text = txt_path.read_text(encoding="utf-8")
        meta = _load_yaml(meta_path) or {}
        return toc_text, meta.get("method", "unknown")

    # ------------------------------------------------------------------
    # Preprocess state
    # ------------------------------------------------------------------

    def save_preprocess_state(self, state: PreprocessState) -> Path:
        path = self.base / "preprocess_state.yaml"
        _dump_yaml(path, asdict(state))
        return path

    def load_preprocess_state(self) -> PreprocessState | None:
        data = _load_yaml(self.base / "preprocess_state.yaml")
        if not data:
            return None
        return PreprocessState(**{k: v for k, v in data.items() if k in PreprocessState.__dataclass_fields__})

    # ------------------------------------------------------------------
    # Promote: draft → production
    # ------------------------------------------------------------------

    def promote_approved(self) -> dict[str, int]:
        """Move approved cards/catalogs/concepts/thoughts to production directories.

        Returns counts of promoted items by type.
        """
        prod_root = Path(__file__).resolve().parents[3] / "content"
        counts = {"cards": 0, "catalogs": 0, "concepts": 0, "thoughts": 0, "figures": 0}

        # Cards
        for card in self.list_draft_cards(status="approved"):
            src = self.base / "cards" / card.chapter / f"{card.card_id}.yaml"
            dst = prod_root / "knowledge_cards" / card.chapter / f"{card.card_id}.yaml"
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                _logger.warning(f"Card {card.card_id} already exists in production, skipping")
                continue
            shutil.copy2(src, dst)
            counts["cards"] += 1

        # Catalogs (additive merge)
        catalogs_dir = self.base / "catalogs"
        if catalogs_dir.exists():
            for path in sorted(catalogs_dir.rglob("*.yaml")):
                rel = path.relative_to(catalogs_dir)
                dst = prod_root / "method_catalog" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    counts["catalogs"] += self._merge_catalog(path, dst)
                else:
                    shutil.copy2(path, dst)
                    counts["catalogs"] += 1

        # Concepts (additive merge)
        concepts_dir = self.base / "concepts"
        if concepts_dir.exists():
            for path in sorted(concepts_dir.rglob("*.yaml")):
                rel = path.relative_to(concepts_dir)
                dst = prod_root / "concepts" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    counts["concepts"] += self._merge_concepts(path, dst)
                else:
                    shutil.copy2(path, dst)
                    counts["concepts"] += 1

        # Figures: copy referenced figures for approved cards
        figures_src = self.base / "figures"
        if figures_src.exists():
            for card in self.list_draft_cards(status="approved"):
                for fig_ref in card.figures:
                    fig_id = fig_ref.get("figure_id", "") if isinstance(fig_ref, dict) else ""
                    if not fig_id:
                        continue
                    # Find the image file in figures/<section_id>/
                    for img_path in figures_src.rglob(f"{fig_id}.*"):
                        if img_path.suffix in (".png", ".jpg", ".jpeg"):
                            dst = prod_root / "figures" / card.chapter / img_path.name
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            if not dst.exists():
                                shutil.copy2(img_path, dst)
                                counts["figures"] += 1

        # Thoughts
        thoughts_dir = self.base / "thoughts"
        if thoughts_dir.exists():
            for path in sorted(thoughts_dir.rglob("*.yaml")):
                data = _load_yaml(path)
                if not data or data.get("status") != "published":
                    continue
                dst = prod_root / "thoughts" / path.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst)
                counts["thoughts"] += 1

        _logger.info(f"Promoted to production: {counts}")
        return counts

    @staticmethod
    def _merge_catalog(src: Path, dst: Path) -> int:
        """Merge new methods into existing catalog (additive, never removes)."""
        src_data = _load_yaml(src) or {}
        dst_data = _load_yaml(dst) or {}
        existing_ids = {m["slot_id"] for m in dst_data.get("methods", []) if "slot_id" in m}
        added = 0
        for method in src_data.get("methods", []):
            if method.get("slot_id") not in existing_ids:
                dst_data.setdefault("methods", []).append(method)
                added += 1
        if added:
            _dump_yaml(dst, dst_data)
        return added

    @staticmethod
    def _merge_concepts(src: Path, dst: Path) -> int:
        """Merge new concepts into existing concept file (additive)."""
        src_data = _load_yaml(src) or {}
        dst_data = _load_yaml(dst) or {}
        existing_ids = {c["concept_id"] for c in dst_data.get("concepts", []) if "concept_id" in c}
        added = 0
        for concept in src_data.get("concepts", []):
            if concept.get("concept_id") not in existing_ids:
                dst_data.setdefault("concepts", []).append(concept)
                added += 1
        if added:
            _dump_yaml(dst, dst_data)
        return added
