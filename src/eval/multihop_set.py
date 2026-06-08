"""
多跳评测集构建 —— 基于图谱结构自动生成需要多跳推理的问题。
典型问题模板:"A 的先修 B 解决什么问题?" / "为什么需要 X?"
Phase 2 实现。
"""


def build_multihop_questions(driver) -> list[dict]:
    """
    遍历图谱中的多跳路径,自动生成评测问题。

    Returns:
        [{"question": str, "answer": str, "hops": int, "path": [str]}, ...]
    """
    raise NotImplementedError("Phase 2 实现")


def load_questions(path: str = "data/multihop_questions.json") -> list[dict]:
    """从文件加载已生成的评测集。"""
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)
