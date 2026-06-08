"""
向量 RAG 基线 —— sentence-transformers 编码 + chromadb 向量检索。
Phase 2 实现。
"""


def build_index(concepts: list[dict]) -> None:
    """
    构建/更新向量索引。将所有概念的 name + definition 编码后存入 chromadb。

    Args:
        concepts: Concept 节点列表(从 Neo4j 或 concepts.json 读取)
    """
    raise NotImplementedError("Phase 2 实现")


def query(question: str, top_k: int = 5) -> list[dict]:
    """
    检索与问题最相关的 top_k 个概念。

    Returns:
        [{"name": str, "definition": str, "score": float}, ...]
    """
    raise NotImplementedError("Phase 2 实现")
