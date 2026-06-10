"""
Phase 3 完整链路演示脚本。

运行示例：
    python scripts/demo_phase3.py --wrong CH3_Q2 CH4_Q7

如果不传 --wrong，脚本会自动选择前 2 个有 TESTS 边的题目作为 demo。
完整结果会保存到 data/phase3_demo_result.json。
"""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.diagnosis.mastery import estimate_mastery  # noqa: E402
from src.diagnosis.trace import get_tested_concepts, load_problems, trace_wrong_problems  # noqa: E402
from src.path.planner import plan_from_wrong_problems  # noqa: E402


RESULT_PATH = ROOT / "data" / "phase3_demo_result.json"


def main() -> None:
    args = parse_args()
    wrong_pids = args.wrong or pick_default_wrong_pids()

    trace_result = trace_wrong_problems(wrong_pids)
    mastery_result = estimate_mastery(wrong_pids)
    path_result = plan_from_wrong_problems(wrong_pids)

    result = {
        "wrong_pids": wrong_pids,
        "tested_concepts_by_problem": build_tested_concepts_summary(trace_result),
        "weak_prerequisite_candidates": trace_result["weak_candidates"],
        "mastery": mastery_result,
        "learning_path": path_result,
    }

    print_demo(result)
    save_result(result)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="演示 Phase 3：错题诊断、掌握度估计、学习路径规划")
    parser.add_argument(
        "--wrong",
        nargs="+",
        help="做错的题目 pid 列表，例如：--wrong CH3_Q2 CH4_Q7",
    )
    return parser.parse_args()


def pick_default_wrong_pids(limit: int = 2) -> list[str]:
    """
    自动选择 demo 题目。

    从 data/candidates/problems_raw.json 中找前几个有 TESTS 边的 pid，
    避免用户不传参数时拿到无法诊断的题。
    """
    selected: list[str] = []
    raw_path = ROOT / "data" / "candidates" / "problems_raw.json"
    for problem in load_problems(str(raw_path)):
        pid = problem["pid"]
        if get_tested_concepts(pid):
            selected.append(pid)
        if len(selected) >= limit:
            break
    return selected


def build_tested_concepts_summary(trace_result: dict) -> list[dict]:
    """提取每道错题对应的直接考查概念，便于答辩展示。"""
    summary: list[dict] = []
    for item in trace_result["traces"]:
        problem = item.get("problem") or {}
        summary.append({
            "pid": item["pid"],
            "stem": problem.get("stem", ""),
            "tested_concepts": item["tested_concepts"],
        })
    return summary


def print_demo(result: dict) -> None:
    """打印适合答辩展示的中文摘要。"""
    print("\n========== Phase 3 完整链路演示 ==========")

    print("\n1. 错题列表")
    for pid in result["wrong_pids"]:
        print(f"- {pid}")

    print("\n2. 每道错题考查的概念")
    for item in result["tested_concepts_by_problem"]:
        concepts = "、".join(item["tested_concepts"]) or "未找到 TESTS 边"
        print(f"- {item['pid']}: {concepts}")
        if item["stem"]:
            print(f"  题干: {item['stem']}")

    print("\n3. 诊断出的薄弱先修点 Top 10")
    for candidate in result["weak_prerequisite_candidates"][:10]:
        print(
            f"- {candidate['name']} "
            f"(depth={candidate['depth']}, difficulty={candidate['difficulty']}): "
            f"{candidate['reason']}"
        )

    print("\n4. 掌握度估计结果 Top 10")
    for item in result["mastery"][:10]:
        print(
            f"- {item['name']}: mastery={item['mastery']}, "
            f"risk={item['risk']}, weakness={item['weakness']}"
        )
        print(f"  {item['reason']}")

    print("\n5. 推荐学习路径")
    for item in result["learning_path"]:
        practice = "、".join(item["practice_pids"]) or "暂无匹配练习"
        mastery_text = f", mastery={item['mastery']}" if "mastery" in item else ""
        print(
            f"{item['step']}. {item['concept']} "
            f"(risk={item.get('risk', 'unknown')}{mastery_text}, difficulty={item['difficulty']})"
        )
        print(f"   推荐理由: {item['reason']}")
        print(f"   练习题: {practice}")


def save_result(result: dict) -> None:
    """保存完整结构化结果，供报告、前端或后续评测复用。"""
    RESULT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n完整结果已保存到: {RESULT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
