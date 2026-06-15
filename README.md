# 操作系统知识图谱

> 面向《操作系统》课程的**认知诊断与学习导航系统**。以人工精校的 1391 节点 / 4982 边知识图谱为核心，结合 GraphRAG、错题溯源与学习路径规划，构建一套**会问答、会诊断、会导航**的学习辅助工具。

---

## 特性

- **三段式 GraphRAG 问答** — 子图召回的「概念定义 + 关系描述 + 教材原文片段」三类上下文拼接送入 LLM，图谱给"是什么/为什么"，原文给"如何实现/算法步骤"。设计参考 [LightRAG](https://github.com/HKUDS/LightRAG) 的多源上下文与 token 预算思路。
- **错题诊断** — 错题 `pid` → `TESTS` 边定位考查概念 → 沿 `PREREQUISITE` DAG 反向溯源 → 薄弱知识点候选（按层级深度排序，附诊断理由）。
- **掌握度估计** — 基于错题溯源结果，按先修链深度加权计算 `weakness` / `mastery` / 风险等级。可解释规则模型，便于答辩说明。
- **学习路径规划** — 合并多个薄弱概念的先修子图，按 `PREREQUISITE` 拓扑序生成复习路径，每步附推荐习题。
- **多跳评测对比** — 从图谱结构自动生成 1/2/3 跳问题集，对比 Vector RAG 与 GraphRAG 在多跳推理上的差异。
- **交互式图谱可视化** — pyvis 生成单文件 HTML，支持节点角色多形状、按度数标签、点击查看邻居、图例显隐切换、深色/浅色双主题。
- **Streamlit Web 界面** — 5 个 Tab 一键启动：GraphRAG 问答、子图检索、错题诊断、学习路径规划、实验结果展示。

> 子图检索、错题诊断、掌握度估计、学习路径规划**完全离线运行，不依赖任何 LLM/embedding API**。仅 GraphRAG 问答与对比实验需要 LLM。

---

## 快速开始

### 1. 安装

```bash
git clone https://github.com/Joyful-bh/os-knowledge-graph.git
cd os-knowledge-graph
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env，至少填入 DEEPSEEK_API_KEY
# 选择 embedding 提供方：ollama（本地）或 siliconflow（云端）
```

### 3. 启动 Web 界面

```bash
streamlit run web/app.py
```

浏览器打开 `http://localhost:8501` 即可使用。

---

## Python API

```python
# ── 加载图（首次调用从 JSON 构建，后续缓存）────────────────────────────
from src.kg.load import get_graph
G = get_graph()

# ── 子图检索（RAG 与诊断共用接口）───────────────────────────────────
from src.retrieval.subgraph import get_subgraph
result = get_subgraph(
    concept_names=["记录型信号量", "PV操作"],
    edge_types=["PREREQUISITE", "RELATED", "SOLVES"],
    hops=2,
)

# ── GraphRAG 问答（默认启用原文 chunk 增强）─────────────────────────
from src.retrieval.graph_rag import answer
print(answer("银行家算法如何判断系统是否处于安全状态？"))

# 关闭 chunk 退化为纯图谱上下文（用于 A/B 对比）
print(answer("...", use_chunks=False))

# ── 错题诊断 → 薄弱知识点 ──────────────────────────────────────────
from src.diagnosis.trace import trace_wrong_problems
diag = trace_wrong_problems(["CH3_Q2", "CH4_Q7"])
print(diag["weak_candidates"])

# ── 掌握度估计 ─────────────────────────────────────────────────────
from src.diagnosis.mastery import estimate_mastery
print(estimate_mastery(wrong_pids=["CH3_Q2", "CH4_Q7"]))

# ── 学习路径规划 ───────────────────────────────────────────────────
from src.path.planner import plan_from_wrong_problems
print(plan_from_wrong_problems(["CH3_Q2", "CH4_Q7"], max_items=10))
```

---

## CLI 工具

```bash
# 知识图谱质量自检（环检测 / 孤立节点 / PREREQUISITE 覆盖率）
python -c "from src.kg.validate import run_all_checks; run_all_checks()"

# 多跳评测 + RAG 对比实验
python -m src.eval.multihop_set      # 生成 data/multihop_questions.json
python -m src.eval.compare           # 生成 data/phase2_compare_results.json

# 错题诊断与学习路径端到端演示
python scripts/demo_phase3.py --wrong CH3_Q2 CH4_Q7

# 生成交互式知识图谱可视化
python scripts/visualize_kg.py                                # 全图，深色主题
python scripts/visualize_kg.py --limit 150 --theme light      # Top-150 核心节点，浅色主题
python scripts/visualize_kg.py --with-problems                # 包含习题节点
```

---

## 架构

```text
                    ┌──────────────────────────────┐
                    │   Streamlit Web UI (5 Tabs)  │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │        web/services.py       │
                    └──────────────┬───────────────┘
        ┌──────────────────┬───────┴───────┬───────────────────┐
        │                  │               │                   │
  ┌─────▼─────┐    ┌───────▼──────┐  ┌─────▼──────┐    ┌──────▼──────┐
  │ GraphRAG  │    │  错题诊断    │  │ 学习路径   │    │  可视化     │
  │ 问答      │    │  掌握度估计  │  │ 规划       │    │ (pyvis HTML)│
  └──┬──┬──┬──┘    └──────┬───────┘  └─────┬──────┘    └─────────────┘
     │  │  │              │                │
     │  │  │      ┌───────▼────────────────▼────────────┐
     │  │  │      │   subgraph.py（子图召回 + 先修溯源）│
     │  │  │      └────────────────┬─────────────────────┘
     │  │  │                       │
     │  │  │                       ▼
     │  │  │              ┌────────────────────┐
     │  │  │              │  NetworkX KG       │
     │  │  │              │  (kg/load.py)      │
     │  │  │              └────────┬───────────┘
     │  │  │                       │
     │  │  │              ┌────────▼───────────────────────────┐
     │  │  │              │ concepts.json · edges.json · ...   │
     │  │  │              └────────────────────────────────────┘
     │  │  │
     │  │  └───►  chunk_store.py  ─── data/candidates/chunks.json
     │  │             （子图节点 → 包含该节点的教材原文段落）
     │  │
     │  └─────►  vector_rag.py  ─── ChromaDB (bge-m3 embedding)
     │
     └────────►  llm_client.py  ─── DeepSeek API
```

**技术栈**：Python 3.10+ · NetworkX · ChromaDB · bge-m3 (Ollama / SiliconFlow) · DeepSeek API · Streamlit · pyvis

---

## 知识图谱 Schema

### 节点

| 节点 | 说明 |
|---|---|
| `Concept` | 主体知识点；通过 `node_role` 区分 **概念 / 机制 / 算法 / 问题** |
| `Problem` | 习题；作为错题诊断的输入锚点 |

算法不单独建节点类型，而是 `node_role="算法"` 的 Concept，并附带 `preemptive` / `starvation_free` / `complexity` / `scenario` 等可选字段。

### 边（6 种，每种边对应一个下游功能）

| 边类型 | 方向 | 用途 |
|---|---|---|
| `PREREQUISITE` ★ | Concept → Concept | 先修依赖（DAG，命脉） |
| `PART_OF` | Concept → Concept | 知识层级 |
| `RELATED` | Concept — Concept | 弱关联（RAG 上下文扩展） |
| `CONFUSABLE` | Concept — Concept | 易混淆（带 `dimensions` 区分维度） |
| `SOLVES` | Concept → Concept | 机制/算法 解决 问题 |
| `TESTS` | Problem → Concept | 习题考查概念 |

完整 Schema 与字段约束见 [`docs/OS_KG_Schema.md`](docs/OS_KG_Schema.md)。

### 数据规模

| 内容 | 数量 |
|---|---:|
| Concept 节点 | 1391 |
| 名称（含别名） | 3067 |
| Problem 节点 | 306 |
| 边总数（去重） | 4982 |
| ├─ PREREQUISITE | 497 |
| ├─ PART_OF | 1966 |
| ├─ RELATED | 1482 |
| ├─ SOLVES | 313 |
| ├─ TESTS | 684 |
| └─ CONFUSABLE | 40 |
| 教材原文 chunks | 276 |
| PREREQUISITE 覆盖率 | 40.8% |

---

## 项目结构

```
.
├── data/
│   ├── concepts.json                  # 概念白名单
│   ├── edges.json                     # 各类边
│   ├── multihop_questions.json        # 多跳评测集
│   ├── phase2_compare_results.json    # RAG 对比实验结果
│   └── candidates/
│       ├── problems_raw.json          # 习题原始数据
│       └── chunks.json                # 教材原文段落（GraphRAG 反查用）
├── src/
│   ├── kg/                            # 图加载、构建、验证
│   │   ├── load.py                    # NetworkX 默认 + Neo4j 懒加载
│   │   ├── validate.py                # DAG 环检测、覆盖度自检
│   │   ├── extract.py                 # LLM 概念抽取
│   │   ├── extract_edges.py           # LLM 边抽取
│   │   └── normalize.py               # 实体归一化
│   ├── retrieval/                     # RAG 与子图召回
│   │   ├── subgraph.py                # 共享子图接口（RAG + 诊断）
│   │   ├── vector_rag.py              # 向量 RAG 基线
│   │   ├── chunk_store.py             # 原文 chunk 反查
│   │   └── graph_rag.py               # GraphRAG（三段式上下文）
│   ├── diagnosis/                     # 认知诊断
│   │   ├── trace.py                   # 错题→薄弱知识点溯源
│   │   └── mastery.py                 # 掌握度估计
│   ├── path/
│   │   └── planner.py                 # 学习路径规划
│   ├── eval/                          # 评测
│   │   ├── multihop_set.py            # 多跳测试集生成
│   │   └── compare.py                 # RAG 对比实验
│   └── llm_client.py                  # LLM 统一封装
├── web/                               # Streamlit Web 应用
│   ├── app.py
│   └── services.py
├── scripts/
│   ├── visualize_kg.py                # 知识图谱交互式可视化
│   └── demo_phase3.py                 # 端到端诊断+路径演示
└── docs/
    ├── OS_KG_Schema.md                # Schema 权威定义
    └── collaboration_contract.md      # 模块接口约定
```

---

## 配置

`.env` 文件字段（从 `.env.example` 复制后填入实际值）：

| 变量 | 说明 | 必填 |
|---|---|:---:|
| `DEEPSEEK_API_KEY` | DeepSeek LLM API 密钥 | ✓（问答与评测） |
| `EMBEDDING_PROVIDER` | `ollama` 或 `siliconflow` | ✓（RAG 功能） |
| `OLLAMA_EMBEDDING_MODEL` | Ollama 模型名（默认 `bge-m3`） | — |
| `SILICONFLOW_API_KEY` | SiliconFlow API 密钥 | `provider=siliconflow` 时需要 |
| `SILICONFLOW_BASE_URL` | 默认 `https://api.siliconflow.com/v1` | — |
| `SILICONFLOW_EMBEDDING_MODEL` | 默认 `BAAI/bge-m3` | — |
| `NEO4J_URI` / `USER` / `PASSWORD` | Neo4j 后端配置 | 仅启用 Neo4j 时 |

**注意**：默认运行路径使用 NetworkX 内存图，**无需安装 Neo4j**。

---

## 文档

- [Schema 权威定义](docs/OS_KG_Schema.md) — 节点 / 边类型、字段约束、设计原则
- [模块协作接口](docs/collaboration_contract.md) — 模块间依赖、数据格式约定

---

## 致谢

本项目为北京航空航天大学《知识图谱》课程大作业。GraphRAG 的三段式上下文与多源召回设计参考自 [LightRAG (HKUDS)](https://github.com/HKUDS/LightRAG)。
