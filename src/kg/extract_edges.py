"""
从 concepts.json（闭集）+ 文本块 + 习题 中抽取各类边。

【抽取策略设计说明】

PREREQUISITE 的特殊处理：
  先修关系是课程结构知识，不是文本表层信号。教材文字不会说"理解X前必须先懂Y"，
  LLM 处理局部文本块只能发现共现，那是 RELATED 的信号，不是 PREREQUISITE 的信号。
  正确策略：以章节内概念分组为单位，把 LLM 当领域专家（而非文本解析器），
  让它凭借对 OS 课程知识结构的理解来构建依赖图。

边的 description 字段（对标 LightRAG）：
  每条边有一句 RAG 可用的语义描述。
  与 reason（"为什么提取这条边"）不同，description 说明的是
  "这条关系在语义上意味着什么"，GraphRAG 检索时直接作为 context 传给 LLM。

各类型抽取来源：
  PART_OF / RELATED / SOLVES  —— 逐块从 chunks.json 抽取（文本表层可见）
  PREREQUISITE                —— 按章节分组，LLM 凭 OS 领域知识构建（不从文本直接提取）
  TESTS                       —— 逐题从 problems_raw.json 抽取
  CONFUSABLE                  —— 从 normalize 报告"保留独立"对派生

运行：
  python -m src.kg.extract_edges                    # 完整流程
  python -m src.kg.extract_edges --skip-prereq      # 跳过先修关系
  python -m src.kg.extract_edges --only-prereq      # 只跑先修关系
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed as _as_completed
from pathlib import Path

from src import llm_client

_MAX_WORKERS = 16   # 并发 LLM 请求数；可通过 --workers 覆盖

# ══════════════════════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════════════════════

VALID_TYPES = {"PREREQUISITE", "PART_OF", "RELATED", "CONFUSABLE", "SOLVES", "TESTS"}
DIRECTED    = {"PREREQUISITE", "PART_OF", "SOLVES", "TESTS"}


# ══════════════════════════════════════════════════════════════════════════════
# 1. 闭集加载与别名解析
# ══════════════════════════════════════════════════════════════════════════════

def load_closed_set(concepts_path: str) -> tuple[list[dict], dict[str, str]]:
    """
    加载 concepts.json，返回 (概念列表, 别名映射)。
    alias_map 将任意名称（规范名或别名）→ 规范名；规范名优先，不被别名覆盖。
    """
    concepts: list[dict] = json.loads(Path(concepts_path).read_text(encoding="utf-8"))
    alias_map: dict[str, str] = {}
    for c in concepts:
        alias_map[c["name"]] = c["name"]          # 第一遍：规范名占位
    for c in concepts:
        for alias in c.get("aliases", []):
            alias = alias.strip()
            if alias and alias not in alias_map:   # 第二遍：别名填入（不覆盖规范名）
                alias_map[alias] = c["name"]
    return concepts, alias_map


def resolve_name(name: str, alias_map: dict[str, str]) -> str | None:
    """将任意名称（含别名）解析为规范名；找不到则返回 None。"""
    return alias_map.get(name.strip())


# ══════════════════════════════════════════════════════════════════════════════
# 2. 从文本块找相关概念
# ══════════════════════════════════════════════════════════════════════════════

def find_relevant_concepts(
    text: str,
    alias_map: dict[str, str],
    max_n: int = 60,
) -> list[str]:
    """
    在文本中查找出现过的概念（规范名或别名均算）。
    只给 LLM 看当前块相关的概念子集，避免全量 1389 个撑爆上下文。
    按出现次数降序，至多返回 max_n 个规范名。
    """
    counter: dict[str, int] = {}
    for term, canonical in alias_map.items():
        count = text.count(term)
        if count > 0:
            counter[canonical] = counter.get(canonical, 0) + count
    ranked = sorted(counter, key=lambda k: -counter[k])
    return ranked[:max_n]


# ══════════════════════════════════════════════════════════════════════════════
# 3. 边的验证与规范化
# ══════════════════════════════════════════════════════════════════════════════

def validate_edge(
    raw: dict,
    concept_name_set: set[str],
    alias_map: dict[str, str],
    role_map: dict[str, str],
    problem_id_set: set[str] | None = None,
) -> dict | None:
    """
    验证并规范化一条边，失败返回 None。

    校验层次（经实体提取经验总结）：
      1. 别名解析 → 规范名（LLM 可能用别名输出）
      2. 端点必须在闭集内
      3. 拒绝自环
      4. 类型合法性
      5. SOLVES 的 source 必须是算法/机制
    """
    src_raw = (raw.get("source") or "").strip()
    tgt_raw = (raw.get("target") or "").strip()
    etype   = (raw.get("type")   or "").strip()

    if not src_raw or not tgt_raw or not etype:
        return None
    if etype not in VALID_TYPES:
        return None

    if etype == "TESTS":
        src = src_raw                              # 习题 pid 不做别名解析
        tgt = resolve_name(tgt_raw, alias_map)
        if tgt is None or tgt not in concept_name_set:
            return None
        if problem_id_set and src not in problem_id_set:
            return None
    else:
        src = resolve_name(src_raw, alias_map)
        tgt = resolve_name(tgt_raw, alias_map)
        if src is None or tgt is None:
            return None
        if src not in concept_name_set or tgt not in concept_name_set:
            return None

    if src == tgt:
        return None

    if etype == "SOLVES" and role_map.get(src) not in {"算法", "机制"}:
        return None

    # description 字段：优先取 description，兼容旧字段 reason
    description = (raw.get("description") or raw.get("reason") or "").strip()

    result: dict = {
        "source":      src,
        "target":      tgt,
        "type":        etype,
        "description": description,
        "chapter":     (raw.get("chapter") or "").strip(),
    }
    if etype == "CONFUSABLE":
        result["dimensions"] = (raw.get("dimensions") or "").strip() or None
    if etype == "PREREQUISITE":
        result["reviewed"] = False                 # 必须人工精校后改为 True
    return result


def deduplicate_edges(edges: list[dict]) -> list[dict]:
    """
    按 (source, target, type) 去重。
    RELATED/CONFUSABLE 无向（排序后去重）；其余有向。
    """
    seen: set[tuple] = set()
    result: list[dict] = []
    for e in edges:
        src, tgt, etype = e["source"], e["target"], e["type"]
        key = (src, tgt, etype) if etype in DIRECTED else (min(src, tgt), max(src, tgt), etype)
        if key not in seen:
            seen.add(key)
            result.append(e)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 4. 文本块 → PART_OF / RELATED / SOLVES（不含 PREREQUISITE）
# ══════════════════════════════════════════════════════════════════════════════

_STRUCT_SYSTEM = (
    "你是操作系统课程的知识图谱构建助手，负责从教材片段中识别概念间关系。"
    "严格按指定 JSON 格式输出，不添加任何额外文字。"
)

# PREREQUISITE 特意不在此 prompt 中：
# 局部文本块能看到的只是概念共现，那是 RELATED 的信号；
# 先修依赖需要对整个课程结构的理解，见 Step 2。
_STRUCT_PROMPT = """\
从以下操作系统教材片段中，识别给定概念列表中各概念之间的关系。

【边类型定义】
PART_OF  A → B：A 是 B 的子概念、组成部分或特例（如：FIFO算法 PART_OF 页面置换算法）
SOLVES   A → B：算法/机制 A 用于解决问题 B（A 的角色必须是算法或机制）
RELATED  A — B：同领域弱关联，无明确依赖方向，常一起讲解或对比

【闭集约束——极其重要】
source 和 target 必须完全匹配以下概念列表中的名称，一字不差。
禁止使用列表外的任何名称。

【可用概念列表】
{concept_list}

【description 格式要求】
description 必须是一句完整的中文句子，说明两概念关系的语义内容，供 RAG 系统直接使用。
  好：银行家算法通过预判分配后是否仍处于安全状态，从而预防死锁的发生。
  差：两者在同一段文本中出现。

【输出格式】
{{
  "edges": [
    {{"source": "A", "target": "B", "type": "PART_OF",  "description": "..."}},
    {{"source": "X", "target": "Y", "type": "SOLVES",   "description": "..."}},
    {{"source": "C", "target": "D", "type": "RELATED",  "description": "..."}}
  ]
}}

注意：source≠target；PART_OF 只提取明确的包含/子类型；SOLVES 的 source 必须是算法或机制；
不确定时宁可不提取；无关系时返回 {{"edges": []}}。

【教材文本（{chapter_str}）】
{text}"""


def extract_struct_edges(
    text: str,
    chapter_str: str,
    relevant_concepts: list[str],
) -> list[dict]:
    """从一个文本块中抽取 PART_OF / RELATED / SOLVES 边。"""
    if len(relevant_concepts) < 2:
        return []
    concept_list = "\n".join(f"- {n}" for n in relevant_concepts)
    prompt = _STRUCT_PROMPT.format(
        chapter_str=chapter_str,
        concept_list=concept_list,
        text=text,
    )
    result = llm_client.call_json(prompt, system=_STRUCT_SYSTEM, temperature=0.1)
    edges = result.get("edges", [])
    if not isinstance(edges, list):
        return []
    for e in edges:
        e["chapter"] = chapter_str
    return edges


# ══════════════════════════════════════════════════════════════════════════════
# 5. PREREQUISITE —— 以章节分组，LLM 凭 OS 领域知识构建依赖图
# ══════════════════════════════════════════════════════════════════════════════

_PREREQ_SYSTEM = (
    "你是操作系统课程的教学专家，对这门课的知识体系和学习顺序有深刻理解。"
    "严格按指定 JSON 格式输出，不添加任何额外文字。"
)

# 先修判断的三分法说明（intra 和 cross 共用核心文本）
_PREREQ_DISTINCTION = """\
【先修关系的严格定义】
PREREQUISITE A → B：B 的核心定义或基本操作中，A 是不可或缺的组成成分。
去掉 A 后，B 的基本含义就完全无法表述（不是"不那么清晰"，是"根本看不懂"）。

✓ 正确示例：
  信号量 → PV操作：PV操作的语义就是对信号量执行原子加减，没有信号量无法定义PV
  请求分页存储管理方式 → 缺页中断：缺页中断是请求分页机制中地址不在内存时的处理路径
  进程 → 进程调度：进程调度的操作对象就是进程，调度概念无法脱离进程独立表述

✗ 错误示例（以下三类不是 PREREQUISITE）：
  [PART_OF 误判] 索引分配 → 单级索引分配   ← 单级是索引分配的子类型，应是 PART_OF
  [PART_OF 误判] 多处理机调度 → 自调度      ← 自调度是多处理机调度的一种实现，应是 PART_OF
  [RELATED 误判] BIOS → UEFI               ← "理解BIOS有助于理解UEFI改进"只是 RELATED
  [RELATED 误判] 进程状态转换 → 实时调度    ← "需要理解进程状态"过于泛化，只是 RELATED

【三种关系区分口诀】
  "B 是 A 的子类型/具体实现/特例"      → PART_OF，不要提取为 PREREQUISITE
  "理解 A 有助于/有关于/对比理解 B"    → RELATED，不要提取为 PREREQUISITE
  "B 的定义直接引用 A，缺 A 就看不懂 B" → PREREQUISITE ✓

【出度约束】每个概念最多列出 8 条先修出边，只保留最直接、最强的依赖。"""

_PREREQ_INTRA_PROMPT = """\
以下是操作系统课程「{chapter_str}」章节中的概念列表。
请根据你对操作系统知识体系的理解，找出这些概念之间真正的先修依赖关系。

{distinction}

{known_partof_section}
【概念列表】（只能从此列表中选取名称，共 {n} 个）
{concept_list}

【description 格式要求】
必须明确说明"B 的哪个定义要素/操作直接引用了 A"，不能用"有助于""相关"等模糊措辞。
  好：PV操作直接作用于信号量的整数值，PV的语义完全依赖信号量的定义
  差：信号量是PV操作的基础

【输出格式】
{{
  "edges": [
    {{"source": "先修概念A", "target": "依赖它的概念B",
      "type": "PREREQUISITE", "description": "..."}}
  ]
}}

source 和 target 必须完全匹配列表中的名称；无强依赖时返回 {{"edges": []}}。"""


_CHAPTER_DEP_PROMPT = """\
以下是操作系统课程各章节及其核心概念（每章列出代表性概念，附简短定义摘要）。
请根据你对操作系统完整知识结构的理解，判断哪些章节之间存在**章节级先修关系**。

章节级先修关系的判断标准：
  - 章节 A 先修章节 B：章节 B 中有多个核心概念的定义直接依赖章节 A 的核心概念
  - 不是"有所了解"这种弱关联，而是"B 章无法脱离 A 章的核心概念自成体系"

【各章节及核心概念】
{chapter_summaries}

【输出格式】
{{
  "chapter_deps": [
    {{
      "prereq_chapter": "先修章节名",
      "target_chapter": "依赖它的章节名",
      "reason": "一句话说明目标章节中哪些核心概念需要先修章节的概念"
    }}
  ]
}}

只列出有实质先修关系的章节对；无先修关系时 chapter_deps 返回空列表。"""


_CHAPTER_PAIR_PROMPT = """\
以下两个操作系统章节之间已判断存在先修关系：
  先修章节：{prereq_chapter}（概念在此章节学习后方可理解目标章节的概念）
  目标章节：{target_chapter}

请从目标章节的概念中，找出哪些概念**直接依赖**先修章节中的哪些概念。

{distinction}

{known_partof_section}
【先修章节「{prereq_chapter}」的概念】（共 {n_prereq} 个，source 必须从此列表选）
{prereq_concepts}

【目标章节「{target_chapter}」的概念】（共 {n_target} 个，target 必须从此列表选）
{target_concepts}

【description 格式要求】
说明目标概念的哪个定义要素或操作直接引用了先修概念，不能用模糊措辞。
  好：缺页中断处理流程依赖"页"的数据结构定义，无页面概念无法描述其处理步骤
  差：进程管理与调度有关联

【输出格式】
{{
  "edges": [
    {{"source": "先修概念A（来自先修章节）",
      "target": "依赖它的概念B（来自目标章节）",
      "type": "PREREQUISITE", "description": "..."}}
  ]
}}

无强依赖时返回 {{"edges": []}}。"""


_VALIDATE_CAND_SYSTEM = (
    "你是操作系统课程的教学专家。"
    "判断给定概念对是否构成真正的先修关系，严格按 JSON 格式输出。"
)

_VALIDATE_CAND_PROMPT = """\
以下是一批候选先修关系（来源：概念 B 的定义文本中出现了概念 A 的名称）。
请逐一判断每对关系是否构成真正的 PREREQUISITE，还是只是 PART_OF 或 RELATED。

{distinction}

【候选对列表】
{candidates}

【输出格式】
{{
  "results": [
    {{
      "source": "概念A",
      "target": "概念B",
      "is_prerequisite": true,
      "description": "说明B的哪个定义要素直接引用了A（若非先修则留空）"
    }}
  ]
}}

每个候选对必须有对应的结果（保持顺序）；非先修关系的 is_prerequisite 设为 false。"""


# 出度裁剪：弱信号词（出现此类词时优先被裁掉）
_WEAK_DESC_SIGNALS = ["有助于", "有帮助", "有关系", "相关", "对比", "加深理解", "可以帮助", "有所了解"]
_MAX_OUT_DEGREE    = 10   # 硬上限；prompt 里要求 8，留 2 条容错
_BATCH_SIZE        = 20   # 章节内批次大小
_OVERLAP           = 5    # 相邻批次重叠数，确保批次边界处的概念对被覆盖


def _trim_prereq_out_degree(
    edges: list[dict],
    max_out: int = _MAX_OUT_DEGREE,
) -> list[dict]:
    """
    强制限制每个概念的 PREREQUISITE 出度。
    超出时优先保留描述不含弱信号词的边；同等情况下保留先出现的边。
    """
    from collections import defaultdict
    by_src: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        by_src[e["source"]].append(e)

    result: list[dict] = []
    for src, es in by_src.items():
        if len(es) <= max_out:
            result.extend(es)
            continue
        strong = [e for e in es if not any(kw in e.get("description", "") for kw in _WEAK_DESC_SIGNALS)]
        weak   = [e for e in es if     any(kw in e.get("description", "") for kw in _WEAK_DESC_SIGNALS)]
        kept = (strong + weak)[:max_out]
        print(f"  [trim] {src}: {len(es)} → {len(kept)} 条（超出度限制 {max_out}）")
        result.extend(kept)
    return result


def _make_chapter_batches(
    ch_concepts: list[dict],
    batch_size: int = _BATCH_SIZE,
    overlap: int = _OVERLAP,
) -> list[list[dict]]:
    """将章节概念列表切分成有重叠的批次，覆盖所有概念。"""
    if len(ch_concepts) <= batch_size:
        return [ch_concepts]
    stride = batch_size - overlap
    batches: list[list[dict]] = []
    start = 0
    while start < len(ch_concepts):
        batches.append(ch_concepts[start: start + batch_size])
        if start + batch_size >= len(ch_concepts):
            break
        start += stride
    return batches


def _build_known_partof_section(
    names_in_scope: set[str],
    known_partof: set[tuple[str, str]],
    max_pairs: int = 30,
) -> str:
    """构建 PART_OF 排除清单文本（限定在给定名称集合内）。"""
    if not known_partof:
        return ""
    pairs = [(a, b) for (a, b) in known_partof if a in names_in_scope and b in names_in_scope]
    if not pairs:
        return ""
    pair_lines = "\n".join(f"  {a} → {b}" for a, b in pairs[:max_pairs])
    return (
        "【已知的 PART_OF 关系——请勿将这些识别为 PREREQUISITE】\n"
        "以下对子已确认是父子/包含关系，不是先修关系，跳过它们：\n"
        + pair_lines + "\n"
    )


def extract_prerequisite_intra_batch(
    batch: list[dict],
    chapter: str,
    known_partof: set[tuple[str, str]],
    model: str = llm_client.DEFAULT_MODEL,
) -> list[dict]:
    """对章节内的一个批次（至多 _BATCH_SIZE 个概念）提取先修关系。"""
    if len(batch) < 2:
        return []
    concept_list = "\n".join(
        f"- {c['name']}（{c.get('node_role','概念')}，难度{c.get('difficulty',1)}）："
        f"{c.get('definition','')[:100]}"
        for c in batch
    )
    names = {c["name"] for c in batch}
    known_partof_section = _build_known_partof_section(names, known_partof)
    prompt = _PREREQ_INTRA_PROMPT.format(
        chapter_str=chapter,
        distinction=_PREREQ_DISTINCTION,
        known_partof_section=known_partof_section,
        n=len(batch),
        concept_list=concept_list,
    )
    result = llm_client.call_json(prompt, system=_PREREQ_SYSTEM, temperature=0.2, model=model)
    edges = result.get("edges", [])
    if not isinstance(edges, list):
        return []
    for e in edges:
        e["chapter"] = chapter
    return edges


def extract_chapter_dependencies(
    concepts: list[dict],
    chapters: list[str],
    model: str = llm_client.DEFAULT_MODEL,
) -> list[dict]:
    """
    一次 LLM 调用，获取章节级依赖图。
    返回 [{prereq_chapter, target_chapter, reason}, ...]。
    """
    parts: list[str] = []
    for ch in chapters:
        ch_concepts = sorted(
            [c for c in concepts if c.get("chapter") == ch],
            key=lambda c: -c.get("difficulty", 1),
        )[:6]
        if not ch_concepts:
            continue
        lines = [f"  - {c['name']}：{c.get('definition','')[:50]}" for c in ch_concepts]
        parts.append(f"【{ch}】\n" + "\n".join(lines))
    if not parts:
        return []
    prompt = _CHAPTER_DEP_PROMPT.format(chapter_summaries="\n".join(parts))
    result = llm_client.call_json(prompt, system=_PREREQ_SYSTEM, temperature=0.2, model=model)
    deps = result.get("chapter_deps", [])
    if not isinstance(deps, list):
        return []
    ch_set = set(chapters)
    return [
        d for d in deps
        if isinstance(d, dict)
        and d.get("prereq_chapter") in ch_set
        and d.get("target_chapter") in ch_set
        and d["prereq_chapter"] != d["target_chapter"]
    ]


def extract_prerequisite_chapter_pair(
    concepts: list[dict],
    prereq_ch: str,
    target_ch: str,
    known_partof: set[tuple[str, str]],
    model: str = llm_client.DEFAULT_MODEL,
    max_per_chapter: int = 25,
) -> list[dict]:
    """对一对章节（先修章 → 目标章）提取跨章节 PREREQUISITE 边。"""
    prereq_concepts = sorted(
        [c for c in concepts if c.get("chapter") == prereq_ch],
        key=lambda c: -c.get("difficulty", 1),
    )[:max_per_chapter]
    target_concepts = sorted(
        [c for c in concepts if c.get("chapter") == target_ch],
        key=lambda c: -c.get("difficulty", 1),
    )[:max_per_chapter]
    if not prereq_concepts or not target_concepts:
        return []

    prereq_list = "\n".join(
        f"- {c['name']}（{c.get('node_role','概念')}）：{c.get('definition','')[:80]}"
        for c in prereq_concepts
    )
    target_list = "\n".join(
        f"- {c['name']}（{c.get('node_role','概念')}）：{c.get('definition','')[:80]}"
        for c in target_concepts
    )
    all_names = {c["name"] for c in prereq_concepts} | {c["name"] for c in target_concepts}
    known_partof_section = _build_known_partof_section(all_names, known_partof)

    prompt = _CHAPTER_PAIR_PROMPT.format(
        prereq_chapter=prereq_ch,
        target_chapter=target_ch,
        distinction=_PREREQ_DISTINCTION,
        known_partof_section=known_partof_section,
        n_prereq=len(prereq_concepts),
        prereq_concepts=prereq_list,
        n_target=len(target_concepts),
        target_concepts=target_list,
    )
    result = llm_client.call_json(prompt, system=_PREREQ_SYSTEM, temperature=0.2, model=model)
    edges = result.get("edges", [])
    if not isinstance(edges, list):
        return []
    for e in edges:
        e["chapter"] = f"{prereq_ch}→{target_ch}"
    return edges


def scan_definition_candidates(
    concepts: list[dict],
    alias_map: dict[str, str],
    existing_pairs: set[tuple[str, str]],
    max_candidates: int = 200,
) -> list[tuple[str, str, str]]:
    """
    扫描概念定义，找出"概念 A 的名称出现在概念 B 的定义中"的候选对。
    返回 [(A_canonical, B_name, B_definition), ...] 列表。
    使用较长词优先匹配，避免短词误命中。
    """
    sorted_terms = sorted(alias_map.keys(), key=len, reverse=True)
    candidates: list[tuple[str, str, str]] = []
    for b in concepts:
        b_def = b.get("definition", "")
        if not b_def:
            continue
        b_name = b["name"]
        found: set[str] = set()
        for term in sorted_terms:
            a_canonical = alias_map[term]
            if a_canonical == b_name or a_canonical in found:
                continue
            if (a_canonical, b_name) in existing_pairs:
                continue
            if term in b_def:
                found.add(a_canonical)
                candidates.append((a_canonical, b_name, b_def))
        if len(candidates) >= max_candidates * 3:
            break
    return candidates[:max_candidates]


def validate_prereq_candidates(
    candidates: list[tuple[str, str, str]],
    concept_map: dict[str, dict],
    model: str = llm_client.DEFAULT_MODEL,
    max_workers: int = _MAX_WORKERS,
    batch_size: int = 10,
) -> list[dict]:
    """
    批量 LLM 验证候选先修对，确认是否真的是 PREREQUISITE。
    candidates: [(A_name, B_name, B_definition), ...]
    """
    if not candidates:
        return []
    batches = [candidates[i: i + batch_size] for i in range(0, len(candidates), batch_size)]
    tasks: list[dict] = []
    for batch in batches:
        lines: list[str] = []
        for idx, (a_name, b_name, b_def) in enumerate(batch, 1):
            a_def = concept_map.get(a_name, {}).get("definition", "")
            lines.append(
                f"{idx}. A={a_name}（{a_def[:50]}）→ B={b_name}\n"
                f"   B 的定义：{b_def[:100]}"
            )
        tasks.append({
            "prompt": _VALIDATE_CAND_PROMPT.format(
                distinction=_PREREQ_DISTINCTION,
                candidates="\n".join(lines),
            ),
            "system": _VALIDATE_CAND_SYSTEM,
            "temperature": 0.1,
        })

    results = llm_client.call_json_batch(tasks, max_workers=max_workers)
    approved: list[dict] = []
    for batch, result in zip(batches, results):
        if isinstance(result, Exception):
            continue
        items = result.get("results", [])
        if not isinstance(items, list):
            continue
        for item, (a_name, b_name, _) in zip(items, batch):
            if not isinstance(item, dict):
                continue
            if item.get("is_prerequisite"):
                approved.append({
                    "source":      a_name,
                    "target":      b_name,
                    "type":        "PREREQUISITE",
                    "description": (item.get("description") or "").strip(),
                    "chapter":     "",
                })
    return approved


# ══════════════════════════════════════════════════════════════════════════════
# 6. 习题 → TESTS
# ══════════════════════════════════════════════════════════════════════════════

_TESTS_SYSTEM = (
    "你是操作系统课程的习题分析助手。"
    "判断一道习题直接考查哪些知识点，严格按 JSON 格式输出。"
)

_TESTS_PROMPT = """\
以下是一道操作系统习题。请从给定的概念列表中，找出该题直接考查的核心概念（最多 5 个）。

【选取标准】直接考查：答对这道题必须掌握的概念；不选"间接相关"的背景概念。

【闭集约束】只能从以下概念列表中选择，完全匹配名称：
{concept_list}

【description 格式要求】
说明该题通过何种方式/场景考查此概念，而非"考查了该概念"。
  好：题目给出资源请求序列，要求学生用银行家算法判断系统是否处于安全状态。
  差：考查了银行家算法的知识。

【输出格式】
{{
  "tests": [
    {{"concept": "概念名", "description": "该题如何考查此概念"}}
  ]
}}

无法从列表匹配时返回 {{"tests": []}}。

【习题（{pid}）】
{stem}"""


def extract_tests_edges(problem: dict, candidate_concepts: list[str]) -> list[dict]:
    """从一道习题中抽取 TESTS 边。"""
    stem = problem.get("stem", "").strip()
    pid  = problem.get("pid", "").strip()
    if not stem or not pid:
        return []
    concept_list = "\n".join(f"- {n}" for n in candidate_concepts)
    prompt = _TESTS_PROMPT.format(pid=pid, concept_list=concept_list, stem=stem)
    result = llm_client.call_json(prompt, system=_TESTS_SYSTEM, temperature=0.1)
    tests = result.get("tests", [])
    if not isinstance(tests, list):
        return []
    return [
        {
            "source":      pid,
            "target":      (t.get("concept") or "").strip(),
            "type":        "TESTS",
            "description": (t.get("description") or "").strip(),
            "chapter":     problem.get("chapter", ""),
        }
        for t in tests
        if (t.get("concept") or "").strip()
    ]


def _get_chapter_candidate_concepts(
    concepts: list[dict],
    chapter_str: str,
    max_n: int = 80,
) -> list[str]:
    """为习题选候选概念：同章优先 + 按难度补充，至多 max_n 个。"""
    same_ch = [c["name"] for c in concepts if c.get("chapter") == chapter_str]
    by_diff = sorted(concepts, key=lambda c: -c.get("difficulty", 1))
    other   = [c["name"] for c in by_diff if c["name"] not in set(same_ch)]
    combined, seen = [], set()
    for name in same_ch + other:
        if name not in seen:
            seen.add(name)
            combined.append(name)
        if len(combined) >= max_n:
            break
    return combined


# ══════════════════════════════════════════════════════════════════════════════
# 7. normalize 报告 → CONFUSABLE
# ══════════════════════════════════════════════════════════════════════════════

_CONFUSABLE_SYSTEM = (
    "你是操作系统课程助手，分析两个概念的主要混淆维度。"
    "严格按 JSON 格式输出，不添加额外文字。"
)

_CONFUSABLE_PROMPT = """\
以下两个操作系统概念在形式或语义上相近，请分析它们的混淆维度。

概念 A：{name_a}
定义 A：{def_a}

概念 B：{name_b}
定义 B：{def_b}

【输出格式】
{{
  "confusable": true,
  "dimensions": "混淆的核心维度（一句话，体现具体的混淆点）",
  "description": "两者关系的语义描述（如：两者都是...，但A在...上不同于B）"
}}

若实际上并不容易混淆：{{"confusable": false, "dimensions": null, "description": null}}"""


def extract_confusable_from_report(
    report_path: str,
    concept_map: dict[str, dict],
    max_pairs: int = 50,
    max_workers: int = _MAX_WORKERS,
) -> list[dict]:
    """
    从 normalize 报告的"保留独立（rejected）"对中派生 CONFUSABLE 候选。

    高相似度但被 LLM 判为不应合并的对，天然是易混淆概念对。
    用 LLM 补充 dimensions 和 description，并发调用。
    """
    report     = json.loads(Path(report_path).read_text(encoding="utf-8"))
    candidates = report.get("candidates", [])
    rejected   = sorted(
        [c for c in candidates
         if isinstance(c.get("decision"), dict)
         and not c["decision"].get("merge")
         and c.get("confidence", 0) >= 0.85],
        key=lambda c: -c.get("confidence", 0),
    )[:max_pairs]

    # 构建所有任务
    tasks: list[dict] = []
    pairs: list[tuple[str, str]] = []
    for pair in rejected:
        na, nb = pair.get("name_a", ""), pair.get("name_b", "")
        if not na or not nb or na not in concept_map or nb not in concept_map:
            continue
        da = concept_map[na].get("definition", "")
        db = concept_map[nb].get("definition", "")
        tasks.append({
            "prompt":      _CONFUSABLE_PROMPT.format(name_a=na, def_a=da, name_b=nb, def_b=db),
            "system":      _CONFUSABLE_SYSTEM,
            "temperature": 0.1,
        })
        pairs.append((na, nb))

    # 并发调用
    results = llm_client.call_json_batch(tasks, max_workers=max_workers)

    edges: list[dict] = []
    for (na, nb), result in zip(pairs, results):
        if isinstance(result, Exception):
            continue
        if result.get("confusable"):
            edges.append({
                "source":      na,
                "target":      nb,
                "type":        "CONFUSABLE",
                "dimensions":  result.get("dimensions") or "",
                "description": result.get("description") or "",
                "chapter":     "",
            })
    return edges


# ══════════════════════════════════════════════════════════════════════════════
# 8. 主流程
# ══════════════════════════════════════════════════════════════════════════════

def run_extraction(
    concepts_path:   str  = "data/concepts.json",
    chunks_path:     str  = "data/candidates/chunks.json",
    problems_path:   str  = "data/candidates/problems_raw.json",
    output_path:     str  = "data/edges.json",
    report_alias:    str  = "data/candidates/normalize_report_alias.json",
    report_embed:    str  = "data/candidates/normalize_report_embed2.json",
    skip_struct:     bool = False,
    skip_prereq:     bool = False,
    skip_tests:      bool = False,
    skip_confusable: bool = False,
    only_prereq:     bool = False,
    max_workers:     int  = _MAX_WORKERS,
    prereq_model:    str  = llm_client.DEFAULT_MODEL,
) -> None:
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[edges] 加载概念闭集：{concepts_path}")
    concepts, alias_map = load_closed_set(concepts_path)
    concept_name_set = {c["name"] for c in concepts}
    concept_map = {c["name"]: c for c in concepts}
    role_map    = {c["name"]: c.get("node_role", "概念") for c in concepts}
    print(f"[edges] 闭集：{len(concept_name_set)} 个概念，{len(alias_map)} 个名称（含别名）")
    print(f"[edges] 并发数：{max_workers}")

    all_edges: list[dict] = []

    # only_prereq 模式：从现有 edges.json 加载已有边，剥离旧 PREREQUISITE，
    # 保留其余边（用于 known_partof + 最终合并写回）
    if only_prereq and out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        old_prereq_cnt = sum(1 for e in existing if e["type"] == "PREREQUISITE")
        base_edges = [e for e in existing if e["type"] != "PREREQUISITE"]
        all_edges.extend(base_edges)
        print(f"[edges] 加载现有 edges.json：{len(existing)} 条，"
              f"剥离旧 PREREQUISITE {old_prereq_cnt} 条，保留 {len(base_edges)} 条")

    # ── Step 1: 文本块 → PART_OF / RELATED / SOLVES（并发）─────────────────
    if not skip_struct and not only_prereq:
        chunks = json.loads(Path(chunks_path).read_text(encoding="utf-8"))
        total_c = len(chunks)
        print(f"\n[edges] Step 1: {total_c} 个文本块 → PART_OF / RELATED / SOLVES（并发 {max_workers}）")

        def _proc_chunk(item: tuple) -> tuple[int, str, list]:
            i, chunk = item
            chapter = chunk.get("chapter", "")
            text    = chunk.get("text", "")
            if not text.strip():
                return i, chapter, []
            relevant = find_relevant_concepts(text, alias_map)
            if len(relevant) < 2:
                return i, chapter, []
            raw_edges = extract_struct_edges(text, chapter, relevant)
            valid = [v for e in raw_edges
                     if (v := validate_edge(e, concept_name_set, alias_map, role_map))]
            return i, chapter, valid

        done_c = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            fs = {pool.submit(_proc_chunk, (i, ch)): i
                  for i, ch in enumerate(chunks, 1)}
            for f in _as_completed(fs):
                done_c += 1
                try:
                    i, chapter, valid = f.result()
                    all_edges.extend(valid)
                    print(f"  [{done_c}/{total_c}] {chapter} → {len(valid)} 条")
                except Exception as ex:
                    print(f"  [{done_c}/{total_c}] 失败：{ex}")

    # ── Step 2: PREREQUISITE —— 4层策略（并发）──────────────────────────────
    if not skip_prereq:
        chapters = sorted({c.get("chapter", "") for c in concepts if c.get("chapter")})
        print(f"\n[edges] Step 2: PREREQUISITE（{len(chapters)} 章，4层策略，并发 {max_workers}）")

        known_partof: set[tuple[str, str]] = {
            (e["source"], e["target"]) for e in all_edges if e["type"] == "PART_OF"
        }
        print(f"  已知 PART_OF 对：{len(known_partof)} 个（将作为排除清单传给 LLM）")

        prereq_edges: list[dict] = []

        # 2a. 章节依赖图（1 次 LLM 调用）
        print("  2a. 章节依赖图（1 次 LLM 调用）")
        chapter_deps = extract_chapter_dependencies(concepts, chapters, model=prereq_model)
        print(f"      → {len(chapter_deps)} 个章节依赖对")

        # 2b. 章节内全量批次先修（各批次全部并发）
        print("  2b. 章节内全量批次先修")
        intra_tasks: list[tuple[str, list[dict]]] = []
        for ch in chapters:
            ch_concepts = sorted(
                [c for c in concepts if c.get("chapter") == ch],
                key=lambda c: -c.get("difficulty", 1),
            )
            for batch in _make_chapter_batches(ch_concepts):
                intra_tasks.append((ch, batch))
        print(f"      共 {len(intra_tasks)} 个批次（{len(chapters)} 章）")

        def _proc_intra_batch(task: tuple) -> tuple[str, list]:
            ch, batch = task
            raw = extract_prerequisite_intra_batch(batch, ch, known_partof, model=prereq_model)
            valid = [v for e in raw
                     if (v := validate_edge(e, concept_name_set, alias_map, role_map))]
            return ch, valid

        ch_batch_counts: dict[str, int] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            fs = {pool.submit(_proc_intra_batch, t): t for t in intra_tasks}
            for f in _as_completed(fs):
                try:
                    ch, valid = f.result()
                    prereq_edges.extend(valid)
                    ch_batch_counts[ch] = ch_batch_counts.get(ch, 0) + len(valid)
                except Exception as ex:
                    print(f"      批次失败：{ex}")
        for ch, cnt in sorted(ch_batch_counts.items()):
            print(f"      {ch} → {cnt} 条")

        # 2c. 章节对跨章先修（并发）
        print("  2c. 章节对跨章先修")
        if chapter_deps:
            def _proc_pair(dep: dict) -> tuple[str, list]:
                pc, tc = dep["prereq_chapter"], dep["target_chapter"]
                raw = extract_prerequisite_chapter_pair(
                    concepts, pc, tc, known_partof, model=prereq_model
                )
                valid = [v for e in raw
                         if (v := validate_edge(e, concept_name_set, alias_map, role_map))]
                return f"{pc}→{tc}", valid

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                fs = {pool.submit(_proc_pair, dep): dep for dep in chapter_deps}
                for f in _as_completed(fs):
                    try:
                        label, valid = f.result()
                        prereq_edges.extend(valid)
                        print(f"      [{label}] → {len(valid)} 条")
                    except Exception as ex:
                        print(f"      跨章失败：{ex}")
        else:
            print("      （无章节依赖对，跳过）")

        # 2d. 定义扫描兜底（批量 LLM 验证孤立概念）
        print("  2d. 定义扫描兜底")
        existing_prereq_pairs: set[tuple[str, str]] = {
            (e["source"], e["target"]) for e in prereq_edges if e["type"] == "PREREQUISITE"
        }
        cands = scan_definition_candidates(concepts, alias_map, existing_prereq_pairs)
        print(f"      定义扫描候选：{len(cands)} 对")
        if cands:
            validated = validate_prereq_candidates(
                cands, concept_map, model=prereq_model, max_workers=max_workers
            )
            valid_v = [v for e in validated
                       if (v := validate_edge(e, concept_name_set, alias_map, role_map))]
            prereq_edges.extend(valid_v)
            print(f"      → LLM 确认 {len(valid_v)} 条")

        # 2e. 出度裁剪（prompt 要求 8，硬限 10）
        prereq_before = len(prereq_edges)
        prereq_edges = _trim_prereq_out_degree(prereq_edges, max_out=_MAX_OUT_DEGREE)
        if len(prereq_edges) < prereq_before:
            print(f"  [trim] 出度裁剪：{prereq_before} → {len(prereq_edges)} 条")

        all_edges.extend(prereq_edges)

        prereq_only = [e for e in prereq_edges if e["type"] == "PREREQUISITE"]
        if prereq_only:
            prereq_path = Path("data/candidates/edges_prerequisite_candidates.json")
            prereq_path.write_text(
                json.dumps(prereq_only, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"  ✓ PREREQUISITE 候选 → {prereq_path}（{len(prereq_only)} 条，待人工精校）")

    # ── Step 3: 习题 → TESTS（并发）────────────────────────────────────────
    if not skip_tests and not only_prereq:
        problems = json.loads(Path(problems_path).read_text(encoding="utf-8"))
        problem_id_set = {p.get("pid", "") for p in problems}
        total_p = len(problems)
        print(f"\n[edges] Step 3: {total_p} 道习题 → TESTS（并发 {max_workers}）")

        def _proc_problem(prob: dict) -> tuple[str, list]:
            chapter = prob.get("chapter", "")
            cands   = _get_chapter_candidate_concepts(concepts, chapter)
            raw_edges = extract_tests_edges(prob, cands)
            valid = [v for e in raw_edges
                     if (v := validate_edge(e, concept_name_set, alias_map, role_map,
                                            problem_id_set=problem_id_set))]
            return prob.get("pid", ""), valid

        done_p = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            fs = {pool.submit(_proc_problem, p): p for p in problems}
            for f in _as_completed(fs):
                done_p += 1
                try:
                    pid, valid = f.result()
                    all_edges.extend(valid)
                    print(f"  [{done_p}/{total_p}] {pid} → {len(valid)} 条 TESTS")
                except Exception as ex:
                    print(f"  [{done_p}/{total_p}] 失败：{ex}")

    # ── Step 4: normalize 报告 → CONFUSABLE（内部已并发）───────────────────
    if not skip_confusable and not only_prereq:
        print(f"\n[edges] Step 4: normalize 报告 → CONFUSABLE（并发 {max_workers}）")
        for rp in [report_alias, report_embed]:
            if not Path(rp).exists():
                continue
            print(f"  {rp} ...", end=" ", flush=True)
            try:
                batch = extract_confusable_from_report(rp, concept_map,
                                                       max_workers=max_workers)
                valid = [v for e in batch
                         if (v := validate_edge(e, concept_name_set, alias_map, role_map))]
                all_edges.extend(valid)
                print(f"→ {len(valid)} 条 CONFUSABLE")
            except Exception as ex:
                print(f"→ 失败：{ex}")

    # ── 汇总、去重、保存 ──────────────────────────────────────────────────────
    all_edges = deduplicate_edges(all_edges)
    type_counts: dict[str, int] = {}
    for e in all_edges:
        type_counts[e["type"]] = type_counts.get(e["type"], 0) + 1

    print(f"\n[edges] 去重后总边数：{len(all_edges)}")
    for t, cnt in sorted(type_counts.items()):
        flag = "  ⚠ 需人工精校" if t == "PREREQUISITE" else ""
        print(f"  {t}: {cnt}{flag}")

    out_path.write_text(
        json.dumps(all_edges, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[edges] ✓ 已写入 {output_path}")


# ══════════════════════════════════════════════════════════════════════════════
# 9. CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="从概念闭集 + 文本块 + 习题中抽取知识图谱边",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--concepts",        default="data/concepts.json")
    parser.add_argument("--chunks",          default="data/candidates/chunks.json")
    parser.add_argument("--problems",        default="data/candidates/problems_raw.json")
    parser.add_argument("--output",          default="data/edges.json")
    parser.add_argument("--skip-struct",     action="store_true", help="跳过文本块→结构边")
    parser.add_argument("--skip-prereq",     action="store_true", help="跳过先修关系提取")
    parser.add_argument("--skip-tests",      action="store_true", help="跳过习题→TESTS")
    parser.add_argument("--skip-confusable", action="store_true", help="跳过CONFUSABLE")
    parser.add_argument("--only-prereq",     action="store_true", help="只跑先修关系")
    parser.add_argument("--workers",         type=int, default=_MAX_WORKERS,
                        help=f"并发 LLM 请求数（默认 {_MAX_WORKERS}）")
    parser.add_argument("--prereq-model",    default=llm_client.DEFAULT_MODEL,
                        help="PREREQUISITE 提取专用模型（默认同全局模型）")

    args = parser.parse_args()
    run_extraction(
        concepts_path   = args.concepts,
        chunks_path     = args.chunks,
        problems_path   = args.problems,
        output_path     = args.output,
        skip_struct     = args.skip_struct,
        skip_prereq     = args.skip_prereq,
        skip_tests      = args.skip_tests,
        skip_confusable = args.skip_confusable,
        only_prereq     = args.only_prereq,
        max_workers     = args.workers,
        prereq_model    = args.prereq_model,
    )
