"""
图谱质量自检 —— 环检测、孤立节点、PREREQUISITE 覆盖率。
默认基于 NetworkX；Neo4j 版本懒加载（函数名后缀 _neo4j）。

用法:
    from src.kg.validate import run_all_checks
    run_all_checks()          # 使用默认 NetworkX 图
"""

import networkx as nx


# ══════════════════════════════════════════════════════════════════════════════
# NetworkX 实现（默认）
# ══════════════════════════════════════════════════════════════════════════════

def check_no_cycles(G: nx.MultiDiGraph | None = None) -> bool:
    """
    检测 PREREQUISITE 边是否构成环（DAG 约束）。
    有环则打印环路并返回 False；无环返回 True。
    """
    if G is None:
        from src.kg.load import get_graph
        G = get_graph()

    prereq_edges = [
        (u, v) for u, v, d in G.edges(data=True)
        if d.get("type") == "PREREQUISITE"
    ]
    sub = nx.DiGraph(prereq_edges)

    try:
        cycle = nx.find_cycle(sub, orientation="original")
        print("⚠ 发现 PREREQUISITE 环：")
        for u, v, _ in cycle:
            print(f"  {u} → {v}")
        return False
    except nx.NetworkXNoCycle:
        print(f"✓ PREREQUISITE DAG 无环"
              f"（{sub.number_of_nodes()} 节点，{sub.number_of_edges()} 条边）")
        return True


def check_isolated_nodes(G: nx.MultiDiGraph | None = None) -> list[str]:
    """
    找出无任何边的孤立 Concept 节点。
    孤立节点通常是抽取遗漏或归一化错误的信号。
    """
    if G is None:
        from src.kg.load import get_graph
        G = get_graph()

    isolated = [
        n for n in G.nodes
        if G.nodes[n].get("label") == "Concept" and G.degree(n) == 0
    ]
    print(f"孤立 Concept 节点：{len(isolated)} 个")
    return isolated


def check_coverage(G: nx.MultiDiGraph | None = None) -> dict[str, dict]:
    """
    按章节统计 PREREQUISITE 覆盖情况。

    Returns:
        {"章节名": {"count": int, "with_prereq": int}, ...}
    """
    if G is None:
        from src.kg.load import get_graph
        G = get_graph()

    # 有至少一条 PREREQUISITE 入边或出边的概念
    prereq_nodes: set[str] = set()
    for u, v, d in G.edges(data=True):
        if d.get("type") == "PREREQUISITE":
            prereq_nodes.add(u)
            prereq_nodes.add(v)

    coverage: dict[str, dict] = {}
    for n, attr in G.nodes(data=True):
        if attr.get("label") != "Concept":
            continue
        ch = attr.get("chapter", "未知")
        if ch not in coverage:
            coverage[ch] = {"count": 0, "with_prereq": 0}
        coverage[ch]["count"] += 1
        if n in prereq_nodes:
            coverage[ch]["with_prereq"] += 1
    return coverage


def run_all_checks(G: nx.MultiDiGraph | None = None) -> None:
    """运行全套自检并打印报告。入库或数据更新后调用一次。"""
    if G is None:
        from src.kg.load import get_graph
        G = get_graph()

    n_concept = sum(1 for _, d in G.nodes(data=True) if d.get("label") == "Concept")
    n_problem = sum(1 for _, d in G.nodes(data=True) if d.get("label") == "Problem")
    # 边去重：不含无向边的反向副本
    n_edges = sum(
        1 for u, v, d in G.edges(data=True)
        if d.get("type") not in ("RELATED", "CONFUSABLE") or u <= v
    )
    print(f"[validate] 节点：{n_concept} Concept + {n_problem} Problem")
    print(f"[validate] 边（去重）：{n_edges} 条")
    print()

    # 环检测
    ok = check_no_cycles(G)
    print()

    # 孤立节点
    isolated = check_isolated_nodes(G)
    if isolated:
        print(f"  示例：{isolated[:5]}")
    print()

    # 覆盖率
    coverage = check_coverage(G)
    total   = sum(v["count"]      for v in coverage.values())
    covered = sum(v["with_prereq"] for v in coverage.values())
    pct = covered / total * 100 if total else 0
    print(f"PREREQUISITE 覆盖率：{covered}/{total} = {pct:.1f}%")
    for ch, stat in sorted(coverage.items()):
        c_pct = stat["with_prereq"] / stat["count"] * 100 if stat["count"] else 0
        bar = "█" * int(c_pct / 5)
        print(f"  {ch:<30s} {stat['with_prereq']:3d}/{stat['count']:3d}  {c_pct:4.0f}%  {bar}")

    return ok


# ══════════════════════════════════════════════════════════════════════════════
# Neo4j 版本（懒加载）
# ══════════════════════════════════════════════════════════════════════════════

def check_no_cycles_neo4j(driver) -> bool:
    """
    Neo4j 版 PREREQUISITE 环检测。
    使用 Cypher 变长路径匹配：若存在从节点出发经 PREREQUISITE 回到自身的路径即为环。
    """
    query = """
    MATCH path = (a:Concept)-[:PREREQUISITE*2..]->(a)
    RETURN [n IN nodes(path) | n.name] AS cycle
    LIMIT 1
    """
    with driver.session() as s:
        result = s.run(query).single()
    if result:
        print(f"⚠ Neo4j 发现 PREREQUISITE 环：{result['cycle']}")
        return False
    print("✓ Neo4j: PREREQUISITE DAG 无环")
    return True


def check_coverage_neo4j(driver) -> None:
    """Neo4j 版覆盖率统计，打印各章节 PREREQUISITE 覆盖情况。"""
    query = """
    MATCH (c:Concept)
    OPTIONAL MATCH (c)-[:PREREQUISITE]-()
    WITH c.chapter AS ch,
         count(c) AS total,
         count(CASE WHEN (c)-[:PREREQUISITE]-() THEN 1 END) AS covered
    RETURN ch, total, covered
    ORDER BY ch
    """
    with driver.session() as s:
        rows = s.run(query).data()
    total_all   = sum(r["total"]   for r in rows)
    covered_all = sum(r["covered"] for r in rows)
    print(f"PREREQUISITE 覆盖率（Neo4j）：{covered_all}/{total_all} "
          f"= {covered_all/total_all*100:.1f}%")
    for r in rows:
        pct = r["covered"] / r["total"] * 100 if r["total"] else 0
        print(f"  {r['ch']:<30s} {r['covered']:3d}/{r['total']:3d}  {pct:4.0f}%")
