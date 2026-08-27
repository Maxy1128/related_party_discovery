"""Dependency-free interactive SVG network explorer for Streamlit."""

from __future__ import annotations

import html
import json
import math
from collections import Counter, defaultdict

import networkx as nx


def interactive_network_html(graph: nx.MultiDiGraph) -> str:
    """Render a self-contained graph with clickable node and edge details."""

    width, height = 1400, 850
    positions = _positions(graph, width, height)
    nodes = []
    for node_id, data in graph.nodes(data=True):
        x, y = positions[node_id]
        nodes.append({
            "id": str(node_id),
            "label": data.get("label") or str(node_id),
            "description": data.get("description"),
            "scope": data.get("scope"),
            "type": data.get("type"),
            "lei": data.get("lei"),
            "country": data.get("country"),
            "address": data.get("address"),
            "ambiguous": bool(data.get("ambiguous")),
            "target": bool(data.get("target")),
            "risk": bool(data.get("risk")),
            "degree": int(graph.degree(node_id)),
            "x": round(x, 2),
            "y": round(y, 2),
            "radius": min(46, 23 + int(math.sqrt(max(graph.degree(node_id), 1)) * 5)),
        })

    raw_edges = list(graph.edges(keys=True, data=True))
    loop_edge_ids = _loop_edge_ids(graph, raw_edges)
    pair_counts = Counter((source, target) for source, target, _, _ in raw_edges)
    pair_indexes: defaultdict[tuple, int] = defaultdict(int)
    edges = []
    edge_svg = []
    for index, (source, target, key, data) in enumerate(raw_edges):
        edge_id = str(data.get("assertion_id") or f"{source}-{target}-{key}-{index}")
        confidence = (data.get("confidence") or "UNVERIFIED").upper()
        specific_relation_type = data.get("proposed_relation_type")
        count = pair_counts[(source, target)]
        offset_index = pair_indexes[(source, target)] - (count - 1) / 2
        pair_indexes[(source, target)] += 1
        edges.append({
            "id": edge_id,
            "source": str(source),
            "target": str(target),
            "source_label": graph.nodes[source].get("label") or str(source),
            "target_label": graph.nodes[target].get("label") or str(target),
            "relation_type": data.get("label"),
            "specific_relation_type": specific_relation_type,
            "assertion_text": data.get("assertion_text"),
            "definition": data.get("definition"),
            "description": data.get("description"),
            "classification": data.get("classification"),
            "confidence": confidence,
            "validation_status": data.get("validation_status"),
            "explicit_or_inferred": data.get("explicit_or_inferred"),
            "event_date": data.get("event_date"),
            "valid_from": data.get("valid_from"),
            "valid_to": data.get("valid_to"),
            "evidence": data.get("evidence"),
            "evidence_quality": data.get("evidence_quality"),
            "source_url": data.get("source_url"),
            "source_title": data.get("source_title"),
            "loop_hint": edge_id in loop_edge_ids,
            "offset": round(offset_index * 46, 2),
        })
        path, label_x, label_y = _edge_path(
            positions[source], positions[target], offset_index * 46
        )
        label = _short(data.get("label") or "RELATION", 24)
        confidence_class = confidence.lower().replace("_", "-")
        loop_class = " loop-hint" if edge_id in loop_edge_ids else ""
        edge_svg.append(
            f'<g class="edge interactive confidence-{confidence_class}{loop_class}" data-edge-id="{html.escape(edge_id)}" '
            f'data-source="{html.escape(str(source))}" data-target="{html.escape(str(target))}" '
            f'data-offset="{offset_index * 46:.2f}" '
            f'tabindex="0" role="button" aria-label="{html.escape(label)}">'
            f'<path class="edge-hit" d="{path}"/>'
            f'<path class="edge-line" d="{path}" marker-end="url(#arrow)"/>'
            f'<text class="edge-label" x="{label_x:.1f}" y="{label_y:.1f}">'
            f'{html.escape(label)}</text></g>'
        )

    node_svg = []
    for node in nodes:
        x, y = node["x"], node["y"]
        css_class = "target" if node["target"] else "risk" if node["risk"] else "standard"
        radius = node["radius"]
        label_lines = _label_lines(node["label"])
        text_parts = []
        for line_index, line in enumerate(label_lines):
            text_parts.append(
                f'<tspan x="{x:.1f}" dy="{15 if line_index else 0}">{html.escape(line)}</tspan>'
            )
        node_svg.append(
            f'<g class="node interactive {css_class}" data-node-id="{html.escape(node["id"])}" data-x="{x:.2f}" data-y="{y:.2f}" '
            f'tabindex="0" role="button" aria-label="{html.escape(node["label"])}">'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}"/>'
            f'<text class="node-label" x="{x:.1f}" y="{y + radius + 18:.1f}">'
            f'{"".join(text_parts)}</text></g>'
        )

    data = _safe_json({"nodes": nodes, "edges": edges})
    entity_types = sorted({str(n["type"]) for n in nodes if n.get("type")})
    relation_types = sorted({str(e["relation_type"]) for e in edges if e.get("relation_type")})
    confidence_levels = [value for value in ("HIGH", "MEDIUM", "LOW", "UNVERIFIED") if any(e["confidence"] == value for e in edges)]
    entity_type_options = "".join(f'<option value="{html.escape(v)}">{html.escape(v)}</option>' for v in entity_types)
    relation_type_options = "".join(f'<option value="{html.escape(v)}">{html.escape(v)}</option>' for v in relation_types)
    confidence_options = "".join(f'<option value="{value}">{value.title()}</option>' for value in confidence_levels)
    initial_node = next((node["id"] for node in nodes if node["target"]), nodes[0]["id"] if nodes else "")
    template = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--ink:#17232d;--muted:#667785;--line:#8c9aa5;--blue:#3d7faf;--gold:#e2a400;--red:#cf5353;--panel:#f7f9fb;--border:#d8e0e6}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 Arial,sans-serif;color:var(--ink);background:white}
.shell{height:920px;display:grid;grid-template-columns:minmax(0,1fr) 380px;border:1px solid var(--border);border-radius:12px;overflow:hidden;background:white}
.canvas{position:relative;min-width:0;background:radial-gradient(circle at 1px 1px,#dfe6eb 1px,transparent 1.2px);background-size:22px 22px}
svg{display:block;width:100%;height:100%;touch-action:none;cursor:grab}svg.panning{cursor:grabbing}
.toolbar{position:absolute;z-index:3;top:14px;left:14px;right:14px;display:flex;gap:6px;flex-wrap:wrap;padding:6px;background:rgba(255,255,255,.96);border:1px solid var(--border);border-radius:9px;box-shadow:0 3px 12px rgba(22,42,57,.1)}
.toolbar button{height:32px;border:0;border-radius:6px;background:#edf2f5;color:#28485f;cursor:pointer}.toolbar button:hover{background:#dfe9ef}.toolbar .zoom{width:34px;font-size:18px}.toolbar .toggle{width:auto;padding:0 10px;font-size:12px;font-weight:700}.toolbar .toggle[aria-pressed="false"]{background:#f4f5f6;color:#788792}.toolbar .label-toggle[aria-pressed="false"]{text-decoration:line-through}
.filters,.search{display:flex;align-items:center;gap:5px}.filters select,.search input{height:32px;border:1px solid var(--border);border-radius:6px;background:white;color:var(--ink)}.filters select{max-width:190px;padding:0 6px}.search{flex:1;min-width:230px}.search input{padding:0 9px;min-width:150px;flex:1}.search-status{font-size:11px;color:var(--muted);min-width:90px;text-align:center}
.legend{position:absolute;z-index:2;left:14px;bottom:12px;display:flex;gap:13px;flex-wrap:wrap;padding:7px 10px;background:rgba(255,255,255,.92);border:1px solid var(--border);border-radius:8px;color:#526674;font-size:12px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}.dot.gold{background:var(--gold)}.dot.blue{background:var(--blue)}.dot.red{background:var(--red)}
.edge-line{fill:none;stroke:var(--line);opacity:.75}.confidence-high .edge-line{stroke-width:4;stroke-dasharray:none;opacity:.95}.confidence-medium .edge-line{stroke-width:3;stroke-dasharray:none;opacity:.85}.confidence-low .edge-line{stroke-width:2;stroke-dasharray:8 5;opacity:.72}.confidence-unverified .edge-line{stroke-width:1.4;stroke-dasharray:3 6;opacity:.55}.edge-hit{fill:none;stroke:transparent;stroke-width:18;cursor:pointer}.edge:hover .edge-line,.edge.selected .edge-line{stroke:#175f91;stroke-width:5;opacity:1}.edge-label{font-size:10px;fill:#566b79;text-anchor:middle;paint-order:stroke;stroke:white;stroke-width:4;stroke-linejoin:round;cursor:pointer}.edge:hover .edge-label,.edge.selected .edge-label{fill:#0e527f;font-weight:700}.canvas.hide-edge-labels .edge-label{display:none}.canvas.hide-edge-labels .edge.selected .edge-label{display:block}.canvas.hide-edge-labels .edge.search-match .edge-label{display:block}
.node circle{stroke:white;stroke-width:4;filter:drop-shadow(0 3px 4px rgba(25,46,61,.18));cursor:pointer}.node.standard circle{fill:var(--blue)}.node.target circle{fill:var(--gold)}.node.risk circle{fill:var(--red)}.node:hover circle,.node.selected circle{stroke:#19384e;stroke-width:6}.node-label{text-anchor:middle;font-size:11px;font-weight:700;fill:#243a49;paint-order:stroke;stroke:white;stroke-width:5;stroke-linejoin:round;pointer-events:none}.canvas.hide-node-labels .node-label{display:none}.canvas.hide-node-labels .node.selected .node-label{display:block}
.node.dragging circle{stroke:#173d56;stroke-width:7}.search-dim{opacity:.12}.search-match .edge-line{stroke:#8b38b5!important;opacity:1!important}.node.search-match circle{stroke:#8b38b5;stroke-width:7}.canvas.hide-node-labels .node.search-match .node-label{display:block}.canvas.show-loops .edge:not(.loop-hint){opacity:.1}.canvas.show-loops .edge.loop-hint .edge-line{stroke:#d13f70;opacity:1}.canvas.show-loops .edge.loop-hint .edge-label{display:block;fill:#9a214c;font-weight:700}.line-sample{display:inline-block;width:24px;vertical-align:middle;margin-right:5px;border-top-color:#70818d;border-top-style:solid}.line-sample.high{border-top-width:4px}.line-sample.medium{border-top-width:3px}.line-sample.low{border-top-width:2px;border-top-style:dashed}.line-sample.unverified{border-top-width:1px;border-top-style:dotted}.line-sample.loop{border-top:3px solid #d13f70}
.details{background:var(--panel);border-left:1px solid var(--border);overflow:auto;padding:22px}.kicker{text-transform:uppercase;letter-spacing:.1em;color:#587589;font-size:11px;font-weight:700}.details h2{font-size:21px;line-height:1.2;margin:7px 0 12px;color:#153c57}.badges{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}.badge{padding:3px 7px;border-radius:999px;background:#e5edf2;color:#345366;font-size:11px;font-weight:700}.badge.high{background:#dff2e5;color:#22623a}.badge.low{background:#fff0d3;color:#79520a}.badge.risk{background:#f9dddd;color:#8b2f2f}
.summary{background:white;border:1px solid var(--border);border-left:4px solid #5e94b8;border-radius:7px;padding:11px 12px;margin:12px 0}.summary small{display:block;color:var(--muted);margin-top:7px}.row{padding:8px 0;border-bottom:1px solid #e1e7eb}.row strong{display:block;color:#5a6d7a;font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:2px}.evidence{white-space:pre-wrap;background:white;border:1px solid var(--border);border-radius:7px;padding:10px;margin-top:6px;max-height:180px;overflow:auto;font-size:12px}.source{display:inline-block;margin-top:12px;color:#0f659c;font-weight:700;text-decoration:none}.source:hover{text-decoration:underline}.connections{margin-top:14px}.connection{display:block;width:100%;text-align:left;margin:5px 0;padding:8px;border:1px solid var(--border);border-radius:7px;background:white;color:#28485f;cursor:pointer}.connection:hover{border-color:#6c9ab8;background:#f0f6fa}.empty{color:var(--muted);padding:30px 4px}
@media(max-width:860px){.shell{height:920px;grid-template-columns:1fr;grid-template-rows:540px 380px}.details{border-left:0;border-top:1px solid var(--border)} }
</style></head><body>
<div class="shell"><section class="canvas" id="canvas">
<div class="toolbar"><button class="zoom" id="zoom-in" title="Zoom in">+</button><button class="zoom" id="zoom-out" title="Zoom out">−</button><button class="zoom" id="reset" title="Reset view and node positions">⌂</button><button class="toggle label-toggle" id="node-label-toggle" type="button" aria-pressed="true">Node labels: On</button><button class="toggle label-toggle" id="edge-label-toggle" type="button" aria-pressed="true">Edge labels: On</button><button class="toggle" id="loop-toggle" type="button" aria-pressed="false">Loop: Off</button><div class="filters"><select id="entity-type-filter" aria-label="Filter entity type"><option value="">All entity types</option>__ENTITY_TYPES__</select><select id="relation-type-filter" aria-label="Filter relationship type"><option value="">All relationship types</option>__RELATION_TYPES__</select><select id="confidence-filter" aria-label="Filter relationship confidence"><option value="">All confidence levels</option>__CONFIDENCE_LEVELS__</select></div><div class="search"><input id="graph-search" type="search" placeholder="Search entity" aria-label="Search entity"><button id="search-clear" type="button">Clear</button><span class="search-status" id="search-status"></span></div></div>
<svg id="graph" viewBox="0 0 1400 850" aria-label="Interactive relationship network"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#8c9aa5"/></marker></defs><g id="viewport">__EDGES____NODES__</g></svg>
<div class="legend"><span><i class="dot gold"></i>Target</span><span><i class="dot blue"></i>Entity</span><span><i class="dot red"></i>Risk signal</span><span><i class="line-sample high"></i>High</span><span><i class="line-sample medium"></i>Medium</span><span><i class="line-sample low"></i>Low</span><span><i class="line-sample unverified"></i>Unverified</span><span><i class="line-sample loop"></i>Directed loop hint</span><span>Click a node or edge for details; drag nodes to untangle the view</span></div>
</section><aside class="details" id="details"><div class="empty">Select a node or relationship.</div></aside></div>
<script>
const DATA=__DATA__;const INITIAL=__INITIAL__;
const nodes=new Map(DATA.nodes.map(n=>{n.initialX=n.x;n.initialY=n.y;return [String(n.id),n]}));const edges=new Map(DATA.edges.map(e=>[String(e.id),e]));
const details=document.getElementById('details');const svg=document.getElementById('graph');const viewport=document.getElementById('viewport');const canvas=document.getElementById('canvas');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const row=(label,value)=>value?`<div class="row"><strong>${esc(label)}</strong>${esc(value)}</div>`:'';
const badge=(value,kind='')=>value?`<span class="badge ${kind}">${esc(value)}</span>`:'';
function safeUrl(value){try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)?u.href:null}catch{return null}}
function clearSelection(){document.querySelectorAll('.selected').forEach(el=>el.classList.remove('selected'))}
function selectNode(id){const n=nodes.get(String(id));if(!n)return;clearSelection();document.querySelector(`[data-node-id="${CSS.escape(String(id))}"]`)?.classList.add('selected');
 const connected=DATA.edges.filter(e=>String(e.source)===String(id)||String(e.target)===String(id));
 details.innerHTML=`<div class="kicker">Entity information</div><h2>${esc(n.label)}</h2><div class="badges">${badge(n.target?'Investigation target':'')}${badge(n.risk?'Risk signal':'','risk')}${badge(n.ambiguous?'Ambiguous identity':'','low')}</div>${n.description?`<div class="summary">${esc(n.description)}<small>AI-generated presentation summary</small></div>`:''}${row('Entity scope',n.scope)}${row('Entity type',n.type)}${row('LEI',n.lei)}${row('Country',n.country)}${row('Registered address',n.address)}${row('Connected graph relationships',n.degree)}<div class="connections"><strong>Relationships in this view</strong>${connected.map(e=>`<button class="connection" data-open-edge="${esc(e.id)}">${esc(e.source_label)} · ${esc(edgeDisplayType(e))} · ${esc(e.target_label)}</button>`).join('')||'<div class="empty">No displayed relationships.</div>'}</div>`;
 details.querySelectorAll('[data-open-edge]').forEach(b=>b.addEventListener('click',()=>selectEdge(b.dataset.openEdge)))}
function edgeDisplayType(e){return e.relation_type==='OTHER_MATERIAL_RELATION'&&e.specific_relation_type?e.specific_relation_type:e.relation_type}
function selectEdge(id){const e=edges.get(String(id));if(!e)return;clearSelection();document.querySelector(`[data-edge-id="${CSS.escape(String(id))}"]`)?.classList.add('selected');const url=safeUrl(e.source_url);
 const specific=e.relation_type==='OTHER_MATERIAL_RELATION'?e.specific_relation_type:null;
 details.innerHTML=`<div class="kicker">Relationship information</div><h2>${esc(e.source_label)} → ${esc(e.target_label||'Not specified')}</h2><div class="badges">${badge(e.relation_type)}${badge(specific)}${badge(e.classification)}${badge(e.confidence,(e.confidence||'').toLowerCase())}${badge(e.validation_status)}${badge(e.loop_hint?'Directed loop hint':'','risk')}</div>${e.description?`<div class="summary">${esc(e.description)}<small>AI-generated presentation summary</small></div>`:''}${row('Specific relationship type',specific)}${row('Evidence-supported relationship statement',e.assertion_text)}${row('Relationship definition',e.definition)}${row('Evidence basis',e.explicit_or_inferred)}${row('Event date',e.event_date)}${row('Valid from',e.valid_from)}${row('Valid to',e.valid_to)}${e.loop_hint?row('Structural interpretation','This edge participates in a directed loop in the displayed graph. A loop is a review lead, not evidence of misconduct.'):''}${e.evidence?`<div class="row"><strong>Supporting evidence</strong><div class="evidence">${esc(e.evidence)}</div></div>`:''}${row('Evidence quality',e.evidence_quality)}${row('Source document',e.source_title)}${url?`<a class="source" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open original source ↗</a>`:''}`}
function nodePoint(n){return {x:Number(n.x),y:Number(n.y)}}
function edgeGeometry(e){let s=nodes.get(String(e.source)),t=nodes.get(String(e.target));let sx=Number(s.x),sy=Number(s.y),tx=Number(t.x),ty=Number(t.y),dx=tx-sx,dy=ty-sy,len=Math.max(Math.hypot(dx,dy),1),ux=dx/len,uy=dy/len;sx+=ux*Number(s.radius);sy+=uy*Number(s.radius);tx-=ux*(Number(t.radius)+5);ty-=uy*(Number(t.radius)+5);let mx=(sx+tx)/2,my=(sy+ty)/2,cx=mx-dy/len*Number(e.offset||0),cy=my+dx/len*Number(e.offset||0);return {path:`M ${sx.toFixed(1)} ${sy.toFixed(1)} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${tx.toFixed(1)} ${ty.toFixed(1)}`,x:cx,y:cy-5}}
function updateEdge(e){const group=document.querySelector(`[data-edge-id="${CSS.escape(String(e.id))}"]`);if(!group)return;const geometry=edgeGeometry(e);group.querySelectorAll('path').forEach(path=>path.setAttribute('d',geometry.path));const label=group.querySelector('.edge-label');label.setAttribute('x',geometry.x.toFixed(1));label.setAttribute('y',geometry.y.toFixed(1))}
function updateNode(id){const n=nodes.get(String(id)),group=document.querySelector(`[data-node-id="${CSS.escape(String(id))}"]`);if(!n||!group)return;group.dataset.x=n.x;group.dataset.y=n.y;const circle=group.querySelector('circle'),label=group.querySelector('.node-label');circle.setAttribute('cx',n.x);circle.setAttribute('cy',n.y);label.setAttribute('x',n.x);label.setAttribute('y',Number(n.y)+Number(n.radius)+18);label.querySelectorAll('tspan').forEach(span=>span.setAttribute('x',n.x));DATA.edges.filter(e=>String(e.source)===String(id)||String(e.target)===String(id)).forEach(updateEdge)}
document.querySelectorAll('[data-node-id]').forEach(el=>{el.addEventListener('click',ev=>{if(el.dataset.dragged==='true'){el.dataset.dragged='false';return}ev.stopPropagation();selectNode(el.dataset.nodeId)});el.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();selectNode(el.dataset.nodeId)}})});
document.querySelectorAll('[data-edge-id]').forEach(el=>{el.addEventListener('click',ev=>{ev.stopPropagation();selectEdge(el.dataset.edgeId)});el.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();selectEdge(el.dataset.edgeId)}})});
function bindLabelToggle(buttonId,className,label){const button=document.getElementById(buttonId);button.addEventListener('click',()=>{const labelsVisible=button.getAttribute('aria-pressed')==='true';const nextVisible=!labelsVisible;button.setAttribute('aria-pressed',String(nextVisible));button.textContent=`${label}: ${nextVisible?'On':'Off'}`;canvas.classList.toggle(className,!nextVisible)})}
bindLabelToggle('node-label-toggle','hide-node-labels','Node labels');bindLabelToggle('edge-label-toggle','hide-edge-labels','Edge labels');
const loopToggle=document.getElementById('loop-toggle');loopToggle.addEventListener('click',()=>{const enabled=loopToggle.getAttribute('aria-pressed')!=='true';loopToggle.setAttribute('aria-pressed',String(enabled));loopToggle.textContent=`Loop: ${enabled?'On':'Off'}`;canvas.classList.toggle('show-loops',enabled);applyFilters()});
function runSearch(){const query=document.getElementById('graph-search').value.trim().toLowerCase(),kind=document.getElementById('search-kind').value;document.querySelectorAll('.search-match,.search-dim').forEach(el=>el.classList.remove('search-match','search-dim'));if(!query){document.getElementById('search-status').textContent='';return}let nodeMatches=[],edgeMatches=[];if(kind!=='relation')nodeMatches=DATA.nodes.filter(n=>[n.label,n.type,n.scope,n.lei].some(v=>String(v||'').toLowerCase().includes(query)));if(kind!=='entity')edgeMatches=DATA.edges.filter(e=>[e.relation_type,e.specific_relation_type,e.classification,e.source_label,e.target_label].some(v=>String(v||'').toLowerCase().includes(query)));const nodeIds=new Set(nodeMatches.map(n=>String(n.id))),edgeIds=new Set(edgeMatches.map(e=>String(e.id)));if(kind==='relation')edgeMatches.forEach(e=>{nodeIds.add(String(e.source));nodeIds.add(String(e.target))});if(kind==='entity')DATA.edges.filter(e=>nodeIds.has(String(e.source))||nodeIds.has(String(e.target))).forEach(e=>edgeIds.add(String(e.id)));document.querySelectorAll('.node').forEach(el=>el.classList.add(nodeIds.has(el.dataset.nodeId)?'search-match':'search-dim'));document.querySelectorAll('.edge').forEach(el=>el.classList.add(edgeIds.has(el.dataset.edgeId)?'search-match':'search-dim'));document.getElementById('search-status').textContent=`${nodeMatches.length} entities · ${edgeMatches.length} edges`;if(nodeMatches.length===1&&edgeMatches.length===0)selectNode(nodeMatches[0].id);if(edgeMatches.length===1&&nodeMatches.length===0)selectEdge(edgeMatches[0].id)}
function applyFilters(){
 const type=document.getElementById('entity-type-filter').value,relation=document.getElementById('relation-type-filter').value,confidence=document.getElementById('confidence-filter').value,query=document.getElementById('graph-search').value.trim().toLowerCase(),loop=loopToggle.getAttribute('aria-pressed')==='true';
 const hasRelationshipFilter=Boolean(relation||confidence||loop);
 const matchingEdges=DATA.edges.filter(e=>(!relation||e.relation_type===relation)&&(!confidence||e.confidence===confidence)&&(!loop||e.loop_hint));
 const relationNodeIds=new Set(matchingEdges.flatMap(e=>[String(e.source),String(e.target)]));
 const visibleNodes=new Set(DATA.nodes.filter(n=>{
   const matchesType=!type||n.type===type, matchesQuery=!query||String(n.label||'').toLowerCase().includes(query);
   const matchesRelationship=!hasRelationshipFilter||relationNodeIds.has(String(n.id));
   return matchesType&&matchesQuery&&matchesRelationship;
 }).map(n=>String(n.id)));
 document.querySelectorAll('.node').forEach(el=>el.style.display=visibleNodes.has(el.dataset.nodeId)?'':'none');
 document.querySelectorAll('.edge').forEach(el=>{const e=edges.get(el.dataset.edgeId),show=matchingEdges.some(candidate=>String(candidate.id)===String(e.id))&&visibleNodes.has(String(e.source))&&visibleNodes.has(String(e.target));el.style.display=show?'':'none'});
 document.getElementById('search-status').textContent=`${visibleNodes.size} entities · ${matchingEdges.filter(e=>visibleNodes.has(String(e.source))&&visibleNodes.has(String(e.target))).length} relationships`
}
document.getElementById('graph-search').addEventListener('input',applyFilters);document.getElementById('search-clear').addEventListener('click',()=>{document.getElementById('graph-search').value='';applyFilters()});document.getElementById('entity-type-filter').addEventListener('change',applyFilters);document.getElementById('relation-type-filter').addEventListener('change',applyFilters);document.getElementById('confidence-filter').addEventListener('change',applyFilters);
let scale=1,tx=0,ty=0,panning=false,draggingNode=null,dragStart=null,last={x:0,y:0};const apply=()=>viewport.setAttribute('transform',`translate(${tx} ${ty}) scale(${scale})`);const zoom=f=>{scale=Math.max(.5,Math.min(2.6,scale*f));apply()};
document.getElementById('zoom-in').onclick=()=>zoom(1.2);document.getElementById('zoom-out').onclick=()=>zoom(1/1.2);document.getElementById('reset').onclick=()=>{scale=1;tx=0;ty=0;apply();nodes.forEach(n=>{n.x=n.initialX;n.y=n.initialY;updateNode(n.id)})};
svg.addEventListener('wheel',ev=>{ev.preventDefault();zoom(ev.deltaY<0?1.1:1/1.1)},{passive:false});svg.addEventListener('pointerdown',ev=>{const node=ev.target.closest('.node');last={x:ev.clientX,y:ev.clientY};if(node){draggingNode=node;dragStart={x:ev.clientX,y:ev.clientY};node.dataset.dragged='false';node.classList.add('dragging');node.setPointerCapture(ev.pointerId);return}if(ev.target.closest('.interactive'))return;panning=true;svg.classList.add('panning');svg.setPointerCapture(ev.pointerId)});svg.addEventListener('pointermove',ev=>{if(draggingNode){const n=nodes.get(draggingNode.dataset.nodeId);n.x=Number(n.x)+(ev.clientX-last.x)/scale;n.y=Number(n.y)+(ev.clientY-last.y)/scale;last={x:ev.clientX,y:ev.clientY};if(Math.hypot(ev.clientX-dragStart.x,ev.clientY-dragStart.y)>6)draggingNode.dataset.dragged='true';updateNode(n.id);return}if(!panning)return;tx+=(ev.clientX-last.x)/scale;ty+=(ev.clientY-last.y)/scale;last={x:ev.clientX,y:ev.clientY};apply()});svg.addEventListener('pointerup',()=>{if(draggingNode){const completedNode=draggingNode;completedNode.classList.remove('dragging');if(completedNode.dataset.dragged!=='true')selectNode(completedNode.dataset.nodeId)}draggingNode=null;panning=false;svg.classList.remove('panning')});svg.addEventListener('pointercancel',()=>{if(draggingNode)draggingNode.classList.remove('dragging');draggingNode=null;panning=false;svg.classList.remove('panning')});
if(INITIAL)selectNode(INITIAL);
</script></body></html>"""
    return (
        template.replace("__EDGES__", "".join(edge_svg))
        .replace("__NODES__", "".join(node_svg))
        .replace("__DATA__", data)
        .replace("__ENTITY_TYPES__", entity_type_options)
        .replace("__RELATION_TYPES__", relation_type_options)
        .replace("__CONFIDENCE_LEVELS__", confidence_options)
        # Retain the legacy search-kind hook for cached/browser integrations;
        # the visible UI now uses dedicated filters and entity-only search.
        .replace("<script>", '<select id="search-kind" aria-label="Search type" style="display:none"><option value="entity">Entities</option></select><script>', 1)
        .replace("__INITIAL__", json.dumps(initial_node))
    )


def _positions(graph: nx.MultiDiGraph, width: int, height: int) -> dict:
    if not graph.nodes:
        return {}
    if len(graph.nodes) == 1:
        node = next(iter(graph.nodes))
        return {node: (width / 2, height / 2)}
    raw = nx.spring_layout(graph, seed=23, k=2.4, iterations=180, weight=None)
    xs = [value[0] for value in raw.values()]
    ys = [value[1] for value in raw.values()]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    return {
        node: (
            120 + (value[0] - x_min) / max(x_max - x_min, 1e-9) * (width - 240),
            105 + (value[1] - y_min) / max(y_max - y_min, 1e-9) * (height - 210),
        )
        for node, value in raw.items()
    }


def _loop_edge_ids(graph: nx.MultiDiGraph, raw_edges: list[tuple]) -> set[str]:
    """Return edge ids inside directed strongly connected structures.

    These are structural review hints only. A directed loop does not itself
    establish circular ownership, misconduct, or any other adverse conclusion.
    """

    simple = nx.DiGraph()
    simple.add_nodes_from(graph.nodes)
    simple.add_edges_from((source, target) for source, target, _, _ in raw_edges)
    cyclic_nodes: set = set()
    for component in nx.strongly_connected_components(simple):
        if len(component) > 1:
            cyclic_nodes.update(component)
        elif component:
            node = next(iter(component))
            if simple.has_edge(node, node):
                cyclic_nodes.add(node)
    result: set[str] = set()
    for index, (source, target, key, data) in enumerate(raw_edges):
        if source in cyclic_nodes and target in cyclic_nodes:
            result.add(str(data.get("assertion_id") or f"{source}-{target}-{key}-{index}"))
    return result


def _edge_path(source: tuple[float, float], target: tuple[float, float], offset: float):
    sx, sy = source
    tx, ty = target
    dx, dy = tx - sx, ty - sy
    length = max(math.hypot(dx, dy), 1)
    ux, uy = dx / length, dy / length
    sx, sy = sx + ux * 31, sy + uy * 31
    tx, ty = tx - ux * 36, ty - uy * 36
    mx, my = (sx + tx) / 2, (sy + ty) / 2
    cx, cy = mx - dy / length * offset, my + dx / length * offset
    return f"M {sx:.1f} {sy:.1f} Q {cx:.1f} {cy:.1f} {tx:.1f} {ty:.1f}", cx, cy - 5


def _label_lines(value: str, width: int = 18) -> list[str]:
    words = value.split()
    if not words:
        return [""]
    lines = [""]
    for word in words:
        candidate = (lines[-1] + " " + word).strip()
        if len(candidate) <= width or not lines[-1]:
            lines[-1] = candidate
        elif len(lines) == 1:
            lines.append(word)
        else:
            lines[-1] = _short(lines[-1] + " " + word, width)
    return lines[:2]


def _short(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _safe_json(value: dict) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _original_node_id(graph: nx.MultiDiGraph, serialized_id: str):
    for node_id in graph.nodes:
        if str(node_id) == serialized_id:
            return node_id
    raise KeyError(serialized_id)
