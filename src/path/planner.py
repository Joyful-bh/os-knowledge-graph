"""
学习路径规划 —— 基于 PREREQUISITE DAG 的拓扑约束搜索。

Phase 3 链路：
错题列表 / 薄弱概念列表 -> 追溯先修链 -> 合并局部 DAG -> 推荐学习顺序。

本模块不调用 LLM，只使用 NetworkX 与项目已有接口。
"""

from collections import defaultdict
from typing import Any

import networkx as nx

from src.diagnosis.mastery import estimate_mastery
from src.kg.load import get_graph
from src.retrieval.subgraph import get_prereq_ancestors


_RISK_ORDER = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


def find_practice_for_concept(
    concept_name: str,
    G: nx.MultiDiGraph | None = None,
    limit: int = 3,
) -> list[str]:
    """
    查找直接考查某概念的习题 pid。

    TESTS 边方向固定为：Problem pid -> Concept name。
    """
    if G is None:
        G = get_graph()

    if concept_name not in G:
        return []

    practice_pids: list[str] = []
    for problem_pid, _, data in G.in_edges(concept_name, data=True):
        if data.get("type") != "TESTS":
            continue
        if G.nodes[problem_pid].get("label") != "Problem":
            continue
        practice_pids.append(problem_pid)
        if len(practice_pids) >= limit:
            break

    return practice_pids


def plan_from_weak_concepts(
    weak_concepts: list[str],
    max_items: int = 10,
    G: nx.MultiDiGraph | None = None,
) -> list[dict]:
    """
    根据薄弱概念列表规划学习路径。

    对每个薄弱概念调用 get_prereq_ancestors()，合并所有先修子图，
    再尽量按 PREREQUISITE 拓扑顺序排序。
    """
    if G is None:
        G = get_graph()

    return _build_learning_plan(
        weak_concepts=weak_concepts,
        max_items=max_items,
        G=G,
        assessment_by_name={},
    )


def plan_from_wrong_problems(
    wrong_pids: list[str],
    max_items: int = 10,
    G: nx.MultiDiGraph | None = None,
) -> list[dict]:
    """
    根据错题列表估计掌握度，并优先为 high / medium 风险概念规划路径。
    """
    if G is None:
        G = get_graph()

    mastery_items = estimate_mastery(wrong_pids=wrong_pids, max_hops=5)
    assessment_by_name = {item["name"]: item for item in mastery_items}

    weak_concepts = [
        item["name"]
        for item in mastery_items
        if item.get("risk") in {"high", "medium"}
    ]
    if not weak_concepts:
        weak_concepts = [item["name"] for item in mastery_items[:max_items]]

    return _build_learning_plan(
        weak_concepts=weak_concepts,
        max_items=max_items,
        G=G,
        assessment_by_name=assessment_by_name,
    )


def plan_path(
    target_concepts: list[str],
    mastery: dict[str, float],
    driver=None,
) -> list[str]:
    """
    兼容旧入口：返回概念名形式的学习顺序。

    driver 参数保留给旧接口兼容；当前实现只使用 NetworkX。
    """
    _ = driver
    assessment_by_name = {
        name: {"mastery": score, "risk": _risk_from_mastery(score)}
        for name, score in mastery.items()
    }
    plan = _build_learning_plan(
        weak_concepts=target_concepts,
        max_items=10,
        G=get_graph(),
        assessment_by_name=assessment_by_name,
    )
    return [item["concept"] for item in plan]


def _build_learning_plan(
    weak_concepts: list[str],
    max_items: int,
    G: nx.MultiDiGraph,
    assessment_by_name: dict[str, dict],
) -> list[dict]:
    """合并先修子图并生成结构化学习路径。"""
    weak_concepts = _dedupe_keep_order([name for name in weak_concepts if name in G])
    if max_items <= 0 or not weak_concepts:
        return []

    nodes, prereq_edges, prerequisite_of = _collect_prereq_subgraph(
        weak_concepts=weak_concepts,
        G=G,
    )
    if not nodes:
        return []

    order = _topological_order(
        nodes=nodes,
        prereq_edges=prereq_edges,
        assessment_by_name=assessment_by_name,
    )

    steps: list[dict] = []
    for concept_name in order[:max_items]:
        node = nodes[concept_name]
        assessment = assessment_by_name.get(concept_name, {})
        dependents = sorted(prerequisite_of.get(concept_name, []))

        step = {
            "step": len(steps) + 1,
            "concept": concept_name,
            "reason": _build_reason(
                concept_name=concept_name,
                weak_concepts=weak_concepts,
                prerequisite_of=dependents,
                assessment=assessment,
            ),
            "difficulty": node.get("difficulty", 1),
            "chapter": node.get("chapter", ""),
            "node_role": node.get("node_role", "概念"),
            "risk": assessment.get("risk", "unknown"),
            "practice_pids": find_practice_for_concept(concept_name, G=G),
        }
        if "mastery" in assessment:
            step["mastery"] = assessment["mastery"]
        if dependents:
            step["prerequisite_of"] = dependents

        steps.append(step)

    return steps


def _collect_prereq_subgraph(
    weak_concepts: list[str],
    G: nx.MultiDiGraph,
) -> tuple[dict[str, dict], list[tuple[str, str]], dict[str, set[str]]]:
    """
    收集多个薄弱概念的先修祖先子图。

    返回：
    - nodes: concept -> 节点属性，额外记录 max_depth 供回退排序使用
    - prereq_edges: (source, target)，source 是 target 的先修
    - prerequisite_of: source -> {target...}
    """
    nodes: dict[str, dict] = {}
    prereq_edges: list[tuple[str, str]] = []
    prerequisite_of: dict[str, set[str]] = defaultdict(set)

    for weak_concept in weak_concepts:
        trace = get_prereq_ancestors(weak_concept, G=G)

        for node in trace["nodes"]:
            name = node["name"]
            old = nodes.get(name)
            if old is None:
                nodes[name] = dict(node)
                nodes[name]["max_depth"] = node.get("depth", 0)
                continue

            old["depth"] = min(old.get("depth", 0), node.get("depth", 0))
            old["max_depth"] = max(old.get("max_depth", 0), node.get("depth", 0))

        for edge in trace["edges"]:
            source, target = edge["source"], edge["target"]
            prereq_edges.append((source, target))
            prerequisite_of[source].add(target)

    return nodes, _dedupe_edges(prereq_edges), prerequisite_of


def _topological_order(
    nodes: dict[str, dict],
    prereq_edges: list[tuple[str, str]],
    assessment_by_name: dict[str, dict],
) -> list[str]:
    """
    按 PREREQUISITE 拓扑排序。

    如果局部子图异常成环，则回退到 depth / difficulty / risk 排序。
    """
    dag = nx.DiGraph()
    dag.add_nodes_from(nodes)
    dag.add_edges_from(prereq_edges)

    def key(name: str) -> tuple:
        node = nodes[name]
        risk = assessment_by_name.get(name, {}).get("risk", "unknown")
        return (
            node.get("difficulty", 1),
            _RISK_ORDER.get(risk, _RISK_ORDER["unknown"]),
            name,
        )

    try:
        return list(nx.lexicographical_topological_sort(dag, key=key))
    except nx.NetworkXUnfeasible:
        return sorted(
            nodes,
            key=lambda name: (
                -nodes[name].get("max_depth", nodes[name].get("depth", 0)),
                nodes[name].get("difficulty", 1),
                _RISK_ORDER.get(
                    assessment_by_name.get(name, {}).get("risk", "unknown"),
                    _RISK_ORDER["unknown"],
                ),
                name,
            ),
        )


def _build_reason(
    concept_name: str,
    weak_concepts: list[str],
    prerequisite_of: list[str],
    assessment: dict[str, Any],
) -> str:
    """生成路径步骤的中文推荐理由。"""
    risk = assessment.get("risk")
    mastery = assessment.get("mastery")

    if concept_name in weak_concepts:
        base = f"「{concept_name}」是诊断出的薄弱概念，需要直接复习。"
    elif prerequisite_of:
        base = (
            f"「{concept_name}」是「{'、'.join(prerequisite_of)}」的先修概念，"
            "应先补齐基础。"
        )
    else:
        base = f"「{concept_name}」位于当前薄弱概念的先修链上，建议复习。"

    if risk and risk != "unknown":
        base += f" 当前风险等级为 {risk}。"
    if mastery is not None:
        base += f" 估计掌握度为 {mastery}。"

    return base


def _risk_from_mastery(mastery: float) -> str:
    """兼容旧 plan_path() 时，把掌握度转换为风险等级。"""
    if mastery < 0.4:
        return "high"
    if mastery < 0.7:
        return "medium"
    return "low"


def _dedupe_keep_order(items: list[str]) -> list[str]:
    """按首次出现顺序去重。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _dedupe_edges(edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """边去重，避免 MultiDiGraph 中重复边影响局部排序。"""
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for edge in edges:
        if edge in seen:
            continue
        seen.add(edge)
        result.append(edge)
    return result


if __name__ == "__main__":
    print("由错题生成学习路径:")
    wrong_plan = plan_from_wrong_problems(["CH1_Q2"], max_items=8)
    for item in wrong_plan:
        print(
            f"{item['step']}. {item['concept']} "
            f"(risk={item.get('risk')}, difficulty={item['difficulty']}) "
            f"practice={item['practice_pids']}"
        )
        print(f"   {item['reason']}")

    print("\n由薄弱概念生成学习路径:")
    weak_plan = plan_from_weak_concepts(["用户与操作系统接口"], max_items=5)
    for item in weak_plan:
        print(f"{item['step']}. {item['concept']} - {item['reason']}")
