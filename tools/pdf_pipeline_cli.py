#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CLI for the PDF book splitting pipeline.

Usage::

    python tools/pdf_pipeline_cli.py extract  --book <name> --toc <path>
    python tools/pdf_pipeline_cli.py analyze  --book <name> --section <id> --content <path>
    python tools/pdf_pipeline_cli.py generate --book <name> --contents-dir <dir>
    python tools/pdf_pipeline_cli.py relate   --book <name>
    python tools/pdf_pipeline_cli.py think    --book <name>
    python tools/pdf_pipeline_cli.py catalog  --book <name>
    python tools/pdf_pipeline_cli.py review   --book <name>
    python tools/pdf_pipeline_cli.py promote  --book <name>
    python tools/pdf_pipeline_cli.py status   --book <name>
    python tools/pdf_pipeline_cli.py run      --book <name> --toc <path> --contents-dir <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from agent.infra.llm import configure as llm_configure
from agent.knowledge.pdf_pipeline.draft_store import DraftStore
from agent.knowledge.pdf_pipeline.pipeline_runner import PipelineRunner


def _parse_page_range(s: str | None) -> tuple[int, int] | None:
    """Parse 'start-end' (1-indexed) into 0-indexed tuple."""
    if not s:
        return None
    parts = s.split("-")
    if len(parts) == 2:
        return int(parts[0]) - 1, int(parts[1]) - 1
    return int(parts[0]) - 1, int(parts[0]) - 1


def _load_vision_headers() -> dict[str, str] | None:
    """Load vision API default headers from VISION_DEFAULT_HEADERS_JSON env var."""
    raw = os.getenv("VISION_DEFAULT_HEADERS_JSON", "").strip()
    if raw:
        try:
            h = json.loads(raw)
            if isinstance(h, dict):
                return {str(k): str(v) for k, v in h.items()}
        except Exception:
            pass
    return None


def _get_runner(args: argparse.Namespace) -> PipelineRunner:
    pdf_path = Path(args.pdf) if getattr(args, "pdf", None) else None
    toc_pages = _parse_page_range(getattr(args, "toc_pages", None))
    return PipelineRunner(
        book_name=args.book,
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_HOST"),
        model=os.getenv("LLM_MODEL"),
        strong_model=os.getenv("LLM_STRONG_MODEL", os.getenv("LLM_MODEL")),
        max_concurrency=getattr(args, "concurrency", 16),
        pdf_path=pdf_path,
        vision_api_key=os.getenv("VISION_API_KEY"),
        vision_base_url=os.getenv("VISION_BASE_URL"),
        vision_model=os.getenv("VISION_MODEL"),
        vision_default_headers=_load_vision_headers(),
        toc_page_range=toc_pages,
    )


def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_section_contents(contents_dir: str) -> dict[str, str]:
    """Load section contents from a directory of text files.

    Expected layout: <contents_dir>/<section_id>.txt (or .md)
    """
    contents = {}
    d = Path(contents_dir)
    if not d.exists():
        print(f"Error: contents directory '{contents_dir}' not found")
        sys.exit(1)
    for f in sorted(d.iterdir()):
        if f.suffix in (".txt", ".md"):
            contents[f.stem] = f.read_text(encoding="utf-8")
    return contents


# ------------------------------------------------------------------
# Subcommands
# ------------------------------------------------------------------


def cmd_extract(args: argparse.Namespace) -> None:
    """Pass 1: Extract book outline from TOC."""
    toc = _load_text(args.toc)
    runner = _get_runner(args)
    outline = asyncio.run(runner.run_pass1(toc))
    print(f"Extracted {len(outline.chapters)} chapters for '{outline.book_name}'")
    for ch in outline.chapters:
        print(f"  [{ch.section_id}] {ch.title} (pp. {ch.page_start}-{ch.page_end})")


def cmd_analyze(args: argparse.Namespace) -> None:
    """Pass 2a+2b for a single section."""
    content = _load_text(args.content)
    runner = _get_runner(args)
    store = runner.store
    outline = store.load_outline()
    if not outline:
        print("Error: no outline found. Run 'extract' first.")
        sys.exit(1)

    # Find section
    from agent.knowledge.pdf_pipeline.pipeline_runner import _flatten_sections
    all_sections = _flatten_sections(outline.chapters)
    section = next((s for s in all_sections if s.section_id == args.section), None)
    if not section:
        print(f"Error: section '{args.section}' not found in outline")
        sys.exit(1)

    cards = asyncio.run(runner.run_pass2_section(section, content, args.chapter or section.title))
    print(f"Generated {len(cards)} cards for section '{section.title}'")
    for c in cards:
        print(f"  [{c.card_type}] {c.card_id}: {c.title}")


def cmd_generate(args: argparse.Namespace) -> None:
    """Pass 2: Process all sections."""
    contents = _load_section_contents(args.contents_dir)
    runner = _get_runner(args)
    cards = asyncio.run(runner.run_pass2_all(contents))
    print(f"Generated {len(cards)} cards total")


def cmd_relate(args: argparse.Namespace) -> None:
    """Pass 3: Cross-section relationships."""
    runner = _get_runner(args)
    cards = asyncio.run(runner.run_pass3())
    print(f"Updated relationships for {len(cards)} cards")


def cmd_questions(args: argparse.Namespace) -> None:
    """Pass 2c: Extract questions from all sections."""
    runner = _get_runner(args)
    questions = asyncio.run(runner.run_pass2c())
    archetypes = sum(1 for q in questions if q.question_type == "archetype")
    exercises = sum(1 for q in questions if q.question_type == "exercise")
    print(f"Extracted {len(questions)} questions ({archetypes} archetypes, {exercises} exercises)")


def cmd_foundation(args: argparse.Namespace) -> None:
    """Foundation: detect book-external prerequisite concepts."""
    runner = _get_runner(args)
    concepts = asyncio.run(runner.run_foundation())
    print(f"Detected {len(concepts)} foundation concepts")
    for c in concepts:
        print(f"  [{c.concept_id}] {c.name} ({len(c.covers)} covers)")


def cmd_think(args: argparse.Namespace) -> None:
    """Pass 3.5: DeepThink."""
    runner = _get_runner(args)
    thoughts = asyncio.run(runner.run_pass3_5())
    print(f"Discovered {len(thoughts)} Thought patterns:")
    for t in thoughts:
        print(f"  [{t.thought_id}] {t.name} — {len(t.linked_cards)} linked cards")


def cmd_catalog(args: argparse.Namespace) -> None:
    """Pass 4: Generate catalogs."""
    runner = _get_runner(args)
    counts = asyncio.run(runner.run_pass4())
    print(f"Generated {counts['catalogs']} catalog files, {counts['concepts']} concept files")


def cmd_review(args: argparse.Namespace) -> None:
    """Interactive review of draft cards."""
    store = DraftStore(args.book)
    drafts = store.list_draft_cards(status="draft")
    if not drafts:
        print("No draft cards to review.")
        return

    print(f"\n{len(drafts)} draft cards to review:\n")
    for i, card in enumerate(drafts):
        print(f"--- [{i+1}/{len(drafts)}] {card.card_id} ({card.card_type}) ---")
        print(f"  Title:   {card.title}")
        print(f"  Summary: {card.summary[:120]}...")
        print(f"  Methods: {len(card.general_methods)}, Hints: {len(card.hints)}, Mistakes: {len(card.common_mistakes)}")
        print(f"  Tags:    {card.method_tags}")
        print()

        while True:
            choice = input("  [a]pprove / [r]eject / [s]kip / [q]uit? ").strip().lower()
            if choice == "a":
                store.approve_card(card.card_id)
                print(f"  -> Approved '{card.card_id}'")
                break
            elif choice == "r":
                store.reject_card(card.card_id)
                print(f"  -> Rejected '{card.card_id}'")
                break
            elif choice == "s":
                print(f"  -> Skipped")
                break
            elif choice == "q":
                print("Review aborted.")
                return
            else:
                print("  Invalid choice. Use a/r/s/q.")

    approved = len(store.list_draft_cards(status="approved"))
    rejected = len(store.list_draft_cards(status="rejected"))
    remaining = len(store.list_draft_cards(status="draft"))
    print(f"\nReview summary: {approved} approved, {rejected} rejected, {remaining} remaining")


def cmd_promote(args: argparse.Namespace) -> None:
    """Promote approved items to production."""
    store = DraftStore(args.book)
    counts = store.promote_approved()
    print(f"Promoted to production:")
    for k, v in counts.items():
        print(f"  {k}: {v}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show pipeline status."""
    store = DraftStore(args.book)
    state = store.load_state()
    if not state:
        print(f"No pipeline state found for '{args.book}'")
        return

    print(f"Book:      {state.book_name}")
    print(f"Pass:      {state.current_pass}")
    print(f"Sections:  {len(state.completed_sections)} completed")
    print(f"Cards:     {state.total_draft_cards} total drafts")
    if state.errors:
        print(f"Errors:    {len(state.errors)}")
        for e in state.errors[-5:]:
            print(f"  - [{e.get('pass')}] {e.get('section_id')}: {e.get('error', '')[:80]}")

    # Card status breakdown
    all_cards = store.list_draft_cards()
    by_status = {}
    for c in all_cards:
        by_status[c.review_status] = by_status.get(c.review_status, 0) + 1
    if by_status:
        print(f"Review:    {by_status}")


def cmd_run(args: argparse.Namespace) -> None:
    """Run the full pipeline."""
    toc = _load_text(args.toc)
    contents = _load_section_contents(args.contents_dir)
    runner = _get_runner(args)
    state = asyncio.run(runner.run_full(
        toc, contents, skip_deepthink=args.skip_deepthink,
    ))
    print(f"\nPipeline complete: {state.total_draft_cards} cards generated")
    if state.errors:
        print(f"  {len(state.errors)} errors encountered")


# ------------------------------------------------------------------
# PDF subcommands (Pass 0)
# ------------------------------------------------------------------


def cmd_pdf_toc(args: argparse.Namespace) -> None:
    """Pass 0a: Extract TOC from PDF."""
    from agent.knowledge.pdf_pipeline.pdf_preprocessor import PDFPreprocessor

    store = DraftStore(args.book)
    toc_pages = _parse_page_range(getattr(args, "toc_pages", None))
    preprocessor = PDFPreprocessor(
        pdf_path=Path(args.pdf),
        book_name=args.book,
        store=store,
        vision_api_key=os.getenv("VISION_API_KEY"),
        vision_base_url=os.getenv("VISION_BASE_URL"),
        vision_model=os.getenv("VISION_MODEL"),
        vision_default_headers=_load_vision_headers(),
        toc_page_range=toc_pages,
    )
    try:
        toc_text = asyncio.run(preprocessor.extract_toc())
        print(f"TOC extracted ({len(toc_text)} chars):\n")
        print(toc_text[:2000])
        if len(toc_text) > 2000:
            print(f"\n... ({len(toc_text) - 2000} more chars)")
    finally:
        preprocessor.close()


def cmd_pdf_extract(args: argparse.Namespace) -> None:
    """Pass 0b+0c: Extract section contents and figures from PDF."""
    from agent.knowledge.pdf_pipeline.pdf_preprocessor import PDFPreprocessor

    store = DraftStore(args.book)
    outline = store.load_outline()
    if not outline:
        print("Error: no outline found. Run 'extract' or 'pdf-toc' first.")
        sys.exit(1)

    toc_pages = _parse_page_range(getattr(args, "toc_pages", None))
    preprocessor = PDFPreprocessor(
        pdf_path=Path(args.pdf),
        book_name=args.book,
        store=store,
        vision_api_key=os.getenv("VISION_API_KEY"),
        vision_base_url=os.getenv("VISION_BASE_URL"),
        vision_model=os.getenv("VISION_MODEL"),
        vision_default_headers=_load_vision_headers(),
        toc_page_range=toc_pages,
    )

    async def _run():
        preprocessor.calibrate_page_offset(outline)
        contents = await preprocessor.extract_all_sections(outline)
        # Extract and describe figures
        from agent.knowledge.pdf_pipeline.pipeline_runner import _flatten_sections
        for sec in _flatten_sections(outline.chapters):
            if sec.level >= 2 and sec.section_id in contents:
                sc = contents[sec.section_id]
                figs = preprocessor.extract_figures(sec)
                if figs:
                    described = await preprocessor.describe_figures(figs)
                    sc.figures = described
                    store.save_section_content(sc)
        return contents

    try:
        contents = asyncio.run(_run())
        print(f"\nExtracted {len(contents)} sections")
        for sid, sc in sorted(contents.items()):
            fig_count = len(sc.figures)
            print(f"  [{sid}] {sc.extraction_method}, {sc.page_count} pages, {fig_count} figures")
    finally:
        preprocessor.close()


def cmd_pdf_run(args: argparse.Namespace) -> None:
    """Run full pipeline from raw PDF (Pass 0 → 4)."""
    runner = _get_runner(args)
    state = asyncio.run(runner.run_from_pdf(
        skip_deepthink=args.skip_deepthink,
    ))
    print(f"\nPDF pipeline complete: {state.total_draft_cards} cards generated")
    if state.errors:
        print(f"  {len(state.errors)} errors encountered")


def cmd_pdf_status(args: argparse.Namespace) -> None:
    """Show PDF preprocessing status."""
    store = DraftStore(args.book)
    state = store.load_preprocess_state()
    if not state:
        print(f"No preprocessing state found for '{args.book}'")
        return

    print(f"Book:           {state.book_name}")
    print(f"PDF:            {state.pdf_path}")
    print(f"Page offset:    {state.page_offset}")
    print(f"TOC extracted:  {state.toc_extracted} ({state.toc_method})")
    print(f"Sections done:  {len(state.processed_sections)}")
    print(f"Total figures:  {state.total_figures}")
    print(f"Vision calls:   {state.total_vision_calls}")
    if state.errors:
        print(f"Errors:         {len(state.errors)}")
        for e in state.errors[-5:]:
            print(f"  - [{e.get('phase')}] {e.get('section_id')}: {e.get('error', '')[:80]}")


def cmd_pdf_dryrun(args: argparse.Namespace) -> None:
    """Estimate API calls without actually making requests."""
    from agent.knowledge.pdf_pipeline.pdf_preprocessor import PDFPreprocessor

    store = DraftStore(args.book)
    outline = store.load_outline()
    if not outline:
        print("Error: no outline found. Run 'extract' or 'pdf-toc' + 'extract' first.")
        sys.exit(1)

    preprocessor = PDFPreprocessor(
        pdf_path=Path(args.pdf),
        book_name=args.book,
        store=store,
    )
    try:
        preprocessor.calibrate_page_offset(outline)
        estimate = preprocessor.dry_run(outline)
        print(f"Dry run estimate for '{args.book}':")
        print(f"  Sections:         {estimate['total_sections']}")
        print(f"  Total pages:      {estimate['total_pages']}")
        print(f"  Native pages:     {estimate['native_pages']} ({estimate['native_ratio']})")
        print(f"  Vision pages:     {estimate['vision_pages']}")
        print(f"  Est. figures:     {estimate['estimated_figures']}")
        print(f"  Est. API calls:   {estimate['estimated_vision_calls']}")
    finally:
        preprocessor.close()


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PDF book splitting pipeline CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--concurrency", type=int, default=16, help="Max concurrent LLM calls (default: 16)")
    sub = parser.add_subparsers(dest="command", required=True)

    # extract
    p = sub.add_parser("extract", help="Pass 1: extract outline from TOC")
    p.add_argument("--book", required=True, help="Book name (used as draft directory name)")
    p.add_argument("--toc", required=True, help="Path to TOC text file")

    # analyze
    p = sub.add_parser("analyze", help="Pass 2: analyze a single section")
    p.add_argument("--book", required=True)
    p.add_argument("--section", required=True, help="Section ID from outline")
    p.add_argument("--content", required=True, help="Path to section content file")
    p.add_argument("--chapter", help="Chapter name (defaults to section title)")

    # generate
    p = sub.add_parser("generate", help="Pass 2: process all sections")
    p.add_argument("--book", required=True)
    p.add_argument("--contents-dir", required=True, help="Directory with <section_id>.txt files")

    # questions
    p = sub.add_parser("questions", help="Pass 2c: extract questions")
    p.add_argument("--book", required=True)

    # relate
    p = sub.add_parser("relate", help="Pass 3: cross-section relationships")
    p.add_argument("--book", required=True)

    # foundation
    p = sub.add_parser("foundation", help="Foundation: detect book-external concepts")
    p.add_argument("--book", required=True)

    # think
    p = sub.add_parser("think", help="Pass 3.5: DeepThink")
    p.add_argument("--book", required=True)

    # catalog
    p = sub.add_parser("catalog", help="Pass 4: generate catalogs")
    p.add_argument("--book", required=True)

    # review
    p = sub.add_parser("review", help="Interactive review of draft cards")
    p.add_argument("--book", required=True)

    # promote
    p = sub.add_parser("promote", help="Promote approved items to production")
    p.add_argument("--book", required=True)

    # status
    p = sub.add_parser("status", help="Show pipeline status")
    p.add_argument("--book", required=True)

    # run
    p = sub.add_parser("run", help="Run full pipeline")
    p.add_argument("--book", required=True)
    p.add_argument("--toc", required=True)
    p.add_argument("--contents-dir", required=True)
    p.add_argument("--skip-deepthink", action="store_true", help="Skip Pass 3.5")

    # --- PDF subcommands (Pass 0) ---

    # pdf-toc
    p = sub.add_parser("pdf-toc", help="Pass 0a: extract TOC from PDF")
    p.add_argument("--book", required=True)
    p.add_argument("--pdf", required=True, help="Path to PDF file")
    p.add_argument("--toc-pages", help="TOC page range, e.g. '1-5' (1-indexed)")

    # pdf-extract
    p = sub.add_parser("pdf-extract", help="Pass 0b+0c: extract section contents and figures from PDF")
    p.add_argument("--book", required=True)
    p.add_argument("--pdf", required=True, help="Path to PDF file")
    p.add_argument("--toc-pages", help="TOC page range for offset calibration, e.g. '4-7' (1-indexed)")

    # pdf-run
    p = sub.add_parser("pdf-run", help="Run full pipeline from raw PDF (Pass 0 → 4)")
    p.add_argument("--book", required=True)
    p.add_argument("--pdf", required=True, help="Path to PDF file")
    p.add_argument("--toc-pages", help="TOC page range, e.g. '1-5' (1-indexed)")
    p.add_argument("--skip-deepthink", action="store_true", help="Skip Pass 3.5")

    # pdf-status
    p = sub.add_parser("pdf-status", help="Show PDF preprocessing status")
    p.add_argument("--book", required=True)

    # pdf-dryrun
    p = sub.add_parser("pdf-dryrun", help="Estimate API calls without making requests")
    p.add_argument("--book", required=True)
    p.add_argument("--pdf", required=True, help="Path to PDF file")

    args = parser.parse_args()

    # Configure LLM from environment
    try:
        llm_configure()
    except Exception:
        pass  # LLM config may not be needed for review/status/promote

    # Configure vision LLM if PDF subcommands
    if args.command and args.command.startswith("pdf"):
        try:
            from agent.infra.vision_llm import configure_vision
            configure_vision()
        except Exception:
            pass

    cmd_map = {
        "extract": cmd_extract,
        "analyze": cmd_analyze,
        "generate": cmd_generate,
        "questions": cmd_questions,
        "relate": cmd_relate,
        "foundation": cmd_foundation,
        "think": cmd_think,
        "catalog": cmd_catalog,
        "review": cmd_review,
        "promote": cmd_promote,
        "status": cmd_status,
        "run": cmd_run,
        "pdf-toc": cmd_pdf_toc,
        "pdf-extract": cmd_pdf_extract,
        "pdf-run": cmd_pdf_run,
        "pdf-status": cmd_pdf_status,
        "pdf-dryrun": cmd_pdf_dryrun,
    }
    cmd_map[args.command](args)


if __name__ == "__main__":
    main()
