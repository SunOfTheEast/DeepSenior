#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG Audit CLI — 审计任务查看、管理与覆盖率分析

用法：
  python audit_cli.py list                       # 列出所有 pending/proposed 条目
  python audit_cli.py list --status pending       # 按状态筛选
  python audit_cli.py list --chapter 解析几何     # 按章节筛选
  python audit_cli.py list --type empty_slot      # 按任务类型筛选
  python audit_cli.py stats                       # 按 task_type 统计
  python audit_cli.py stats --by status           # 按 status 统计
  python audit_cli.py stats --by chapter          # 按 chapter 统计
  python audit_cli.py gaps                        # 覆盖率缺口分析
  python audit_cli.py show <id>                   # 查看单条记录详情
  python audit_cli.py approve <id> [--notes ...]  # 审批通过
  python audit_cli.py reject <id> [--notes ...]   # 审批拒绝
  python audit_cli.py report                      # 生成完整审计报告
"""

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from agent.knowledge.audit_store import AuditStore
from agent.knowledge.data_structures import AuditStatus


def _store(args) -> AuditStore:
    path = getattr(args, "file", None)
    return AuditStore(path=path) if path else AuditStore()


# ── Commands ─────────────────────────────────────────────────────────────

def cmd_list(args):
    store = _store(args)
    kwargs = {}
    if args.status:
        kwargs["status"] = args.status
    if args.chapter:
        kwargs["chapter"] = args.chapter
    if args.type:
        kwargs["task_type"] = args.type
    kwargs["limit"] = args.limit

    entries = store.query(**kwargs)
    if not entries:
        print("(无匹配条目)")
        return

    # 表头
    print(f"{'ID':<14} {'Status':<10} {'Type':<25} {'Chapter':<10} {'Slot':<28} {'Created':<22}")
    print("─" * 110)
    for e in entries:
        eid = e.get("id", "?")[:12]
        status = e.get("status", "?")
        ttype = e.get("task_type", "?")[:24]
        chapter = (e.get("chapter") or "?")[:9]
        slot = (e.get("router_primary_slot") or "-")[:27]
        created = (e.get("created_at") or "?")[:21]
        print(f"{eid:<14} {status:<10} {ttype:<25} {chapter:<10} {slot:<28} {created:<22}")

    print(f"\n共 {len(entries)} 条")


def cmd_stats(args):
    store = _store(args)
    group_by = args.by or "task_type"
    counts = store.stats(group_by=group_by)
    if not counts:
        print("(无数据)")
        return

    print(f"\n{'=' * 50}")
    print(f"  Audit 统计 (group_by={group_by})")
    print(f"{'=' * 50}")

    total = sum(counts.values())
    for key, count in sorted(counts.items(), key=lambda x: -x[1]):
        pct = count / total * 100 if total else 0
        bar = "█" * int(pct / 2)
        print(f"  {key:<30} {count:>5}  ({pct:5.1f}%) {bar}")
    print(f"  {'─' * 46}")
    print(f"  {'Total':<30} {total:>5}")
    print()


def cmd_gaps(args):
    store = _store(args)
    gaps = store.coverage_gaps()
    if not gaps:
        print("(无覆盖缺口 — 所有检索均正常)")
        return

    print(f"\n{'=' * 70}")
    print("  覆盖率缺口分析（按章节+topic 聚合，按出现次数降序）")
    print(f"{'=' * 70}")
    print(f"  {'Chapter':<12} {'Topic':<20} {'Count':>6}  {'Types':<35} {'Sample Approaches'}")
    print(f"  {'─' * 95}")

    for gap in gaps:
        chapter = (gap["chapter"] or "?")[:11]
        topic = (gap["topic"] or "?")[:19]
        count = gap["count"]
        types = ", ".join(gap["task_types"])[:34]
        approaches = ", ".join(gap["sample_approaches"][:3])[:40]
        print(f"  {chapter:<12} {topic:<20} {count:>6}  {types:<35} {approaches}")

    print(f"\n  共 {len(gaps)} 个缺口，建议优先为高频缺口补充知识卡。\n")


def cmd_show(args):
    store = _store(args)
    record = store.get_by_id(args.id)
    if not record:
        print(f"未找到 id={args.id}")
        return

    print(f"\n{'=' * 60}")
    print(f"  Audit Entry: {record.get('id', '?')}")
    print(f"{'=' * 60}")
    for key, val in record.items():
        if key == "id":
            continue
        print(f"  {key:<25} {val}")
    print()


def cmd_approve(args):
    store = _store(args)
    record = store.get_by_id(args.id)
    if not record:
        print(f"未找到 id={args.id}")
        return

    current = record.get("status", "?")
    # pending → proposed → approved
    if current == AuditStatus.PENDING.value:
        # 跳过 proposed 直接 approve（简化流程）
        ok1 = store.update_status(args.id, AuditStatus.PROPOSED.value, notes="auto-proposed for approval")
        if not ok1:
            print(f"状态推进失败: {current} → proposed")
            return
        ok2 = store.update_status(args.id, AuditStatus.APPROVED.value, notes=args.notes)
        if ok2:
            print(f"已审批通过: {args.id} ({current} → approved)")
        else:
            print(f"状态推进失败: proposed → approved")
    elif current == AuditStatus.PROPOSED.value:
        ok = store.update_status(args.id, AuditStatus.APPROVED.value, notes=args.notes)
        if ok:
            print(f"已审批通过: {args.id} ({current} → approved)")
        else:
            print(f"状态推进失败: {current} → approved")
    else:
        print(f"当前状态为 {current}，无法审批 (需要 pending 或 proposed)")


def cmd_reject(args):
    store = _store(args)
    record = store.get_by_id(args.id)
    if not record:
        print(f"未找到 id={args.id}")
        return

    current = record.get("status", "?")
    if current in {AuditStatus.PENDING.value, AuditStatus.PROPOSED.value}:
        ok = store.update_status(args.id, AuditStatus.REJECTED.value, notes=args.notes)
        if ok:
            print(f"已拒绝: {args.id} ({current} → rejected)")
        else:
            print(f"状态推进失败: {current} → rejected")
    else:
        print(f"当前状态为 {current}，无法拒绝 (需要 pending 或 proposed)")


def cmd_report(args):
    store = _store(args)
    total = store.count()
    if total == 0:
        print("(无审计数据)")
        return

    type_stats = store.stats(group_by="task_type")
    status_stats = store.stats(group_by="status")
    chapter_stats = store.stats(group_by="chapter")
    gaps = store.coverage_gaps()

    lines = [
        "# RAG Audit Report",
        "",
        f"- **总条目数**: {total}",
        f"- **数据文件**: `{store.path}`",
        "",
        "## 按任务类型",
        "",
        "| Type | Count |",
        "|------|-------|",
    ]
    for k, v in sorted(type_stats.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")

    lines += [
        "",
        "## 按状态",
        "",
        "| Status | Count |",
        "|--------|-------|",
    ]
    for k, v in sorted(status_stats.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")

    lines += [
        "",
        "## 按章节",
        "",
        "| Chapter | Count |",
        "|---------|-------|",
    ]
    for k, v in sorted(chapter_stats.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")

    if gaps:
        lines += [
            "",
            "## 覆盖缺口 (需补充知识卡)",
            "",
            "| Chapter | Topic | Count | Types | Sample Approaches |",
            "|---------|-------|-------|-------|-------------------|",
        ]
        for gap in gaps[:20]:
            types = ", ".join(gap["task_types"])
            approaches = ", ".join(gap["sample_approaches"][:3])
            lines.append(
                f"| {gap['chapter']} | {gap['topic']} | {gap['count']} "
                f"| {types} | {approaches} |"
            )

    report_path = _PROJECT_ROOT / "audit_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成: {report_path}")

    # 也输出到 stdout
    print()
    print("\n".join(lines))


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RAG Audit CLI")
    parser.add_argument("--file", help="指定 JSONL 文件路径 (默认 data/rag_audit/entries.jsonl)")
    sub = parser.add_subparsers(dest="command")

    # list
    p_list = sub.add_parser("list", help="列出 audit 条目")
    p_list.add_argument("--status", choices=[s.value for s in AuditStatus])
    p_list.add_argument("--chapter")
    p_list.add_argument("--type", help="task_type 筛选")
    p_list.add_argument("--limit", type=int, default=50)

    # stats
    p_stats = sub.add_parser("stats", help="统计")
    p_stats.add_argument("--by", choices=["task_type", "status", "chapter"], default="task_type")

    # gaps
    sub.add_parser("gaps", help="覆盖缺口分析")

    # show
    p_show = sub.add_parser("show", help="查看单条记录")
    p_show.add_argument("id", help="条目 ID")

    # approve
    p_approve = sub.add_parser("approve", help="审批通过")
    p_approve.add_argument("id", help="条目 ID")
    p_approve.add_argument("--notes", default="")

    # reject
    p_reject = sub.add_parser("reject", help="审批拒绝")
    p_reject.add_argument("id", help="条目 ID")
    p_reject.add_argument("--notes", default="")

    # report
    sub.add_parser("report", help="生成完整审计报告")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "list": cmd_list,
        "stats": cmd_stats,
        "gaps": cmd_gaps,
        "show": cmd_show,
        "approve": cmd_approve,
        "reject": cmd_reject,
        "report": cmd_report,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
