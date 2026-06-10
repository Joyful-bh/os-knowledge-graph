"""
Streamlit 交互界面：操作系统知识图谱问答与诊断系统。

运行：
    streamlit run web/app.py
"""

from pathlib import Path
import sys

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.services import (  # noqa: E402
    ask_graphrag,
    diagnose_wrong_pids,
    estimate_mastery_for_pids,
    load_compare_results,
    plan_learning_path,
    retrieve_subgraph,
)


EDGE_TYPES = ["PREREQUISITE", "RELATED", "SOLVES", "PART_OF", "CONFUSABLE", "TESTS"]


def main() -> None:
    """渲染 Streamlit 主页面。"""
    st.set_page_config(
        page_title="操作系统知识图谱问答与诊断系统",
        page_icon="📚",
        layout="wide",
    )
    st.title("操作系统知识图谱问答与诊断系统")
    render_sidebar()

    tabs = st.tabs([
        "GraphRAG 问答",
        "子图检索",
        "错题诊断",
        "学习路径规划",
        "实验结果",
    ])

    with tabs[0]:
        safe_render(render_graphrag_tab)
    with tabs[1]:
        safe_render(render_subgraph_tab)
    with tabs[2]:
        safe_render(render_diagnosis_tab)
    with tabs[3]:
        safe_render(render_path_tab)
    with tabs[4]:
        safe_render(render_results_tab)


def render_sidebar() -> None:
    """侧边栏：项目说明、运行提示和 GraphRAG 参数。"""
    st.sidebar.header("项目说明")
    st.sidebar.write(
        "本系统基于操作系统课程知识图谱，提供 GraphRAG 问答、"
        "子图检索、错题诊断、掌握度估计与学习路径规划。"
    )
    st.sidebar.info(
        "运行前请确认依赖已安装。GraphRAG 问答需要本地向量索引、"
        "Embedding 服务和 LLM API Key 可用。"
    )

    st.sidebar.header("GraphRAG 参数")
    st.session_state["top_k"] = st.sidebar.slider("top_k", 1, 10, 3)
    st.session_state["rag_hops"] = st.sidebar.slider("hops", 1, 4, 2)


def render_graphrag_tab() -> None:
    """Tab 1：GraphRAG 问答。"""
    st.subheader("GraphRAG 问答")

    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = []

    for message in st.session_state["chat_messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("请输入操作系统相关问题")
    if not question:
        return

    st.session_state["chat_messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("正在调用 GraphRAG..."):
            result = ask_graphrag(
                question=question,
                top_k=st.session_state.get("top_k", 3),
                hops=st.session_state.get("rag_hops", 2),
            )
        if result.get("error"):
            st.error(result["error"])
            answer = ""
        else:
            answer = result.get("answer", "")
            st.markdown(answer or "未生成回答。")

    if answer:
        st.session_state["chat_messages"].append({"role": "assistant", "content": answer})


def render_subgraph_tab() -> None:
    """Tab 2：子图检索。"""
    st.subheader("子图检索")

    concept_text = st.text_input("概念名（可用逗号分隔多个）", value="操作系统")
    edge_types = st.multiselect("边类型", EDGE_TYPES, default=["PREREQUISITE", "RELATED", "SOLVES"])
    hops = st.slider("检索跳数", 1, 4, 2, key="subgraph_hops")

    if not st.button("检索子图"):
        return

    concepts = parse_csv(concept_text)
    if not concepts:
        st.warning("请至少输入一个概念名。")
        return

    result = retrieve_subgraph(concept_names=concepts, edge_types=edge_types, hops=hops)
    if result.get("error"):
        st.error(result["error"])
        return

    st.write(f"节点数：{len(result.get('nodes', []))}，边数：{len(result.get('edges', []))}")
    show_dataframe("节点", result.get("nodes", []))
    show_dataframe("边", result.get("edges", []))

    with st.expander("查看原始 JSON"):
        st.json(result)


def render_diagnosis_tab() -> None:
    """Tab 3：错题诊断。"""
    st.subheader("错题诊断")

    pid_text = st.text_input("错题 pid（可用逗号分隔多个）", value="CH3_Q2, CH4_Q7", key="diag_pids")
    if not st.button("开始诊断"):
        return

    pids = parse_csv(pid_text)
    if not pids:
        st.warning("请至少输入一个错题 pid。")
        return

    result = diagnose_wrong_pids(pids)
    if result.get("error"):
        st.error(result["error"])
        return

    st.markdown("#### 每道题考查概念")
    concept_rows = []
    for item in result.get("traces", []):
        problem = item.get("problem") or {}
        concept_rows.append({
            "pid": item.get("pid"),
            "stem": problem.get("stem", ""),
            "tested_concepts": "、".join(item.get("tested_concepts", [])),
        })
    show_dataframe("考查概念", concept_rows)

    st.markdown("#### 薄弱候选概念")
    weak_candidates = result.get("weak_candidates", [])
    if not weak_candidates:
        st.info("暂未诊断出薄弱候选概念，请检查 pid 是否存在且是否有 TESTS 边。")
        return
    show_dataframe("weak_candidates", weak_candidates)


def render_path_tab() -> None:
    """Tab 4：学习路径规划。"""
    st.subheader("学习路径规划")

    pid_text = st.text_input("错题 pid（可用逗号分隔多个）", value="CH3_Q2, CH4_Q7", key="path_pids")
    max_items = st.slider("最多返回步骤数", 1, 30, 10)
    if not st.button("生成学习路径"):
        return

    pids = parse_csv(pid_text)
    if not pids:
        st.warning("请至少输入一个错题 pid。")
        return

    mastery = estimate_mastery_for_pids(pids)
    path = plan_learning_path(pids, max_items=max_items)

    st.markdown("#### 掌握度估计")
    if mastery:
        show_dataframe("mastery", mastery)
    else:
        st.info("暂无掌握度结果。")

    st.markdown("#### 推荐学习路径")
    if not path:
        st.info("暂无推荐学习路径。")
        return

    rows = []
    for item in path:
        rows.append({
            "step": item.get("step"),
            "concept": item.get("concept"),
            "reason": item.get("reason"),
            "difficulty": item.get("difficulty"),
            "chapter": item.get("chapter"),
            "node_role": item.get("node_role"),
            "risk": item.get("risk"),
            "mastery": item.get("mastery"),
            "practice_pids": "、".join(item.get("practice_pids", [])),
        })
    show_dataframe("learning_path", rows)


def render_results_tab() -> None:
    """Tab 5：Phase 2 实验结果。"""
    st.subheader("实验结果")

    result = load_compare_results()
    if result is None:
        st.warning("未找到 data/phase2_compare_results.json，请先运行：python -m src.eval.compare")
        return

    vector = result.get("vector_rag", {})
    graph = result.get("graph_rag", {})

    col1, col2 = st.columns(2)
    col1.metric("Vector RAG Accuracy", format_accuracy(vector.get("accuracy")))
    col2.metric("GraphRAG Accuracy", format_accuracy(graph.get("accuracy")))

    detail_rows = []
    for method_name, method_result in result.items():
        for row in method_result.get("details", []):
            detail = dict(row)
            detail["method"] = method_name
            detail_rows.append(detail)

    st.markdown("#### Details")
    if detail_rows:
        show_dataframe("details", detail_rows)
    else:
        st.info("实验结果中没有 details 字段。")


def parse_csv(text: str) -> list[str]:
    """解析逗号分隔输入，兼容中文逗号。"""
    return [
        item.strip()
        for item in text.replace("，", ",").split(",")
        if item.strip()
    ]


def show_dataframe(title: str, rows: list[dict]) -> None:
    """统一表格展示，空数据给友好提示。"""
    if not rows:
        st.info(f"{title} 暂无数据。")
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def format_accuracy(value) -> str:
    """把 accuracy 数值格式化为百分比。"""
    if value is None:
        return "N/A"
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return str(value)


def safe_render(render_func) -> None:
    """页面级兜底，确保异常显示为 st.error。"""
    try:
        render_func()
    except Exception as exc:
        st.error(f"{exc.__class__.__name__}: {exc}")


if __name__ == "__main__":
    main()
