# 两人分工协作约定

> 本文档是双方并行开发的"约定"。所有跨模块调用都以本文档为准，改动需双方确认。
>
> **A 负责**：知识图谱构建（概念抽取、入库、精校）
> **B 负责**：应用层（向量 RAG、GraphRAG、学习路径、认知诊断）

---

## 1. 为什么可以并行，有什么风险

双方的工作在逻辑上可以解耦：B 的算法不依赖"完整图谱"，只依赖"数据格式"。
B 可以先用手工写的小型 mock 数据调通算法，等 A 的真实 KG 进来后再集成。

**主要风险：格式对不上。** 如果数据格式与代码预期不一致，集成时会大量返工。
本文档就是为了消灭这个风险。

---

## 2. 数据文件格式约定

这三个 JSON 文件是 A 的输出、B 的输入，格式必须严格遵守。

### 2.1 `data/concepts.json` — 概念白名单

A 维护，B 用来建向量索引。

```json
[
  {
    "name": "进程控制块",
    "definition": "存储进程所有运行状态信息的数据结构，是进程存在的唯一标识。",
    "chapter": "4.进程与线程",
    "difficulty": 1,
    "node_role": "概念",
    "aliases": ["PCB", "process control block"]
  },
  {
    "name": "时间片轮转",
    "definition": "将CPU时间分成固定长度的时间片，按队列顺序依次分配给各就绪进程。",
    "chapter": "4.调度",
    "difficulty": 2,
    "node_role": "算法",
    "aliases": ["RR调度", "Round Robin"],
    "preemptive": true,
    "starvation_free": true,
    "complexity": "O(1) 调度决策",
    "scenario": "分时系统、交互式环境"
  },
  {
    "name": "死锁",
    "definition": "多个进程因循环等待资源而永久阻塞的状态。",
    "chapter": "4.死锁",
    "difficulty": 2,
    "node_role": "问题",
    "aliases": ["deadlock"]
  }
]
```

**字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 规范名称，**全图唯一**，B 的所有查询以此为主键 |
| `definition` | string | 是 | 一句话定义，20–50 字，向量索引的核心文本 |
| `chapter` | string | 是 | 所属章节，格式固定为 `"序号.名称"`（见附录） |
| `difficulty` | int 1–3 | 是 | 1基础 / 2进阶 / 3综合 |
| `node_role` | enum | 是 | `"概念"` / `"机制"` / `"算法"` / `"问题"` |
| `aliases` | string[] | 否 | 别名列表，B 用于实体链接，A 负责维护准确性 |
| `preemptive` | bool | 仅算法 | 是否抢占式 |
| `starvation_free` | bool | 仅算法 | 是否避免饥饿 |
| `complexity` | string | 仅算法 | 时间/调度开销 |
| `scenario` | string | 仅算法 | 典型适用场景 |

### 2.2 `data/edges.json` — 所有边

A 维护（PREREQUISITE 需人工精校），B 用于了解图结构、辅助测试。

```json
[
  {
    "type": "PREREQUISITE",
    "source": "临界区",
    "target": "信号量机制",
    "description": "信号量机制以临界区问题为解决对象，理解临界区是掌握信号量的前提。"
  },
  {
    "type": "PART_OF",
    "source": "时间片轮转",
    "target": "进程调度",
    "description": "时间片轮转是进程调度策略之一。"
  },
  {
    "type": "RELATED",
    "source": "信号量机制",
    "target": "管程",
    "description": "管程是对信号量机制的高级抽象，两者均用于进程同步。"
  },
  {
    "type": "SOLVES",
    "source": "银行家算法",
    "target": "死锁",
    "description": "银行家算法通过安全状态检测避免死锁的发生。"
  },
  {
    "type": "CONFUSABLE",
    "source": "进程",
    "target": "线程",
    "description": "进程与线程均为执行单位，但资源拥有粒度不同。",
    "dimensions": [
      {"aspect": "资源拥有", "a_value": "独立地址空间", "b_value": "共享进程地址空间"},
      {"aspect": "调度单位", "a_value": "否（以线程为单位）", "b_value": "是"}
    ]
  }
]
```

**`source` / `target` 的值必须与 `concepts.json` 中的 `name` 字段精确匹配。**

边类型汇总：

| 类型 | 方向 | source | target | 备注 |
|------|------|--------|--------|------|
| `PREREQUISITE` | 有向 | Concept | Concept | DAG 约束，必须无环 |
| `PART_OF` | 有向 | Concept | Concept | 子概念→父概念 |
| `RELATED` | 无向 | Concept | Concept | 只存一条，BFS 双向 |
| `CONFUSABLE` | 无向 | Concept | Concept | 同上，带 `dimensions` |
| `SOLVES` | 有向 | Concept | Concept | 机制/算法→问题 |
| `TESTS` | 有向 | Problem | Concept | pid→概念名 |

### 2.3 `data/candidates/problems_raw.json` — 习题原始数据

> 当前路径：`data/candidates/problems_raw.json`（306 道题）。Phase 3 精校后将移至 `data/problems.json`。

```json
[
  {
    "pid": "CH3_Q2",
    "stem": "用银行家算法判断以下系统是否处于安全状态：……",
    "answer": "安全状态。安全序列为 P1→P3→P2。",
    "type": "综合",
    "chapter": "3.处理机调度与死锁"
  }
]
```

- `pid` 格式固定为 `CH{章号}_Q{题号}`，方便按章筛选
- `answer` 教材无答案时为空字符串 `""`
- TESTS 边（习题→概念）存于 `data/edges.json`，而非 problem 节点内

---

## 3. 核心共享接口：`subgraph.py`

这是唯一一个"A 的数据直接驱动 B 的算法"的接口，必须严格对齐。

**当前状态：已实现（`src/retrieval/subgraph.py`）**，B 直接调用即可，无需自行实现。

### 3.1 `get_subgraph()` — RAG 上下文扩展

```python
from src.retrieval.subgraph import get_subgraph

result = get_subgraph(
    concept_names=["记录型信号量"],
    edge_types=["PREREQUISITE", "RELATED", "SOLVES"],  # 默认值
    hops=2,                                             # 默认值
    G=None,                                             # None 时自动加载图
)
```

**参数说明：**

| 参数 | 说明 |
|------|------|
| `concept_names` | 起始概念的规范名称列表，必须与 `Concept.name` 精确匹配 |
| `edge_types` | 要遍历的边类型，只能是 Schema 中定义的六种之一 |
| `hops` | 跳数。`hops=1` 返回直接邻居；`hops=2` 返回邻居的邻居 |
| `G` | 可选 NetworkX 图对象；None 时自动调用 `get_graph()` |

**返回格式：**

```python
{
    "nodes": [
        {
            "name":       "记录型信号量",
            "node_role":  "机制",
            "difficulty": 2,
            "definition": "用于进程同步与互斥的整型变量，支持P/V原子操作。",
            "chapter":    "4.进程同步"
        },
        ...
    ],
    "edges": [
        {
            "type":        "PREREQUISITE",
            "source":      "临界区",
            "target":      "记录型信号量",
            "description": "信号量机制以临界区问题为解决对象，理解临界区是掌握信号量的前提。"
        },
        ...
    ]
}
```

**返回约定：**
- 起始概念本身一定包含在 `nodes` 中（即使孤立节点）
- `edges` 只包含 `nodes` 集合内部的边
- 节点固定 5 个字段：`name`、`node_role`、`difficulty`、`definition`、`chapter`
- 边固定 4 个字段：`type`、`source`、`target`、`description`
- `RELATED` / `CONFUSABLE` 为无向边，去重后只出现一条（source < target 字典序）
- 不在图中的起始概念名被静默忽略，不报错

### 3.2 `get_prereq_ancestors()` — 诊断溯源专用

```python
from src.retrieval.subgraph import get_prereq_ancestors

result = get_prereq_ancestors(
    concept_name="银行家算法",
    max_hops=5,   # 默认值
    G=None,
)
```

沿 PREREQUISITE 入边**反向**追溯祖先概念，用于诊断模块（`diagnosis/trace.py`）。

**返回格式：**

```python
{
    "nodes": [
        {"name": "银行家算法", "depth": 0, "node_role": "算法", ...},
        {"name": "安全状态",   "depth": 1, "node_role": "概念", ...},
        {"name": "死锁避免",   "depth": 2, "node_role": "机制", ...},
    ],
    "edges": [
        {"source": "安全状态", "target": "银行家算法", "type": "PREREQUISITE"},
    ]
}
```

- `depth=0` 为目标概念本身，`depth=1` 为直接先修，以此类推
- 只返回 `Concept` 节点（排除 `Problem`）

---

## 4. 图的加载方式（NetworkX，无需外部服务）

**默认后端为 NetworkX，B 无需安装 Neo4j。**

```python
from src.kg.load import get_graph

G = get_graph()   # 首次调用加载 JSON，后续返回缓存
# G 是 nx.MultiDiGraph，可直接传给 get_subgraph(G=G) 避免重复加载
```

Neo4j 为可选后端（`load_to_neo4j()`），需要在 `.env` 中配置：

```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=（各自设置）
```

---

## 5. 实体链接策略（用户输入 → Concept.name）

用户输入"PCB"或"分页"时，系统需要找到对应的规范 `Concept.name`。**双方约定使用方案 A**：

**方案 A：别名表精确匹配**

- A 在 `concepts.json` 的 `aliases` 字段中维护所有常见别名
- B 在调用任何接口前，先用 alias 表将用户输入归一化到规范名

```python
# B 的实体链接逻辑（示意）
def resolve_concept_name(user_input: str, concepts: list[dict]) -> str | None:
    for c in concepts:
        if user_input == c["name"]:
            return c["name"]
        if user_input in c.get("aliases", []):
            return c["name"]
    return None  # 未找到，可回退到向量最近邻
```

**分工责任：**
- A 保证 `aliases` 字段覆盖常见缩写（PCB、TLB、FCFS、RR 等）
- B 实现 `resolve_concept_name()`，找不到时用向量相似度兜底，不要直接报错

---

## 6. 章节名称统一格式

为了 `chapter` 字段和按章筛选功能的一致性，章节名固定使用以下写法（与 `concepts.json` 中实际使用的值对齐）：

| 章节 | 标准写法 |
|------|----------|
| 第1章（引论） | `"1.引论"` 或 `"1.操作系统引论"` |
| 第2章（启动） | `"2.启动"` |
| 第2章（进程） | `"2.进程管理"` |
| 第3章（内存） | `"3.内存管理"` |
| 第3章（调度） | `"3.处理机调度与死锁"` |
| 第4章（进程） | `"4.进程与线程"` |
| 第4章（同步） | `"4.进程同步"` |
| 第4章（调度） | `"4.调度"` |
| 第4章（死锁） | `"4.死锁"` |
| 第5章（IO）  | `"5.IO管理"` |
| 第5章（设备） | `"5.设备管理"` |
| 第6章（文件） | `"6.文件管理"` |
| 第6章（磁盘） | `"6.磁盘管理"` |
| 第7章（接口） | `"7.操作系统接口"` |
| 第7章（文件） | `"7.文件系统"` |
| 第8章 | `"8.网络操作系统"` |
| 第9章 | `"9.系统安全性"` |
| 第10章 | `"10.UNIX系统内核结构"` |

---

## 7. 当前进度与 B 的接入点

| 阶段 | 状态 | B 的行动 |
|------|------|---------|
| **Phase 1：KG 构建** | **✅ 完成** | 直接读取 `data/concepts.json` / `data/edges.json` |
| 数据规模 | 1389 Concept + 306 Problem，4978 条边 | 无需等待，数据已就绪 |
| `src/kg/load.py` | **✅ 已实现** | `from src.kg.load import get_graph` 即可 |
| `src/retrieval/subgraph.py` | **✅ 已实现** | `from src.retrieval.subgraph import get_subgraph` |
| **Phase 2：RAG** | **🔲 待 B 实现** | 见下表 |
| **Phase 3：诊断 + 路径** | **🔲 待实现** | 依赖 Phase 2 完成 |

**B 需要实现的文件（Phase 2）：**

| 文件 | 功能 | 依赖 |
|------|------|------|
| `src/retrieval/vector_rag.py` | 向量 RAG 基线 | `data/concepts.json` |
| `src/retrieval/graph_rag.py` | GraphRAG（子图 + LLM） | `get_subgraph()` |
| `src/eval/multihop_set.py` | 多跳测试集构建 | `data/edges.json` |
| `src/eval/compare.py` | 两套系统对比实验 | 上两项 |

---

## 8. 接口变更流程

本文档中任何接口发生变更（函数签名、JSON 字段名、返回格式），必须：

1. 在本文档中更新对应章节
2. 在 `CLAUDE.md` 的"共享接口约定"节同步更新
3. 口头通知对方，确认后再合并代码

**不允许静默改动接口**，哪怕"感觉更合理"也要先沟通。
