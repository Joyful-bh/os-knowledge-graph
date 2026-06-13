"""
KG 增强 RAG —— 向量粗召回实体后，用子图扩展上下文再交给 LLM 生成。

Phase 2 的 GraphRAG 流程：
用户问题 -> vector_rag.query() 找到规范 Concept 名称
        -> subgraph.get_subgraph() 扩展知识图谱邻域
        -> 将节点与边整理成中文上下文
        -> llm_client.call() 基于上下文生成答案
"""

from src import llm_client
from src.retrieval import vector_rag
from src.retrieval.subgraph import get_subgraph


EDGE_TYPES = ["PREREQUISITE", "RELATED", "SOLVES", "CONFUSABLE", "PART_OF"]


def format_graph_context(subgraph_result: dict) -> str:
    """
    将 get_subgraph() 返回的节点和边整理为 LLM 易读的中文文本。

    Args:
        subgraph_result: get_subgraph() 的返回结果，格式见 docs/collaboration_contract.md

    Returns:
        包含“概念部分”和“关系部分”的中文上下文字符串。
    """
    nodes = subgraph_result.get("nodes", [])
    edges = subgraph_result.get("edges", [])

    lines: list[str] = ["【概念部分】"]
    if not nodes:
        lines.append("无")
    else:
        for node in nodes:
            lines.append(
                "- "
                f"名称：{node.get('name', '')}；"
                f"角色：{node.get('node_role', '')}；"
                f"难度：{node.get('difficulty', '')}；"
                f"章节：{node.get('chapter', '')}；"
                f"定义：{node.get('definition', '')}"
            )

    lines.append("")
    lines.append("【关系部分】")
    if not edges:
        lines.append("无")
    else:
        for edge in edges:
            lines.append(
                "- "
                f"{edge.get('source', '')} "
                f"-[{edge.get('type', '')}]-> "
                f"{edge.get('target', '')}："
                f"{edge.get('description', '')}"
            )

    return "\n".join(lines)


def answer(question: str, top_k: int = 3, hops: int = 2) -> str:
    """
    用 GraphRAG 回答问题。

    流程：
    1. 向量检索得到规范 Concept 名称；
    2. 调用 A 同学实现的 get_subgraph() 扩展图谱上下文；
    3. 要求 LLM 只依据图谱上下文回答。

    Args:
        question: 用户问题
        top_k: 向量检索返回的种子概念数
        hops: 子图扩展跳数

    Returns:
        LLM 生成的答案文本
    """
    seed_results = vector_rag.query(question, top_k=top_k)
    seed_names = [item["name"] for item in seed_results if item.get("name")]

    if not seed_names:
        return "未能从向量索引中检索到相关概念，无法进行图谱增强回答。"

    subgraph_result = get_subgraph(
        concept_names=seed_names,
        edge_types=EDGE_TYPES,
        hops=hops,
    )
    graph_context = format_graph_context(subgraph_result)

    prompt = f"""
请只基于下面给定的操作系统知识图谱上下文回答问题。
不要使用上下文之外的知识补全关系、定义或结论。
如果图谱上下文不足以支持回答，必须明确说“图谱中没有足够依据”。

回答结构必须使用以下三段：
1. 直接回答
2. 图谱依据
3. 补充说明

【用户问题】
{question}

【向量检索得到的种子概念】
{", ".join(seed_names) if seed_names else "无"}

【图谱上下文】
{graph_context}
""".strip()

    system = "你是操作系统课程的知识图谱问答助手，必须忠实依据给定图谱上下文回答。"
    return llm_client.call(prompt, system=system, temperature=0.2)
