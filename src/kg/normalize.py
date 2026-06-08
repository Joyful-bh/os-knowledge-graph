"""
实体归一化 —— 去重、合并别名，确保 Concept.name 全图唯一。

工作流：
  1. 别名精确匹配        → 候选对列表
  2. 嵌入向量相似度检测  → 候选对列表（可选）
  3. LLM（默认）或人工逐对审核 → 合并决策
  4. 应用决策，写出 data/concepts.json

CLI：
  python -m src.kg.normalize                   # LLM 审核，嵌入阈值 0.82
  python -m src.kg.normalize --human           # 人工交互审核
  python -m src.kg.normalize --fast            # 只做别名匹配，跳过嵌入
  python -m src.kg.normalize --threshold 0.85  # 调整嵌入候选阈值
"""

import json
import argparse
from pathlib import Path
from collections import defaultdict


# ─────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────

def _norm_key(s: str) -> str:
    return s.strip().lower()


def _is_chinese(s: str) -> bool:
    return any('一' <= c <= '鿿' for c in s)


def _pick_canonical(a: dict, b: dict) -> tuple[dict, dict]:
    """选规范名（保留）与被吸收名。优先级：中文名 > 较长 > difficulty 高。"""
    a_cn, b_cn = _is_chinese(a["name"]), _is_chinese(b["name"])
    if a_cn and not b_cn:
        return a, b
    if b_cn and not a_cn:
        return b, a
    if len(a["name"]) != len(b["name"]):
        return (a, b) if len(a["name"]) >= len(b["name"]) else (b, a)
    return (a, b) if a.get("difficulty", 1) >= b.get("difficulty", 1) else (b, a)


def _merge_into(canonical: dict, absorbed: dict) -> dict:
    """将 absorbed 的别名并入 canonical，保留更长的 definition。"""
    result = dict(canonical)
    existing = {_norm_key(result["name"])} | {_norm_key(x) for x in result.get("aliases", [])}
    for name in [absorbed["name"]] + absorbed.get("aliases", []):
        if _norm_key(name) not in existing:
            result.setdefault("aliases", []).append(name)
            existing.add(_norm_key(name))
    if len(absorbed.get("definition", "")) > len(result.get("definition", "")):
        result["definition"] = absorbed["definition"]
    return result


# ─────────────────────────────────────────────
# 步骤 1：别名精确匹配候选
# ─────────────────────────────────────────────

def _find_alias_candidates(concepts: list[dict]) -> list[dict]:
    """
    找出「concept A 的 name 出现在 concept B 的 aliases 中」（或反之）的所有对。
    返回候选对列表，格式与嵌入候选一致。
    """
    key_to_indices: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(concepts):
        key_to_indices[_norm_key(c["name"])].append(i)
        for alias in c.get("aliases", []):
            key_to_indices[_norm_key(alias)].append(i)

    candidates = []
    seen: set[tuple[int, int]] = set()

    for key, indices in key_to_indices.items():
        unique = list(dict.fromkeys(indices))   # 保序去重
        for a in range(len(unique)):
            for b in range(a + 1, len(unique)):
                i, j = unique[a], unique[b]
                pk = (min(i, j), max(i, j))
                if pk in seen:
                    continue
                seen.add(pk)
                candidates.append({
                    "idx_a": i, "idx_b": j,
                    "name_a": concepts[i]["name"],
                    "name_b": concepts[j]["name"],
                    "def_a":  concepts[i].get("definition", ""),
                    "def_b":  concepts[j].get("definition", ""),
                    "role_a": concepts[i].get("node_role", ""),
                    "role_b": concepts[j].get("node_role", ""),
                    "source":     "alias_match",
                    "confidence": 1.0,
                    "matched_key": key,
                })
    return candidates


# ─────────────────────────────────────────────
# 步骤 2：嵌入相似度候选
# ─────────────────────────────────────────────

def _find_embed_candidates(
    concepts: list[dict],
    threshold: float,
    model_name: str,
) -> list[dict]:
    """
    用嵌入向量找出 similarity >= threshold 的概念对。
    返回同格式候选列表，按相似度降序排列。
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("[normalize] ⚠️  sentence-transformers 未安装，跳过嵌入步骤。pip install sentence-transformers")
        return []

    n = len(concepts)
    if n <= 1:
        return []

    print(f"[normalize] 加载嵌入模型：{model_name}")
    model = SentenceTransformer(model_name)
    texts = [f"{c['name']}：{c.get('definition', '')}" for c in concepts]
    print(f"[normalize] 计算 {n} 条嵌入向量 ...")
    emb = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
    sims = emb @ emb.T

    candidates = []
    for i in range(n):
        for j in range(i + 1, n):
            s = float(sims[i, j])
            if s >= threshold:
                candidates.append({
                    "idx_a": i, "idx_b": j,
                    "name_a": concepts[i]["name"],
                    "name_b": concepts[j]["name"],
                    "def_a":  concepts[i].get("definition", ""),
                    "def_b":  concepts[j].get("definition", ""),
                    "role_a": concepts[i].get("node_role", ""),
                    "role_b": concepts[j].get("node_role", ""),
                    "source":     "embedding",
                    "confidence": round(s, 4),
                })

    candidates.sort(key=lambda x: -x["confidence"])
    return candidates


# ─────────────────────────────────────────────
# 步骤 3a：LLM 审核
# ─────────────────────────────────────────────

_REVIEW_SYSTEM = "你是操作系统课程知识图谱的审核员，专门判断两个概念条目是否应当合并为同一知识点。"

_REVIEW_PROMPT_TMPL = """\
以下是 {n} 对疑似重复或同义的操作系统概念，请逐对判断是否合并。

判断标准：
- 只有语义完全等价（含全称/简称、中英文别名关系）才合并
- 对立概念（如抢占式/非抢占式、单任务/多任务、CPU繁忙型/IO繁忙型、静态/动态）绝对不合并
- 包含、因果、相关等关系也不合并
- 若合并，规范名优先选中文、较完整（较长）的名称

输出严格的 JSON 数组（共 {n} 条，顺序与输入完全一致，不得有注释或多余字段）：
[
  {{
    "merge": true,
    "canonical": "保留的规范名称",
    "reason": "一句话理由"
  }},
  {{
    "merge": false,
    "canonical": null,
    "reason": "一句话理由"
  }},
  ...
]

待审核概念对：
{pairs_json}
"""


def _llm_review_batch(pairs: list[dict]) -> list[dict]:
    """向 LLM 发送一批候选对，返回决策列表。"""
    from src.llm_client import call_json

    pairs_info = []
    for idx, p in enumerate(pairs):
        pairs_info.append({
            "编号": idx + 1,
            "来源": p["source"],
            "相似度": p["confidence"],
            "概念A": {"名称": p["name_a"], "定义": p["def_a"], "类型": p["role_a"]},
            "概念B": {"名称": p["name_b"], "定义": p["def_b"], "类型": p["role_b"]},
        })

    prompt = _REVIEW_PROMPT_TMPL.format(
        n=len(pairs),
        pairs_json=json.dumps(pairs_info, ensure_ascii=False, indent=2),
    )

    result = call_json(prompt, system=_REVIEW_SYSTEM, temperature=0.1)

    if isinstance(result, list) and len(result) == len(pairs):
        return result

    # 响应异常 → 保守处理（全部不合并）
    print(f"[normalize]   ⚠️  LLM 返回 {len(result) if isinstance(result, list) else '?'} 条，"
          f"期望 {len(pairs)} 条，该批次保守处理（不合并）")
    return [{"merge": False, "canonical": None, "reason": "LLM响应异常，保守处理"} for _ in pairs]


def _llm_review(candidates: list[dict], batch_size: int = 15) -> list[dict]:
    """批量调用 LLM 审核所有候选对，返回与 candidates 等长的决策列表。"""
    print(f"[normalize] 调用 LLM 审核 {len(candidates)} 对候选（批次大小 {batch_size}）...")
    decisions = []
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start:start + batch_size]
        end = min(start + batch_size, len(candidates))
        print(f"[normalize]   批次 {start + 1}–{end} ...")
        decisions.extend(_llm_review_batch(batch))
    return decisions


# ─────────────────────────────────────────────
# 步骤 3b：人工交互审核
# ─────────────────────────────────────────────

def _human_review(candidates: list[dict]) -> list[dict]:
    """逐对展示候选，由用户键入 y/n 决定是否合并。"""
    decisions = []
    total = len(candidates)

    print(f"\n{'='*60}")
    print(f"人工审核模式：共 {total} 对候选")
    print("y=合并  n=不合并  q=退出（未审核的对保守处理为不合并）")
    print('='*60 + '\n')

    for i, pair in enumerate(candidates):
        print(f"[{i + 1}/{total}]  sim={pair['confidence']:.4f}  来源={pair['source']}")
        print(f"  A: {pair['name_a']}（{pair['role_a']}）")
        print(f"     {pair['def_a']}")
        print(f"  B: {pair['name_b']}（{pair['role_b']}）")
        print(f"     {pair['def_b']}")

        while True:
            ans = input("  合并? [y/n/q]: ").strip().lower()
            if ans in ("y", "n", "q"):
                break

        if ans == "q":
            print(f"[normalize] 用户提前退出，已审核 {i}/{total} 对")
            decisions.append({"merge": False, "canonical": None, "reason": "用户跳过"})
            decisions.extend(
                [{"merge": False, "canonical": None, "reason": "未审核"} for _ in candidates[i + 1:]]
            )
            return decisions

        if ans == "y":
            can, _ = _pick_canonical(
                {"name": pair["name_a"], "difficulty": 1},
                {"name": pair["name_b"], "difficulty": 1},
            )
            suggested = can["name"]
            custom = input(f"  规范名 [{suggested}]（直接回车接受）: ").strip()
            canonical = custom if custom else suggested
            decisions.append({"merge": True, "canonical": canonical, "reason": "人工确认"})
        else:
            decisions.append({"merge": False, "canonical": None, "reason": "人工否定"})
        print()

    return decisions


# ─────────────────────────────────────────────
# 步骤 4：应用合并决策
# ─────────────────────────────────────────────

def _apply_decisions(
    concepts: list[dict],
    candidates: list[dict],
    decisions: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    应用合并决策，支持传递性（A=B + B=C → A=B=C）。
    使用名称作为 key，通过链式路径查找规范名。
    返回 (归一化后列表, apply_log)。
    """
    # absorbed_to_canonical[absorbed_name] = canonical_name
    absorbed_to_canonical: dict[str, str] = {}

    def resolve(name: str) -> str:
        visited: set[str] = set()
        while name in absorbed_to_canonical:
            if name in visited:  # 环路保护（理论上不会发生）
                break
            visited.add(name)
            name = absorbed_to_canonical[name]
        return name

    apply_log = []
    name_lookup = {c["name"]: c for c in concepts}

    for pair, dec in zip(candidates, decisions):
        if not dec.get("merge"):
            continue

        can_a = resolve(pair["name_a"])
        can_b = resolve(pair["name_b"])
        if can_a == can_b:
            continue

        # 验证 LLM 返回的规范名是否合法（必须是两者之一）
        llm_canonical = (dec.get("canonical") or "").strip()
        if llm_canonical in (can_a, can_b):
            canonical = llm_canonical
            absorbed = can_b if canonical == can_a else can_a
        else:
            # LLM 返回了无效名称，回退到启发式规则
            a_obj = name_lookup.get(can_a, {"name": can_a, "difficulty": 1})
            b_obj = name_lookup.get(can_b, {"name": can_b, "difficulty": 1})
            can_obj, abs_obj = _pick_canonical(a_obj, b_obj)
            canonical, absorbed = can_obj["name"], abs_obj["name"]

        absorbed_to_canonical[absorbed] = canonical
        apply_log.append({
            "kept":     canonical,
            "absorbed": absorbed,
            "reason":   dec.get("reason", ""),
        })

    # 按规范名分组
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in concepts:
        groups[resolve(c["name"])].append(c)

    result = []
    for canonical_name, group in groups.items():
        if len(group) == 1:
            result.append(group[0])
            continue
        # 以 canonical_name 对应的 concept 为基础（若存在），否则用第一个
        base = next((c for c in group if c["name"] == canonical_name), group[0])
        merged = base
        for c in group:
            if c is not base:
                merged = _merge_into(merged, c)
        result.append(merged)

    return result, apply_log


# ─────────────────────────────────────────────
# 公开接口
# ─────────────────────────────────────────────

def normalize_concepts(
    concepts: list[dict],
    threshold: float = 0.82,
    skip_embedding: bool = False,
    use_llm: bool = True,
    batch_size: int = 15,
    model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
) -> tuple[list[dict], dict]:
    """
    概念归一化主入口。

    Args:
        concepts:       extract.py 输出的原始概念列表
        threshold:      嵌入相似度阈值（≥ 此值才纳入候选）
        skip_embedding: True 时跳过嵌入步骤，只做别名匹配
        use_llm:        True=LLM 审核；False=人工交互审核
        batch_size:     LLM 每批处理的候选对数（越大 API 调用越少，但响应可靠性略降）
        model_name:     sentence-transformers 模型名

    Returns:
        (normalized_concepts, report)
        report 包含 candidates（含决策）和 apply_log
    """
    print(f"[normalize] 输入：{len(concepts)} 个概念")

    # 步骤 1
    alias_candidates = _find_alias_candidates(concepts)
    print(f"[normalize] 别名候选：{len(alias_candidates)} 对")

    # 步骤 2
    embed_candidates: list[dict] = []
    if not skip_embedding:
        embed_candidates = _find_embed_candidates(concepts, threshold, model_name)
        print(f"[normalize] 嵌入候选（sim>={threshold}）：{len(embed_candidates)} 对")

    # 合并候选，去重（同一对可能被两种方法都发现）
    seen: set[tuple[int, int]] = set()
    all_candidates: list[dict] = []
    for c in alias_candidates + embed_candidates:
        pk = (min(c["idx_a"], c["idx_b"]), max(c["idx_a"], c["idx_b"]))
        if pk not in seen:
            seen.add(pk)
            all_candidates.append(c)

    # alias 优先展示，其次按相似度降序
    all_candidates.sort(key=lambda x: (-(x["source"] == "alias_match"), -x["confidence"]))
    n_alias = sum(1 for c in all_candidates if c["source"] == "alias_match")
    n_embed = len(all_candidates) - n_alias
    print(f"[normalize] 合并去重后：{len(all_candidates)} 对（别名 {n_alias} + 嵌入 {n_embed}）")

    if not all_candidates:
        report = {
            "input_count": len(concepts),
            "output_count": len(concepts),
            "candidates": [],
            "apply_log": [],
        }
        return concepts, report

    # 步骤 3：审核
    decisions = _llm_review(all_candidates, batch_size) if use_llm else _human_review(all_candidates)

    # 将决策写回候选记录（方便报告查看）
    for c, d in zip(all_candidates, decisions):
        c["decision"] = d

    # 步骤 4：应用
    merged, apply_log = _apply_decisions(concepts, all_candidates, decisions)
    n_merged = sum(1 for d in decisions if d.get("merge"))
    print(f"[normalize] 完成：{len(concepts)} → {len(merged)} 个概念（批准合并 {n_merged} 对）")

    report = {
        "input_count":  len(concepts),
        "output_count": len(merged),
        "candidates":   all_candidates,    # 每条含 decision 字段
        "apply_log":    apply_log,
    }
    return merged, report


def resolve_name(name: str, concepts: list[dict]) -> str | None:
    """将别名或变体归一到规范 Concept.name。供边抽取端点对齐使用。"""
    key = _norm_key(name)
    for c in concepts:
        if _norm_key(c["name"]) == key:
            return c["name"]
        if any(_norm_key(a) == key for a in c.get("aliases", [])):
            return c["name"]
    return None


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="概念实体归一化：候选发现 → LLM/人工审核 → 写出 concepts.json")
    parser.add_argument("--input",     default="data/candidates/concepts_raw.json", help="输入文件")
    parser.add_argument("--output",    default="data/concepts.json",                help="归一化输出")
    parser.add_argument("--report",    default="data/candidates/normalize_report.json", help="审核报告")
    parser.add_argument("--threshold", type=float, default=0.82, help="嵌入相似度阈值（默认 0.82）")
    parser.add_argument("--fast",      action="store_true", help="跳过嵌入，只做别名匹配")
    parser.add_argument("--human",     action="store_true", help="人工交互审核（默认 LLM 审核）")
    parser.add_argument("--batch",     type=int, default=15,  help="LLM 批次大小（默认 15）")
    parser.add_argument("--model",     default="paraphrase-multilingual-MiniLM-L12-v2")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        concepts = json.load(f)

    normalized, report = normalize_concepts(
        concepts,
        threshold=args.threshold,
        skip_embedding=args.fast,
        use_llm=not args.human,
        batch_size=args.batch,
        model_name=args.model,
    )

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    print(f"[normalize] ✓ 已写入 {args.output}（{len(normalized)} 个概念）")

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[normalize] ✓ 报告已写入 {args.report}")

    n_merged = sum(1 for c in report["candidates"] if c.get("decision", {}).get("merge"))
    n_rejected = len(report["candidates"]) - n_merged
    print(f"[normalize] 审核结果：{n_merged} 对合并，{n_rejected} 对保留独立")


if __name__ == "__main__":
    main()
