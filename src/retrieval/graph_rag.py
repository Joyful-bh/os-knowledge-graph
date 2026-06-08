"""
KG 增强 RAG —— 向量粗召回实体后,用子图扩展上下文再交给 LLM 生成。
依赖 vector_rag.query() 和 subgraph.get_subgraph()。
Phase 2 实现。
"""

from src.retrieval import subgraph, vector_rag  # noqa: F401
from src import llm_client  # noqa: F401


def answer(question: str, top_k: int = 3, hops: int = 2) -> str:
    """
    用 GraphRAG 回答问题。
    流程:向量检索 top_k 概念 → 子图召回 N 跳邻域 → 拼接上下文 → LLM 生成。

    Args:
        question: 用户问题
        top_k: 向量检索返回的概念数
        hops: 子图扩展跳数

    Returns:
        LLM 生成的答案文本
    """
    raise NotImplementedError("Phase 2 实现")
