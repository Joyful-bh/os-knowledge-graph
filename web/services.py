"""
Web 服务层封装。

本文件只负责给页面提供稳定、易消费的函数入口：
- 调用 src 下已有核心能力
- 捕获缺文件、缺 API key、后端运行异常
- 返回空结果或 error 字段，避免 Web 页面直接崩溃

不在这里实现核心算法。
"""

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def ask_graphrag(question: str, top_k: int = 3, hops: int = 2) -> dict:
    """
    调用 GraphRAG 回答问题。

    Returns:
        成功：{"answer": str, "error": None}
        失败：{"answer": "", "error": str}
    """
    try:
        from src.retrieval.graph_rag import answer

        return {
            "answer": answer(question=question, top_k=top_k, hops=hops),
            "error": None,
        }
    except Exception as exc:
        return {"answer": "", "error": _format_error(exc)}


def retrieve_subgraph(
    concept_names: list[str],
    edge_types: list[str],
    hops: int = 2,
) -> dict:
    """
    召回一组概念的图谱邻域。

    Web 页面可直接读取返回中的 nodes / edges；失败时返回空图和 error。
    """
    try:
        from src.retrieval.subgraph import get_subgraph

        result = get_subgraph(
            concept_names=concept_names,
            edge_types=edge_types,
            hops=hops,
        )
        return {
            "nodes": result.get("nodes", []),
            "edges": result.get("edges", []),
            "error": None,
        }
    except Exception as exc:
        return {"nodes": [], "edges": [], "error": _format_error(exc)}


def diagnose_wrong_pids(pids: list[str]) -> dict:
    """
    对错题 pid 列表做认知诊断。

    失败时保留与 trace_wrong_problems() 类似的空结构，便于页面统一渲染。
    """
    try:
        from src.diagnosis.trace import trace_wrong_problems

        result = trace_wrong_problems(pids)
        result.setdefault("error", None)
        return result
    except Exception as exc:
        return {
            "pids": pids,
            "traces": [],
            "weak_candidates": [],
            "error": _format_error(exc),
        }


def estimate_mastery_for_pids(pids: list[str]) -> list[dict]:
    """
    根据错题 pid 列表估计概念掌握度。

    失败时返回空列表，避免页面崩溃。
    """
    try:
        from src.diagnosis.mastery import estimate_mastery

        return estimate_mastery(wrong_pids=pids)
    except Exception:
        return []


def plan_learning_path(pids: list[str], max_items: int = 10) -> list[dict]:
    """
    根据错题 pid 列表生成推荐学习路径。

    失败时返回空列表，页面可显示“暂无推荐路径”。
    """
    try:
        from src.path.planner import plan_from_wrong_problems

        return plan_from_wrong_problems(wrong_pids=pids, max_items=max_items)
    except Exception:
        return []


def load_compare_results(path: str = "data/phase2_compare_results.json") -> dict | None:
    """
    读取 Phase 2 对比实验结果。

    文件不存在或 JSON 无法解析时返回 None，不向 Web 页面抛异常。
    """
    try:
        result_path = _resolve_path(path)
        if not result_path.exists():
            return None
        return json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_path(path: str) -> Path:
    """把相对路径解析到项目根目录，绝对路径保持不变。"""
    result_path = Path(path)
    if result_path.is_absolute():
        return result_path
    return ROOT / result_path


def _format_error(exc: Exception) -> str:
    """
    把后端异常转成简短字符串。

    Web 层不隐藏错误类型，便于定位缺 API key、缺依赖或本地服务未启动等问题。
    """
    return f"{exc.__class__.__name__}: {exc}"
