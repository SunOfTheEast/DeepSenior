#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Data structures for the PDF book splitting pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Pass 1 — Structure Extraction
# ---------------------------------------------------------------------------


@dataclass
class SectionOutline:
    """A node in the book's hierarchical outline tree."""
    section_id: str
    title: str
    level: int                          # 1=章, 2=节, 3=小节
    page_start: int
    page_end: int
    knowledge_types: list[str] = field(default_factory=list)  # "概念"/"定理"/"方法"/"技巧"
    section_type: str = "content"  # "content" | "exercise" | "answer"
    children: list[SectionOutline] = field(default_factory=list)


@dataclass
class BookOutline:
    """Complete structural outline of a textbook."""
    book_name: str
    chapters: list[SectionOutline]
    total_pages: int
    extraction_model: str = ""
    extraction_timestamp: str = ""


# ---------------------------------------------------------------------------
# Pass 2a — Section-level Pedagogical Analysis
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeAtom:
    """A single knowledge unit identified within a section."""
    name: str
    atom_type: str          # "概念" / "定理" / "方法" / "技巧" / "性质"
    importance: str         # "核心" / "重要" / "补充"
    description: str = ""


@dataclass
class AtomDependency:
    """Directed dependency between two knowledge atoms within a section."""
    from_atom: str
    to_atom: str
    relationship: str       # "前置" / "扩展" / "互补"


@dataclass
class HintIdea:
    """A hint design idea for a specific knowledge atom."""
    target_atom: str
    hint_level: int         # 1=方向感, 2=具体思路, 3=几乎给出答案
    text: str


@dataclass
class SectionAnalysis:
    """Pedagogical analysis output for a single section (Pass 2a)."""
    section_id: str
    knowledge_atoms: list[KnowledgeAtom] = field(default_factory=list)
    student_pain_points: list[str] = field(default_factory=list)
    hidden_traps: list[str] = field(default_factory=list)
    dependencies: list[AtomDependency] = field(default_factory=list)
    hint_ideas: list[HintIdea] = field(default_factory=list)
    raw_content_summary: str = ""
    # Phase 2 retrieval fields — front-load dependency signals for Pass 3
    teaches_concepts: list[str] = field(default_factory=list)
    requires_concepts: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    formulae_spoken: list[str] = field(default_factory=list)
    formulae_raw: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pass 2b — Draft Card
# ---------------------------------------------------------------------------


@dataclass
class DraftCard:
    """A knowledge card in draft state, before promotion to production."""
    card_id: str
    card_type: str              # "chapter" / "anchor" / "leaf"
    parent_card_id: str | None
    chapter: str
    title: str
    summary: str
    general_methods: list[str] = field(default_factory=list)
    hints: dict[int, str] = field(default_factory=dict)
    common_mistakes: list[str] = field(default_factory=list)
    prerequisite_card_ids: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    problem_tags: list[str] = field(default_factory=list)
    method_tags: list[str] = field(default_factory=list)
    thinking_tags: list[str] = field(default_factory=list)
    # Coarse cluster labels (filled by Pass 4 tag clustering)
    problem_type: list[str] = field(default_factory=list)
    method_type: list[str] = field(default_factory=list)
    thinking_type: list[str] = field(default_factory=list)
    formula_cues: list[str] = field(default_factory=list)
    thought_ids: list[str] = field(default_factory=list)
    source_section_id: str = ""
    generation_model: str = ""
    figures: list[dict] = field(default_factory=list)  # [{figure_id, description, figure_type}]
    review_status: str = "draft"    # "draft" / "approved" / "rejected"
    # Phase 2 retrieval fields
    teaches_concepts: list[str] = field(default_factory=list)
    requires_concepts: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    formulae_spoken: list[str] = field(default_factory=list)
    formulae_raw: list[str] = field(default_factory=list)
    source_page_start: int = 0
    source_page_end: int = 0
    assumed_knowledge: list[str] = field(default_factory=list)  # foundation concept IDs


@dataclass
class DraftQuestion:
    """从 PDF 中提取的题目（原型题或练习题）。"""
    question_id: str           # "q_{chapter_idx:02d}_{seq:03d}"
    chapter: str
    section_id: str
    question_type: str         # "archetype" | "exercise"
    source_label: str          # "例1", "习题3.2第5题"
    stem: str
    solution_text: str = ""    # 仅 archetype 有
    figures: list[dict] = field(default_factory=list)
    difficulty: int = 0        # 0=未评估, 1-5
    source_page: int = 0
    generation_model: str = ""
    review_status: str = "draft"
    solution_paths: list[dict] = field(default_factory=list)  # [{method, card_ids, key_steps, solution_text}]
    bound_card_ids: list[str] = field(default_factory=list)   # union of all paths


@dataclass
class FoundationConcept:
    """书外基础概念——本书假定学生已掌握但未教授的知识。"""
    concept_id: str              # "fnd_001"
    name: str                    # "多项式运算"
    covers: list[str] = field(default_factory=list)   # 原始概念字符串
    description: str = ""
    difficulty: int = 1          # 1=基础, 2=进阶, 3=综合
    source_book: str = ""        # 首次创建此概念的书名


# ---------------------------------------------------------------------------
# PDF Preprocessing (Pass 0)
# ---------------------------------------------------------------------------


@dataclass
class ExtractedFigure:
    """A figure extracted from the PDF."""
    figure_id: str               # "fig_{section_id}_{index:03d}"
    section_id: str
    page_num: int
    source: str                  # "embedded" | "vision_llm"
    description: str             # short description (≤60 chars)
    image_filename: str          # relative to figures/ directory
    figure_type: str = ""        # "diagram" | "graph" | "table" | "geometry" | "formula"
    width: int = 0
    height: int = 0
    image_hash: str = ""         # md5 for dedup


@dataclass
class PageQuality:
    """Quality assessment for a single page's native text extraction."""
    page_num: int
    char_count: int
    garbled_ratio: float         # ratio of garbled/unrecognizable chars
    has_math_fragments: bool     # broken LaTeX fragments detected
    has_figures: bool            # page contains embedded images
    needs_vision: bool           # overall verdict: should re-extract with vision


@dataclass
class SectionContent:
    """Rich content extracted from a section (text + figures)."""
    section_id: str
    text: str                    # full text (native or vision-merged)
    figures: list[ExtractedFigure] = field(default_factory=list)
    page_count: int = 0
    extraction_method: str = ""  # "native" | "vision" | "hybrid" | "manual"
    extraction_model: str = ""
    vision_page_count: int = 0   # pages actually sent to vision (cost tracking)


@dataclass
class PreprocessState:
    """Tracks PDF preprocessing progress (separate from PipelineState)."""
    book_name: str
    pdf_path: str = ""
    page_offset: int = 0         # PDF physical page vs book page number offset
    toc_extracted: bool = False
    toc_method: str = ""         # "bookmarks" | "native_text" | "vision"
    processed_sections: list[str] = field(default_factory=list)
    total_figures: int = 0
    total_vision_calls: int = 0  # cost tracking
    errors: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline State (resumability)
# ---------------------------------------------------------------------------


@dataclass
class PipelineState:
    """Tracks pipeline progress for crash recovery and resumability."""
    book_name: str
    current_pass: str = "init"      # "pass1"/"pass2a"/"pass2b"/"pass3"/"pass3.5"/"pass4"/"done"
    completed_sections: list[str] = field(default_factory=list)
    outline: BookOutline | None = None
    total_draft_cards: int = 0
    errors: list[dict] = field(default_factory=list)
