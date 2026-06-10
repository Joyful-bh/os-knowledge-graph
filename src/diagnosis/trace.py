"""
认知诊断溯源 —— 从错题反向追踪薄弱先修知识点。

Phase 3 的目标链路：
错题 pid -> TESTS 边定位考查概念 -> PREREQUISITE 反向溯源 -> 薄弱候选概念。

本模块只消费 Phase 1 已有接口，不修改图谱数据与共享接口：
- src.kg.load.get_graph()
- src.retrieval.subgraph.get_prereq_ancestors()
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import networkx as nx

from src.kg.load import get_graph
from src.retrieval.subgraph import get_prereq_ancestors


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PROBLEMS_PATH = _PROJECT_ROOT / "data" / "problems.json"
_RAW_PROBLEMS_PATH = _PROJECT_ROOT / "data" / "candidates" / "problems_raw.json"
_EDGES_PATH = _PROJECT_ROOT / "data" / "edges.json"


def load_problems(path: str | None = None) -> list[dict]:
    """
    读取习题数据。

    优先级：
    1. 显式传入的 path
    2. data/problems.json
    3. data/candidates/problems_raw.json
    """
    if path is not None:
        problem_path = Path(path)
    elif _PROBLEMS_PATH.exists():
        problem_path = _PROBLEMS_PATH
    else:
        problem_path = _RAW_PROBLEMS_PATH

    if not problem_path.exists():
        raise FileNotFoundError(f"习题文件不存在: {problem_path}")

    return json.loads(problem_path.read_text(encoding="utf-8"))


def get_problem_by_pid(pid: str) -> dict | None:
    """按 pid 查找习题；找不到时返回 None，便于上层做容错展示。"""
    for problem in load_problems():
        if problem.get("pid") == pid:
            return problem
    return None


def get_tested_concepts(pid: str, G: nx.MultiDiGraph | None = None) -> list[str]:
    """
    查询某道题考查的概念列表。

    首选 NetworkX 图中的 TESTS 边；如果图中查不到，再回退到 data/edges.json。
    TESTS 边方向固定为：Problem pid -> Concept name。
    """
    if G is None:
        G = get_graph()

    concepts: list[str] = []

    # 图里存在 Problem 节点时，直接读取出边。
    if pid in G:
        for _, target, data in G.out_edges(pid, data=True):
            if data.get("type") != "TESTS":
                continue
            if G.nodes[target].get("label") == "Concept":
                concepts.append(target)

    if concepts:
        return _dedupe_keep_order(concepts)

    # 回退路径：便于将来替换 problem 文件或图缓存未更新时仍能诊断。
    if _EDGES_PATH.exists():
        edges = json.loads(_EDGES_PATH.read_text(encoding="utf-8"))
        concepts = [
            edge["target"]
            for edge in edges
            if edge.get("type") == "TESTS" and edge.get("source") == pid
        ]

    return _dedupe_keep_order(concepts)


def trace_wrong_problem(
    pid: str,
    max_hops: int = 5,
    G: nx.MultiDiGraph | None = None,
) -> dict:
    """
    对单道错题做诊断溯源。

    返回结构面向后续 mastery.py / planner.py：
    - problem: 题目信息
    - tested_concepts: 该题直接考查的概念
    - prerequisite_traces: 每个考查概念对应的先修链
    - weak_candidates: 可疑薄弱概念，含 evidence 字段与 reason
    """
    if G is None:
        G = get_graph()

    problem = get_problem_by_pid(pid)
    tested_concepts = get_tested_concepts(pid, G=G)

    prerequisite_traces: list[dict] = []
    weak_candidates: list[dict] = []

    for target_concept in tested_concepts:
        if target_concept not in G:
            prerequisite_traces.append({
                "target_concept": target_concept,
                "nodes": [],
                "edges": [],
                "warning": "考查概念不在图中，无法做先修溯源。",
            })
            continue

        trace = get_prereq_ancestors(target_concept, max_hops=max_hops, G=G)
        prerequisite_traces.append({
            "target_concept": target_concept,
            "nodes": trace["nodes"],
            "edges": trace["edges"],
        })

        for node in trace["nodes"]:
            weak_candidates.append(_build_candidate(
                node=node,
                evidence_pid=pid,
                evidence_target_concept=target_concept,
            ))

    return {
        "pid": pid,
        "problem": problem,
        "tested_concepts": tested_concepts,
        "prerequisite_traces": prerequisite_traces,
        "weak_candidates": _sort_candidates(_dedupe_candidates(weak_candidates)),
    }


def trace_wrong_problems(
    pids: list[str],
    max_hops: int = 5,
    G: nx.MultiDiGraph | None = None,
) -> dict:
    """
    汇总多道错题的诊断结果。

    aggregate weak_candidates 按概念名合并，用 hit_count 和 evidences 保留证据。
    evidence_pid / evidence_target_concept 保留首个证据，满足单条候选的基础字段约定。
    """
    if G is None:
        G = get_graph()

    traces = [
        trace_wrong_problem(pid=pid, max_hops=max_hops, G=G)
        for pid in pids
    ]

    aggregate: dict[str, dict[str, Any]] = {}
    for item in traces:
        for candidate in item["weak_candidates"]:
            name = candidate["name"]
            evidence = {
                "pid": candidate["evidence_pid"],
                "target_concept": candidate["evidence_target_concept"],
                "depth": candidate["depth"],
            }

            if name not in aggregate:
                merged = dict(candidate)
                merged["hit_count"] = 1
                merged["evidences"] = [evidence]
                aggregate[name] = merged
                continue

            merged = aggregate[name]
            merged["hit_count"] += 1
            merged["evidences"].append(evidence)
            merged["depth"] = min(merged["depth"], candidate["depth"])

    weak_candidates = list(aggregate.values())
    for candidate in weak_candidates:
        candidate["reason"] = _build_aggregate_reason(candidate)

    return {
        "pids": pids,
        "traces": traces,
        "weak_candidates": _sort_candidates(weak_candidates),
    }


def trace_from_problem(pid: str, driver=None) -> list[dict]:
    """
    兼容旧入口：返回单题诊断出的薄弱候选概念列表。

    driver 参数保留给旧接口兼容；当前默认实现只使用 NetworkX。
    """
    _ = driver
    return trace_wrong_problem(pid)["weak_candidates"]


def trace_from_concepts(concept_names: list[str]) -> list[dict]:
    """
    给定一组已知薄弱概念，溯源其先修链。

    该函数不依赖错题 pid，因此 evidence_pid 使用空字符串。
    """
    G = get_graph()
    candidates: list[dict] = []

    for concept_name in concept_names:
        if concept_name not in G:
            continue

        trace = get_prereq_ancestors(concept_name, G=G)
        for node in trace["nodes"]:
            candidates.append(_build_candidate(
                node=node,
                evidence_pid="",
                evidence_target_concept=concept_name,
            ))

    return _sort_candidates(_dedupe_candidates(candidates))


def _dedupe_keep_order(items: list[str]) -> list[str]:
    """按首次出现顺序去重，避免同一 TESTS 概念重复进入诊断。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _build_candidate(
    node: dict,
    evidence_pid: str,
    evidence_target_concept: str,
) -> dict:
    """把 get_prereq_ancestors() 的节点包装成诊断候选项。"""
    depth = int(node.get("depth", 0))
    name = node.get("name", "")
    return {
        "name": name,
        "depth": depth,
        "node_role": node.get("node_role", "概念"),
        "difficulty": node.get("difficulty", 1),
        "chapter": node.get("chapter", ""),
        "definition": node.get("definition", ""),
        "evidence_pid": evidence_pid,
        "evidence_target_concept": evidence_target_concept,
        "reason": _build_reason(
            name=name,
            depth=depth,
            evidence_pid=evidence_pid,
            evidence_target_concept=evidence_target_concept,
        ),
    }


def _build_reason(
    name: str,
    depth: int,
    evidence_pid: str,
    evidence_target_concept: str,
) -> str:
    """生成可直接展示给前端或报告的中文诊断理由。"""
    if depth == 0:
        return (
            f"错题 {evidence_pid} 直接考查「{evidence_target_concept}」，"
            f"「{name}」本身可能未掌握。"
        )
    return (
        f"错题 {evidence_pid} 考查「{evidence_target_concept}」，"
        f"「{name}」是其第 {depth} 层先修概念，可能是错误根因。"
    )


def _build_aggregate_reason(candidate: dict) -> str:
    """多题汇总时，用命中次数和最近证据重写 reason。"""
    evidences = candidate.get("evidences", [])
    target_counts: dict[str, int] = defaultdict(int)
    for evidence in evidences:
        target_counts[evidence["target_concept"]] += 1

    targets = "、".join(target_counts.keys())
    return (
        f"该概念被 {candidate.get('hit_count', 1)} 条错题证据命中，"
        f"关联考查概念包括「{targets}」，建议优先排查。"
    )


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    """
    单题候选去重。

    同一道题多个目标概念可能共享先修节点；保留 depth 更浅的证据，
    因为它通常更接近当前错题考查点。
    """
    best: dict[tuple[str, str], dict] = {}
    for candidate in candidates:
        key = (candidate["name"], candidate["evidence_target_concept"])
        old = best.get(key)
        if old is None or candidate["depth"] < old["depth"]:
            best[key] = candidate
    return list(best.values())


def _sort_candidates(candidates: list[dict]) -> list[dict]:
    """
    诊断候选排序。

    先展示直接考查概念，再展示近层先修；同层内优先简单概念，便于学习路径接上。
    """
    return sorted(
        candidates,
        key=lambda item: (
            item.get("depth", 0),
            item.get("difficulty", 1),
            item.get("chapter", ""),
            item.get("name", ""),
        ),
    )


if __name__ == "__main__":
    # 简单 smoke test：可替换成任意 problems.json / problems_raw.json 中的 pid。
    demo_pid = "CH5_Q11"
    result = trace_wrong_problem(demo_pid, max_hops=3)

    print(f"错题: {demo_pid}")
    print(f"题干: {result['problem']['stem'] if result['problem'] else '未找到题目'}")
    print(f"考查概念: {', '.join(result['tested_concepts'])}")
    print("薄弱候选 Top 10:")
    for candidate in result["weak_candidates"][:10]:
        print(
            f"- {candidate['name']} "
            f"(depth={candidate['depth']}, difficulty={candidate['difficulty']}): "
            f"{candidate['reason']}"
        )
