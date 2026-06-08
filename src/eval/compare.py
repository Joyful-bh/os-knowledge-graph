"""
对比实验 —— 向量 RAG vs GraphRAG 在多跳问题上的准确率对比。
这是答辩核心叙事的实验依据:展示 KG 的多跳推理增益。
Phase 2 实现。
"""

from src.retrieval import vector_rag, graph_rag  # noqa: F401


def run_comparison(questions: list[dict]) -> dict:
    """
    分别用向量 RAG 和 GraphRAG 回答多跳问题,输出对比报告。

    Args:
        questions: multihop_set.build_multihop_questions() 的输出

    Returns:
        {
            "vector_rag": {"accuracy": float, "details": [...]},
            "graph_rag":  {"accuracy": float, "details": [...]},
        }
    """
    raise NotImplementedError("Phase 2 实现")


def judge_answer(predicted: str, ground_truth: str) -> bool:
    """
    用 LLM 判断预测答案是否与标准答案等价。
    避免字符串精确匹配带来的误差。
    """
    raise NotImplementedError("Phase 2 实现")
