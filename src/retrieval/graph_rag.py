"""
KG 增强 RAG —— 向量粗召回实体后，用子图扩展上下文再交给 LLM 生成。

完整流程：
用户问题 -> vector_rag.query() 找到规范 Concept 名称
        -> subgraph.get_subgraph() 扩展知识图谱邻域（节点定义 + 边描述）
        -> chunk_store.retrieve_chunks_by_concepts() 反查原文片段（细节补充）
        -> 三段式上下文（概念 / 关系 / 原文）交给 LLM 生成答案

设计取舍：
- 概念定义提供"是什么"的结构化骨架（短、准、稳）
- 边描述提供"概念间如何关联"的图谱推理路径（多跳依据）
- 原文片段提供"如何实现/算法细节"的丰富语料（补充图谱的不足）

上下文预算（参考 LightRAG 三段独立预算的思路）：
- 概念节点：子图返回的全部节点定义（每个 ~30 字，可控）
- 边：子图返回的全部边描述（每条 ~50 字，可控）
- 原文：最多 4 个 chunks × 600 字 = 2400 字
"""

from src import llm_client
from src.retrieval import vector_rag
from src.retrieval.chunk_store import format_chunks_for_prompt, retrieve_chunks_by_concepts
from src.retrieval.subgraph import get_subgraph


EDGE_TYPES = ["PREREQUISITE", "RELATED", "SOLVES", "CONFUSABLE", "PART_OF"]
DEFAULT_MAX_CHUNKS = 4          # 原文片段最多取几段
DEFAULT_MAX_CHUNK_CHARS = 600   # 单段原文截断字符数


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


def answer(
    question: str,
    top_k: int = 3,
    hops: int = 2,
    use_chunks: bool = True,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
) -> str:
    """
    用 GraphRAG 回答问题。

    流程：
    1. 向量检索得到规范 Concept 名称；
    2. 调用 get_subgraph() 扩展知识图谱邻域；
    3. （可选）按子图节点反查原文 chunks，补充实现细节；
    4. 拼接三段式上下文交给 LLM。

    Args:
        question:        用户问题
        top_k:           向量检索返回的种子概念数
        hops:            子图扩展跳数
        use_chunks:      是否启用原文 chunk 召回（默认 True）
        max_chunks:      最多召回的 chunk 数（默认 4）
        max_chunk_chars: 单 chunk 截断字符数（默认 600）

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

    # ── 原文片段召回（可选）─────────────────────────────────────────────
    chunk_context = ""
    if use_chunks:
        # 用子图全部节点反查 chunks，命中越多越优先
        node_names = [n.get("name", "") for n in subgraph_result.get("nodes", [])]
        chunks = retrieve_chunks_by_concepts(
            concept_names=node_names or seed_names,
            max_chunks=max_chunks,
            max_chunk_chars=max_chunk_chars,
        )
        chunk_context = format_chunks_for_prompt(chunks)

    # ── 拼接 prompt：三段式上下文 ────────────────────────────────────────
    chunk_block = ""
    if use_chunks:
        chunk_block = f"""

【教材原文片段】（用于补充图谱中缺失的实现细节）
{chunk_context}"""

    prompt = f"""
请基于下面给定的操作系统课程上下文回答问题。
上下文包括三类信息：图谱概念定义、图谱关系、教材原文片段。
- 优先用「图谱概念」和「图谱关系」搭建答案的逻辑骨架（"是什么"、"为什么"、"如何关联"）
- 用「教材原文片段」补充具体的实现细节、算法步骤、公式或例子
- 不要使用上下文之外的知识；上下文不足时必须明确说"图谱与原文均没有足够依据"

回答结构必须使用以下三段：
1. 直接回答
2. 图谱依据（引用相关概念名和关系）
3. 补充说明（如果原文片段有进一步的细节，可在此简述）

【用户问题】
{question}

【向量检索得到的种子概念】
{", ".join(seed_names) if seed_names else "无"}

【图谱上下文】
{graph_context}{chunk_block}
""".strip()

    system = (
        "你是操作系统课程的知识图谱问答助手，必须忠实依据给定上下文回答；"
        "图谱负责「是什么/为什么」，原文片段负责「如何实现」，二者结合作答。"
    )
    return llm_client.call(prompt, system=system, temperature=0.2)
