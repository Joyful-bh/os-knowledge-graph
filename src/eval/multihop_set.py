"""
多跳评测集构建 —— 基于图谱结构自动生成需要多跳推理的问题。

本文件不调用 LLM，也不依赖 Neo4j。评测题完全由 data/concepts.json
和 data/edges.json 中的确定性图结构生成，便于复现实验。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONCEPTS_PATH = PROJECT_ROOT / "data" / "concepts.json"
EDGES_PATH = PROJECT_ROOT / "data" / "edges.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "multihop_questions.json"

# 15:45:15 = 20%:60%:20%，既保留二跳核心题，也加入基础题和挑战题。
QUESTIONS_PER_TYPE = 15
ONE_HOP_QUESTIONS = 15
THREE_HOP_QUESTIONS = 15
CANDIDATE_MULTIPLIER = 3


def _load_json(path: Path) -> list[dict]:
    """读取 JSON 列表文件。"""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_path(path: str) -> Path:
    """兼容从项目根目录或其他工作目录运行脚本的情况。"""
    p = Path(path)
    if p.exists():
        return p
    return PROJECT_ROOT / path


def _is_good_path(path: list[str], concept_names: set[str]) -> bool:
    """
    基础质量过滤：
    - 路径节点都必须是 Concept 白名单中的规范名称；
    - 二跳路径中不能出现重复节点，避免 A->B->A 这类绕回问题；
    - 跳过以数字开头的节点名，减少“0进程”这类不适合答辩展示的题。
    """
    if len(set(path)) != len(path):
        return False
    for name in path:
        if name not in concept_names:
            return False
        if name and name[0].isdigit():
            return False
    return True


def _group_edges(edges: list[dict], concept_names: set[str]) -> dict[str, list[dict]]:
    """按边类型分组，只保留 Concept -> Concept 的边。"""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        source = edge.get("source", "")
        target = edge.get("target", "")
        if source not in concept_names or target not in concept_names:
            continue
        grouped[edge.get("type", "")].append(edge)
    return grouped


def _out_edges(edges: list[dict]) -> dict[str, list[dict]]:
    """构建 source -> 出边列表的邻接表。"""
    adjacency: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        adjacency[edge["source"]].append(edge)
    return adjacency


def _append_unique(questions: list[dict], seen_paths: set[tuple[str, ...]], item: dict) -> None:
    """按 path 去重，保证评测题不会重复。"""
    key = tuple(item["path"])
    if key in seen_paths:
        return
    seen_paths.add(key)
    questions.append(item)


def _take_unique(
    candidates: list[dict],
    limit: int,
    questions: list[dict],
    seen_questions: set[str],
    seen_paths: set[tuple[str, ...]],
    seen_endpoints: set[tuple[str, str]],
) -> None:
    """
    从候选题中按顺序选择，做最终去重。

    去重规则：
    - question 不能重复；
    - path 不能重复；
    - 起点和终点相同的问题不能重复；
    - hops 统一按 path 的边数写入。
    """
    added = 0
    for item in candidates:
        path = item["path"]
        question = item["question"]
        path_key = tuple(path)
        endpoint_key = (path[0], path[-1])

        if question in seen_questions:
            continue
        if path_key in seen_paths:
            continue
        if endpoint_key in seen_endpoints:
            continue

        selected = dict(item)
        selected["hops"] = len(path) - 1
        questions.append(selected)
        seen_questions.add(question)
        seen_paths.add(path_key)
        seen_endpoints.add(endpoint_key)
        added += 1

        if added >= limit:
            return


def _build_direct_questions(
    prereq_edges: list[dict],
    solves_edges: list[dict],
    concept_names: set[str],
    limit: int,
) -> list[dict]:
    """生成 1-hop 基础关系题，帮助对比单跳召回与多跳推理。"""
    questions: list[dict] = []
    seen_paths: set[tuple[str, ...]] = set()

    # 先修关系适合做基础理解题，语义稳定，不会显得牵强。
    for edge in prereq_edges:
        a = edge["source"]
        b = edge["target"]
        path = [a, b]
        if not _is_good_path(path, concept_names):
            continue

        _append_unique(questions, seen_paths, {
            "question": f"为什么学习「{a}」有助于理解「{b}」？",
            "answer": (
                f"图谱直接依据是：「{a}」-[PREREQUISITE]->「{b}」。"
                f"这表示「{a}」是理解「{b}」的前置知识。"
                f"图谱依据：{edge.get('description', '')}"
            ),
            "hops": 1,
            "path": path,
            "type": "PREREQUISITE_DIRECT",
        })
        if len(questions) >= limit:
            return questions

    # 如果先修题不足，再用 SOLVES 关系补充直接因果题。
    for edge in solves_edges:
        a = edge["source"]
        b = edge["target"]
        path = [a, b]
        if not _is_good_path(path, concept_names):
            continue

        _append_unique(questions, seen_paths, {
            "question": f"为什么说「{a}」有助于应对「{b}」？",
            "answer": (
                f"图谱直接依据是：「{a}」-[SOLVES]->「{b}」。"
                f"这表示「{a}」用于解决或应对「{b}」。"
                f"图谱依据：{edge.get('description', '')}"
            ),
            "hops": 1,
            "path": path,
            "type": "SOLVES_DIRECT",
        })
        if len(questions) >= limit:
            return questions

    return questions


def _build_prereq_prereq(
    prereq_edges: list[dict],
    concept_names: set[str],
    limit: int,
) -> list[dict]:
    """生成 PREREQUISITE -> PREREQUISITE 二跳先修链问题。"""
    adjacency = _out_edges(prereq_edges)
    questions: list[dict] = []
    seen_paths: set[tuple[str, ...]] = set()

    for first in prereq_edges:
        a = first["source"]
        b = first["target"]
        for second in adjacency.get(b, []):
            c = second["target"]
            path = [a, b, c]
            if not _is_good_path(path, concept_names):
                continue

            _append_unique(questions, seen_paths, {
                "question": f"为什么理解「{c}」之前需要先学习「{a}」？",
                "answer": (
                    f"因为图谱中存在二跳先修链：「{a}」-[PREREQUISITE]->「{b}」"
                    f"-[PREREQUISITE]->「{c}」。这表示「{a}」是「{b}」的先修，"
                    f"而「{b}」又是「{c}」的先修，所以「{a}」是理解「{c}」"
                    f"的间接前置知识。图谱依据：{first.get('description', '')}；"
                    f"{second.get('description', '')}"
                ),
                "hops": 2,
                "path": path,
                "type": "PREREQUISITE_PREREQUISITE",
            })
            if len(questions) >= limit:
                return questions

    return questions


def _build_prereq_solves(
    prereq_edges: list[dict],
    solves_edges: list[dict],
    concept_names: set[str],
    limit: int,
) -> list[dict]:
    """生成 PREREQUISITE -> SOLVES 的“先修到解决问题”链。"""
    solves_adjacency = _out_edges(solves_edges)
    questions: list[dict] = []
    seen_paths: set[tuple[str, ...]] = set()

    for first in prereq_edges:
        a = first["source"]
        b = first["target"]
        for second in solves_adjacency.get(b, []):
            c = second["target"]
            path = [a, b, c]
            if not _is_good_path(path, concept_names):
                continue

            _append_unique(questions, seen_paths, {
                "question": f"为什么学习「{a}」有助于理解「{c}」的解决思路？",
                "answer": (
                    f"图谱路径是：「{a}」-[PREREQUISITE]->「{b}」-[SOLVES]->「{c}」。"
                    f"这表示「{a}」是理解「{b}」的前置知识，而「{b}」用于解决或应对"
                    f"「{c}」。图谱依据：{first.get('description', '')}；"
                    f"{second.get('description', '')}"
                ),
                "hops": 2,
                "path": path,
                "type": "PREREQUISITE_SOLVES",
            })
            if len(questions) >= limit:
                return questions

    return questions


def _build_solves_solves(
    solves_edges: list[dict],
    concept_names: set[str],
    limit: int,
) -> list[dict]:
    """生成 SOLVES -> SOLVES 的二跳因果链问题。"""
    adjacency = _out_edges(solves_edges)
    questions: list[dict] = []
    seen_paths: set[tuple[str, ...]] = set()

    for first in solves_edges:
        a = first["source"]
        b = first["target"]
        for second in adjacency.get(b, []):
            c = second["target"]
            path = [a, b, c]
            if not _is_good_path(path, concept_names):
                continue

            _append_unique(questions, seen_paths, {
                "question": f"为什么说「{a}」可能间接帮助应对「{c}」？",
                "answer": (
                    f"图谱路径是：「{a}」-[SOLVES]->「{b}」-[SOLVES]->「{c}」。"
                    f"因此「{a}」通过「{b}」间接关联到「{c}」这个被解决或应对的对象。"
                    f"图谱依据：{first.get('description', '')}；{second.get('description', '')}"
                ),
                "hops": 2,
                "path": path,
                "type": "SOLVES_SOLVES",
            })
            if len(questions) >= limit:
                return questions

    return questions


def _three_hop_item(first: dict, second: dict, third: dict) -> dict:
    """根据三条边的类型生成自然问题，answer 保留完整结构化路径。"""
    a = first["source"]
    b = first["target"]
    c = second["target"]
    d = third["target"]
    edge_types = [first["type"], second["type"], third["type"]]
    type_name = "_".join(edge_types)

    if edge_types == ["PREREQUISITE", "PREREQUISITE", "PREREQUISITE"]:
        question = f"为什么说「{a}」是理解「{d}」时可以提前掌握的基础？"
        explanation = (
            f"这表示「{a}」支撑「{b}」，「{b}」支撑「{c}」，"
            f"而「{c}」又支撑「{d}」，所以「{a}」是理解「{d}」的更早期基础。"
        )
    elif edge_types[-1] == "SOLVES":
        question = f"为什么学习「{a}」有助于分析「{d}」的解决思路？"
        explanation = (
            f"这表示「{a}」先帮助理解后续概念，最终关联到用于解决或应对"
            f"「{d}」的知识。"
        )
    else:
        question = f"为什么说「{a}」可能间接帮助理解「{d}」？"
        explanation = f"这表示「{a}」经过中间知识「{b}」和「{c}」间接关联到「{d}」。"

    path_text = (
        f"「{a}」-[{edge_types[0]}]->「{b}」"
        f"-[{edge_types[1]}]->「{c}」"
        f"-[{edge_types[2]}]->「{d}」"
    )
    return {
        "question": question,
        "answer": (
            f"完整图谱路径是：{path_text}。{explanation}"
            f"图谱依据：{first.get('description', '')}；"
            f"{second.get('description', '')}；{third.get('description', '')}"
        ),
        "hops": 3,
        "path": [a, b, c, d],
        "type": type_name,
    }


def _build_three_hop_questions(
    prereq_edges: list[dict],
    solves_edges: list[dict],
    concept_names: set[str],
    limit: int,
) -> list[dict]:
    """生成 3-hop 挑战题，优先选择语义清晰的先修链和先修-解决链。"""
    edge_groups = {
        "PREREQUISITE": prereq_edges,
        "SOLVES": solves_edges,
    }
    adjacency = {
        edge_type: _out_edges(edges)
        for edge_type, edges in edge_groups.items()
    }
    sequences = [
        ("PREREQUISITE", "PREREQUISITE", "PREREQUISITE"),
        ("PREREQUISITE", "PREREQUISITE", "SOLVES"),
        ("PREREQUISITE", "SOLVES", "SOLVES"),
        ("SOLVES", "SOLVES", "SOLVES"),
    ]

    questions: list[dict] = []
    seen_paths: set[tuple[str, ...]] = set()

    for sequence in sequences:
        for first in edge_groups[sequence[0]]:
            a = first["source"]
            b = first["target"]
            for second in adjacency[sequence[1]].get(b, []):
                c = second["target"]
                for third in adjacency[sequence[2]].get(c, []):
                    d = third["target"]
                    path = [a, b, c, d]
                    if not _is_good_path(path, concept_names):
                        continue

                    _append_unique(questions, seen_paths, _three_hop_item(first, second, third))
                    if len(questions) >= limit:
                        return questions

    return questions


def build_multihop_questions(driver) -> list[dict]:
    """
    遍历图谱中的多跳路径，自动生成评测问题。

    Args:
        driver: 保留原有签名。当前实现基于 JSON 文件构建，不使用 Neo4j driver。

    Returns:
        [
            {
                "question": str,
                "answer": str,
                "hops": int,
                "path": [str],
                "type": str,
            },
            ...
        ]
    """
    _ = driver  # 保留参数是为了兼容旧接口，当前不依赖数据库。

    concepts = _load_json(CONCEPTS_PATH)
    edges = _load_json(EDGES_PATH)
    concept_names = {concept["name"] for concept in concepts}
    grouped_edges = _group_edges(edges, concept_names)

    prereq_edges = grouped_edges.get("PREREQUISITE", [])
    solves_edges = grouped_edges.get("SOLVES", [])

    candidate_limit = QUESTIONS_PER_TYPE * CANDIDATE_MULTIPLIER
    direct_candidates = _build_direct_questions(
        prereq_edges,
        solves_edges,
        concept_names,
        ONE_HOP_QUESTIONS * CANDIDATE_MULTIPLIER,
    )
    prereq_prereq_candidates = _build_prereq_prereq(prereq_edges, concept_names, candidate_limit)
    prereq_solves_candidates = _build_prereq_solves(
        prereq_edges,
        solves_edges,
        concept_names,
        candidate_limit,
    )
    solves_solves_candidates = _build_solves_solves(solves_edges, concept_names, candidate_limit)
    three_hop_candidates = _build_three_hop_questions(
        prereq_edges,
        solves_edges,
        concept_names,
        THREE_HOP_QUESTIONS * CANDIDATE_MULTIPLIER,
    )

    questions: list[dict] = []
    seen_questions: set[str] = set()
    seen_paths: set[tuple[str, ...]] = set()
    seen_endpoints: set[tuple[str, str]] = set()

    _take_unique(
        direct_candidates,
        ONE_HOP_QUESTIONS,
        questions,
        seen_questions,
        seen_paths,
        seen_endpoints,
    )
    for candidates in [prereq_prereq_candidates, prereq_solves_candidates, solves_solves_candidates]:
        _take_unique(
            candidates,
            QUESTIONS_PER_TYPE,
            questions,
            seen_questions,
            seen_paths,
            seen_endpoints,
        )
    _take_unique(
        three_hop_candidates,
        THREE_HOP_QUESTIONS,
        questions,
        seen_questions,
        seen_paths,
        seen_endpoints,
    )

    return questions


def save_questions(questions: list[dict], path: str = "data/multihop_questions.json") -> None:
    """保存评测集。只写入 multihop_questions.json，不会覆盖概念或边文件。"""
    output_path = _resolve_path(path)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)


def load_questions(path: str = "data/multihop_questions.json") -> list[dict]:
    """从文件加载已生成的评测集。"""
    with _resolve_path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    questions = build_multihop_questions(None)
    save_questions(questions, str(DEFAULT_OUTPUT_PATH))
    hops_count = Counter(item["hops"] for item in questions)

    print(f"已生成 {len(questions)} 条多跳评测问题，保存到 {DEFAULT_OUTPUT_PATH}")
    print("hops 分布：")
    for hops in sorted(hops_count):
        print(f"hops={hops}: {hops_count[hops]}")
    print("5 条示例：")
    for i, item in enumerate(questions[68:73], start=1):
        print(f"\n{i}. {item['question']}")
        print(f"   答案：{item['answer']}")
        print(f"   路径：{' -> '.join(item['path'])}")
        print(f"   类型：{item['type']}，跳数：{item['hops']}")
