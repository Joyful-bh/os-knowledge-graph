"""
对比实验 —— 向量 RAG vs GraphRAG 在评测问题上的准确率对比。

核心对照：
- 向量 RAG 只能看到向量检索出的概念 name + definition；
- GraphRAG 可以在向量检索种子概念基础上扩展知识图谱子图。

该实验用于答辩中说明：当问题需要关系和中间节点时，图谱上下文是否带来增益。
"""

from __future__ import annotations

import json
from pathlib import Path

from src import llm_client
from src.retrieval import graph_rag, vector_rag


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS_PATH = PROJECT_ROOT / "data" / "multihop_questions.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "phase2_compare_results.json"
DEFAULT_MAIN_LIMIT = 75


def _format_vector_context(hits: list[dict]) -> str:
    """将向量召回结果整理成只含概念定义的上下文。"""
    if not hits:
        return "无"

    lines: list[str] = []
    for item in hits:
        lines.append(
            f"- 名称：{item.get('name', '')}；定义：{item.get('definition', '')}"
        )
    return "\n".join(lines)


def _answer_with_vector_rag(question: str) -> str:
    """
    普通向量 RAG 基线。

    注意：这里故意只使用 Concept 的 name + definition，不使用边、不使用 path，
    这样才能和 GraphRAG 的图结构上下文形成公平对比。
    """
    hits = vector_rag.query(question, top_k=5)
    context = _format_vector_context(hits)

    prompt = f"""
请只基于下面给出的概念定义回答操作系统课程问题。
如果这些概念定义不足以支持回答，请明确说明“给定概念定义中没有足够依据”。

【用户问题】
{question}

【向量检索概念上下文】
{context}
""".strip()

    system = "你是操作系统课程的向量 RAG 问答助手，只能依据给定概念定义回答。"
    return llm_client.call(prompt, system=system, temperature=0.2)


def _judge_answer_detail(predicted: str, ground_truth: str) -> dict:
    """
    用 LLM 判断预测答案是否覆盖标准答案的关键语义。

    返回 dict 是为了 run_comparison 记录 reason；公开函数 judge_answer 仍返回 bool。
    """
    prompt = f"""
请判断“模型回答”与“标准答案”在语义上是否一致。

判断标准：
- 不要求逐字一致；
- 只要模型回答覆盖了标准答案中的关键概念、关键中间节点或关键因果/先修关系，就判为 correct=true；
- 如果模型回答遗漏关键中间节点、关系方向错误、或只给出泛泛解释，则判为 correct=false；
- 只输出 JSON，不要添加 markdown。

请严格返回：
{{"correct": true/false, "reason": "..."}}

【标准答案】
{ground_truth}

【模型回答】
{predicted}
""".strip()

    result = llm_client.call_json(prompt, temperature=0.0)
    if not isinstance(result, dict):
        return {"correct": False, "reason": "评判模型未返回 JSON 对象"}

    return {
        "correct": bool(result.get("correct", False)),
        "reason": str(result.get("reason", "")),
    }


def judge_answer(predicted: str, ground_truth: str) -> bool:
    """
    用 LLM 判断预测答案是否与标准答案等价。
    避免字符串精确匹配带来的误差。
    """
    return _judge_answer_detail(predicted, ground_truth)["correct"]


def _evaluate_one_method(
    questions: list[dict],
    method_name: str,
    answer_func,
) -> dict:
    """对单个方法逐题生成答案、评判并计算准确率。"""
    details: list[dict] = []

    for idx, item in enumerate(questions, start=1):
        question = item["question"]
        ground_truth = item["answer"]

        print(f"[{method_name}] {idx}/{len(questions)} {question}")
        predicted = answer_func(question)
        judge = _judge_answer_detail(predicted, ground_truth)

        details.append({
            "question": question,
            "ground_truth": ground_truth,
            "predicted": predicted,
            "correct": judge["correct"],
            "reason": judge["reason"],
        })

    correct_count = sum(1 for item in details if item["correct"])
    accuracy = correct_count / len(details) if details else 0.0

    return {
        "accuracy": accuracy,
        "details": details,
    }


def run_comparison(questions: list[dict]) -> dict:
    """
    分别用向量 RAG 和 GraphRAG 回答评测问题，输出对比报告。

    Args:
        questions: multihop_set.build_multihop_questions() 的输出

    Returns:
        {
            "vector_rag": {"accuracy": float, "details": [...]},
            "graph_rag":  {"accuracy": float, "details": [...]},
        }
    """
    vector_result = _evaluate_one_method(
        questions,
        method_name="vector_rag",
        answer_func=_answer_with_vector_rag,
    )
    graph_result = _evaluate_one_method(
        questions,
        method_name="graph_rag",
        answer_func=lambda question: graph_rag.answer(question, top_k=3, hops=2),
    )

    return {
        "vector_rag": vector_result,
        "graph_rag": graph_result,
    }


def _load_questions(path: Path = DEFAULT_QUESTIONS_PATH) -> list[dict]:
    """读取已生成的评测集。"""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_results(result: dict, path: Path = DEFAULT_OUTPUT_PATH) -> None:
    """保存对比实验结果。"""
    with path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    questions = _load_questions()
    questions = questions[:DEFAULT_MAIN_LIMIT]  # 评测问题较多时，可以先对前 N 条进行快速对比实验

    print(f"读取 {len(questions)} 条评测问题进行快速对比实验")
    result = run_comparison(questions)
    _save_results(result)

    print("\n实验结果：")
    print(f"vector_rag accuracy: {result['vector_rag']['accuracy']:.2%}")
    print(f"graph_rag accuracy:  {result['graph_rag']['accuracy']:.2%}")
    print(f"结果已保存到 {DEFAULT_OUTPUT_PATH}")
