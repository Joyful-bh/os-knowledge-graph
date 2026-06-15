"""
原文 chunk 检索 —— GraphRAG 的细节补充。

设计参考 LightRAG 的 entity_chunks 映射 + token 预算思路，但做了两点简化：
  1. chunks 规模小（~280）+ 教材文本概念名都是规范的，关键词反查就足够准
  2. 不引入新的向量索引依赖；向量召回作为可选开关（默认关闭）

主路径：子图节点名（含别名）→ 反查 chunks → 按命中概念数排序 → 预算截断
"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CHUNKS_PATH = _PROJECT_ROOT / "data" / "candidates" / "chunks.json"
_CONCEPTS_PATH = _PROJECT_ROOT / "data" / "concepts.json"


@lru_cache(maxsize=1)
def _load_chunks() -> list[dict]:
    """加载 chunks.json；缓存。"""
    if not _CHUNKS_PATH.exists():
        return []
    return json.loads(_CHUNKS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _load_alias_map() -> dict[str, list[str]]:
    """
    构建 规范概念名 → [所有名称（含规范名+别名）] 的映射。
    chunk 反查时，任一名称命中即算这个概念出现过。
    """
    if not _CONCEPTS_PATH.exists():
        return {}
    concepts = json.loads(_CONCEPTS_PATH.read_text(encoding="utf-8"))
    alias_map: dict[str, list[str]] = {}
    for c in concepts:
        name = c["name"]
        aliases = c.get("aliases", []) or []
        # 把规范名也放进列表里，作为命中候选
        # 长名优先（避免短名误命中：如 "进程" 会命中 "进程调度"）
        all_names = sorted({name, *aliases}, key=len, reverse=True)
        alias_map[name] = all_names
    return alias_map


def retrieve_chunks_by_concepts(
    concept_names: list[str],
    max_chunks: int = 4,
    max_chunk_chars: int = 600,
    min_hits: int = 1,
) -> list[dict]:
    """
    根据子图概念名反查相关原文 chunks。

    Args:
        concept_names: 子图节点的规范概念名列表
        max_chunks:    最多返回多少个 chunks
        max_chunk_chars: 每个 chunk 截取的最大字符数（保留开头）
        min_hits:      chunk 至少命中多少个目标概念才入选

    Returns:
        [
            {
                "chunk_id":      "...",
                "chapter":       "...",
                "source":        "...",
                "text":          "（截断后的原文）",
                "hit_concepts":  ["命中的概念A", ...],
            },
            ...
        ]
        按 hit_concepts 数量降序。
    """
    chunks = _load_chunks()
    if not chunks or not concept_names:
        return []

    alias_map = _load_alias_map()
    target_set = set(concept_names)

    # 给每个 chunk 算"命中了多少个目标概念"
    chunk_hits: dict[str, set[str]] = defaultdict(set)
    chunk_by_id: dict[str, dict] = {}
    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        chunk_by_id[chunk_id] = chunk
        text = chunk.get("text", "")
        for concept_name in target_set:
            # 优先用规范名匹配，没命中再用别名
            aliases = alias_map.get(concept_name, [concept_name])
            for alias in aliases:
                if alias and alias in text:
                    chunk_hits[chunk_id].add(concept_name)
                    break  # 这个概念已命中，去看下一个

    # 过滤 + 排序：命中数降序；同分时按 chunk_id 稳定排序
    ranked = sorted(
        (
            (chunk_id, hits)
            for chunk_id, hits in chunk_hits.items()
            if len(hits) >= min_hits
        ),
        key=lambda x: (-len(x[1]), x[0]),
    )

    result: list[dict] = []
    for chunk_id, hits in ranked[:max_chunks]:
        chunk = chunk_by_id[chunk_id]
        text = chunk.get("text", "")
        truncated = text[:max_chunk_chars]
        if len(text) > max_chunk_chars:
            truncated += "…"
        result.append({
            "chunk_id":     chunk_id,
            "chapter":      chunk.get("chapter", ""),
            "source":       chunk.get("source", ""),
            "text":         truncated,
            "hit_concepts": sorted(hits),
        })
    return result


def format_chunks_for_prompt(chunks: list[dict]) -> str:
    """
    将 chunks 整理为 LLM 易读的中文上下文段落。
    每段标注章节来源和命中概念，让 LLM 知道这段原文为什么相关。
    """
    if not chunks:
        return "（无相关原文片段）"

    lines: list[str] = []
    for i, c in enumerate(chunks, 1):
        hit_str = "、".join(c.get("hit_concepts", []))
        lines.append(
            f"片段 {i}（来源：{c.get('chapter', '?')}；涉及概念：{hit_str}）\n"
            f"{c.get('text', '')}"
        )
    return "\n\n".join(lines)
