"""
KG 抽取 —— 从 PDF 中抽取 Concept 候选、Problem 候选与文本块。

两步流程：
  Step 1（本脚本）：开放式抽取，输出到 data/candidates/
  Step 2（extract_edges.py）：基于 concepts.json 闭集抽取各类边

支持模式：
  自动模式   按"第X章"标题自动切章，可用 --chapters 筛选
  手动模式   用 --name 指定章节名，配合 --start/--end 指定页范围
             （适用于 PPT 转 PDF、无章节标题的参考文献等）
  增量模式   默认开启，新结果追加到已有候选文件，不覆盖

典型用法：
  # 主教材，自动检测章节，只处理第1-7章
  python -m src.kg.extract --pdf data/raw/计算机操作系统.pdf --chapters 1 2 3 4 5 6 7

  # PPT 转成的 PDF，没有章节标题，手动指定
  python -m src.kg.extract --pdf data/raw/memory_slides.pdf --start 1 --end 45 --name "4.存储器管理"

  # 参考书的某一部分（第10-80页），自动检测其中的章节
  python -m src.kg.extract --pdf data/raw/ref_book.pdf --start 10 --end 80

  # 从头开始，覆盖已有候选文件
  python -m src.kg.extract --pdf data/raw/计算机操作系统.pdf --overwrite
"""

import argparse
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

from src import llm_client

# ── 章节汉字数字映射 ──────────────────────────────────────────────────────────
_CN_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_CHAPTER_RE = re.compile(r"^第([一二三四五六七八九十]+)章\s*(.+)")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PDF 文本提取
# ═══════════════════════════════════════════════════════════════════════════════

def extract_text_by_chapter(
    pdf_path: str,
    start_page: int | None = None,
    end_page: int | None = None,
    chapter_name: str | None = None,
    skip_toc_pages: int = 9,
) -> list[dict]:
    """
    打开 PDF，返回按章切分的文本列表。

    支持两种模式：
    - 自动模式（chapter_name=None）：识别"第X章"标题自动切章。
      每个章号只记录首次出现（跳过目录页眉），跳过前 skip_toc_pages 页。
    - 手动模式（chapter_name 非 None）：不做章节检测，
      将 [start_page, end_page] 范围内的全部文本视为一个章节。
      适用于 PPT 转 PDF、无章节标题的文档。

    Args:
        pdf_path:       PDF 文件路径
        start_page:     起始页（1-indexed，含）；None 表示从第1页开始
        end_page:       结束页（1-indexed，含）；None 表示到最后一页
        chapter_name:   手动指定章节名，非 None 时启用手动模式
        skip_toc_pages: 自动模式下跳过的前 N 页（目录/前言），避免误检

    Returns:
        [{"chapter_num", "title", "chapter_str", "body", "exercises"}, ...]
    """
    doc = fitz.open(pdf_path)
    total = len(doc)

    # 页范围转为 0-indexed，做边界保护
    p_start = max(0, (start_page - 1) if start_page else 0)
    p_end   = min(total, end_page if end_page else total)

    pages_text = [
        _clean_page(doc[i].get_text())
        for i in range(p_start, p_end)
    ]

    # ── 手动模式：整个范围视为一个章节 ──────────────────────────────────────
    if chapter_name is not None:
        raw = "\n".join(pages_text).strip()
        ex_match = re.search(r"\n\s*习\s*题\s*\n", raw)
        body      = raw[: ex_match.start()].strip() if ex_match else raw
        exercises = raw[ex_match.start() :].strip() if ex_match else ""
        # chapter_num 设为 0，表示来自手动指定的源（不参与自动章号排序）
        return [{
            "chapter_num": 0,
            "title":       chapter_name.split(".", 1)[-1] if "." in chapter_name else chapter_name,
            "chapter_str": chapter_name,
            "body":        body,
            "exercises":   exercises,
        }]

    # ── 自动模式：检测"第X章"标题 ─────────────────────────────────────────────
    # 页眉每页都重复章节标题，只记录每个章号的首次出现（真正的章节起始页）
    seen_chapters: set[int] = set()
    chapter_starts: list[tuple[int, int, str]] = []  # (local_idx, chapter_num, title)

    toc_skip = max(0, skip_toc_pages - p_start)  # 相对于 pages_text 的偏移
    for local_i, text in enumerate(pages_text):
        if local_i < toc_skip:
            continue
        for line in text.split("\n")[:6]:
            m = _CHAPTER_RE.match(line.strip())
            if not m:
                continue
            cn    = _CN_NUM.get(m.group(1), 0)
            title = re.sub(r"\s+", "", m.group(2)).strip()
            if cn > 0 and len(title) <= 20 and cn not in seen_chapters:
                chapter_starts.append((local_i, cn, title))
                seen_chapters.add(cn)
                break

    if not chapter_starts:
        print("[extract] 警告：未检测到章节标题。"
              "如果这是 PPT 或无章节格式的 PDF，请用 --name 参数指定章节名。")
        return []

    print(f"[extract] 识别到 {len(chapter_starts)} 个章节：")
    for _, cn, t in chapter_starts:
        print(f"  第{cn}章 {t}")

    chapters = []
    for idx, (local_start, cn, title) in enumerate(chapter_starts):
        local_end = (
            chapter_starts[idx + 1][0] if idx + 1 < len(chapter_starts) else len(pages_text)
        )
        raw = "\n".join(pages_text[local_start:local_end])
        ex_match  = re.search(r"\n\s*习\s*题\s*\n", raw)
        body      = raw[: ex_match.start()].strip() if ex_match else raw.strip()
        exercises = raw[ex_match.start() :].strip() if ex_match else ""
        chapters.append({
            "chapter_num": cn,
            "title":       title,
            "chapter_str": f"{cn}.{title}",
            "body":        body,
            "exercises":   exercises,
        })

    return chapters


def _clean_page(text: str) -> str:
    """去除页码行（·N· 格式或孤立数字行）等常见页眉/页脚噪声。"""
    lines = []
    for line in text.split("\n"):
        s = line.strip()
        if re.fullmatch(r"[·•\d\s]+", s) and len(s) <= 6:
            continue
        lines.append(line)
    return "\n".join(lines)


def chunk_text(text: str, max_chars: int = 3000) -> list[str]:
    """将长文本按段落切块，每块不超过 max_chars 字符，段落不被截断。"""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = para
        else:
            current = (current + "\n\n" + para) if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 概念抽取
# ═══════════════════════════════════════════════════════════════════════════════

_CONCEPT_SYSTEM = (
    "你是操作系统课程的知识图谱构建助手，负责从教材中精准提取知识点。"
    "严格按指定 JSON 格式输出，不添加任何额外文字。"
)

_CONCEPT_PROMPT = """\
从以下操作系统教材片段（{chapter_str}）中，抽取所有值得建立节点的知识概念。

【粒度标准】
✓ 纳入：能单独出一道考题、在课件中独立成节的知识点（如"分页机制"、"银行家算法"、"死锁"）
✗ 排除：过细的实现细节（如"页表项的有效位"）、泛化短语（如"提高效率"）

【node_role 判断规则】
- "概念"：描述"是什么"的一般知识点（进程、临界区、地址空间）
- "机制"：解决问题的方法或手段（信号量、分页机制、缓冲池）
- "算法"：有明确执行步骤的算法 —— 必须填写专属字段（FCFS、LRU、银行家算法）
- "问题"：需要被解决的现象或挑战（死锁、内存碎片、优先级反转）

【difficulty 评分】
1 = 基础（能复述定义即可）
2 = 进阶（需理解运作机制）
3 = 综合（需联系多个概念分析）

【aliases 填写规则 —— 重要】
aliases 只放"同一概念的其他写法"，包括：缩写、英文名、别称、简称。
✗ 禁止放进 aliases 的内容：
  1. 概念名称本身（不要把 name 的值复制进 aliases）
  2. 上位/下位概念（"批处理系统"不是"多道批处理系统"的别名）
  3. 相关但独立的概念（"P操作"和"V操作"不是"PV操作"的别名，应独立提取）
  4. 有明确区分的变体（"装入时动态链接"和"运行时动态链接"有本质区别，须各自独立提取，不能折叠进"动态链接"的别名）

【definition 质量要求】
- 20-50字，说明核心含义
- 若该概念有容易混淆的近义词，定义中应体现区别特征（例如：明确"与X不同，本概念......"）

【输出格式】返回合法 JSON，根键为 "concepts"：
{{
  "concepts": [
    {{
      "name": "规范中文名称（优先用完整中文，不用缩写）",
      "definition": "一句话定义，20-50字，说明核心含义和区别特征",
      "node_role": "概念|机制|算法|问题",
      "difficulty": 1,
      "aliases": ["常见缩写", "英文名", "别称"],
      "preemptive": null,
      "starvation_free": null,
      "complexity": null,
      "scenario": null
    }}
  ]
}}

注意：
- preemptive/starvation_free/complexity/scenario 仅 node_role="算法" 时填写，其余填 null
- aliases 可为空数组 []
- 若该片段无值得提取的概念，返回 {{"concepts": []}}

【教材文本】
{text}"""

_VALID_ROLES = {"概念", "机制", "算法", "问题"}


def _validate_concept(c: dict, chapter_str: str) -> dict | None:
    """
    验证并清洗单条 LLM 输出的概念。返回 None 表示丢弃。

    修复的已知 LLM 缺陷：
    - 把概念名本身放进 aliases（自引用）
    - aliases 包含空字符串
    - node_role / difficulty 字段不合法
    - name 或 definition 缺失/为空
    """
    name = (c.get("name") or "").strip()
    definition = (c.get("definition") or "").strip()
    if not name or not definition or len(definition) < 5:
        return None

    # 清理 aliases：去空、去自引用、去重（保序）
    raw_aliases = c.get("aliases") or []
    if not isinstance(raw_aliases, list):
        raw_aliases = []
    seen: set[str] = {name}  # 初始放入 name，防止自引用
    aliases: list[str] = []
    for a in raw_aliases:
        a = str(a).strip()
        if a and a not in seen:
            seen.add(a)
            aliases.append(a)

    # 合法化 node_role
    node_role = c.get("node_role", "概念")
    if node_role not in _VALID_ROLES:
        node_role = "概念"

    # 合法化 difficulty
    try:
        difficulty = int(c.get("difficulty", 1))
        difficulty = max(1, min(3, difficulty))
    except (TypeError, ValueError):
        difficulty = 1

    result: dict = {
        "name":       name,
        "definition": definition,
        "node_role":  node_role,
        "difficulty": difficulty,
        "aliases":    aliases,
        "chapter":    chapter_str,
    }
    if node_role == "算法":
        for f in ("preemptive", "starvation_free", "complexity", "scenario"):
            result[f] = c.get(f)  # 保留 LLM 填写的值（可能为 null）
    return result


def extract_concepts_from_chunk(
    text: str,
    chapter_str: str,
) -> list[dict]:
    """从一段教材正文中抽取概念候选，自动附加章节信息。"""
    prompt = _CONCEPT_PROMPT.format(chapter_str=chapter_str, text=text)
    result = llm_client.call_json(prompt, system=_CONCEPT_SYSTEM, temperature=0.1)

    cleaned = []
    for c in result.get("concepts", []):
        validated = _validate_concept(c, chapter_str)
        if validated is not None:
            cleaned.append(validated)
    return cleaned


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 习题抽取
# ═══════════════════════════════════════════════════════════════════════════════

_PROBLEM_SYSTEM = (
    "你是操作系统课程的习题整理助手。"
    "从给定的习题文本中结构化提取每道题，严格按指定 JSON 格式输出，不遗漏任何题目。"
)

_PROBLEM_PROMPT = """\
从以下操作系统教材习题文本（{chapter_str}）中，提取所有习题。

【题目类型判断标准】
- "记忆"：只需复述定义/列举特征，一句话能答（"什么是进程？"）
- "关系"：需比较两概念或解释因果关系（"进程和线程有什么区别？"）
- "综合"：给具体场景让分析/计算，需多概念联合推理（"用银行家算法判断……"）

【pid 编号规则】按出现顺序从 {pid_prefix}_Q1 开始连续编号。

【输出格式】返回合法 JSON，根键为 "problems"：
{{
  "problems": [
    {{
      "pid": "{pid_prefix}_Q1",
      "stem": "完整题干（严格保持原文，不省略）",
      "answer": "",
      "type": "记忆|关系|综合",
      "chapter": "{chapter_str}"
    }}
  ]
}}

注意：
- answer 统一留空字符串""（后续人工/LLM 补充）
- stem 必须完整，不得用"……"省略
- 若文本中无习题，返回 {{"problems": []}}

【习题文本】
{text}"""


def extract_problems_from_chapter(
    exercises_text: str,
    chapter_str: str,
    pid_prefix: str,
) -> list[dict]:
    """从章末习题文本中抽取题目候选。"""
    if not exercises_text.strip():
        return []
    prompt = _PROBLEM_PROMPT.format(
        chapter_str=chapter_str,
        pid_prefix=pid_prefix,
        text=exercises_text,
    )
    result = llm_client.call_json(prompt, system=_PROBLEM_SYSTEM, temperature=0.1)
    return [p for p in result.get("problems", []) if p.get("stem", "").strip()]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 增量合并与保存
# ═══════════════════════════════════════════════════════════════════════════════

def deduplicate_concepts(concepts: list[dict]) -> list[dict]:
    """
    粗去重：name 精确相同的保留第一个并合并 aliases。
    合并后额外清洗：移除任何与规范名冲突的别名（防止同批次内 alias == 另一概念的 name）。
    同义词/近义词的归一化在 normalize.py 中进行（或人工审核时处理）。
    """
    seen: dict[str, int] = {}
    result: list[dict] = []
    for c in concepts:
        name = c.get("name", "").strip()
        if not name:
            continue
        if name not in seen:
            seen[name] = len(result)
            result.append(c)
        else:
            existing = result[seen[name]]
            merged = set(existing.get("aliases", [])) | set(c.get("aliases", []))
            merged.discard(name)          # 防止自引用
            existing["aliases"] = sorted(merged)

    # 第二步：收集全部规范名，清除各概念中与其他规范名冲突的别名
    all_names = {c["name"] for c in result}
    for c in result:
        c["aliases"] = [a for a in c.get("aliases", []) if a not in all_names]
    return result


def deduplicate_problems(problems: list[dict]) -> list[dict]:
    """按 pid 去重，保留第一个。"""
    seen: set[str] = set()
    result = []
    for p in problems:
        pid = p.get("pid", "")
        if pid and pid not in seen:
            seen.add(pid)
            result.append(p)
    return result


def _load_existing(path: Path) -> list:
    """加载已有的 JSON 数组文件，文件不存在则返回空列表。"""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_json(path: Path, data: list) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 主流程
# ═══════════════════════════════════════════════════════════════════════════════

def run_extraction(
    pdf_path: str = "data/raw/计算机操作系统.pdf",
    output_dir: str = "data/candidates",
    only_chapters: list[int] | None = None,
    start_page: int | None = None,
    end_page: int | None = None,
    chapter_name: str | None = None,
    incremental: bool = True,
) -> None:
    """
    完整抽取流程：PDF → 候选概念 + 候选习题 + 文本块。

    输出文件（增量模式下追加，--overwrite 则重建）：
      data/candidates/concepts_raw.json
      data/candidates/problems_raw.json
      data/candidates/chunks.json

    Args:
        pdf_path:      教材 PDF 路径
        output_dir:    候选文件输出目录
        only_chapters: 仅处理这些章号（自动模式下有效）
        start_page:    起始页（1-indexed），None 表示第 1 页
        end_page:      结束页（1-indexed），None 表示最后一页
        chapter_name:  手动章节名（非 None 时启用手动模式，忽略 only_chapters）
        incremental:   True = 追加到已有文件；False = 覆盖重建
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    source_stem = Path(pdf_path).stem   # 用于 chunk_id 前缀，区分不同来源

    print(f"[extract] 来源：{pdf_path}")
    if start_page or end_page:
        print(f"[extract] 页范围：{start_page or 1} – {end_page or '末页'}")
    if chapter_name:
        print(f"[extract] 手动章节：{chapter_name}")

    # 提取章节文本
    chapter_data = extract_text_by_chapter(
        pdf_path,
        start_page=start_page,
        end_page=end_page,
        chapter_name=chapter_name,
    )
    if not chapter_data:
        print("[extract] 未获得任何章节文本，退出。")
        sys.exit(1)

    # 自动模式下按章号筛选
    if chapter_name is None and only_chapters:
        chapter_data = [c for c in chapter_data if c["chapter_num"] in only_chapters]
        print(f"[extract] 筛选章节：{only_chapters}")

    # 加载已有候选（增量模式）
    concepts_path = out / "concepts_raw.json"
    problems_path = out / "problems_raw.json"
    chunks_path   = out / "chunks.json"

    existing_concepts = _load_existing(concepts_path) if incremental else []
    existing_problems = _load_existing(problems_path) if incremental else []
    existing_chunks   = _load_existing(chunks_path)   if incremental else []
    existing_chunk_ids = {c["chunk_id"] for c in existing_chunks}

    if incremental and (existing_concepts or existing_problems):
        print(f"[extract] 增量模式：已有 {len(existing_concepts)} 个概念，"
              f"{len(existing_problems)} 道习题，{len(existing_chunks)} 个文本块")

    new_concepts: list[dict] = []
    new_problems: list[dict] = []
    new_chunks:   list[dict] = []

    for ch in chapter_data:
        cn          = ch["chapter_num"]
        chapter_str = ch["chapter_str"]
        # pid 前缀：有效章号用 CH{n}，手动模式用章节名的前几个字
        pid_prefix  = f"CH{cn}" if cn > 0 else re.sub(r"[^\w]", "", chapter_str)[:8]

        print(f"\n{'='*60}")
        print(f"{chapter_str}  (正文 {len(ch['body'])} 字，习题 {len(ch['exercises'])} 字)")
        print("="*60)

        # ── 概念抽取（分块） ──────────────────────────────────────────────────
        body_chunks = chunk_text(ch["body"], max_chars=3000)
        chapter_concepts: list[dict] = []

        for i, chunk in enumerate(body_chunks):
            chunk_id = f"{source_stem}_{chapter_str}_C{i+1:03d}"
            print(f"  块 {i+1}/{len(body_chunks)} ({len(chunk)} 字)...", end=" ", flush=True)

            # 收集文本块（增量模式下跳过已有的）
            if chunk_id not in existing_chunk_ids:
                new_chunks.append({
                    "chunk_id": chunk_id,
                    "source":   pdf_path,
                    "chapter":  chapter_str,
                    "text":     chunk,
                })

            try:
                batch = extract_concepts_from_chunk(chunk, chapter_str)
                chapter_concepts.extend(batch)
                print(f"→ {len(batch)} 个概念")
            except Exception as e:
                print(f"→ 失败：{e}")

        chapter_concepts = deduplicate_concepts(chapter_concepts)
        print(f"  ✓ 本章概念（章内去重后）：{len(chapter_concepts)} 个")
        new_concepts.extend(chapter_concepts)

        # ── 习题抽取 ──────────────────────────────────────────────────────────
        if ch["exercises"]:
            print(f"  习题段抽取...", end=" ", flush=True)
            try:
                problems = extract_problems_from_chapter(
                    ch["exercises"], chapter_str, pid_prefix
                )
                print(f"→ {len(problems)} 道")
                new_problems.extend(problems)
            except Exception as e:
                print(f"→ 失败：{e}")

    # ── 合并、去重、保存 ──────────────────────────────────────────────────────
    all_concepts = deduplicate_concepts(existing_concepts + new_concepts)
    all_problems = deduplicate_problems(existing_problems + new_problems)
    all_chunks   = existing_chunks + new_chunks

    print(f"\n{'='*60}")
    print(f"本次新增：{len(new_concepts)} 个概念候选，{len(new_problems)} 道习题，"
          f"{len(new_chunks)} 个文本块")
    print(f"合并后总计：{len(all_concepts)} 个概念，{len(all_problems)} 道习题，"
          f"{len(all_chunks)} 个文本块")

    _save_json(concepts_path, all_concepts)
    _save_json(problems_path, all_problems)
    _save_json(chunks_path,   all_chunks)

    print(f"\n已保存：")
    print(f"  {concepts_path}  ({len(all_concepts)} 条)")
    print(f"  {problems_path}  ({len(all_problems)} 条)")
    print(f"  {chunks_path}    ({len(all_chunks)} 条)")
    print(f"\n下一步：")
    print(f"  人工审核 concepts_raw.json → 审核完毕后保存为 data/concepts.json")
    print(f"  运行边抽取：python -m src.kg.extract_edges")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="从 PDF 教材/PPT 转换文件中抽取概念、习题与文本块",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--pdf",  default="data/raw/计算机操作系统.pdf",
        help="PDF 文件路径",
    )
    parser.add_argument(
        "--out",  default="data/candidates",
        help="候选文件输出目录（默认 data/candidates）",
    )
    parser.add_argument(
        "--chapters", nargs="*", type=int, metavar="N",
        help="自动模式：只处理指定章号，如 --chapters 1 2 3",
    )
    parser.add_argument(
        "--start", type=int, default=None, metavar="PAGE",
        help="起始页（1-indexed，含）",
    )
    parser.add_argument(
        "--end", type=int, default=None, metavar="PAGE",
        help="结束页（1-indexed，含）",
    )
    parser.add_argument(
        "--name", default=None, metavar="CHAPTER_NAME",
        help='手动指定章节名，启用手动模式，如 --name "4.存储器管理"',
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="覆盖已有候选文件（默认为增量追加）",
    )

    args = parser.parse_args()
    run_extraction(
        pdf_path      = args.pdf,
        output_dir    = args.out,
        only_chapters = args.chapters,
        start_page    = args.start,
        end_page      = args.end,
        chapter_name  = args.name,
        incremental   = not args.overwrite,
    )
