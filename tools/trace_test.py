#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调用树追踪脚本 — 在 auto_test 运行期间记录 agent/ 下的函数调用层次，
输出到 call_tree.md
"""

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_STR = str(_PROJECT_ROOT) + os.sep

# ─── Trace state ─────────────────────────────────────────────────────────────

_trace_lines: list[str] = []
_depth = 0
_call_counts: dict[str, int] = {}  # "file:func" -> count
_seen_frames: set[int] = set()     # id(frame) -> 已记录过
_frame_depth: dict[int, int] = {}  # id(frame) -> 进入时的 depth
_lock = threading.Lock()

# 只追踪这些目录
_INCLUDE_DIRS = ("agent/", "tools/")
# 跳过的函数名模式
_SKIP_FUNCS = frozenset({
    "<module>", "<listcomp>", "<dictcomp>", "<setcomp>", "<genexpr>",
    "__repr__", "__str__", "__hash__", "__eq__", "__ne__",
    "__lt__", "__le__", "__gt__", "__ge__",
    "__len__", "__bool__", "__contains__",
    "__getattr__", "__getattribute__", "__setattr__",
    "__get__", "__set__",
})


def _make_rel(filename: str) -> str | None:
    """返回相对路径，不属于项目则返回 None。"""
    if not filename.startswith(_PROJECT_STR):
        return None
    rel = filename[len(_PROJECT_STR):]
    if not any(rel.startswith(d) for d in _INCLUDE_DIRS):
        return None
    if "__pycache__" in rel:
        return None
    return rel


def _trace_calls(frame, event, arg):
    global _depth

    if event == "call":
        rel = _make_rel(frame.f_code.co_filename)
        if rel is None:
            return None

        func_name = frame.f_code.co_name
        if func_name in _SKIP_FUNCS:
            return _trace_calls

        fid = id(frame)

        # async 帧恢复时会重复触发 call，跳过已记录的帧
        with _lock:
            if fid in _seen_frames:
                return _trace_calls
            _seen_frames.add(fid)

            lineno = frame.f_lineno
            key = f"{rel}:{func_name}"
            _call_counts[key] = _call_counts.get(key, 0) + 1

            indent = "│ " * _depth
            _trace_lines.append(f"{indent}├─ {rel}:{lineno}  {func_name}()")
            _frame_depth[fid] = _depth
            _depth += 1

        return _trace_calls

    if event == "return":
        rel = _make_rel(frame.f_code.co_filename)
        if rel is None:
            return None
        func_name = frame.f_code.co_name
        if func_name in _SKIP_FUNCS:
            return _trace_calls

        fid = id(frame)
        with _lock:
            if fid in _frame_depth:
                _depth = _frame_depth.pop(fid)
                _seen_frames.discard(fid)

    return _trace_calls


# ─── Main ────────────────────────────────────────────────────────────────────

async def main():
    # 选择场景
    scenario = "sequence"
    for arg in sys.argv[1:]:
        if arg in ("ellipse", "sequence"):
            scenario = arg

    # Bootstrap debug_cli
    sys.argv = ["trace_test.py", "--live"]
    sys.path.insert(0, str(_PROJECT_ROOT / "tools"))

    import auto_test

    # 覆盖 auto_test 的 _real_argv 以传入场景
    auto_test._real_argv = ["trace_test.py", scenario]

    print(f"=== 开始追踪 ({scenario}) ===\n", flush=True)

    # 启用追踪
    sys.settrace(_trace_calls)
    threading.settrace(_trace_calls)

    t0 = time.time()
    try:
        await auto_test.main()
    finally:
        sys.settrace(None)
        threading.settrace(None)
    elapsed = time.time() - t0

    # ─── 输出调用树 ─────────────────────────────────────────────────────
    out = _PROJECT_ROOT / "call_tree.md"

    lines = [
        "# 函数调用树",
        "",
        f"- **场景**: `{scenario}`",
        f"- **日期**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **总耗时**: {elapsed:.1f}s",
        f"- **调用总数**: {len(_trace_lines)}",
        "",
        "## 调用频次 Top 30",
        "",
        "| 次数 | 函数 |",
        "|------|------|",
    ]

    sorted_counts = sorted(_call_counts.items(), key=lambda x: -x[1])[:30]
    for key, count in sorted_counts:
        lines.append(f"| {count} | `{key}` |")
    lines.append("")

    lines.append("## 完整调用树")
    lines.append("")
    lines.append("```")
    # 折叠连续重复行
    prev = None
    repeat = 0
    for tl in _trace_lines:
        if tl == prev:
            repeat += 1
            continue
        if repeat > 0:
            lines.append(f"{prev}  ×{repeat + 1}")
            repeat = 0
        else:
            if prev is not None:
                lines.append(prev)
        prev = tl
    if prev is not None:
        if repeat > 0:
            lines.append(f"{prev}  ×{repeat + 1}")
        else:
            lines.append(prev)
    lines.append("```")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n>>> 调用树已写入: {out} ({len(_trace_lines)} 条记录)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
