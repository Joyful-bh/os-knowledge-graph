# 操作系统课程知识图谱

## 1. 项目简介

本项目面向《操作系统》课程，构建了一个以课程概念、习题和知识关系为核心的知识图谱，并在此基础上实现问答、错题诊断、掌握度估计和学习路径规划。

项目主线是：知识图谱不只是问答材料库，更适合表达“先修依赖”和“多跳推理”。因此，本项目先构建操作系统知识图谱，再通过 GraphRAG 验证图谱多跳关系对问答的增益，最后把同一套 `PREREQUISITE` 关系用于错题诊断和学习导航。

目标系统可以概括为：**会问答、会诊断、会导航的操作系统学习辅助系统**。

---

## 2. 系统功能

已实现功能包括：

- **知识图谱加载与验证**：默认使用 NetworkX 内存图，无需启动外部图数据库；Neo4j 写入为可选能力。
- **子图检索**：根据一个或多个概念召回指定边类型和跳数范围内的局部知识图谱。
- **Vector RAG**：使用向量检索从概念库中召回相关概念，作为普通 RAG 基线。
- **GraphRAG**：先向量召回种子概念，再扩展图谱邻域，将节点定义和边描述作为上下文交给 LLM 回答。
- **多跳评测与对比实验**：生成基于图谱关系的多跳问题，比较 Vector RAG 与 GraphRAG 的表现。
- **错题诊断**：输入错题 pid，通过 `TESTS` 边定位考查概念，再沿 `PREREQUISITE` 反向追溯薄弱先修点。
- **掌握度估计**：根据错题诊断结果为概念计算 weakness、mastery 和风险等级。
- **学习路径规划**：合并薄弱概念的先修子图，尽量按 `PREREQUISITE` 拓扑顺序推荐复习路径，并给出相关练习题。
- **Streamlit Web 界面**：提供 GraphRAG 问答、子图检索、错题诊断、学习路径规划和实验结果展示。

需要说明的是，GraphRAG 问答和评测会调用大模型；子图检索、错题诊断、掌握度估计和学习路径规划本身不依赖 LLM。

---

## 3. 三阶段工作总结

### Phase 1：操作系统知识图谱构建

Phase 1 完成了课程知识图谱的基础数据和共享接口：

- `data/concepts.json`：概念白名单，包含 1389 个 Concept 节点。
- `data/edges.json`：图谱关系，包含 4978 条去重后边。
- `data/candidates/problems_raw.json`：306 道习题原始数据。
- `src/kg/load.py`：默认构建 NetworkX 图，并提供 Neo4j 可选写入。
- `src/kg/validate.py`：提供 PREREQUISITE 环检测、孤立节点检查和覆盖度检查。
- `src/retrieval/subgraph.py`：提供 RAG 和诊断共同使用的子图接口。

核心约束是：`PREREQUISITE` 表示先修依赖，必须保持有向无环，直接支撑诊断和路径规划。

### Phase 2：RAG 问答与多跳评测

Phase 2 完成了两套问答系统和实验验证：

- `src/retrieval/vector_rag.py`：向量 RAG 基线。
- `src/retrieval/graph_rag.py`：图谱增强 RAG。
- `src/eval/multihop_set.py`：从图谱中确定性生成多跳评测集。
- `src/eval/compare.py`：运行 Vector RAG 与 GraphRAG 对比实验。

实验结果保存到 `data/phase2_compare_results.json`。如果该文件不存在，可运行：

```bash
python -m src.eval.multihop_set
python -m src.eval.compare
```

运行完成后，Web 端“实验结果”页面会读取并展示其中的 accuracy 和 details。

### Phase 3：错题诊断、掌握度估计与学习路径

Phase 3 将图谱关系用于学习诊断：

- `src/diagnosis/trace.py`：实现“错题 pid -> 考查概念 -> 先修链溯源 -> 薄弱候选概念”。
- `src/diagnosis/mastery.py`：根据错题诊断结果估计概念掌握度和风险等级。
- `src/path/planner.py`：根据薄弱概念或错题列表生成推荐学习路径。
- `scripts/demo_phase3.py`：演示 Phase 3 完整链路，并可保存结构化结果。
- `web/services.py` 与 `web/app.py`：封装后端能力并提供交互界面。

---

## 4. 技术架构

```text
data/concepts.json + data/edges.json + problems
        |
        v
src.kg.load.get_graph()  ->  NetworkX MultiDiGraph
        |
        +--> src.retrieval.subgraph.get_subgraph()
        |       |
        |       +--> GraphRAG 上下文扩展
        |
        +--> src.retrieval.subgraph.get_prereq_ancestors()
                |
                +--> 错题诊断 trace.py
                        |
                        +--> 掌握度模型 mastery.py
                                |
                                +--> 学习路径 planner.py

web/app.py -> web/services.py -> src/* 核心模块
```

主要技术栈：

- Python 3.10+
- NetworkX：默认图存储与图算法
- ChromaDB / Ollama bge-m3：向量检索与 embedding
- DeepSeek API：LLM 问答与评测判断
- Streamlit + pandas：Web 展示与表格交互
- Neo4j：可选图数据库后端，不是默认运行依赖

---

## 5. 数据与 Schema 简介

Schema 以“每类结构都服务下游功能”为原则，完整定义见 `docs/OS_KG_Schema.md`。

### 节点类型

| 节点 | 说明 |
|---|---|
| `Concept` | 主体知识点，包含概念、机制、算法、问题，通过 `node_role` 区分 |
| `Problem` | 习题节点，是错题诊断的输入锚点 |

算法不单独建节点类型，而是 `node_role="算法"` 的 Concept，并可包含 `preemptive`、`starvation_free`、`complexity`、`scenario` 等可选字段。

### 边类型

| 边类型 | 方向 | 用途 |
|---|---|---|
| `PREREQUISITE` | Concept -> Concept | 先修依赖，支撑诊断和学习路径 |
| `PART_OF` | Concept -> Concept | 知识层级与主题聚合 |
| `RELATED` | Concept -- Concept | RAG 上下文扩展 |
| `CONFUSABLE` | Concept -- Concept | 易混淆概念对比 |
| `TESTS` | Problem -> Concept | 习题考查概念，连接错题与知识点 |
| `SOLVES` | Concept -> Concept | 机制/算法解决问题，支撑因果链问答 |

当前数据概况：

| 内容 | 数量 |
|---|---:|
| Concept 节点 | 1389 |
| 名称（含别名） | 3067 |
| Problem 节点 | 306 |
| 边（去重后） | 4978 |
| PREREQUISITE | 496 |
| PART_OF | 1965 |
| RELATED | 1481 |
| SOLVES | 312 |
| TESTS | 684 |
| CONFUSABLE | 40 |
| PREREQUISITE 覆盖率 | 40.8% |

---

## 6. 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量文件
cp .env.example .env

# 3. 如果需要 GraphRAG / Vector RAG，准备 embedding 模型
ollama pull bge-m3

# 4. 验证图谱质量
python -c "from src.kg.validate import run_all_checks; run_all_checks()"
```

如果只使用子图检索、错题诊断、掌握度估计和学习路径规划，可以不调用大模型，也可以不配置 `DEEPSEEK_API_KEY`。如果要使用 GraphRAG 问答或运行 LLM 评测，需要在 `.env` 中配置：

```text
DEEPSEEK_API_KEY=sk-...
```

可选 Neo4j 配置如下，仅在调用 Neo4j 写入能力时需要：

```text
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
```

---

## 7. Web 端启动方法

安装依赖并配置 `.env` 后运行：

```bash
streamlit run web/app.py
```

Web 页面包含 5 个 Tab：

1. GraphRAG 问答
2. 子图检索
3. 错题诊断
4. 学习路径规划
5. 实验结果

GraphRAG 问答需要 `DEEPSEEK_API_KEY`、向量检索依赖和 embedding 服务可用。其他诊断与路径功能主要依赖本地图谱数据，可离线运行。

---

## 8. 核心 API 示例

### 加载图

```python
from src.kg.load import get_graph

G = get_graph()
```

### 子图检索

```python
from src.retrieval.subgraph import get_subgraph

result = get_subgraph(
    concept_names=["记录型信号量", "PV操作"],
    edge_types=["PREREQUISITE", "RELATED", "SOLVES"],
    hops=2,
)

print(result["nodes"])
print(result["edges"])
```

### GraphRAG 问答

```python
from src.retrieval.graph_rag import answer

text = answer("为什么学习临界区有助于理解信号量？", top_k=3, hops=2)
print(text)
```

### 错题诊断

```python
from src.diagnosis.trace import trace_wrong_problems

result = trace_wrong_problems(["CH3_Q2", "CH4_Q7"])
print(result["weak_candidates"])
```

### 掌握度估计

```python
from src.diagnosis.mastery import estimate_mastery

mastery = estimate_mastery(wrong_pids=["CH3_Q2", "CH4_Q7"])
print(mastery)
```

### 学习路径规划

```python
from src.path.planner import plan_from_wrong_problems

path = plan_from_wrong_problems(["CH3_Q2", "CH4_Q7"], max_items=10)
print(path)
```

### Web 服务层封装

```python
from web.services import diagnose_wrong_pids, plan_learning_path

diagnosis = diagnose_wrong_pids(["CH3_Q2"])
learning_path = plan_learning_path(["CH3_Q2"])
```

---

## 9. 实验与演示

### Phase 2：RAG 对比实验

```bash
python -m src.eval.multihop_set
python -m src.eval.compare
```

`multihop_set.py` 会生成 `data/multihop_questions.json`。`compare.py` 会运行 Vector RAG 与 GraphRAG 对比，并保存 `data/phase2_compare_results.json`。若该结果文件不存在，请先运行上述命令；README 不固定具体实验数值，最终以生成的 JSON 文件为准。

### Phase 3：诊断与路径演示

```bash
python scripts/demo_phase3.py --wrong CH3_Q2 CH4_Q7
```

脚本会输出：

- 错题列表
- 每道错题考查的概念
- 诊断出的薄弱先修点
- 掌握度估计结果
- 推荐学习路径

并保存完整结果到：

```text
data/phase3_demo_result.json
```

---

## 10. 分工说明

本项目按阶段和接口解耦协作：

| 阶段 | 主要内容 | 关键产出 |
|---|---|---|
| Phase 1 | 知识图谱构建与共享接口 | `concepts.json`、`edges.json`、`load.py`、`subgraph.py` |
| Phase 2 | RAG 双系统与多跳评测 | `vector_rag.py`、`graph_rag.py`、`multihop_set.py`、`compare.py` |
| Phase 3 | 认知诊断与学习导航 | `trace.py`、`mastery.py`、`planner.py`、Web 演示 |

跨模块协作的关键接口是 `src/retrieval/subgraph.py`：

- `get_subgraph()`：供 GraphRAG 做图谱上下文扩展。
- `get_prereq_ancestors()`：供诊断和路径规划追溯先修链。

接口格式和数据约定见 `docs/collaboration_contract.md`。

---

## 11. 当前局限与改进方向

当前系统已形成完整闭环，但仍有一些限制：

- **图谱质量依赖人工精校**：尤其是 `PREREQUISITE` 边，方向和粒度会直接影响诊断结果。
- **习题答案覆盖不足**：部分习题 `answer` 为空，当前诊断主要依赖错题 pid，而不是自动判分。
- **掌握度模型较简化**：当前 weakness 和 mastery 使用可解释规则计算，没有进行参数学习或长期学习记录建模。
- **GraphRAG 依赖外部服务**：问答需要 embedding 服务、向量库和 DeepSeek API，离线环境下只能使用图检索和诊断路径功能。
- **Web 端以演示为主**：当前 Streamlit 页面适合课程答辩展示，尚未实现用户账号、学习历史持久化和可视化图布局交互。
- **Neo4j 是可选后端**：默认运行路径使用 NetworkX，Neo4j Browser 展示需要额外启动服务和写入图数据。

后续可以改进的方向：

- 扩充和人工校验更多高价值先修边、易混淆边。
- 引入学生多次答题记录，改进掌握度估计。
- 增加图可视化组件，展示局部先修链和推荐路径。
- 将诊断结果与题目推荐、复习材料推荐进一步结合。
- 对 GraphRAG 的召回、上下文压缩和评测指标做更细粒度分析。

---

## 相关文档

- `docs/OS_KG_Schema.md`：Schema 权威定义。
- `docs/collaboration_contract.md`：数据格式、接口约定和协作说明。
- `CLAUDE.md`：项目目标、开发约定和阶段说明。
