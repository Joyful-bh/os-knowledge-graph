"""
学习路径规划 —— 基于 PREREQUISITE DAG 的拓扑约束搜索。
输入目标概念集和当前掌握度,输出最优学习顺序。
Phase 3 实现。
"""


def plan_path(
    target_concepts: list[str],
    mastery: dict[str, float],
    driver=None,
) -> list[str]:
    """
    给定目标概念和当前掌握度,输出最优学习路径(拓扑序)。

    策略:拓扑排序 PREREQUISITE 子图 → 过滤已掌握节点 → 按难度微调顺序。

    Args:
        target_concepts: 最终要学会的概念名称列表
        mastery: 当前掌握度 {"概念名": 0.0–1.0}(未出现则视为 0)
        driver: Neo4j 驱动

    Returns:
        建议学习顺序(已掌握概念可跳过或标注)
    """
    raise NotImplementedError("Phase 3 实现")
