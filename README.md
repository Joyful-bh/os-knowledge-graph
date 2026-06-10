# 操作系统课程知识图谱

**「会诊断、会导航的 OS 学习系统」** —— 2 人 + AI 辅助课程大作业。

核心叙事：把 PREREQUISITE 多跳推理用于认知诊断（错题→薄弱先修链），并通过对比实验验证 GraphRAG 相对向量 RAG 的真实增益。

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt
ollama pull bge-m3

# 2. 配置 API Key（GraphRAG 问答需要 DEEPSEEK_API_KEY）
cp .env.example .env    # 填入 DEEPSEEK_API_KEY

# 3. 验证图谱质量
python -c "from src.kg.validate import run_all_checks; run_all_checks()"

# 4. 试用子图召回
python -c "
from src.retrieval.subgraph import get_subgraph
r = get_subgraph(['记录型信号量'], hops=2)
print(f'节点：{len(r[\"nodes\"])}，边：{len(r[\"edges\"])}')
"

# 5. 生成 Phase 2 多跳评测集并运行对比实验（默认会调用 Ollama + DeepSeek）
python -m src.eval.multihop_set
python -m src.eval.compare

# 6. 启动 Web 端
streamlit run web/app.py
```

如果只使用子图检索、错题诊断、掌握度估计和学习路径规划，可以不调用大模型，也不需要配置 `DEEPSEEK_API_KEY`。如果要使用 GraphRAG 问答，需要在 `.env` 中配置 `DEEPSEEK_API_KEY`，并确保向量检索依赖和 embedding 服务可用。

---

## 项目结构

```
.
├── data/
│   ├── concepts.json          # ✅ 1389 个概念（闭集）
│   ├── edges.json             # ✅ 4978 条边（6 种类型）
│   ├── multihop_questions.json     # ✅ Phase 2：75 条评测问题
│   ├── phase2_compare_results.json # ✅ Phase 2：向量 RAG vs GraphRAG 结果
│   └── candidates/
│       └── problems_raw.json  # ✅ 306 道习题（Phase 3 精校后移至 data/problems.json）
├── src/
│   ├── llm_client.py          # ✅ DeepSeek API 封装（温度/JSON解析/重试）
│   ├── kg/
│   │   ├── load.py            # ✅ NetworkX 图加载（默认）+ Neo4j 懒加载
│   │   ├── validate.py        # ✅ 环检测 / 孤立节点 / 覆盖率报告
│   │   ├── extract.py         # ✅ 概念抽取
│   │   ├── extract_edges.py   # ✅ 边抽取（4 层 PREREQUISITE 策略）
│   │   └── normalize.py       # ✅ 实体归一化
│   ├── retrieval/
│   │   ├── subgraph.py        # ✅ 子图召回（RAG + 诊断共用接口）
│   │   ├── vector_rag.py      # ✅ Phase 2：向量 RAG 基线
│   │   └── graph_rag.py       # ✅ Phase 2：GraphRAG
│   ├── diagnosis/
│   │   ├── trace.py           # 🔲 Phase 3：错题→先修链溯源
│   │   └── mastery.py         # 🔲 Phase 3：掌握度模型
│   ├── path/
│   │   └── planner.py         # 🔲 Phase 3：学习路径规划
│   └── eval/
│       ├── multihop_set.py    # ✅ Phase 2：多跳测试集
│       └── compare.py         # ✅ Phase 2：向量 RAG vs GraphRAG 对比
└── docs/
    ├── OS_KG_Schema.md        # Schema 权威文档
    └── collaboration_contract.md  # 双人协作接口约定
```

---

## 数据统计（Phase 1 完成状态）

| 内容 | 数量 |
|------|------|
| Concept 节点 | 1389 个 |
| 名称（含别名） | 3067 个 |
| Problem 节点 | 306 个 |
| 边（总计，含无向反向） | ~6500 条 |
| 边（去重后） | 4978 条 |
| PREREQUISITE | 496 条（DAG 已验证无环） |
| PART_OF | 1965 条 |
| RELATED | 1481 条 |
| SOLVES | 312 条 |
| TESTS | 684 条 |
| CONFUSABLE | 40 条 |
| PREREQUISITE 覆盖率 | 40.8%（567/1389 概念） |

---

## 核心 API

### 1. 加载图

```python
from src.kg.load import get_graph

G = get_graph()   # nx.MultiDiGraph，首次加载后缓存
# G 可以传给任何需要图的函数，避免重复加载
```

### 2. 子图召回（RAG 用）

```python
from src.retrieval.subgraph import get_subgraph

result = get_subgraph(
    concept_names=["记录型信号量", "PV操作"],
    edge_types=["PREREQUISITE", "RELATED", "SOLVES"],  # 默认值
    hops=2,
)

# result["nodes"] → [{"name", "node_role", "difficulty", "definition", "chapter"}, ...]
# result["edges"] → [{"type", "source", "target", "description"}, ...]
```

- 起始概念不在图中时静默忽略，不报错
- RELATED / CONFUSABLE 为无向边，去重后只返回一条

### 3. 先修溯源（诊断用）

```python
from src.retrieval.subgraph import get_prereq_ancestors

result = get_prereq_ancestors(
    concept_name="死锁避免",
    max_hops=5,
)

# result["nodes"] → 每个节点带 "depth" 字段（0=目标概念，1=直接先修，…）
# result["edges"] → 子图内的 PREREQUISITE 边
```

### 4. 实体链接（用户输入 → 规范名）

```python
import json

concepts = json.loads(open("data/concepts.json", encoding="utf-8").read())

def resolve_concept_name(user_input: str) -> str | None:
    for c in concepts:
        if user_input == c["name"]:
            return c["name"]
        if user_input in c.get("aliases", []):
            return c["name"]
    return None  # 回退到向量相似度
```

---

## Phase 2 完成状态（B 同学）

Phase 2 已实现普通向量 RAG、GraphRAG、多跳评测集和对比实验。整体流程：

```text
用户问题
  → vector_rag.query() 检索规范 Concept
  → 普通向量 RAG：只使用 name + definition
  → GraphRAG：调用 get_subgraph() 扩展图谱上下文
  → llm_client.call() 生成答案
  → llm_client.call_json() 判断答案是否覆盖标准答案
```

### `src/retrieval/vector_rag.py`

```python
def build_index(concepts: list[dict]) -> None:
    """使用 bge-m3 生成 embedding，并 upsert 到 chroma_db/os_concepts。"""

def query(question: str, top_k: int = 5) -> list[dict]:
    """返回 [{"name": str, "definition": str, "score": float}, ...]"""
```

- Embedding 模型：默认使用本地 Ollama `bge-m3`；也支持 SiliconFlow `BAAI/bge-m3`
- 向量库：ChromaDB，持久化目录 `chroma_db/`，collection 名 `os_concepts`
- 索引文本包含 `name`、`definition`、`chapter`、`node_role`、`difficulty`、`aliases`，算法节点还会加入 `preemptive`、`starvation_free`、`complexity`、`scenario`
- `query()` 在 collection 为空时会自动读取 `data/concepts.json` 并构建索引

Embedding provider 通过 `.env` 切换：

```bash
# 默认：本地 Ollama
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBEDDING_MODEL=bge-m3

# 跨平台验证：SiliconFlow
EMBEDDING_PROVIDER=siliconflow
SILICONFLOW_API_KEY=sk-...
SILICONFLOW_BASE_URL=https://api.siliconflow.com/v1
SILICONFLOW_EMBEDDING_MODEL=BAAI/bge-m3
```

切换 embedding provider 后，建议生成新的 `chroma_db_2/` 或重新调用 `build_index(load_concepts())`，确保索引和查询使用同一套 embedding 服务。

### `src/retrieval/graph_rag.py`

```python
def answer(question: str, top_k: int = 3, hops: int = 2) -> str:
    """
    向量检索 top_k 概念
    → get_subgraph() 扩展 N 跳邻域
    → 将节点定义 + 边描述拼接为上下文
    → LLM 生成答案
    """
```

GraphRAG 使用向量检索返回的规范 Concept 名称作为种子，调用共享接口：

```python
get_subgraph(
    concept_names=seed_names,
    edge_types=["PREREQUISITE", "RELATED", "SOLVES", "CONFUSABLE", "PART_OF"],
    hops=hops,
)
```

随后将概念字段和边描述格式化为中文上下文，要求 LLM 只基于图谱上下文回答；如果依据不足，需要明确说明“图谱中没有足够依据”。

### `src/eval/multihop_set.py` + `src/eval/compare.py`

多跳评测集从 `data/concepts.json` 和 `data/edges.json` 确定性生成，不调用 LLM。当前生成 75 条问题：

| hops | 数量 | 用途 |
|------|------|------|
| 1 | 15 | 基础关系题 |
| 2 | 45 | 核心多跳评测 |
| 3 | 15 | 挑战题 |

问题模板避免在 `question` 中暴露“几跳”“PREREQUISITE”“路径”等内部结构；标准答案和 `path` 字段保留完整图谱依据，便于自动评判。

运行方式：

```bash
# 生成 data/multihop_questions.json，并打印 hops 分布
python -m src.eval.multihop_set

# 默认读取 75 条评测题，保存 data/phase2_compare_results.json
python -m src.eval.compare
```

当前一次完整对比实验结果：

| 方法 | 准确率 |
|------|--------|
| 向量 RAG | 58.67% |
| GraphRAG | 70.67% |

该结果支持项目主线：在需要中间节点和关系推理的问题上，GraphRAG 相比只看概念定义的向量 RAG 有更稳定的增益。

---

## 环境配置

`.env` 文件需要：

```
DEEPSEEK_API_KEY=sk-...
# 可选，只在使用 Neo4j 时填写
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
```

**注意**：Neo4j 为可选项。`get_graph()` 默认加载 NetworkX，不需要任何数据库服务。

---

## 相关文档

- [Schema 权威定义](docs/OS_KG_Schema.md) — 节点/边类型、字段约束
- [双人协作约定](docs/collaboration_contract.md) — 接口格式、分工说明、实体链接策略
