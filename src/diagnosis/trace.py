"""
认知诊断溯源 —— 从错题反向追踪薄弱先修知识点。
依赖 retrieval/subgraph.get_subgraph() 获取 PREREQUISITE 子图。
Phase 3 实现。
"""

from src.retrieval.subgraph import get_subgraph  # noqa: F401


def trace_from_problem(pid: str, driver=None) -> list[dict]:
    """
    给定错题 ID,经 TESTS 边定位考查概念,再沿 PREREQUISITE 反向溯源。

    Args:
        pid: Problem.pid
        driver: Neo4j 驱动(为 None 时自动创建)

    Returns:
        薄弱知识点列表,按先修层级由浅到深排序:
        [{"name": str, "definition": str, "difficulty": int, "depth": int}, ...]
    """
    raise NotImplementedError("Phase 3 实现")


def trace_from_concepts(concept_names: list[str]) -> list[dict]:
    """
    给定一组已知薄弱概念,溯源其先修链,返回需要补充学习的节点集合。
    trace_from_problem 的底层调用。
    """
    raise NotImplementedError("Phase 3 实现")
