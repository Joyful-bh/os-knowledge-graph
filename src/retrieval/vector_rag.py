"""
向量 RAG 基线 —— bge-m3 编码 + ChromaDB 持久化检索。

本文件只负责“问题 -> 相关 Concept”的向量召回：
1. 读取 data/concepts.json 中的闭集概念；
2. 将符合 Schema 的概念字段拼成可检索文本；
3. 使用本地 Ollama 或 SiliconFlow BAAI/bge-m3 生成 embedding；
4. 写入 chroma_db/os_concepts，查询时返回规范概念名。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHROMA_DIR = PROJECT_ROOT / "chroma_db"
COLLECTION_NAME = "os_concepts"
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "ollama").strip().lower()
OLLAMA_EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "bge-m3")
SILICONFLOW_EMBEDDING_MODEL = os.getenv("SILICONFLOW_EMBEDDING_MODEL", "BAAI/bge-m3")
SILICONFLOW_BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.com/v1")
SILICONFLOW_BATCH_SIZE = 64


def load_concepts(path: str = "data/concepts.json") -> list[dict]:
    """
    读取 Concept 列表，默认读取项目根目录下的 data/concepts.json。

    Args:
        path: concepts.json 路径；相对路径会优先按当前目录解析，
              不存在时再按项目根目录解析。
    """
    concept_path = Path(path)
    if not concept_path.exists():
        concept_path = PROJECT_ROOT / path

    with concept_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _as_text(value: Any) -> str:
    """将 metadata / 文本字段转成稳定字符串，避免 None 进入索引文本。"""
    if value is None:
        return ""
    return str(value)


def _concept_to_document(concept: dict) -> str:
    """
    将 Concept 节点转成向量库中的文本。

    文本字段严格围绕 Schema 中的 Concept 属性组织：
    name、definition 是检索核心；chapter、node_role、difficulty 帮助区分语境；
    aliases 与算法专属字段用于提升实体链接和算法类问题的召回。
    """
    aliases = concept.get("aliases") or []
    alias_text = "、".join(_as_text(alias) for alias in aliases if _as_text(alias))

    lines = [
        f"概念名称：{_as_text(concept.get('name'))}",
        f"定义：{_as_text(concept.get('definition'))}",
        f"所属章节：{_as_text(concept.get('chapter'))}",
        f"节点角色：{_as_text(concept.get('node_role'))}",
        f"难度：{_as_text(concept.get('difficulty'))}",
    ]

    if alias_text:
        lines.append(f"别名：{alias_text}")

    # 算法节点可能带有 Schema 规定的专属字段，加入文本有利于算法对比类检索。
    algorithm_fields = {
        "preemptive": "是否抢占式",
        "starvation_free": "是否避免饥饿",
        "complexity": "复杂度或调度开销",
        "scenario": "典型适用场景",
    }
    for key, label in algorithm_fields.items():
        value = concept.get(key)
        if value is not None and value != "":
            lines.append(f"{label}：{_as_text(value)}")

    return "\n".join(lines)


def _concept_to_metadata(concept: dict) -> dict:
    """Chroma metadata 只保存轻量字段，供 query() 直接组装返回值。"""
    return {
        "name": _as_text(concept.get("name")),
        "definition": _as_text(concept.get("definition")),
        "chapter": _as_text(concept.get("chapter")),
        "node_role": _as_text(concept.get("node_role")),
        "difficulty": int(concept.get("difficulty") or 1),
    }


def _get_collection():
    """打开持久化 Chroma collection；首次调用时自动创建。"""
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _embed_texts_ollama(texts: list[str]) -> list[list[float]]:
    """
    使用本地 Ollama bge-m3 生成 embedding。

    优先使用新版 ollama.embed 的批量接口；如果本地 ollama 包版本较旧，
    则回退到 ollama.embeddings 的单条接口。
    """
    import ollama

    if not texts:
        return []

    embed = getattr(ollama, "embed", None)
    if embed is not None:
        response = embed(model=OLLAMA_EMBEDDING_MODEL, input=texts)
        embeddings = response.get("embeddings")
        if embeddings is not None:
            return embeddings

    embeddings: list[list[float]] = []
    for text in texts:
        response = ollama.embeddings(model=OLLAMA_EMBEDDING_MODEL, prompt=text)
        embeddings.append(response["embedding"])
    return embeddings


def _embed_texts_siliconflow(texts: list[str]) -> list[list[float]]:
    """
    使用 SiliconFlow 的 OpenAI 兼容 embeddings API 生成 BAAI/bge-m3 向量。

    需要在 .env 中配置：
        EMBEDDING_PROVIDER=siliconflow
        SILICONFLOW_API_KEY=...
    """
    from openai import OpenAI

    if not texts:
        return []

    api_key = os.getenv("SILICONFLOW_API_KEY")
    if not api_key:
        raise RuntimeError("使用 SiliconFlow embedding 时需要设置 SILICONFLOW_API_KEY")

    client = OpenAI(api_key=api_key, base_url=SILICONFLOW_BASE_URL)
    embeddings: list[list[float]] = []

    # 分批请求，避免一次性提交 1389 个概念时请求体过大。
    for start in range(0, len(texts), SILICONFLOW_BATCH_SIZE):
        batch = texts[start:start + SILICONFLOW_BATCH_SIZE]
        response = client.embeddings.create(
            model=SILICONFLOW_EMBEDDING_MODEL,
            input=batch,
            encoding_format="float",
        )
        embeddings.extend(item.embedding for item in response.data)

    return embeddings


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """根据 EMBEDDING_PROVIDER 选择本地或远端 embedding 服务。"""
    if EMBEDDING_PROVIDER == "ollama":
        return _embed_texts_ollama(texts)
    if EMBEDDING_PROVIDER == "siliconflow":
        return _embed_texts_siliconflow(texts)

    raise ValueError(
        "EMBEDDING_PROVIDER 只支持 'ollama' 或 'siliconflow'，"
        f"当前值为：{EMBEDDING_PROVIDER}"
    )


def build_index(concepts: list[dict]) -> None:
    """
    构建/更新向量索引。

    Args:
        concepts: data/concepts.json 中的 Concept 节点列表。

    说明：
        - 使用 Concept.name 作为 Chroma id，保证重复运行时 upsert 覆盖旧记录；
        - 不写入任何图谱数据文件，只更新 chroma_db/ 中的本地向量库。
    """
    if not concepts:
        return

    ids = [_as_text(concept.get("name")) for concept in concepts]
    documents = [_concept_to_document(concept) for concept in concepts]
    metadatas = [_concept_to_metadata(concept) for concept in concepts]
    embeddings = _embed_texts(documents)

    collection = _get_collection()
    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )


def query(question: str, top_k: int = 5) -> list[dict]:
    """
    检索与问题最相关的 top_k 个概念。

    Returns:
        [{"name": str, "definition": str, "score": float}, ...]

    score 由 Chroma 的距离转换而来：距离越小，score 越大。
    """
    collection = _get_collection()
    if collection.count() == 0:
        build_index(load_concepts())
        collection = _get_collection()

    question_embedding = _embed_texts([question])[0]
    result = collection.query(
        query_embeddings=[question_embedding],
        n_results=top_k,
        include=["metadatas", "distances"],
    )

    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    hits: list[dict] = []
    for metadata, distance in zip(metadatas, distances):
        # cosine 距离越小越相关；用 1/(1+d) 转成“越大越相关”的直观分数。
        score = 1.0 / (1.0 + float(distance))
        hits.append({
            "name": _as_text(metadata.get("name")),
            "definition": _as_text(metadata.get("definition")),
            "score": float(score),
        })

    return hits
