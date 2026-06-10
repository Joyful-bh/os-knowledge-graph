"""
掌握度模型 —— 基于错题诊断结果估计概念掌握概率。

本模块不调用 LLM，只消费 diagnosis.trace 的诊断输出：
- trace_wrong_problems(pids)
- get_tested_concepts(pid)

规则设计保持可解释，便于课程答辩说明：
错得越直接、距离考查概念越近，薄弱分 weakness 越高；
做对某题会削弱其直接考查概念的 weakness。
"""

from collections import defaultdict
from typing import Any

from src.diagnosis.trace import get_tested_concepts, trace_wrong_problems
from src.kg.load import get_graph


_DEPTH_WEIGHTS = {
    0: 1.0,  # 错题直接考查概念
    1: 0.7,  # 直接先修
    2: 0.5,  # 二层先修
}
_DEEP_PREREQ_WEIGHT = 0.3
_CORRECT_DISCOUNT = 0.5
_RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


def score_to_risk(mastery: float) -> str:
    """
    将掌握度映射为风险等级。

    mastery < 0.4        -> high
    0.4 <= mastery < 0.7 -> medium
    mastery >= 0.7       -> low
    """
    if mastery < 0.4:
        return "high"
    if mastery < 0.7:
        return "medium"
    return "low"


def estimate_mastery(
    wrong_pids: list[str],
    correct_pids: list[str] | None = None,
    max_hops: int = 5,
) -> list[dict]:
    """
    根据错题和正确题估计概念掌握度。

    Args:
        wrong_pids: 做错的题目 pid 列表。
        correct_pids: 做对的题目 pid 列表；只用于削弱对应概念的 weakness。
        max_hops: 错题先修链最大追溯层数。

    Returns:
        [
            {
                "name": 概念名,
                "mastery": 掌握度 0.0-1.0,
                "risk": "high" / "medium" / "low",
                "weakness": 薄弱分,
                "evidence_pids": 相关错题 pid,
                "evidence_target_concepts": 错题直接考查概念,
                "reason": 中文解释,
            },
            ...
        ]
    """
    correct_pids = correct_pids or []
    G = get_graph()

    # concept -> 累计诊断信息
    stats: dict[str, dict[str, Any]] = defaultdict(_new_stat)

    wrong_trace = trace_wrong_problems(wrong_pids, max_hops=max_hops, G=G)
    for candidate in wrong_trace["weak_candidates"]:
        name = candidate["name"]
        stat = stats[name]

        evidence_pids = _candidate_evidence_pids(candidate)
        evidence_targets = _candidate_evidence_targets(candidate)
        stat["weakness"] += _candidate_weakness(candidate)
        stat["evidence_pids"].update(evidence_pids)
        stat["evidence_target_concepts"].update(evidence_targets)
        stat["min_depth"] = min(stat["min_depth"], candidate.get("depth", 0))

    # 正确题只作为反证：直接考查概念做对，说明该概念的 weakness 应适度下降。
    for pid in correct_pids:
        for concept_name in get_tested_concepts(pid, G=G):
            stat = stats[concept_name]
            stat["weakness"] = max(0.0, stat["weakness"] - _CORRECT_DISCOUNT)
            stat["correct_evidence_pids"].add(pid)

    if not stats:
        return []

    max_weakness = max(stat["weakness"] for stat in stats.values())
    if max_weakness <= 0:
        max_weakness = 1.0

    results: list[dict] = []
    for name, stat in stats.items():
        weakness = round(stat["weakness"], 4)
        mastery = max(0.0, min(1.0, 1 - weakness / max_weakness))
        mastery = round(mastery, 4)
        risk = score_to_risk(mastery)

        results.append({
            "name": name,
            "mastery": mastery,
            "risk": risk,
            "weakness": weakness,
            "evidence_pids": sorted(stat["evidence_pids"]),
            "evidence_target_concepts": sorted(stat["evidence_target_concepts"]),
            "reason": _build_reason(
                name=name,
                risk=risk,
                weakness=weakness,
                evidence_pids=sorted(stat["evidence_pids"]),
                evidence_target_concepts=sorted(stat["evidence_target_concepts"]),
                correct_evidence_pids=sorted(stat["correct_evidence_pids"]),
            ),
        })

    return sorted(
        results,
        key=lambda item: (
            _RISK_ORDER[item["risk"]],
            -item["weakness"],
            item["mastery"],
            item["name"],
        ),
    )


def get_weak_concepts(mastery: list[dict] | dict[str, float], threshold: float = 0.6) -> list[str]:
    """
    返回掌握度低于阈值的概念列表。

    兼容两种输入：
    - 新版 estimate_mastery() 的 list[dict]
    - 旧版 {"概念名": 掌握概率} 字典
    """
    if isinstance(mastery, dict):
        return [name for name, prob in mastery.items() if prob < threshold]

    return [
        item["name"]
        for item in mastery
        if item.get("mastery", 1.0) < threshold
    ]


def _new_stat() -> dict[str, Any]:
    """创建单个概念的累计统计容器。"""
    return {
        "weakness": 0.0,
        "evidence_pids": set(),
        "evidence_target_concepts": set(),
        "correct_evidence_pids": set(),
        "min_depth": 999,
    }


def _weakness_weight(depth: int) -> float:
    """按先修链深度返回错题造成的 weakness 增量。"""
    return _DEPTH_WEIGHTS.get(depth, _DEEP_PREREQ_WEIGHT)


def _candidate_weakness(candidate: dict) -> float:
    """
    计算一个 trace 候选项贡献的 weakness。

    trace_wrong_problems() 会把同名概念合并到一条 candidate 中，
    因此这里优先按 evidences 中的每条证据分别累加。
    """
    evidences = candidate.get("evidences")
    if evidences:
        return sum(_weakness_weight(e.get("depth", 0)) for e in evidences)

    return _weakness_weight(candidate.get("depth", 0))


def _candidate_evidence_pids(candidate: dict) -> set[str]:
    """从 trace.py 的候选项中提取错题证据 pid。"""
    evidences = candidate.get("evidences")
    if evidences:
        return {e["pid"] for e in evidences if e.get("pid")}

    pid = candidate.get("evidence_pid")
    return {pid} if pid else set()


def _candidate_evidence_targets(candidate: dict) -> set[str]:
    """从 trace.py 的候选项中提取被错题直接考查的概念。"""
    evidences = candidate.get("evidences")
    if evidences:
        return {
            e["target_concept"]
            for e in evidences
            if e.get("target_concept")
        }

    target = candidate.get("evidence_target_concept")
    return {target} if target else set()


def _build_reason(
    name: str,
    risk: str,
    weakness: float,
    evidence_pids: list[str],
    evidence_target_concepts: list[str],
    correct_evidence_pids: list[str],
) -> str:
    """生成可解释的中文诊断理由。"""
    if evidence_pids:
        wrong_part = (
            f"概念「{name}」由错题 {', '.join(evidence_pids)} 命中，"
            f"关联考查概念包括「{'、'.join(evidence_target_concepts)}」"
        )
    else:
        wrong_part = f"概念「{name}」暂无错题命中"

    correct_part = ""
    if correct_evidence_pids:
        correct_part = (
            f"；但正确题 {', '.join(correct_evidence_pids)} "
            "提供了反证，因此已适当下调薄弱分"
        )

    return f"{wrong_part}，weakness={weakness}，风险等级为 {risk}{correct_part}。"


if __name__ == "__main__":
    # 简单 smoke test：CH1_Q2 有直接考查概念，也能触发部分先修概念。
    demo = estimate_mastery(
        wrong_pids=["CH1_Q2"],
        correct_pids=["CH1_Q1"],
        max_hops=3,
    )

    print("掌握度诊断 Top 10:")
    for item in demo[:10]:
        print(
            f"- {item['name']}: mastery={item['mastery']}, "
            f"risk={item['risk']}, weakness={item['weakness']}"
        )
        print(f"  {item['reason']}")
