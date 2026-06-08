"""
子图召回 —— RAG 与诊断模块的共用接口。

graph_rag.py 调用此接口扩展上下文；diagnosis/trace.py 调用此接口反向溯源先修。
修改函数签名或返回格式必须同步更新 docs/collaboration_contract.md。
"""

import networkx as nx

from src.kg.load import UNDIRECTED_TYPES, get_graph

# 有效边类型集合（防止拼写错误传入）
_VALID_EDGE_TYPES = {"PREREQUISITE", "PART_OF", "RELATED", "CONFUSABLE", "SOLVES", "TESTS"}


def get_subgraph(
    concept_names: list[str],
    edge_types: list[str] = ["PREREQUISITE", "RELATED", "SOLVES"],
    hops: int = 2,
    G: nx.MultiDiGraph | None = None,
) -> dict:
    """
    给定一组概念，返回其 N 跳邻域子图（双向 BFS，无向边自动对称处理）。

    Args:
        concept_names: 起始概念的规范名称列表（需与 Concept.name 精确匹配）
        edge_types:    遍历的边类型列表；只能是 Schema 定义的六种之一
        hops:          BFS 跳数（默认 2）
        G:             可选的 NetworkX 图对象；为 None 时自动调用 get_graph()

    Returns:
        {
            "nodes": [
                {
                    "name":      str,   # 规范名称
                    "node_role": str,   # 概念 / 机制 / 算法 / 问题
                    "difficulty": int,  # 1-3
                    "definition": str,
                    "chapter":   str,
                },
                ...
            ],
            "edges": [
                {
                    "type":        str,  # 边类型
                    "source":      str,  # 起始节点（规范名）
                    "target":      str,  # 目标节点（规范名）
                    "description": str,  # 边语义描述（供 RAG 直接用作上下文）
                },
                ...
            ]
        }

    注意：
        - 起始概念本身一定出现在 nodes 中（即使不在图里也跳过，不报错）
        - edges 只包含 nodes 集合内部的边
        - RELATED / CONFUSABLE 为无向边，只出现一次（source < target 字典序）
        - 不在图中的起始概念名会被静默忽略
    """
    if G is None:
        G = get_graph()

    edge_type_set = set(edge_types) & _VALID_EDGE_TYPES

    # ── BFS：从种子概念向外扩展 N 跳 ─────────────────────────────────────────
    visited: set[str] = set()
    frontier: set[str] = set()

    for name in concept_names:
        if name in G:
            visited.add(name)
            frontier.add(name)

    for _ in range(hops):
        next_frontier: set[str] = set()
        for node in frontier:
            # 出边
            for _, v, data in G.out_edges(node, data=True):
                if data.get("type") in edge_type_set and v not in visited:
                    visited.add(v)
                    next_frontier.add(v)
            # 入边（双向 BFS，支持 trace.py 反向溯源先修）
            for u, _, data in G.in_edges(node, data=True):
                if data.get("type") in edge_type_set and u not in visited:
                    visited.add(u)
                    next_frontier.add(u)
        frontier = next_frontier

    # ── 收集节点（只要 Concept，排除 Problem）────────────────────────────────
    nodes: list[dict] = []
    for n in visited:
        attr = G.nodes[n]
        if attr.get("label") != "Concept":
            continue
        nodes.append({
            "name":       n,
            "node_role":  attr.get("node_role",  "概念"),
            "difficulty": attr.get("difficulty", 1),
            "definition": attr.get("definition", ""),
            "chapter":    attr.get("chapter",    ""),
        })

    # ── 收集边（仅子图内部的边，无向边去重）──────────────────────────────────
    seen_keys: set[tuple] = set()
    edges: list[dict] = []
    for u in visited:
        for _, v, data in G.out_edges(u, data=True):
            etype = data.get("type")
            if etype not in edge_type_set or v not in visited:
                continue
            # 无向边：按字典序固定方向，只保留一条
            if etype in UNDIRECTED_TYPES:
                key = (min(u, v), max(u, v), etype)
            else:
                key = (u, v, etype)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            edges.append({
                "type":        etype,
                "source":      u,
                "target":      v,
                "description": data.get("description", ""),
            })

    return {"nodes": nodes, "edges": edges}


def get_prereq_ancestors(
    concept_name: str,
    max_hops: int = 5,
    G: nx.MultiDiGraph | None = None,
) -> dict:
    """
    沿 PREREQUISITE 边反向追溯祖先概念（仅用于诊断溯源）。

    与 get_subgraph 的区别：只沿 PREREQUISITE 入边向上走，
    返回带层级深度的祖先列表，供 trace.py 按优先级展示。

    Args:
        concept_name: 目标概念（学生答错的知识点）
        max_hops:     最多向上追溯的层数
        G:            可选图对象

    Returns:
        {
            "nodes": [{"name": str, "depth": int, ...Concept 属性}, ...],
            "edges": [{"source": str, "target": str, "type": "PREREQUISITE"}, ...]
        }
        depth=0 为目标概念本身，depth=1 为直接先修，以此类推。
    """
    if G is None:
        G = get_graph()

    visited: dict[str, int] = {}   # name → depth
    queue: list[tuple[str, int]] = [(concept_name, 0)]

    while queue:
        node, depth = queue.pop(0)
        if node in visited or depth > max_hops:
            continue
        visited[node] = depth
        if depth < max_hops:
            for u, _, data in G.in_edges(node, data=True):
                if data.get("type") == "PREREQUISITE" and u not in visited:
                    queue.append((u, depth + 1))

    nodes: list[dict] = []
    for n, depth in visited.items():
        attr = G.nodes.get(n, {})
        if attr.get("label") != "Concept":
            continue
        nodes.append({
            "name":       n,
            "depth":      depth,
            "node_role":  attr.get("node_role",  "概念"),
            "difficulty": attr.get("difficulty", 1),
            "definition": attr.get("definition", ""),
            "chapter":    attr.get("chapter",    ""),
        })

    # 只保留 visited 内部的 PREREQUISITE 边
    edges: list[dict] = []
    for n in visited:
        for u, _, data in G.in_edges(n, data=True):
            if data.get("type") == "PREREQUISITE" and u in visited:
                edges.append({"source": u, "target": n, "type": "PREREQUISITE"})

    return {"nodes": nodes, "edges": edges}
