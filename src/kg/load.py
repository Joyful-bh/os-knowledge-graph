"""
图数据加载 —— 默认 NetworkX，懒加载 Neo4j。

用法:
    from src.kg.load import get_graph
    G = get_graph()          # NetworkX MultiDiGraph，随时可用

    # 有 Neo4j 服务时写入（可选）:
    from src.kg.load import load_to_neo4j
    load_to_neo4j()
"""

import json
from collections import defaultdict
from pathlib import Path

import networkx as nx

_CONCEPTS_PATH = "data/concepts.json"
_EDGES_PATH    = "data/edges.json"
_PROBLEMS_PATH = "data/candidates/problems_raw.json"

# 无向边类型：NetworkX 里双向都加，Neo4j 里只写一个方向用无向查询
UNDIRECTED_TYPES = {"RELATED", "CONFUSABLE"}

# 模块级图单例
_graph: nx.MultiDiGraph | None = None


# ══════════════════════════════════════════════════════════════════════════════
# NetworkX 实现（默认）
# ══════════════════════════════════════════════════════════════════════════════

def get_graph(
    concepts_path: str = _CONCEPTS_PATH,
    edges_path:    str = _EDGES_PATH,
    problems_path: str = _PROBLEMS_PATH,
    reload: bool = False,
) -> nx.MultiDiGraph:
    """
    获取 NetworkX 图。首次调用时从 JSON 加载；后续返回缓存。
    reload=True 强制重新加载（数据文件更新后使用）。
    """
    global _graph
    if _graph is None or reload:
        _graph = _build_graph(concepts_path, edges_path, problems_path)
    return _graph


def _build_graph(
    concepts_path: str,
    edges_path:    str,
    problems_path: str,
) -> nx.MultiDiGraph:
    """从 JSON 构建 NetworkX MultiDiGraph。"""
    G = nx.MultiDiGraph()

    # ── Concept 节点 ──────────────────────────────────────────────────────────
    concepts = json.loads(Path(concepts_path).read_text(encoding="utf-8"))
    for c in concepts:
        G.add_node(c["name"], label="Concept",
                   **{k: v for k, v in c.items() if k != "name"})

    # ── Problem 节点 ──────────────────────────────────────────────────────────
    prob_path = Path(problems_path)
    if prob_path.exists():
        problems = json.loads(prob_path.read_text(encoding="utf-8"))
        for p in problems:
            G.add_node(p["pid"], label="Problem",
                       **{k: v for k, v in p.items() if k != "pid"})

    # ── 边 ────────────────────────────────────────────────────────────────────
    edges = json.loads(Path(edges_path).read_text(encoding="utf-8"))
    for e in edges:
        src, tgt, etype = e["source"], e["target"], e["type"]
        attrs = {k: v for k, v in e.items() if k not in ("source", "target", "type")}
        G.add_edge(src, tgt, type=etype, **attrs)
        if etype in UNDIRECTED_TYPES:          # 无向边补充反向
            G.add_edge(tgt, src, type=etype, **attrs)

    n_concept = sum(1 for _, d in G.nodes(data=True) if d.get("label") == "Concept")
    n_problem = sum(1 for _, d in G.nodes(data=True) if d.get("label") == "Problem")
    print(f"[load] NetworkX 图构建完毕：{n_concept} Concept + {n_problem} Problem 节点，"
          f"{G.number_of_edges()} 条边（含无向反向）")
    return G


# ══════════════════════════════════════════════════════════════════════════════
# Neo4j 懒加载实现
# ══════════════════════════════════════════════════════════════════════════════

def _get_neo4j_driver():
    """
    懒加载 Neo4j 驱动。
    未安装 neo4j 包、.env 缺配置、或服务未启动时，均抛出 RuntimeError。
    """
    try:
        import os
        from dotenv import load_dotenv
        load_dotenv()
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        )
        driver.verify_connectivity()
        return driver
    except ImportError:
        raise RuntimeError("neo4j 包未安装，请运行: pip install neo4j")
    except KeyError as e:
        raise RuntimeError(f".env 缺少配置项: {e}")
    except Exception as e:
        raise RuntimeError(f"Neo4j 连接失败: {e}")


def load_to_neo4j(
    concepts_path: str = _CONCEPTS_PATH,
    edges_path:    str = _EDGES_PATH,
    problems_path: str = _PROBLEMS_PATH,
    driver=None,
    batch_size: int = 500,
) -> None:
    """
    将概念、边、习题写入 Neo4j（MERGE 幂等，可重复运行）。
    写入完成后自动触发 PREREQUISITE 环检测。
    需要 Neo4j 服务在线且 .env 中配置 NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD。
    """
    if driver is None:
        driver = _get_neo4j_driver()

    concepts = json.loads(Path(concepts_path).read_text(encoding="utf-8"))
    edges    = json.loads(Path(edges_path).read_text(encoding="utf-8"))
    prob_path = Path(problems_path)
    problems = json.loads(prob_path.read_text(encoding="utf-8")) if prob_path.exists() else []

    print("[load→Neo4j] 创建索引…")
    with driver.session() as s:
        s.run("CREATE INDEX concept_name IF NOT EXISTS FOR (n:Concept) ON (n.name)")
        s.run("CREATE INDEX problem_pid  IF NOT EXISTS FOR (p:Problem) ON (p.pid)")

    _neo4j_load_concepts(driver, concepts, batch_size)
    _neo4j_load_problems(driver, problems, batch_size)
    _neo4j_load_edges(driver, edges, batch_size)

    # 写入 PREREQUISITE 后立即检环
    from src.kg.validate import check_no_cycles_neo4j
    ok = check_no_cycles_neo4j(driver)
    if not ok:
        print("⚠ 检测到 PREREQUISITE 环，请人工排查后重新写入。")


def _neo4j_load_concepts(driver, concepts: list[dict], batch_size: int) -> None:
    query = """
    UNWIND $batch AS c
    MERGE (n:Concept {name: c.name})
    SET n.definition = c.definition,
        n.chapter    = c.chapter,
        n.difficulty = c.difficulty,
        n.node_role  = c.node_role,
        n.aliases    = c.aliases
    """
    _neo4j_batch(driver, query, concepts, batch_size)
    print(f"  ✓ Concept 节点：{len(concepts)} 个")


def _neo4j_load_problems(driver, problems: list[dict], batch_size: int) -> None:
    if not problems:
        return
    query = """
    UNWIND $batch AS p
    MERGE (n:Problem {pid: p.pid})
    SET n.stem    = p.stem,
        n.answer  = p.answer,
        n.type    = p.type,
        n.chapter = p.chapter
    """
    _neo4j_batch(driver, query, problems, batch_size)
    print(f"  ✓ Problem 节点：{len(problems)} 个")


def _neo4j_load_edges(driver, edges: list[dict], batch_size: int) -> None:
    # Concept→Concept 有向边模板
    _CC_DIRECTED = {
        "PREREQUISITE": (
            "UNWIND $batch AS e "
            "MATCH (a:Concept {name: e.source}), (b:Concept {name: e.target}) "
            "MERGE (a)-[r:PREREQUISITE]->(b) "
            "SET r.description = e.description, r.reviewed = e.reviewed"
        ),
        "PART_OF": (
            "UNWIND $batch AS e "
            "MATCH (a:Concept {name: e.source}), (b:Concept {name: e.target}) "
            "MERGE (a)-[r:PART_OF]->(b) SET r.description = e.description"
        ),
        "SOLVES": (
            "UNWIND $batch AS e "
            "MATCH (a:Concept {name: e.source}), (b:Concept {name: e.target}) "
            "MERGE (a)-[r:SOLVES]->(b) SET r.description = e.description"
        ),
    }
    # Concept—Concept 无向边（只写一个方向，查询用无向模式）
    _CC_UNDIRECTED = {
        "RELATED": (
            "UNWIND $batch AS e "
            "MATCH (a:Concept {name: e.source}), (b:Concept {name: e.target}) "
            "MERGE (a)-[r:RELATED]-(b) SET r.description = e.description"
        ),
        "CONFUSABLE": (
            "UNWIND $batch AS e "
            "MATCH (a:Concept {name: e.source}), (b:Concept {name: e.target}) "
            "MERGE (a)-[r:CONFUSABLE]-(b) "
            "SET r.description = e.description, r.dimensions = e.dimensions"
        ),
    }
    # Problem→Concept
    _PC = {
        "TESTS": (
            "UNWIND $batch AS e "
            "MATCH (p:Problem {pid: e.source}), (c:Concept {name: e.target}) "
            "MERGE (p)-[r:TESTS]->(c) SET r.description = e.description"
        ),
    }
    all_templates = {**_CC_DIRECTED, **_CC_UNDIRECTED, **_PC}

    by_type: dict[str, list] = defaultdict(list)
    for e in edges:
        by_type[e["type"]].append(e)

    for etype, tmpl in all_templates.items():
        batch = by_type.get(etype, [])
        if not batch:
            continue
        _neo4j_batch(driver, tmpl, batch, batch_size)
        print(f"  ✓ {etype} 边：{len(batch)} 条")


def _neo4j_batch(driver, query: str, data: list, batch_size: int) -> None:
    """将 data 按 batch_size 分批提交。"""
    with driver.session() as s:
        for i in range(0, len(data), batch_size):
            s.run(query, {"batch": data[i: i + batch_size]})
