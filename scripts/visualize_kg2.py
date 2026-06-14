#!/usr/bin/env python3
"""
知识图谱可视化模块
===================
支持三种可视化方式：
  1. pyvis 交互式 HTML（推荐，支持缩放/拖拽/搜索）
  2. matplotlib 静态图
  3. 文本摘要输出

用法：
  python visualize_kg.py                    # 生成交互式HTML
  python visualize_kg.py --data knowledge_graph_data.json
  python visualize_kg.py --matplotlib       # 生成静态PNG图片
  python visualize_kg.py --summary          # 打印文本摘要
"""

import json
import argparse
from pathlib import Path
from collections import defaultdict


def load_kg_data(json_path: str = "knowledge_graph_data.json") -> dict:
    """加载知识图谱JSON数据"""
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ============================================================
# 方式一：pyvis 交互式 HTML 可视化（推荐）
# ============================================================

def visualize_pyvis(kg_data: dict, output: str = "知识图谱可视化.html"):
    """
    使用 pyvis 生成交互式HTML知识图谱
    支持：缩放、拖拽、节点搜索、悬停信息、按关系类型着色
    """
    from pyvis.network import Network
    
    concepts = kg_data.get("concepts", [])
    relations = kg_data.get("relations", [])
    chapters = kg_data.get("chapters", [])
    
    # 配色方案：按关系类型着色边
    edge_colors = {
        "DEPENDS_ON":        "#e74c3c",  # 红色 - 依赖
        "GENERALIZES":       "#8e44ad",  # 紫色 - 推广
        "ANALOGOUS_TO":      "#3498db",  # 蓝色 - 类比
        "COMPUTES":          "#2ecc71",  # 绿色 - 计算
        "CHARACTERIZES":     "#e67e22",  # 橙色 - 刻画
        "IMPLEMENTS":        "#1abc9c",  # 青色 - 实现
        "IS_SUBCONCEPT_OF":  "#95a5a6",  # 灰色 - 归属
        "APPLIES_TO":        "#f39c12",  # 黄色 - 应用
        "DESCRIBES":         "#27ae60",  # 绿 - 公式描述概念
        "EXPRESSES":         "#2980b9",  # 蓝 - 公式表达关系
        "DERIVES_FROM":      "#8e44ad",  # 紫 - 公式推导链
        "USES_CONCEPT":      "#e67e22",  # 橙 - 公式使用概念
        "RELATED_TO":        "#3498db",  # 蓝 - 语义关联
        "BELONGS_TO_CHAPTER":"#95a5a6",  # 灰 - 章节归属
        "NEXT":              "#bdc3c7",  # 淡灰 - 顺序
        "CONTAINS":          "#7f8c8d",  # 深灰 - 包含
    }
    
    # 概念聚类配色（按聚类类别着色节点）
    cluster_colors = {
        "变换与频谱分析": "#3498db",  # 蓝
        "系统分析与表征": "#e74c3c",  # 红
        "滤波器设计":     "#2ecc71",  # 绿
        "采样与重构":     "#f39c12",  # 橙
        "卷积与相关":     "#9b59b6",  # 紫
        "信号基础":       "#1abc9c",  # 青
        "其他":           "#95a5a6",  # 灰
    }
    
    # 创建网络
    net = Network(
        height="900px",
        width="100%",
        directed=True,
        notebook=False,
        bgcolor="#ffffff",
        font_color="#333333",
    )
    
    # 设置物理引擎参数
    net.set_options("""
    {
      "physics": {
        "forceAtlas2Based": {
          "gravitationalConstant": -80,
          "centralGravity": 0.01,
          "springLength": 150,
          "springConstant": 0.08,
          "damping": 0.4
        },
        "maxVelocity": 30,
        "minVelocity": 0.75,
        "solver": "forceAtlas2Based",
        "timestep": 0.35
      },
      "interaction": {
        "hover": true,
        "tooltipDelay": 100,
        "navigationButtons": true,
        "keyboard": true
      },
      "edges": {
        "arrows": {
          "to": { "enabled": true, "scaleFactor": 0.5 }
        },
        "smooth": {
          "type": "continuous"
        }
      }
    }
    """)
    
    # 按聚类分组给概念着色
    concept_color_map = {}
    for concept in concepts:
        name = concept.get("name", "")
        # 根据聚类分配颜色
        if any(kw in name for kw in ["傅里叶", "变换", "级数", "拉氏", "Z变换", "DFT", "FFT", "DTFT"]):
            concept_color_map[name] = cluster_colors["变换与频谱分析"]
        elif any(kw in name for kw in ["系统", "响应", "函数", "稳定性", "因果", "LTI", "极点", "零点"]):
            concept_color_map[name] = cluster_colors["系统分析与表征"]
        elif any(kw in name for kw in ["滤波器", "滤波", "巴特沃斯", "切比雪夫"]):
            concept_color_map[name] = cluster_colors["滤波器设计"]
        elif any(kw in name for kw in ["采样", "重构"]):
            concept_color_map[name] = cluster_colors["采样与重构"]
        elif any(kw in name for kw in ["卷积", "相关"]):
            concept_color_map[name] = cluster_colors["卷积与相关"]
        elif any(kw in name for kw in ["信号", "冲激", "阶跃", "周期"]):
            concept_color_map[name] = cluster_colors["信号基础"]
        else:
            concept_color_map[name] = cluster_colors["其他"]
    
    # 构建概念名->索引映射
    concept_names = {c.get("name", ""): i for i, c in enumerate(concepts)}
    
    # 添加概念节点
    for concept in concepts:
        name = concept.get("name", "")
        if not name or len(name) < 2:
            continue
        
        sources = concept.get("sources", [])
        occ = concept.get("occurrence_count", 0)
        weight = concept.get("total_weight", 0)
        
        # 节点大小与重要程度相关
        size = max(15, min(40, 15 + occ * 2))
        color = concept_color_map.get(name, "#95a5a6")
        
        # 构建tooltip
        tooltip = f"<b>{name}</b><br>"
        tooltip += f"出现频次: {occ}<br>"
        if sources:
            tooltip += f"来源: {', '.join(sources[:3])}"
        
        # 本体层归属
        category = concept.get('category', '')
        cat_name = concept.get('category_name', '')
        cat_color = concept.get('category_color', '')
        if cat_name:
            tooltip += f"<br>🏷 本体类别: {cat_name}"
        
        net.add_node(
            n_id=name,
            label=name,
            title=tooltip,
            color=cat_color if cat_color else color,
            size=size,
            font={"size": 14, "color": "#333333"},
            shape="dot",
            category=category,
            meta_class=concept.get('meta_class', ''),
        )
    
    # 添加章节节点
    for ch in chapters:
        ch_title = ch.get("title", "")
        ch_num = ch.get("number")
        if ch_num is None:
            continue
        
        ch_name = f"第{ch_num}章: {ch_title}"
        net.add_node(
            n_id=ch_name,
            label=f"Ch{ch_num}: {ch_title[:10]}...",
            title=f"<b>{ch_name}</b><br>页码: {ch.get('pages', '?')}",
            color="#bdc3c7",
            size=20,
            shape="box",
            font={"size": 10},
        )
    
    # 记录所有已添加的节点ID（提前定义，供公式节点使用）
    added_nodes = set(concept_names.keys())
    for ch in chapters:
        if ch.get("number") is not None:
            added_nodes.add(f"第{ch['number']}章: {ch['title']}")
    edge_added = 0
    
    # 添加公式节点（菱形，橙色）
    formulas = kg_data.get("formulas", [])
    for i, f in enumerate(formulas[:199]):  # 全部公式
        fname = f.get("name", f"公式{i}")
        ftext = f.get("text", "")
        ftype = f.get("type", "")
        fsrc = f.get("source", "")
        flatex = f.get("latex", "")
        fmeaning = f.get("meaning", "")
        
        if (not ftext or len(ftext) < 3) and not flatex:
            continue
        
        node_id = f"FORMULA:{fname}"
        label = fname[:15] + ("..." if len(fname) > 15 else "")
        
        # 构建丰富tooltip：LaTeX + 含义 + 原文
        tooltip = f"<b>{fname}</b>"
        if ftype:
            tooltip += f" <span style='color:#888'>[{ftype}]</span>"
        if flatex:
            tooltip += f"<br><code style='background:#fff3e0;padding:2px 4px;font-size:11px'>${flatex[:150]}$</code>"
        if fmeaning:
            tooltip += f"<br>📖 {fmeaning}"
        elif ftext:
            tooltip += f"<br>{ftext[:120]}"
        
        net.add_node(
            n_id=node_id,
            label=label,
            title=tooltip,
            color="#f39c12",
            size=12,
            shape="diamond",
            font={"size": 8, "color": "#e67e22"},
        )
        added_nodes.add(node_id)
        
        # 公式 → 来源章节
        if fsrc:
            for ch in chapters:
                if ch.get("title", "") == fsrc:
                    ch_name = f"第{ch.get('number')}章: {ch.get('title','')}"
                    net.add_edge(
                        source=node_id,
                        to=ch_name,
                        title="来源",
                        label="来源",
                        color="#f39c12",
                        width=0.5,
                        dashes=True,
                        font={"size": 6, "color": "#f39c12"},
                    )
                    edge_added += 1
    
    # 添加关系边 — 缺失的节点自动创建
    for rel in relations:
        subj = rel.get("subject", "")
        obj = rel.get("object", "")
        rel_type = rel.get("relation", "")
        
        if not subj or not obj or subj == obj:
            continue
        
        # 对缺失节点自动创建（小尺寸、灰色，表示从文本抽取的非核心节点）
        for node_name in [subj, obj]:
            if node_name not in added_nodes:
                # 截断过长的文本
                label = node_name[:20] + ("..." if len(node_name) > 20 else "")
                net.add_node(
                    n_id=node_name,
                    label=label,
                    title=f"<b>{node_name}</b><br><i>从文本抽取</i>",
                    color="#d5dbdb",
                    size=8,
                    shape="dot",
                    font={"size": 8, "color": "#999999"},
                )
                added_nodes.add(node_name)
        
        color = edge_colors.get(rel_type, "#cccccc")
        width = 2.0 if rel_type in ("DEPENDS_ON", "GENERALIZES") else 1.0
        
        net.add_edge(
            source=subj,
            to=obj,
            title=rel_type,
            label=rel_type,
            color=color,
            width=width,
            arrows="to",
            font={"size": 8, "color": color},
        )
        edge_added += 1
    
    print(f"    实际添加边: {edge_added}/{len(relations)}")
    
    # 保存
    net.save_graph(output)
    
    # ===== 注入本体层切换面板 =====
    kg_ontology = kg_data.get('ontology', {})
    _inject_ontology_toggle(output, kg_ontology)
    
    print(f"\n  [pyvis] 交互式知识图谱已保存: {output}")
    print(f"    节点: {len(net.nodes)}  边: {len(net.edges)}")
    print(f"    用浏览器打开该文件即可交互浏览")
    
    return output


def _inject_ontology_toggle(html_path: str, ontology: dict):
    """双层本体切换面板 — 元类层 + 领域层"""
    
    if not ontology:
        return
    
    # 元类数据
    meta_classes = ontology.get('meta_classes', [])
    # 领域类数据
    domain_cats = ontology.get('domain_categories', [])
    
    # 本体层节点名字 = 所有元类名 + 所有领域名
    meta_names = [mc['name'] for mc in meta_classes]
    domain_names = [dc['name'] for dc in domain_cats]
    all_onto_names = meta_names + domain_names
    
    mc_json = json.dumps(meta_classes, ensure_ascii=False)
    dc_json = json.dumps(domain_cats, ensure_ascii=False)
    
    meta_rows = ''.join(
        f'<div style="padding:1px 0;cursor:pointer;" onclick="filterByMetaClass(&quot;{mc["id"]}&quot;)" title="{mc["description"]}">'
        f'<span style="display:inline-block;width:10px;height:10px;background:{mc["color"]};border-radius:50%;margin-right:3px;"></span>'
        f'{mc["name"]} <span style="color:#888;">({mc["entity_count"]})</span></div>'
        for mc in meta_classes
    )
    domain_rows = ''.join(
        f'<div style="padding:1px 0;cursor:pointer;" onclick="filterByCategory(&quot;{dc["id"]}&quot;)" title="{dc["description"]}">'
        f'<span style="display:inline-block;width:10px;height:10px;background:{dc["color"]};border-radius:50%;margin-right:3px;"></span>'
        f'{dc.get("icon","")} {dc["name"]} <span style="color:#888;">({dc["concept_count"]})</span></div>'
        for dc in domain_cats
    )

    panel_html = f"""
<div id="ontology-panel" style="
    position:fixed; top:10px; left:10px; background:#fff; border:1px solid #ddd;
    border-radius:8px; box-shadow:0 2px 12px rgba(0,0,0,.15); z-index:9999;
    padding:12px; font-family:-apple-system,BlinkMacSystemFont,sans-serif;
    font-size:12px; max-width:290px; max-height:85vh; overflow-y:auto;">
  
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
    <b style="color:#333;">🧠 本体层 (TBox)</b>
    <button onclick="document.getElementById('ontology-panel').style.display='none'"
            style="background:none;border:none;cursor:pointer;font-size:14px;">✕</button>
  </div>
  
  <div style="margin-bottom:8px;display:flex;flex-wrap:wrap;gap:3px;">
    <button id="btn-all" onclick="showAllLayer()" 
            style="padding:5px 10px;background:#3498db;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;">
      全图</button>
    <button id="btn-onto" onclick="showOntologyLayer()"
            style="padding:5px 10px;background:#95a5a6;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;">
      仅本体</button>
    <button id="btn-meta" onclick="showMetaOnly()"
            style="padding:5px 10px;background:#95a5a6;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;">
      仅元类</button>
    <button id="btn-hide-onto" onclick="toggleOntoNodes()"
            style="padding:5px 10px;background:#2ecc71;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;">
      本体（已隐藏）</button>
  </div>
  
  <details open style="margin-bottom:6px;">
    <summary style="font-weight:bold;cursor:pointer;margin-bottom:4px;">
      🏷 元类 ({len(meta_classes)})
      <span style="font-weight:normal;color:#888;font-size:11px;">— 实体类型定义</span>
    </summary>
    {meta_rows}
  </details>
  
  <details open>
    <summary style="font-weight:bold;cursor:pointer;margin-bottom:4px;">
      📂 领域 ({len(domain_cats)})
      <span style="font-weight:normal;color:#888;font-size:11px;">— 学科主题分类</span>
    </summary>
    {domain_rows}
  </details>
  
  <div style="margin-top:6px;font-size:11px;color:#888;border-top:1px solid #eee;padding-top:4px;">
    本体层 = {len(all_onto_names)} 个类 | 实体: {sum(mc['entity_count'] for mc in meta_classes)} 个
  </div>
</div>

<script>
var ontoNames = {json.dumps(all_onto_names, ensure_ascii=False)};
var metaIds = {json.dumps([mc['id'] for mc in meta_classes], ensure_ascii=False)};
var domainIds = {json.dumps([dc['id'] for dc in domain_cats], ensure_ascii=False)};

var ontoHidden = true;  // 默认隐藏本体节点，避免遮挡实体图谱

function _resetButtons() {{
    ['btn-all','btn-onto','btn-meta'].forEach(function(id){{
        document.getElementById(id).style.background = '#95a5a6';
    }});
}}

function _applyOntoHidden() {{
    // 本体层节点 = ontonames 中的名称
    if (ontoHidden) {{
        var nodes = network.body.data.nodes.get();
        nodes.forEach(function(n){{
            if (ontoNames.indexOf(n.label) >= 0) {{
                network.body.data.nodes.update({{id: n.id, hidden: true}});
            }}
        }});
        document.getElementById('btn-hide-onto').textContent = '本体（已隐藏）';
        document.getElementById('btn-hide-onto').style.background = '#2ecc71';
    }} else {{
        var nodes = network.body.data.nodes.get();
        nodes.forEach(function(n){{
            if (ontoNames.indexOf(n.label) >= 0) {{
                network.body.data.nodes.update({{id: n.id, hidden: false}});
            }}
        }});
        document.getElementById('btn-hide-onto').textContent = '本体（已显示）';
        document.getElementById('btn-hide-onto').style.background = '#e67e22';
    }}
}}

function toggleOntoNodes() {{
    ontoHidden = !ontoHidden;
    if (ontoHidden) {{
        showAllLayer();  // 先全部显示，再隐藏本体节点
        _applyOntoHidden();
    }} else {{
        showAllLayer();  // 全部显示（含本体节点）
    }}
    _resetButtons();
    document.getElementById('btn-all').style.background = '#3498db';
}}

function showAllLayer() {{
    _resetButtons();
    document.getElementById('btn-all').style.background = '#3498db';
    network.body.data.nodes.forEach(function(n){{
        network.body.data.nodes.update({{id: n.id, hidden: againstHidden(n)}});
    }});
    network.body.data.edges.forEach(function(e){{
        network.body.data.edges.update({{id: e.id, hidden: edgeHidden(e)}});
    }});
    network.fit();
}}

function againstHidden(n) {{
    return ontoHidden && ontoNames.indexOf(n.label) >= 0;
}}
function edgeHidden(e) {{
    if (!ontoHidden) return false;
    var from = network.body.data.nodes.get(e.from);
    var to = network.body.data.nodes.get(e.to);
    return (from && ontoNames.indexOf(from.label) >= 0) || (to && ontoNames.indexOf(to.label) >= 0);
}}

function showOntologyLayer() {{
    _resetButtons();
    document.getElementById('btn-onto').style.background = '#e74c3c';
    // TBox: 仅元类节点 + 领域节点，不含具体实体
    var nodes = network.body.data.nodes.get();
    var vis = new Set();
    nodes.forEach(function(n){{
        if (ontoNames.indexOf(n.label) >= 0) vis.add(n.id);
    }});
    nodes.forEach(function(n){{
        network.body.data.nodes.update({{id: n.id, hidden: !vis.has(n.id)}});
    }});
    network.body.data.edges.forEach(function(e){{
        network.body.data.edges.update({{id: e.id, hidden: !(vis.has(e.from) && vis.has(e.to))}});
    }});
    network.fit();
}}

function showMetaOnly() {{
    _resetButtons();
    document.getElementById('btn-meta').style.background = '#8e44ad';
    var nodes = network.body.data.nodes.get();
    var vis = new Set();
    nodes.forEach(function(n){{
        if (metaNames.indexOf(n.label) >= 0) vis.add(n.id);
    }});
    nodes.forEach(function(n){{
        network.body.data.nodes.update({{id: n.id, hidden: !vis.has(n.id)}});
    }});
    network.body.data.edges.forEach(function(e){{
        network.body.data.edges.update({{id: e.id, hidden: !(vis.has(e.from) && vis.has(e.to))}});
    }});
    network.fit();
}}

function filterByCategory(catId) {{
    _resetButtons();
    ontoHidden = true;
    var nodes = network.body.data.nodes.get();
    var vis = new Set();
    nodes.forEach(function(n){{
        if (n.category === catId) vis.add(n.id);
    }});
    nodes.forEach(function(n){{
        network.body.data.nodes.update({{id: n.id, hidden: !vis.has(n.id)}});
    }});
    network.body.data.edges.forEach(function(e){{
        network.body.data.edges.update({{id: e.id, hidden: !(vis.has(e.from) || vis.has(e.to))}});
    }});
    network.fit();
}}

function filterByMetaClass(mid) {{
    _resetButtons();
    ontoHidden = true;
    var nodes = network.body.data.nodes.get();
    var vis = new Set();
    nodes.forEach(function(n){{
        if (n.meta_class === mid) vis.add(n.id);
        metaIds.forEach(function(id,idx){{
            if (id===mid && n.label===metaNames[idx]) vis.add(n.id);
        }});
    }});
    nodes.forEach(function(n){{
        network.body.data.nodes.update({{id: n.id, hidden: !vis.has(n.id)}});
    }});
    network.body.data.edges.forEach(function(e){{
        network.body.data.edges.update({{id: e.id, hidden: !(vis.has(e.from) || vis.has(e.to))}});
    }});
    network.fit();
}}

var metaNames = {json.dumps(meta_names, ensure_ascii=False)};

// 页面加载后自动隐藏本体节点（默认视图 = 纯实体图谱）
setTimeout(function() {{
    if (typeof network !== 'undefined') {{
        _applyOntoHidden();
        _resetButtons();
        document.getElementById('btn-all').style.background = '#3498db';
    }}
}}, 1500);
</script>
"""
    
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    
    # 同时也通过 pyvis 的 XMLhttpRequest 方式注入初始隐藏
    html = html.replace('</body>', panel_html + '\n</body>')
    
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"    本体层切换面板已注入 (元类{len(meta_classes)} + 领域{len(domain_cats)})")


# ============================================================
# 方式二：matplotlib 静态图
# ============================================================

def visualize_matplotlib(kg_data: dict, output: str = "知识图谱-静态图.png"):
    """
    使用 matplotlib + networkx 生成静态知识图谱
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import networkx as nx
    import numpy as np
    
    # 配置中文字体
    plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Heiti SC', 'STHeiti', 'Arial Unicode MS']
    plt.rcParams['axes.unicode_minus'] = False
    
    concepts = kg_data.get("concepts", [])
    relations = kg_data.get("relations", [])
    
    # 取核心概念（高权重前40个）
    core_concepts = sorted(
        concepts,
        key=lambda x: x.get("total_weight", 0),
        reverse=True
    )[:40]
    core_names = {c["name"] for c in core_concepts}
    
    # 创建图
    G = nx.DiGraph()
    
    # 添加节点
    for c in core_concepts:
        name = c["name"]
        G.add_node(name, weight=c.get("total_weight", 1))
    
    # 添加边
    edge_colors_map = {
        "DEPENDS_ON": "#e74c3c",
        "GENERALIZES": "#8e44ad",
        "ANALOGOUS_TO": "#3498db",
        "COMPUTES": "#2ecc71",
        "CHARACTERIZES": "#e67e22",
        "IMPLEMENTS": "#1abc9c",
        "IS_SUBCONCEPT_OF": "#bdc3c7",
        "APPLIES_TO": "#f39c12",
    }
    
    for rel in relations:
        subj = rel.get("subject", "")
        obj = rel.get("object", "")
        if subj in core_names and obj in core_names:
            G.add_edge(subj, obj, relation=rel.get("relation", ""))
    
    if len(G.nodes) == 0:
        print("  没有可绘图的节点")
        return None
    
    # 布局
    plt.figure(figsize=(24, 20), dpi=150)
    
    # 使用分层布局
    try:
        pos = nx.kamada_kawai_layout(G)
    except:
        pos = nx.spring_layout(G, k=2, iterations=50)
    
    # 节点颜色
    node_colors = []
    for node in G.nodes():
        if any(kw in node for kw in ["傅里叶"]):
            node_colors.append("#3498db")
        elif any(kw in node for kw in ["拉氏", "拉普拉斯"]):
            node_colors.append("#9b59b6")
        elif any(kw in node for kw in ["Z变换"]):
            node_colors.append("#1abc9c")
        elif any(kw in node for kw in ["系统", "响应", "LTI"]):
            node_colors.append("#e74c3c")
        elif any(kw in node for kw in ["滤波"]):
            node_colors.append("#2ecc71")
        elif any(kw in node for kw in ["采样"]):
            node_colors.append("#f39c12")
        else:
            node_colors.append("#95a5a6")
    
    # 节点大小
    node_sizes = [G.nodes[n].get("weight", 1) * 80 + 200 for n in G.nodes()]
    
    # 绘图
    nx.draw_networkx_nodes(G, pos, node_size=node_sizes, node_color=node_colors, 
                          alpha=0.9, edgecolors="#ffffff", linewidths=1)
    nx.draw_networkx_labels(G, pos, font_size=8, font_family="sans-serif",
                           font_weight="bold")
    
    # 按关系类型绘制不同的边
    for rel_type, color in edge_colors_map.items():
        edgelist = [(u, v) for u, v, d in G.edges(data=True) 
                    if d.get("relation") == rel_type]
        if edgelist:
            nx.draw_networkx_edges(G, pos, edgelist=edgelist,
                                  edge_color=color, alpha=0.6,
                                  arrowstyle='->', arrowsize=10,
                                  connectionstyle='arc3,rad=0.1')
    
    # 图例
    legend_elements = [
        mpatches.Patch(color="#e74c3c", label="DEPENDS_ON (依赖)"),
        mpatches.Patch(color="#8e44ad", label="GENERALIZES (推广)"),
        mpatches.Patch(color="#3498db", label="ANALOGOUS_TO (类比)"),
        mpatches.Patch(color="#2ecc71", label="COMPUTES (计算)"),
        mpatches.Patch(color="#e67e22", label="CHARACTERIZES (刻画)"),
        mpatches.Patch(color="#1abc9c", label="IMPLEMENTS (实现)"),
    ]
    plt.legend(handles=legend_elements, loc='lower left', fontsize=10,
              framealpha=0.9, ncol=2)
    
    plt.title("信号与系统 知识图谱 (核心概念)", fontsize=20, fontweight='bold', pad=20)
    plt.axis('off')
    plt.tight_layout()
    plt.savefig(output, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    
    print(f"\n  [matplotlib] 静态知识图谱已保存: {output}")
    return output


# ============================================================
# 方式三：文本摘要
# ============================================================

def print_summary(kg_data: dict):
    """打印知识图谱文本摘要"""
    concepts = kg_data.get("concepts", [])
    relations = kg_data.get("relations", [])
    chapters = kg_data.get("chapters", [])
    stats = kg_data.get("statistics", {})
    
    print("\n" + "=" * 70)
    print("  信号与系统 - 知识图谱摘要")
    print("=" * 70)
    
    print(f"\n  基本统计:")
    print(f"    总概念数:   {len(concepts)}")
    print(f"    总关系数:   {len(relations)}")
    print(f"    章节数:     {len(chapters)}")
    print(f"    公式数:     {stats.get('total_formulas', 'N/A')}")
    print(f"    定义数:     {stats.get('total_definitions', 'N/A')}")
    
    # 关系类型分布
    rel_dist = defaultdict(int)
    for r in relations:
        rel_dist[r.get("relation", "?")] += 1
    
    print(f"\n  关系分布:")
    for rt, count in sorted(rel_dist.items(), key=lambda x: -x[1]):
        bar = "█" * min(30, count)
        print(f"    {rt:<20s} {count:>4d}  {bar}")
    
    # 核心概念 Top-15
    core = sorted(concepts, key=lambda x: x.get("total_weight", 0), reverse=True)[:15]
    print(f"\n  核心概念 Top-15:")
    for i, c in enumerate(core, 1):
        name = c.get("name", "?")
        occ = c.get("occurrence_count", 0)
        sources = c.get("sources", [])
        print(f"    {i:>2}. {name:<25s} (跨{len(sources):>2}章, 频次{occ:>3})")
    
    # 章节列表
    print(f"\n  章节结构:")
    for ch in chapters:
        num = ch.get("number", "?")
        title = ch.get("title", "?")
        topics = ch.get("topics", [])
        print(f"    第{num:>2}章  {title}  ({len(topics)}个主题)")
    
    # 核心依赖链
    print(f"\n  核心知识依赖链:")
    dep_chain = [
        "正交函数集 → 正交分解 → 傅里叶级数 → 傅里叶变换 → 拉普拉斯变换 → 系统函数 → 稳定性",
        "Z变换 → 系统函数 → 稳定性",
        "傅里叶变换 → 采样定理 → 信号重构",
        "拉普拉斯变换 → 模拟滤波器",
        "Z变换 → 数字滤波器（FIR/IIR）",
    ]
    for chain in dep_chain:
        print(f"    {chain}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="知识图谱可视化")
    parser.add_argument("--data", default="knowledge_graph_data.json",
                       help="知识图谱JSON数据路径")
    parser.add_argument("--pyvis", action="store_true", default=True,
                       help="生成pyvis交互式HTML (默认)")
    parser.add_argument("--matplotlib", action="store_true",
                       help="生成matplotlib静态PNG图")
    parser.add_argument("--summary", action="store_true",
                       help="打印文本摘要")
    parser.add_argument("--all", action="store_true",
                       help="生成所有可视化输出")
    args = parser.parse_args()
    
    # 加载数据
    print("加载知识图谱数据...")
    kg_data = load_kg_data(args.data)
    
    # 生成可视化
    if args.summary or args.all:
        print_summary(kg_data)
    
    if (args.pyvis and not args.matplotlib) or args.all:
        output_html = "知识图谱可视化.html" if not args.all else "知识图谱可视化-pyvis.html"
        visualize_pyvis(kg_data, output_html)
    
    if args.matplotlib or args.all:
        output_png = "知识图谱-静态图.png" if not args.all else "知识图谱可视化-matplotlib.png"
        visualize_matplotlib(kg_data, output_png)
    
    print("\n完成！")
