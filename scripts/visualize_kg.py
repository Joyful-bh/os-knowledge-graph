"""
知识图谱可视化 —— pyvis 生成交互式 HTML。

用法：
    python scripts/visualize_kg.py
    python scripts/visualize_kg.py --limit 150          # 只画度数最高的 150 个节点
    python scripts/visualize_kg.py --theme light        # 白色背景版（适合贴 PPT）
    python scripts/visualize_kg.py --with-problems
    python scripts/visualize_kg.py --output xxx.html

CLI 参数：
    --limit N        渲染节点数；-1 表示全部（默认），>0 表示按度数取 Top-N 个 Concept
    --theme dark|light  配色主题，默认 dark
    --with-problems  同时渲染 Problem 节点
    --output PATH    输出 HTML 路径

交互：
- 鼠标滚轮缩放；左键拖动平移
- 悬停节点查看名称、定义、章节
- 单击节点：高亮邻居与相关边，左侧面板按关系类型分组列出邻居
- 单击空白：复位视图
- 单击图例：切换该类节点/边的显隐
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pyvis.network import Network  # noqa: E402

from src.kg.load import UNDIRECTED_TYPES, get_graph  # noqa: E402


# ── 节点配色（按 node_role 区分）─────────────────────────────────────────────
ROLE_COLORS: dict[str, str] = {
    "概念": "#4A90E2",   # 蓝：基础概念
    "机制": "#50C878",   # 绿：解决问题的手段
    "算法": "#FF8C42",   # 橙：具体算法
    "问题": "#E15759",   # 红：现象/问题
}
PROBLEM_COLOR = "#9C8C8C"
DEFAULT_COLOR = "#888888"

# ── 节点形状（多形状产生视觉层级）───────────────────────────────────────────
ROLE_SHAPES: dict[str, str] = {
    "概念": "dot",        # 圆点：最基础
    "机制": "triangle",   # 三角：动作感
    "算法": "square",     # 方块：步骤感
    "问题": "diamond",    # 菱形：警示感
}
PROBLEM_SHAPE = "hexagon"

# ── 关系分组中文标签（点击面板使用）─────────────────────────────────────────
RELATION_LABELS: dict[tuple, str] = {
    ("PREREQUISITE", "out"): "后续依赖（此概念是先修）",
    ("PREREQUISITE", "in"):  "先修条件",
    ("SOLVES",       "out"): "解决的问题",
    ("SOLVES",       "in"):  "解决方法",
    ("PART_OF",      "out"): "所属",
    ("PART_OF",      "in"):  "包含",
    ("RELATED",      "any"): "相关概念",
    ("CONFUSABLE",   "any"): "易混淆",
    ("TESTS",        "out"): "考查的概念",
    ("TESTS",        "in"):  "涉及此概念的习题",
}

# ── 主题配色 ──────────────────────────────────────────────────────────────────
THEMES: dict[str, dict] = {
    "dark": {
        "bg":                 "#1B1B1F",
        "text":               "#EAEAEA",
        "label_color":        "#FFFFFF",
        "label_stroke":       "#1B1B1F",
        "panel_bg":           "rgba(20,20,25,0.72)",
        "panel_border":       "rgba(255,255,255,0.08)",
        "panel_strong_bg":    "rgba(20,20,25,0.88)",
        "panel_strong_border":"rgba(255,255,255,0.14)",
        "accent":             "#FFD479",
        "muted":              "#888",
        "hover_bg":           "rgba(255,255,255,0.08)",
        "fade_node":          "rgba(80,80,90,0.18)",
        "fade_edge":          "rgba(60,60,70,0.08)",
        "definition_bg":      "rgba(255,255,255,0.04)",
        "item_bg":            "rgba(255,255,255,0.06)",
        "item_hover_bg":      "rgba(255,212,121,0.18)",
        "item_hover_color":   "#FFD479",
        "code_bg":            "rgba(255,255,255,0.08)",
    },
    "light": {
        "bg":                 "#FAFBFC",
        "text":               "#222",
        "label_color":        "#1A1A1A",
        "label_stroke":       "#FFFFFF",
        "panel_bg":           "rgba(255,255,255,0.86)",
        "panel_border":       "rgba(0,0,0,0.08)",
        "panel_strong_bg":    "rgba(255,255,255,0.96)",
        "panel_strong_border":"rgba(0,0,0,0.14)",
        "accent":             "#C2410C",
        "muted":              "#777",
        "hover_bg":           "rgba(0,0,0,0.04)",
        "fade_node":          "rgba(200,200,205,0.45)",
        "fade_edge":          "rgba(200,200,205,0.25)",
        "definition_bg":      "rgba(0,0,0,0.03)",
        "item_bg":            "rgba(0,0,0,0.05)",
        "item_hover_bg":      "rgba(194,65,12,0.12)",
        "item_hover_color":   "#C2410C",
        "code_bg":            "rgba(0,0,0,0.06)",
    },
}


def _get_edge_styles(theme: str) -> dict[str, dict]:
    """按主题返回边样式（白底需更高饱和度才看得清弱边）。"""
    if theme == "dark":
        return {
            "PREREQUISITE": {"color": "rgba(225,87,89,0.88)",  "width": 1.7, "label": "先修依赖"},
            "SOLVES":       {"color": "rgba(242,142,43,0.82)", "width": 1.5, "label": "解决"},
            "CONFUSABLE":   {"color": "rgba(176,122,161,0.82)","width": 1.5, "label": "易混淆"},
            "RELATED":      {"color": "rgba(160,203,232,0.18)","width": 0.5, "label": "相关"},
            "PART_OF":      {"color": "rgba(215,206,206,0.15)","width": 0.5, "label": "从属"},
            "TESTS":        {"color": "rgba(121,112,110,0.32)","width": 0.4, "label": "考查"},
        }
    # light：背景白，弱边需要更高 alpha 和更深色
    return {
        "PREREQUISITE": {"color": "rgba(192,42,52,0.92)",   "width": 1.9, "label": "先修依赖"},
        "SOLVES":       {"color": "rgba(217,113,28,0.85)",  "width": 1.6, "label": "解决"},
        "CONFUSABLE":   {"color": "rgba(143,90,134,0.85)",  "width": 1.6, "label": "易混淆"},
        "RELATED":      {"color": "rgba(90,150,200,0.30)",  "width": 0.6, "label": "相关"},
        "PART_OF":      {"color": "rgba(160,150,150,0.28)", "width": 0.6, "label": "从属"},
        "TESTS":        {"color": "rgba(110,100,100,0.40)", "width": 0.5, "label": "考查"},
    }


def _compute_label_set(
    degrees: dict[str, int],
    rendered_concepts: set[str],
    target_count: int,
    min_degree: int = 3,
) -> set[str]:
    """
    决定哪些节点显示标签。

    规则：
    - 按度数排序，取 Top-target_count 个
    - 度数 < min_degree 的节点不显示标签（避免叶子节点标签污染）
    - 若 rendered_concepts 数量较少，自动放宽 min_degree
    """
    if len(rendered_concepts) <= 80:
        # 节点很少时，显示全部标签
        return set(rendered_concepts)
    if len(rendered_concepts) <= 200:
        min_degree = 1
    ranked = sorted(rendered_concepts, key=lambda n: -degrees.get(n, 0))
    return {n for n in ranked[:target_count] if degrees.get(n, 0) >= min_degree}


def build_visualization(
    output_path: Path,
    include_problems: bool = False,
    limit: int = -1,
    theme: str = "dark",
) -> None:
    """构建并保存可视化 HTML。"""
    if theme not in THEMES:
        raise ValueError(f"theme 必须是 dark/light，收到：{theme}")

    theme_cfg = THEMES[theme]
    edge_styles = _get_edge_styles(theme)

    G = get_graph()
    degrees = dict(G.degree())

    # ── 选择要渲染的节点集 ─────────────────────────────────────────────────
    all_concepts = [
        (n, attr) for n, attr in G.nodes(data=True)
        if attr.get("label") == "Concept"
    ]
    if limit > 0:
        ranked = sorted(all_concepts, key=lambda x: -degrees.get(x[0], 0))
        rendered_concepts: set[str] = {n for n, _ in ranked[:limit]}
    else:
        rendered_concepts = {n for n, _ in all_concepts}

    rendered_problems: set[str] = set()
    if include_problems:
        rendered_problems = {
            n for n, attr in G.nodes(data=True)
            if attr.get("label") == "Problem"
        }
    rendered_all: set[str] = rendered_concepts | rendered_problems

    # ── 确定显示标签的节点集 ───────────────────────────────────────────────
    # 节点少时尽量全标（用户主动筛选，标签是核心信息）；节点多时按度数取 Top-K
    n_rendered = len(rendered_concepts)
    if n_rendered <= 200:
        label_target = n_rendered
    elif n_rendered <= 500:
        label_target = max(100, n_rendered // 3)
    else:
        label_target = min(180, max(120, n_rendered // 9))
    label_set = _compute_label_set(degrees, rendered_concepts, label_target)

    # ── 节点大小参数（节点少时整体偏大）────────────────────────────────────
    if n_rendered <= 80:
        size_base, size_factor, size_max = 14, 2.5, 32
        font_size = 13
    elif n_rendered <= 300:
        size_base, size_factor, size_max = 11, 2.0, 24
        font_size = 12
    else:
        size_base, size_factor, size_max = 8, 1.6, 18
        font_size = 11

    # ── 创建 pyvis 网络 ─────────────────────────────────────────────────────
    net = Network(
        height="100vh",
        width="100%",
        bgcolor=theme_cfg["bg"],
        font_color=theme_cfg["text"],
        directed=True,
        notebook=False,
        cdn_resources="in_line",
    )

    # ── 添加 Concept 节点 ──────────────────────────────────────────────────
    role_counter: Counter = Counter()
    for node in rendered_concepts:
        attr = G.nodes[node]
        role = attr.get("node_role", "概念")
        color = ROLE_COLORS.get(role, DEFAULT_COLOR)
        shape = ROLE_SHAPES.get(role, "dot")
        size = min(size_max, size_base + math.log2(degrees.get(node, 1) + 1) * size_factor)

        show_label = node in label_set
        net.add_node(
            node,
            label=node if show_label else "",
            title=_build_concept_tooltip(node, attr, degrees.get(node, 0), theme_cfg),
            color=color,
            size=size,
            borderWidth=0,
            shape=shape,
            font={
                "size": font_size if show_label else 0,
                "color": theme_cfg["label_color"],
                "strokeWidth": 3,
                "strokeColor": theme_cfg["label_stroke"],
                "face": "Microsoft YaHei, Arial, sans-serif",
            },
            role=role,
            chapter=attr.get("chapter", ""),
            definition=attr.get("definition", ""),
            difficulty=attr.get("difficulty", 1),
        )
        role_counter[role] += 1

    # ── 添加 Problem 节点 ───────────────────────────────────────────────────
    for node in rendered_problems:
        attr = G.nodes[node]
        net.add_node(
            node,
            label="",
            title=f"<b>{node}</b><br/>{attr.get('stem', '')[:80]}…",
            color=PROBLEM_COLOR,
            size=5,
            shape=PROBLEM_SHAPE,
            borderWidth=0,
            role="问题(习题)",
            chapter=attr.get("chapter", ""),
            definition=attr.get("stem", "")[:120],
            difficulty=0,
        )

    # ── 添加边（仅渲染节点之间）────────────────────────────────────────────
    seen_undirected: set[tuple] = set()
    edges_by_type: Counter = Counter()
    for u, v, data in G.edges(data=True):
        if u not in rendered_all or v not in rendered_all:
            continue
        etype = data.get("type", "")
        if etype not in edge_styles:
            continue
        if etype == "TESTS" and not include_problems:
            continue

        if etype in UNDIRECTED_TYPES:
            key = (min(u, v), max(u, v), etype)
            if key in seen_undirected:
                continue
            seen_undirected.add(key)
            arrows = ""
        else:
            arrows = "to"

        style = edge_styles[etype]
        net.add_edge(
            u, v,
            color=style["color"],
            width=style["width"],
            title=f"{style['label']}：{data.get('description', '')}",
            arrows=arrows,
            smooth={"enabled": True, "type": "continuous", "roundness": 0.25},
            etype=etype,
        )
        edges_by_type[etype] += 1

    # ── 物理引擎参数（节点少时弹簧更长，更松散）─────────────────────────────
    if n_rendered <= 80:
        spring_length, grav_const = 220, -80
    elif n_rendered <= 300:
        spring_length, grav_const = 170, -100
    else:
        spring_length, grav_const = 140, -120

    net.set_options(f"""
    {{
      "physics": {{
        "solver": "forceAtlas2Based",
        "forceAtlas2Based": {{
          "gravitationalConstant": {grav_const},
          "centralGravity": 0.005,
          "springLength": {spring_length},
          "springConstant": 0.06,
          "damping": 0.5,
          "avoidOverlap": 1
        }},
        "stabilization": {{
          "enabled": true,
          "iterations": 600,
          "updateInterval": 25,
          "fit": true
        }},
        "minVelocity": 0.6,
        "maxVelocity": 30,
        "timestep": 0.45
      }},
      "interaction": {{
        "hover": true,
        "tooltipDelay": 120,
        "navigationButtons": true,
        "keyboard": true,
        "hideEdgesOnDrag": true,
        "hideEdgesOnZoom": true,
        "multiselect": false
      }},
      "nodes": {{
        "scaling": {{ "min": 6, "max": 32 }}
      }},
      "edges": {{
        "smooth": {{ "enabled": true, "type": "continuous", "roundness": 0.25 }},
        "arrows": {{ "to": {{ "scaleFactor": 0.35 }} }},
        "selectionWidth": 1.2
      }}
    }}
    """)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    net.save_graph(str(output_path))
    _inject_overlay(
        output_path, len(rendered_concepts), len(rendered_problems),
        role_counter, edges_by_type, len(label_set), theme_cfg, edge_styles,
        limit, len(all_concepts),
    )

    print(f"✓ 已生成可视化：{output_path}")
    print(f"  主题：{theme}")
    if limit > 0:
        print(f"  节点过滤：Top-{limit} 概念（按度数）/ 共 {len(all_concepts)} 个 Concept")
    print(f"  渲染：Concept {len(rendered_concepts)}，Problem {len(rendered_problems)}")
    print(f"  角色分布：{dict(role_counter)}")
    print(f"  显示标签：{len(label_set)} 个节点")
    print("  边统计：")
    for etype, n in edges_by_type.most_common():
        print(f"    {etype:<13s} {n}")


def _build_concept_tooltip(name: str, attr: dict, degree: int, theme_cfg: dict) -> str:
    """生成节点悬停 tooltip（HTML）。"""
    role = attr.get("node_role", "概念")
    chapter = attr.get("chapter", "")
    difficulty = attr.get("difficulty", 1)
    definition = attr.get("definition", "")
    aliases = attr.get("aliases", []) or []
    alias_text = f"<br/><i>别名：{ '、'.join(aliases) }</i>" if aliases else ""

    return (
        f"<div style='max-width:340px;font-family:sans-serif;'>"
        f"<b style='font-size:14px;'>{name}</b><br/>"
        f"<span style='color:{theme_cfg['muted']};font-size:12px;'>"
        f"{chapter} · {role} · 难度 {difficulty} · 度 {degree}</span>"
        f"{alias_text}<br/>"
        f"<span style='font-size:12px;'>{definition}</span>"
        f"</div>"
    )


def _inject_overlay(
    output_path: Path,
    n_concept: int,
    n_problem: int,
    role_counter: Counter,
    edges_by_type: Counter,
    n_labels: int,
    theme_cfg: dict,
    edge_styles: dict,
    limit: int,
    total_concepts: int,
) -> None:
    """在 HTML 中注入标题栏、图例、点击高亮逻辑、侧边详情面板。"""
    role_legend_items = []
    for role, color in ROLE_COLORS.items():
        cnt = role_counter.get(role, 0)
        if cnt == 0:
            continue
        role_legend_items.append(
            f'<div class="legend-item legend-role" data-role="{role}" title="单击切换显隐">'
            f'<span class="dot" style="background:{color}"></span>'
            f'<span>{role}</span><span class="count">{cnt}</span></div>'
        )
    if n_problem:
        role_legend_items.append(
            f'<div class="legend-item legend-role" data-role="__problem__" title="单击切换显隐">'
            f'<span class="dot square" style="background:{PROBLEM_COLOR}"></span>'
            f'<span>习题</span><span class="count">{n_problem}</span></div>'
        )
    role_legend_html = "".join(role_legend_items)

    edge_legend_html = "".join(
        f'<div class="legend-item legend-edge" data-etype="{etype}" title="单击切换显隐">'
        f'<span class="line" style="background:{style["color"]};height:{max(1.5, style["width"])}px"></span>'
        f'<span>{style["label"]}</span><span class="count">{edges_by_type.get(etype, 0)}</span>'
        f'</div>'
        for etype, style in edge_styles.items()
        if edges_by_type.get(etype, 0) > 0
    )

    relation_labels_js = json.dumps(
        {f"{k[0]}|{k[1]}": v for k, v in RELATION_LABELS.items()},
        ensure_ascii=False,
    )

    subtitle_parts = [f"{n_concept} 概念"]
    if n_problem:
        subtitle_parts.append(f"+ {n_problem} 习题")
    if limit > 0:
        subtitle_parts.append(f"（Top-{limit} / {total_concepts}）")
    subtitle_parts.append(f"{n_labels} 标注")
    subtitle = " · ".join(subtitle_parts)

    overlay_html = f"""
<style>
  body {{ margin: 0; background: {theme_cfg['bg']}; font-family: 'Microsoft YaHei', -apple-system, sans-serif; }}
  #mynetwork {{ outline: none; }}

  #title-bar {{
    position: fixed; top: 12px; left: 12px; z-index: 1000;
    color: {theme_cfg['text']}; padding: 10px 16px;
    background: {theme_cfg['panel_bg']}; border-radius: 8px;
    font-size: 13px; backdrop-filter: blur(8px);
    border: 1px solid {theme_cfg['panel_border']};
  }}
  #title-bar b {{ font-size: 15px; color: {theme_cfg['accent']}; }}

  #legend {{
    position: fixed; top: 12px; right: 12px; z-index: 1000;
    color: {theme_cfg['text']}; padding: 12px 14px;
    background: {theme_cfg['panel_bg']}; border-radius: 8px;
    font-size: 12px; backdrop-filter: blur(8px);
    width: 210px;
    border: 1px solid {theme_cfg['panel_border']};
  }}
  #legend h4 {{ margin: 4px 0 6px; color: {theme_cfg['accent']}; font-size: 12px; letter-spacing: 1px; }}
  .legend-item {{
    display: flex; align-items: center; margin: 2px 0;
    padding: 3px 6px; border-radius: 4px;
    cursor: pointer; transition: all 0.18s;
    user-select: none;
  }}
  .legend-item:hover {{ background: {theme_cfg['hover_bg']}; }}
  .legend-item.is-hidden {{ opacity: 0.32; }}
  .legend-item.is-hidden .dot,
  .legend-item.is-hidden .line {{ filter: grayscale(1) brightness(0.6); }}
  .legend-item.is-hidden span:not(.dot):not(.line):not(.count) {{ text-decoration: line-through; }}
  .legend-item .dot {{
    width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; display: inline-block; flex-shrink: 0;
  }}
  .legend-item .dot.square {{ border-radius: 2px; }}
  .legend-item .line {{
    width: 22px; margin-right: 8px; display: inline-block; flex-shrink: 0; border-radius: 1px;
  }}
  .legend-item .count {{ margin-left: auto; color: {theme_cfg['muted']}; font-size: 11px; }}
  #legend-hint {{
    font-size: 10.5px; color: {theme_cfg['muted']}; margin-top: 8px;
    padding-top: 6px; border-top: 1px dashed {theme_cfg['panel_border']};
  }}

  #detail-panel {{
    position: fixed; top: 12px; left: 12px; z-index: 1100;
    color: {theme_cfg['text']}; padding: 16px 18px;
    background: {theme_cfg['panel_strong_bg']}; border-radius: 10px;
    font-size: 13px; backdrop-filter: blur(10px);
    width: 360px; max-height: 88vh; overflow-y: auto;
    border: 1px solid {theme_cfg['panel_strong_border']};
    display: none;
    box-shadow: 0 4px 24px rgba(0,0,0,0.18);
  }}
  #detail-panel.visible {{ display: block; }}
  #detail-panel .close {{
    float: right; cursor: pointer; color: {theme_cfg['muted']};
    font-size: 18px; line-height: 1; padding: 0 4px;
  }}
  #detail-panel .close:hover {{ color: {theme_cfg['accent']}; }}
  #detail-panel .concept-name {{
    font-size: 16px; font-weight: bold; color: {theme_cfg['accent']};
    margin-bottom: 4px;
  }}
  #detail-panel .meta {{
    font-size: 11px; color: {theme_cfg['muted']}; margin-bottom: 10px;
  }}
  #detail-panel .meta .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    background: {theme_cfg['code_bg']}; margin-right: 6px;
  }}
  #detail-panel .definition {{
    font-size: 12px; line-height: 1.55;
    padding: 8px 10px; background: {theme_cfg['definition_bg']};
    border-left: 3px solid {theme_cfg['accent']}; border-radius: 3px;
    margin-bottom: 12px;
  }}
  #detail-panel .relation-group {{ margin-top: 10px; }}
  #detail-panel .relation-group .title {{
    font-size: 11px; color: {theme_cfg['accent']}; letter-spacing: 1px;
    margin-bottom: 4px; border-bottom: 1px solid {theme_cfg['panel_border']}; padding-bottom: 3px;
  }}
  #detail-panel .relation-group .item {{
    display: inline-block; padding: 3px 9px; margin: 3px 4px 3px 0;
    background: {theme_cfg['item_bg']}; border-radius: 12px;
    font-size: 11.5px; cursor: pointer; transition: all 0.15s;
  }}
  #detail-panel .relation-group .item:hover {{
    background: {theme_cfg['item_hover_bg']}; color: {theme_cfg['item_hover_color']};
  }}
  #detail-panel .empty {{ color: {theme_cfg['muted']}; font-size: 11px; padding: 10px 0; }}

  #status {{
    position: fixed; bottom: 12px; left: 50%; transform: translateX(-50%);
    z-index: 1000; color: {theme_cfg['accent']}; padding: 6px 16px;
    background: {theme_cfg['panel_strong_bg']}; border-radius: 14px;
    font-size: 12px; transition: opacity 1.5s;
    border: 1px solid {theme_cfg['accent']};
  }}

  #detail-panel::-webkit-scrollbar {{ width: 6px; }}
  #detail-panel::-webkit-scrollbar-thumb {{ background: {theme_cfg['muted']}; border-radius: 3px; }}
</style>

<div id="title-bar">
  <b>操作系统知识图谱</b> &nbsp;·&nbsp; {subtitle}
</div>

<div id="legend">
  <h4>节点角色</h4>
  {role_legend_html}
  <h4 style="margin-top:12px;">边类型</h4>
  {edge_legend_html}
  <div id="legend-hint">单击图例可切换显隐</div>
</div>

<div id="detail-panel">
  <span class="close" onclick="resetView()">×</span>
  <div id="panel-content"></div>
</div>

<div id="status">正在计算布局…</div>

<script>
  const RELATION_LABELS = {relation_labels_js};
  const FADE_NODE_COLOR = '{theme_cfg["fade_node"]}';
  const FADE_EDGE_COLOR = '{theme_cfg["fade_edge"]}';

  let originalNodeStyles = {{}};
  let originalEdgeStyles = {{}};

  document.addEventListener('DOMContentLoaded', function() {{
    const wait = setInterval(function() {{
      if (typeof network === 'undefined') return;
      clearInterval(wait);

      nodes.forEach(n => {{
        originalNodeStyles[n.id] = {{ color: n.color, size: n.size }};
      }});
      edges.forEach(e => {{
        originalEdgeStyles[e.id] = {{ color: e.color, width: e.width }};
      }});

      network.on('stabilizationIterationsDone', function() {{
        network.setOptions({{ physics: false }});
        const status = document.getElementById('status');
        status.innerHTML = '✓ 布局已稳定 · 单击节点查看邻居 · 单击图例切换显隐';
        setTimeout(() => {{ status.style.opacity = '0'; }}, 2500);
      }});

      network.on('click', function(params) {{
        if (params.nodes.length > 0) focusOnNode(params.nodes[0]);
        else resetView();
      }});

      document.querySelectorAll('.legend-role').forEach(el => {{
        el.addEventListener('click', () => toggleRole(el.dataset.role));
      }});
      document.querySelectorAll('.legend-edge').forEach(el => {{
        el.addEventListener('click', () => toggleEdgeType(el.dataset.etype));
      }});
    }}, 100);
  }});

  function focusOnNode(nodeId) {{
    const node = nodes.get(nodeId);
    if (!node) return;

    const connectedEdgeIds = network.getConnectedEdges(nodeId);
    const neighborIds = new Set([nodeId]);
    const relations = {{}};

    connectedEdgeIds.forEach(eid => {{
      const e = edges.get(eid);
      if (!e) return;
      const isOut = e.from === nodeId;
      const otherId = isOut ? e.to : e.from;
      neighborIds.add(otherId);

      let dirKey;
      if (e.etype === 'RELATED' || e.etype === 'CONFUSABLE') dirKey = 'any';
      else dirKey = isOut ? 'out' : 'in';
      const groupKey = e.etype + '|' + dirKey;
      const groupLabel = RELATION_LABELS[groupKey] || e.etype;
      if (!relations[groupLabel]) relations[groupLabel] = [];
      if (!relations[groupLabel].includes(otherId)) relations[groupLabel].push(otherId);
    }});

    const nodeUpdates = [];
    nodes.forEach(n => {{
      if (neighborIds.has(n.id)) {{
        nodeUpdates.push({{ id: n.id, color: originalNodeStyles[n.id].color }});
      }} else {{
        nodeUpdates.push({{ id: n.id, color: FADE_NODE_COLOR }});
      }}
    }});
    nodes.update(nodeUpdates);

    const edgeUpdates = [];
    edges.forEach(e => {{
      if (connectedEdgeIds.includes(e.id)) {{
        edgeUpdates.push({{
          id: e.id,
          color: brightenColor(originalEdgeStyles[e.id].color),
          width: Math.max(2.0, originalEdgeStyles[e.id].width * 2)
        }});
      }} else {{
        edgeUpdates.push({{ id: e.id, color: FADE_EDGE_COLOR, width: 0.3 }});
      }}
    }});
    edges.update(edgeUpdates);

    renderPanel(node, relations);
  }}

  function brightenColor(color) {{
    const m = String(color).match(/rgba?\\(([^)]+)\\)/);
    if (m) {{
      const parts = m[1].split(',').map(s => s.trim());
      return `rgba(${{parts[0]}},${{parts[1]}},${{parts[2]}},1.0)`;
    }}
    return color;
  }}

  function renderPanel(node, relations) {{
    const panel = document.getElementById('detail-panel');
    const content = document.getElementById('panel-content');
    const roleColor = node.color || '#888';

    let html = `
      <div class="concept-name">${{node.id}}</div>
      <div class="meta">
        <span class="badge" style="color:${{roleColor}}">● ${{node.role || ''}}</span>
        <span class="badge">${{node.chapter || ''}}</span>
        <span class="badge">难度 ${{node.difficulty || 1}}</span>
      </div>
      <div class="definition">${{escapeHtml(node.definition || '（无定义）')}}</div>
    `;

    const groupOrder = [
      "先修条件", "后续依赖（此概念是先修）",
      "解决方法", "解决的问题",
      "易混淆", "相关概念",
      "所属", "包含",
      "考查的概念", "涉及此概念的习题"
    ];
    const orderedGroups = groupOrder.filter(g => relations[g]);
    Object.keys(relations).forEach(g => {{
      if (!orderedGroups.includes(g)) orderedGroups.push(g);
    }});

    if (orderedGroups.length === 0) {{
      html += `<div class="empty">该节点没有可见的邻居（可能为孤立节点）</div>`;
    }} else {{
      orderedGroups.forEach(group => {{
        const items = relations[group];
        html += `<div class="relation-group"><div class="title">${{group}} · ${{items.length}}</div><div>`;
        items.forEach(name => {{
          html += `<span class="item" onclick="focusOnNode('${{escapeJs(name)}}')">${{escapeHtml(name)}}</span>`;
        }});
        html += `</div></div>`;
      }});
    }}

    content.innerHTML = html;
    panel.classList.add('visible');
  }}

  function resetView() {{
    const nodeUpdates = nodes.getIds().map(id => ({{
      id: id, color: originalNodeStyles[id].color
    }}));
    nodes.update(nodeUpdates);
    const edgeUpdates = edges.getIds().map(id => ({{
      id: id, color: originalEdgeStyles[id].color, width: originalEdgeStyles[id].width
    }}));
    edges.update(edgeUpdates);
    document.getElementById('detail-panel').classList.remove('visible');
  }}

  // ── 图例切换：显隐节点角色 / 边类型 ─────────────────────────────────────
  const hiddenRoles = new Set();
  const hiddenEdgeTypes = new Set();

  function _nodeRoleKey(n) {{
    if (n.role && n.role.indexOf('习题') >= 0) return '__problem__';
    return n.role;
  }}

  function toggleRole(role) {{
    if (hiddenRoles.has(role)) hiddenRoles.delete(role);
    else hiddenRoles.add(role);
    document.querySelectorAll('.legend-role').forEach(el => {{
      el.classList.toggle('is-hidden', hiddenRoles.has(el.dataset.role));
    }});
    const updates = [];
    nodes.forEach(n => {{
      updates.push({{ id: n.id, hidden: hiddenRoles.has(_nodeRoleKey(n)) }});
    }});
    nodes.update(updates);
  }}

  function toggleEdgeType(etype) {{
    if (hiddenEdgeTypes.has(etype)) hiddenEdgeTypes.delete(etype);
    else hiddenEdgeTypes.add(etype);
    document.querySelectorAll('.legend-edge').forEach(el => {{
      el.classList.toggle('is-hidden', hiddenEdgeTypes.has(el.dataset.etype));
    }});
    const updates = [];
    edges.forEach(e => {{
      updates.push({{ id: e.id, hidden: hiddenEdgeTypes.has(e.etype) }});
    }});
    edges.update(updates);
  }}

  function escapeHtml(s) {{
    return String(s).replace(/[&<>"']/g, c => ({{
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }}[c]));
  }}
  function escapeJs(s) {{
    return String(s).replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'");
  }}
</script>
"""

    html = output_path.read_text(encoding="utf-8")
    html = html.replace("</body>", overlay_html + "\n</body>")
    output_path.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="操作系统知识图谱可视化")
    parser.add_argument(
        "--output", default="data/kg_visualization.html",
        help="输出 HTML 路径（默认 data/kg_visualization.html）",
    )
    parser.add_argument(
        "--limit", type=int, default=-1,
        help="渲染节点数；-1 表示全部（默认），>0 表示按度数取 Top-N 个 Concept",
    )
    parser.add_argument(
        "--theme", choices=["dark", "light"], default="dark",
        help="配色主题（默认 dark）；light 适合贴 PPT 与打印",
    )
    parser.add_argument(
        "--with-problems", action="store_true",
        help="同时绘制 Problem 节点和 TESTS 边（默认仅 Concept）",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path

    build_visualization(
        output_path,
        include_problems=args.with_problems,
        limit=args.limit,
        theme=args.theme,
    )


if __name__ == "__main__":
    main()
