"""
掌握度模型 —— 基于答题记录估计各概念的掌握概率。
采用简化版 DINA 模型:利用 TESTS 边确定习题的知识点向量(Q-matrix)。
Phase 3 实现。
"""


def estimate_mastery(answered: list[dict], driver=None) -> dict[str, float]:
    """
    根据学生答题记录估计各概念掌握度。

    Args:
        answered: [{"pid": str, "correct": bool}, ...]
        driver: Neo4j 驱动

    Returns:
        {"概念名": 掌握概率(0.0–1.0), ...}
    """
    raise NotImplementedError("Phase 3 实现")


def get_weak_concepts(mastery: dict[str, float], threshold: float = 0.6) -> list[str]:
    """
    返回掌握度低于阈值的概念列表(即诊断出的薄弱点)。

    Args:
        mastery: estimate_mastery() 的输出
        threshold: 掌握度阈值,低于此值视为薄弱
    """
    return [name for name, prob in mastery.items() if prob < threshold]
