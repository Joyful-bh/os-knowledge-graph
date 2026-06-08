# CLAUDE.md

## 项目目标

构建《操作系统》课程知识图谱,并基于它实现一个**「会诊断、会导航的 OS 学习系统」**。
- **表层功能**(基础分):知识问答、RAG。
- **内核亮点**:用 GraphRAG 支撑的认知诊断与学习路径规划。
- **核心叙事**(答辩主线):普通做法把 KG 当问答知识库;我们发现**多跳推理**才是 KG 相对向量检索的真正增益(实验验证)→ 把该增益**用在**认知诊断的错误溯源上 → 最终做成能诊断、能导航的学习系统。

这是 2 人 + AI 辅助的课程大作业。代码追求**清晰可讲、可复现**,而非工程级健壮性。

## 技术栈

- 语言:Python 3.10+
- 图存储:**NetworkX 为默认**（内存图，无需外部服务）；Neo4j 懒加载可选（`load_to_neo4j()`）
- 向量检索:本地向量库(如 chroma / faiss),sentence-transformers 或所选 embedding
- LLM:通过统一封装的 `llm_client` 调用(抽取、问答、诊断均走它)
- 可视化:轻量前端(路径/诊断结果)；Neo4j Browser 可选（需自行启动服务）

## 知识图谱 Schema(权威定义,以此为准)

**节点类型**
- `Concept`:唯一主体节点。属性 `name`(全图唯一)、`definition`、`chapter`、`difficulty`(1-3)、`node_role`(`概念`/`机制`/`算法`/`问题`)、`aliases`(别名数组)。当 `node_role="算法"` 时可填专属字段:`preemptive`、`starvation_free`、`complexity`、`scenario`。
- `Problem`:习题。属性 `pid`、`stem`、`answer`、`type`(`记忆`/`关系`/`综合`)。

**边类型**(每条边对应一个下游功能,不设计无用结构)
- `PREREQUISITE`(有向,Concept→Concept):先修依赖。★命脉,必须是 DAG。支撑学习路径 + 诊断溯源。
- `PART_OF`(有向,Concept→Concept):从属/包含。取自章节结构。
- `RELATED`(无向,Concept—Concept):弱关联。RAG 上下文扩展。
- `CONFUSABLE`(无向,Concept—Concept):易混淆,带 `dimensions` 属性。混淆诊断器。
- `TESTS`(有向,Problem→Concept):习题考查哪些概念。错题→知识点链路。
- `SOLVES`(有向,Concept→Concept):机制/算法 解决 问题。因果链问答(多跳素材)。

**关键约束**
- `PREREQUISITE` 全图必须无环(DAG)。任何写入后都要能跑环检测。
- `Concept.name` 全图唯一;别名归一到 `aliases`,不建独立别名节点。
- 算法**不是**独立节点类型,就是 `node_role="算法"` 的 Concept。

> 完整 Schema 见 `docs/OS_KG_Schema.md`。修改 Schema 必须同步更新该文档与本文件。

## 目录结构(约定)

```
.
├── CLAUDE.md
├── docs/
│   └── OS_KG_Schema.md        # Schema 权威文档
├── data/
│   ├── raw/                   # 课件/教材原始文本
│   ├── concepts.json          # 概念白名单(闭集,1389 个)
│   ├── edges.json             # 各类边(4978 条,DAG 已验证)
│   └── candidates/
│       └── problems_raw.json  # 习题原始数据(306 道)
├── src/
│   ├── llm_client.py          # LLM 调用统一封装(温度、JSON解析、重试)
│   ├── kg/
│   │   ├── extract.py         # LLM 抽取(概念/边),分类型
│   │   ├── normalize.py       # 实体归一化(去重/合并别名)
│   │   ├── load.py            # NetworkX 图加载(默认) + Neo4j 懒加载
│   │   └── validate.py        # 环检测/孤立点/覆盖度自检
│   ├── retrieval/
│   │   ├── vector_rag.py      # 向量 RAG 基线
│   │   ├── subgraph.py        # 子图召回(共享接口,见下)
│   │   └── graph_rag.py       # KG 增强 RAG
│   ├── diagnosis/
│   │   ├── trace.py           # 错题→TESTS→PREREQUISITE 反向溯源
│   │   └── mastery.py         # 掌握度模型(简化 DINA)
│   ├── path/
│   │   └── planner.py         # 学习路径(DAG 拓扑约束搜索)
│   └── eval/
│       ├── multihop_set.py    # 多跳评测集
│       └── compare.py         # 向量RAG vs GraphRAG 对比实验
└── app/                       # 演示前端
```

## 共享接口约定(两人分工的交汇点,务必遵守)

**子图召回接口** `subgraph.py` —— RAG 与诊断模块共用,**已实现**（`src/retrieval/subgraph.py`）:
```python
def get_subgraph(concept_names: list[str],
                 edge_types: list[str] = ["PREREQUISITE", "RELATED", "SOLVES"],
                 hops: int = 2,
                 G: nx.MultiDiGraph | None = None) -> dict:
    """给定一组概念,返回其 N 跳邻域子图（双向 BFS）。
    返回 {"nodes": [...], "edges": [...]},边用 source/target 字段。
    retrieval/graph_rag.py 与 diagnosis/trace.py 都调用此函数。
    """

def get_prereq_ancestors(concept_name: str, max_hops: int = 5, G=None) -> dict:
    """沿 PREREQUISITE 入边反向追溯祖先，nodes 带 depth 字段。诊断专用。"""
```
B 直接调用即可。改动此签名需双方同步。

## 编码约定

- LLM 抽取一律走 `llm_client`,温度 0–0.3,强制 JSON 输出 + 安全解析 + 失败重试。
- 抽取时**给定概念白名单让 LLM 在闭集内选**,禁止自由生成新实体(防别名分裂/幻觉)。
- 分类型抽取:定义类 LLM 抽;PREREQUISITE 用 LLM 出候选 + 人工精校;CONFUSABLE 人工为主。
- 任何写入 Neo4j 的操作后,涉及 PREREQUISITE 的都要能触发 `validate.py` 的环检测。
- 优先写小而清晰、便于答辩讲解的代码;不过度抽象。
- 中文是项目主要语言;概念名、定义、注释用中文,代码标识符用英文。

## 不要做的事

- 不要把算法建成独立节点类型(它是 `node_role="算法"` 的 Concept)。
- 不要引入 `Person`/`Year`/`SAME_AS`/共现边等对功能无贡献的结构。
- 不要让 LLM 自由生成实体名(必须在白名单闭集内选)。
- 不要跳过 PREREQUISITE 的人工精校——它直接决定路径与诊断的可信度。
- 不要追求工程级健壮性而牺牲代码可读性;这是课程作业,清晰可讲优先。

## 当前进度

- [x] 方案设计、Schema v2.0 定稿
- [x] 阶段0:基建(llm_client + 接口约定 + 项目骨架)
- [x] 阶段1:KG 构建(1389 概念 / 4978 边 / DAG 验证 / subgraph.py)
- [ ] 阶段2:RAG 双系统 + 多跳对比实验
- [ ] 阶段3:认知诊断 + 学习路径
- [ ] 阶段4:集成 + 报告 + 答辩 PPT

> 开始新任务前,先确认当前所处阶段,并检查依赖的上游产物是否就绪(如阶段2的 GraphRAG、阶段3均依赖阶段1的图谱)。
