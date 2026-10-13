import { useEffect, useId, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Search, GitBranch, LoaderCircle, X, Plus, Minus, Maximize, Download, ArrowLeft } from "lucide-react";
import { request } from "./api";
import { visiblePedigree, layoutPedigree, expandLevels, fitView, zoomView, NODE_WIDTH, NODE_HEIGHT } from "./pedigreeLayout";
import "./PedigreeWorkspace.css";

const roles={female:"♀ 母本",male:"♂ 父本",parent:"亲本 / 来源",unknown:"关系未注明"};
const kinds={variety:"品种 / 材料",cross:"杂交组合",text:"来源文字节点",unknown:"类型未注明"};
const short=value=>String(value||"").length>15?String(value).slice(0,15)+"…":String(value||"");
const number=value=>Number(value||0).toLocaleString();
const safeLink=value=>/^https?:\/\//i.test(value||"")?value:undefined;

function catalogLayout(catalog,group,records) {
  const nodes=[{id:"catalog",name:"国家水稻品种库",subtitle:`${number(catalog?.total)} 个品种`,x:70,y:250,kind:"catalog",circle:true,is_root:true}];
  const edges=[];
  const groups=group?(catalog?.groups||[]).filter(g=>g.code===group):catalog?.groups||[];
  groups.forEach((g,index)=>{
    const node={id:`group-${g.code}`,name:g.name,subtitle:`${number(g.count)} 个品种`,group:g.code,x:350,y:group?250:70+index*150,kind:"catalog",circle:true};
    nodes.push(node); edges.push({id:`catalog-${g.code}`,from:nodes[0],to:node,catalog:true});
    if(group) records.forEach((record,i)=>{
      const leaf={...record,id:`variety-${record.id}`,variety_id:record.id,x:660+(i%4)*245,y:40+Math.floor(i/4)*90,kind:"catalog-variety",subtitle:record.edge_count?`已有 ${record.node_count} 个系谱节点`:"未收录亲本关系"};
      nodes.push(leaf); edges.push({id:`contains-${record.id}`,from:node,to:leaf,catalog:true});
    });
  });
  return {nodes,edges,width:Math.max(650,...nodes.map(n=>n.x+NODE_WIDTH+60)),height:Math.max(660,...nodes.map(n=>n.y+110)),labels:[]};
}

export default function PedigreeWorkspace() {
  const [catalog,setCatalog]=useState(null);
  const [keyword,setKeyword]=useState("");
  const [group,setGroup]=useState("");
  const [onlyPedigree,setOnlyPedigree]=useState(false);
  const [list,setList]=useState(null);
  const [listBusy,setListBusy]=useState(false);
  const [error,setError]=useState("");
  const [graph,setGraph]=useState(null);
  const [selectedVariety,setSelectedVariety]=useState(null);
  const [graphBusy,setGraphBusy]=useState(false);
  const [expanded,setExpanded]=useState(new Set());
  const [showAll,setShowAll]=useState(false);
  const [viewport,setViewport]=useState({width:900,height:600});
  const [view,setView]=useState({scale:1,x:0,y:0});
  const [selectedNode,setSelectedNode]=useState(null);
  const [detail,setDetail]=useState(null);
  const [detailBusy,setDetailBusy]=useState(false);
  const [detailError,setDetailError]=useState("");
  const requests=useRef({});
  const stage=useRef(null);
  const svg=useRef(null);
  const drag=useRef(null);
  const dialog=useRef(null);
  const detailBody=useRef(null);
  const backdropPressed=useRef(false);
  const titleId=useId();
  const markerId=useId().replaceAll(":","");
  const visible=useMemo(()=>visiblePedigree(graph,expanded,showAll),[graph,expanded,showAll]);
  const layout=useMemo(()=>graph?layoutPedigree(graph,visible):catalogLayout(catalog,group,list?.records||[]),[graph,visible,catalog,group,list]);
  const parentIds=useMemo(()=>new Set((graph?.edges||[]).map(e=>String(e.child_id))),[graph]);

  function controller(kind) { requests.current[kind]?.abort(); const next=new AbortController(); requests.current[kind]=next; return next; }
  async function browse(next={keyword,group,only:onlyPedigree,page:1}) {
    const control=controller("list"); setListBusy(true); setList(null); setError("");
    try {
      const params=new URLSearchParams({q:next.keyword||"",group:next.group||"",only_pedigree:String(next.only),page:String(next.page),page_size:"30"});
      const response=await request(`/api/research/pedigree/varieties?${params}`,{signal:control.signal});
      if(!control.signal.aborted) setList({...response,only:next.only});
    } catch(e) {if(!control.signal.aborted) {setList(null);setError(e.message);}}
    finally {if(!control.signal.aborted)setListBusy(false);}
  }
  useEffect(()=>{
    const control=controller("catalog");
    request("/api/research/pedigree/catalog",{signal:control.signal}).then(data=>{if(!control.signal.aborted)setCatalog(data);}).catch(e=>{if(!control.signal.aborted)setError(e.message);});
    browse({keyword:"",group:"",only:false,page:1});
    return ()=>Object.values(requests.current).forEach(c=>c.abort());
  },[]);
  useEffect(()=>{
    if(!stage.current)return;
    const observer=new ResizeObserver(([entry])=>setViewport({width:entry.contentRect.width,height:entry.contentRect.height}));
    observer.observe(stage.current); return ()=>observer.disconnect();
  },[]);
  useEffect(()=>{setView(fitView(layout,viewport));},[layout,viewport]);
  useEffect(()=>{
    const element=svg.current;
    const wheel=e=>{
      e.preventDefault(); const bounds=element.getBoundingClientRect();
      setView(current=>zoomView(current,Math.exp(-Math.max(-100,Math.min(100,e.deltaY))*.003),{x:e.clientX-bounds.left,y:e.clientY-bounds.top}));
    };
    element.addEventListener("wheel",wheel,{passive:false}); return ()=>element.removeEventListener("wheel",wheel);
  },[]);

  async function selectVariety(record) {
    const control=controller("graph"); requests.current.detail?.abort(); dialog.current?.close();
    setSelectedVariety(record); setGraph(null);setGraphBusy(true);setError("");setShowAll(false);
    try {
      const response=await request(`/api/research/pedigree/varieties/${record.id}/graph`,{signal:control.signal});
      if(!control.signal.aborted){setGraph(response);setExpanded(new Set(response.root_ids.map(String)));}
    } catch(e){if(!control.signal.aborted)setError(e.message);}
    finally {if(!control.signal.aborted)setGraphBusy(false);}
  }
  function directory(nextGroup=group) {
    requests.current.graph?.abort(); setGraphBusy(false);setGraph(null);setSelectedVariety(null);setShowAll(false);
    if(nextGroup!==group){setGroup(nextGroup);browse({keyword,group:nextGroup,only:onlyPedigree,page:1});}
  }
  function chooseGroup(nextGroup) { directory(nextGroup); if(nextGroup===group)browse({keyword,group:nextGroup,only:onlyPedigree,page:1}); }
  function toggleNode(node) {
    setShowAll(false);
    setExpanded(current=>{const next=new Set(current);next.has(String(node.id))?next.delete(String(node.id)):next.add(String(node.id));return next;});
  }
  async function openDetail(node) {
    if(drag.current?.moved)return;
    const control=controller("detail");setSelectedNode(node);setDetail(null);setDetailError("");setDetailBusy(Boolean(node.variety_id));
    if(!dialog.current.open)dialog.current.showModal();detailBody.current?.scrollTo({top:0,left:0});
    if(!node.variety_id)return;
    try {
      const response=await request(`/api/research/variety-search/ricedata/${node.variety_id}`,{signal:control.signal});
      if(!control.signal.aborted)setDetail(response);
    } catch(e){if(!control.signal.aborted)setDetailError(e.message);}
    finally {if(!control.signal.aborted)setDetailBusy(false);}
  }
  function nodeAction(node) {
    if(node.id==="catalog")return chooseGroup("");
    if(node.group)return chooseGroup(node.group);
    if(node.kind==="catalog-variety")return selectVariety({id:node.variety_id,name:node.name});
    openDetail(node);
  }
  function outside(e) {if(e.target!==e.currentTarget)return false;const r=e.currentTarget.getBoundingClientRect();return e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom;}
  function downloadGraph() {
    const clone=svg.current.cloneNode(true);
    clone.setAttribute("xmlns","http://www.w3.org/2000/svg");clone.setAttribute("viewBox",`0 0 ${layout.width} ${layout.height}`);clone.setAttribute("width",layout.width);clone.setAttribute("height",layout.height);
    clone.querySelector(".pg-world").removeAttribute("transform");
    const footer=document.createElementNS("http://www.w3.org/2000/svg","text");footer.setAttribute("x","20");footer.setAttribute("y",String(layout.height-8));footer.setAttribute("font-size","12");footer.setAttribute("fill","#516d60");
    footer.textContent=graph?`${graph.variety.name} · 当前可见 ${visible.nodes.length}/${graph.node_count} 节点 · 国家水稻数据中心 · 仅为已收录系谱，非完整育种证明`:"品种目录分组（不是亲缘关系） · 国家水稻数据中心";
    clone.appendChild(footer);
    const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)],{type:"image/svg+xml;charset=utf-8"}));
    const anchor=document.createElement("a");anchor.href=url;anchor.download=`${(graph?.variety.name||"品种目录").replace(/[\\/:*?"<>|]/g,"_")}-系谱.svg`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }

  return <section className="pedigree-workspace">
    <header className="pg-heading"><div><h2><GitBranch size={22}/>水稻品种与祖先系谱</h2><p>国家水稻数据中心 · {catalog?`${number(catalog.total)} 个品种，${number(catalog.with_pedigree)} 个有已收录系谱连线`:"正在核对品种目录…"}</p></div><button className="secondary-button" onClick={()=>{directory("");setKeyword("");setOnlyPedigree(false);browse({keyword:"",group:"",only:false,page:1});}}>返回全部品种</button></header>
    <form className="pg-search" onSubmit={e=>{e.preventDefault();directory();browse();}}><input aria-label="搜索系谱品种" placeholder="搜索品种名或别名，例如田两优9号" maxLength={120} value={keyword} onChange={e=>setKeyword(e.target.value)}/><select aria-label="系谱品种分组" value={group} onChange={e=>chooseGroup(e.target.value)}><option value="">全部类型</option>{catalog?.groups.map(g=><option key={g.code} value={g.code}>{g.name}（{number(g.count)}）</option>)}</select><label><input type="checkbox" checked={onlyPedigree} onChange={e=>{const only=e.target.checked;setOnlyPedigree(only);directory();browse({keyword,group,only,page:1});}}/>仅有系谱</label><button className="primary-button" type="submit" disabled={listBusy}><Search size={16}/>检索</button></form>
    {error&&<p className="pg-error" role="alert">{error}</p>}
    <div className="pg-layout"><aside className="pg-list"><div className="pg-list-heading"><strong>{list?`匹配 ${number(list.total)} 个品种`:"品种目录"}</strong>{listBusy&&<LoaderCircle className="spin" size={15}/>}</div><p>选择品种查看系谱。目录分组不是亲缘关系。</p><div className="pg-list-records">{list?.records.map(record=><button type="button" key={record.id} aria-pressed={selectedVariety?.id===record.id} onClick={()=>selectVariety(record)}><strong>{record.name}</strong><span>{record.variety_type||"类型未记载"} · {record.edge_count?`${record.node_count}个已存节点` : "未收录亲本关系"}</span></button>)}{!listBusy&&list&&!list.records.length&&<p>未找到符合条件的品种，可减少条件或改用别名检索。</p>}</div><div className="pg-list-pages"><button disabled={!list||list.page<=1||listBusy} onClick={()=>browse({keyword:list.keyword,group:list.group,only:list.only,page:list.page-1})}>上一页</button><span>{list?.page||1} / {Math.max(1,Math.ceil((list?.total||0)/30))}</span><button disabled={!list?.has_more||listBusy} onClick={()=>browse({keyword:list.keyword,group:list.group,only:list.only,page:list.page+1})}>下一页</button></div></aside>
      <div className="pg-canvas-panel"><div className="pg-toolbar"><div><strong>{selectedVariety?.name||"品种库概览"}</strong><span>{graph?`显示 ${visible.nodes.length} / ${graph.node_count} 个来源节点，${visible.edges.length} 条连线` : "点击分类浏览品种，再点击品种展开系谱"}</span></div><div className="pg-actions">{graph&&<><button className="primary-button" onClick={()=>{setExpanded(new Set(graph.nodes.map(n=>String(n.id))));setShowAll(true);}}>一键展开全部</button><button className="secondary-button" onClick={()=>{setExpanded(expandLevels(graph,3));setShowAll(false);}}>展开3层</button><button className="secondary-button" onClick={()=>{setExpanded(new Set());setShowAll(false);}}>收起全部</button></>}<button className="secondary-button" onClick={()=>directory()} disabled={!selectedVariety}><ArrowLeft size={14}/>目录</button></div></div>
        {graph?.warnings.length>0&&<div className="pg-warnings">{graph.warnings.map((warning,index)=><p key={index}>{warning}</p>)}</div>}
        <div className="pg-stage" ref={stage} aria-busy={graphBusy}>
          {graphBusy&&<div className="pg-loading" role="status"><LoaderCircle className="spin" size={22}/>正在读取服务器系谱…</div>}
          <svg ref={svg} width="100%" height="100%" viewBox={`0 0 ${viewport.width} ${viewport.height}`} role="group" aria-label={graph?`${graph.variety.name}的亲本祖先系谱图`:"品种目录分类图（不是亲缘关系）"} onPointerDown={e=>{if(e.button!==0)return;drag.current={x:e.clientX,y:e.clientY,moved:false};if(e.target===e.currentTarget)e.currentTarget.setPointerCapture(e.pointerId);}} onPointerMove={e=>{if(!drag.current||!e.buttons)return;const dx=e.clientX-drag.current.x,dy=e.clientY-drag.current.y;if(Math.abs(dx)+Math.abs(dy)>2)drag.current.moved=true;if(drag.current.moved&&!e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.setPointerCapture(e.pointerId);if(drag.current.moved)setView(v=>({...v,x:v.x+dx,y:v.y+dy}));drag.current.x=e.clientX;drag.current.y=e.clientY;}} onPointerUp={e=>{if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);}} onPointerCancel={()=>{drag.current=null;}}>
            <defs><marker id={markerId} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#87a99a"/></marker></defs><g className="pg-world" transform={`translate(${view.x},${view.y}) scale(${view.scale})`}>
              {layout.labels.map(label=><text key={label.label} x={label.x} y={label.y} fill="#59796b" fontSize="13">{label.label}</text>)}
              {layout.edges.map(edge=>{const sx=edge.from.x+NODE_WIDTH,sy=edge.from.y+NODE_HEIGHT/2,tx=edge.to.x,ty=edge.to.y+NODE_HEIGHT/2;return <g key={edge.id}><path d={`M${sx},${sy} C${sx+38},${sy} ${tx-38},${ty} ${tx},${ty}`} fill="none" stroke={edge.catalog?"#d1dfd4":edge.role==="female"?"#b587a0":edge.role==="male"?"#829bbc":"#87a99a"} strokeWidth="1.4" markerEnd={edge.catalog?undefined:`url(#${markerId})`}/>{!edge.catalog&&<text x={(sx+tx)/2} y={(sy+ty)/2-5} fontSize="10" textAnchor="middle" fill="#486758">{roles[edge.role]||roles.unknown}</text>}</g>;})}
              {layout.nodes.map(node=>{const isRoot=node.is_root;const hasParents=parentIds.has(String(node.id));const fill=isRoot?"#155e4a":node.kind==="cross"?"#fff2dc":node.kind==="text"?"#f0f3f2":node.kind==="catalog"?"#b87935":"#e7f4ed";return <g key={node.id} transform={`translate(${node.x},${node.y})`} role="button" tabIndex={0} aria-label={`${node.name}，${node.kind?.startsWith("catalog")?"打开":"查看资料"}`} onClick={()=>nodeAction(node)} onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();drag.current=null;nodeAction(node);}}} style={{cursor:"pointer"}}><title>{node.name}{node.disconnected?"（未连接来源节点）":""}</title>{node.circle?<circle cx="105" cy="30" r={isRoot?54:42} fill={fill} stroke="#fff" strokeWidth="3"/>:<rect width={NODE_WIDTH} height={NODE_HEIGHT} rx="9" fill={fill} stroke={node.disconnected?"#b98943":isRoot?"#155e4a":"#a7c8b5"} strokeDasharray={!node.variety_id&&node.kind!=="catalog-variety"&&!isRoot?"4 3":undefined}/>}
                <text x={node.circle?105:12} y={node.circle?31:25} textAnchor={node.circle?"middle":undefined} fontSize={node.circle?14:13} fontWeight="600" fill={isRoot||node.circle?"#fff":"#214d3a"}>{short(node.name)}</text><text x={node.circle?105:12} y={node.circle?102:45} textAnchor={node.circle?"middle":undefined} fontSize="11" fill="#517866">{node.subtitle||(node.variety_id?"可查看关联品种档案":`${kinds[node.kind]||kinds.unknown} · 未关联档案`)}</text>
                {graph&&hasParents&&<g role="button" tabIndex={0} aria-label={`${expanded.has(String(node.id))?"收起":"展开"}${node.name}的亲本`} aria-expanded={expanded.has(String(node.id))} onClick={e=>{e.stopPropagation();if(!drag.current?.moved)toggleNode(node);}} onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();e.stopPropagation();toggleNode(node);}}}><circle cx="197" cy="16" r="11" fill="#fff" stroke="#81ad96"/><text x="197" y="20" textAnchor="middle" fontSize="17" fill="#24654c">{expanded.has(String(node.id))?"−":"+"}</text></g>}
              </g>;})}
            </g>
          </svg><div className="pg-zoom"><button title="放大" aria-label="放大图谱" onClick={()=>setView(v=>zoomView(v,1.25,{x:viewport.width/2,y:viewport.height/2}))}><Plus size={17}/></button><span>{Math.round(view.scale*100)}%</span><button title="缩小" aria-label="缩小图谱" onClick={()=>setView(v=>zoomView(v,.8,{x:viewport.width/2,y:viewport.height/2}))}><Minus size={17}/></button><button title="适应窗口" aria-label="图谱适应窗口" onClick={()=>setView(fitView(layout,viewport))}><Maximize size={17}/></button><button title="导出当前可见图谱" aria-label="导出当前可见图谱" onClick={downloadGraph}><Download size={17}/></button></div>
          <div className="pg-help">滚轮缩放 · 拖动平移 · 点击节点查看资料{graph?" · 节点 + / − 展开收起":" · 目录连线仅表示分类包含"}</div>
        </div>
        <div className="pg-legend">{graph?<><span>绿色：品种 / 材料</span><span>浅黄：杂交组合</span><span>灰色：来源文字</span><span>虚线边框：未关联档案</span><span>箭头：当前节点 → 亲本来源</span><span>系谱层级不是 F1 / F2 世代</span>{graph.snapshot?.crawled_at&&<span>系谱采集：{new Date(graph.snapshot.crawled_at).toLocaleDateString("zh-CN")}</span>}</>:<span>先选择分类或左侧品种。分类目录不表示遗传关系；每页浏览30个品种，不一次绘制全部品种。</span>}</div>
      </div>
    </div>
    <dialog ref={dialog} className="pg-detail" aria-labelledby={titleId} onClose={()=>{requests.current.detail?.abort();backdropPressed.current=false;}} onPointerDown={e=>{backdropPressed.current=outside(e);}} onClick={e=>{if(backdropPressed.current&&outside(e))dialog.current.close();backdropPressed.current=false;}}><header><h2 id={titleId}>{selectedNode?.name||"节点资料"}</h2><button aria-label="关闭节点详情" onClick={()=>dialog.current.close()}><X size={18}/></button></header><div className="pg-detail-body" ref={detailBody} tabIndex={0} role="region" aria-label="节点详情，可滚动">{selectedNode&&<><p>{kinds[selectedNode.kind]||"来源节点"} · {selectedNode.variety_id?"已有关联品种档案":"未关联完整品种档案"}</p>{selectedNode.disconnected&&<p className="pg-warning">此节点未连接到当前品种，不认定为其祖先。</p>}{!selectedNode.variety_id&&<p>来源系谱记录了这个名称，但没有可确认关联的品种档案。不会根据同名自动拼接性状或亲缘关系。</p>}{selectedNode.was_expandable&&!selectedNode.expansion_succeeded&&<p className="pg-warning">原页面允许展开该节点，但采集时未成功；不能解释为没有更早祖先。</p>}{safeLink(selectedNode.source_url||graph?.snapshot?.source_url)&&<a href={safeLink(selectedNode.source_url||graph?.snapshot?.source_url)} target="_blank" rel="noreferrer">查看原始系谱来源</a>}</>}{detailBusy&&<p role="status">正在读取品种档案…</p>}{detailError&&<p className="pg-error" role="alert">{detailError}</p>}{detail&&<><h3>关联品种档案</h3><dl><dt>名称</dt><dd>{detail.record.name}</dd><dt>品种类型</dt><dd>{detail.record.variety_type||"未记载"}</dd><dt>亲本来源</dt><dd>{detail.record.parentage||"未记载"}</dd><dt>育种单位</dt><dd>{detail.record.breeder||"未记载"}</dd></dl><div className="assistant-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{table:({children})=><div className="pg-table-scroll"><table>{children}</table></div>,a:({href,children})=><a href={href} target="_blank" rel="noreferrer">{children}</a>}}>{detail.content}</ReactMarkdown></div><details><summary>查看原文与来源</summary>{detail.evidence?.map((e,i)=><section key={i}><h4>{e.title}</h4>{safeLink(e.url)&&<a href={safeLink(e.url)} target="_blank" rel="noreferrer">查看原始品种网页</a>}{e.excerpts?.map((excerpt,index)=><div key={index}><strong>{excerpt.title}</strong><p>{excerpt.text}</p></div>)}</section>)}</details><button className="primary-button" onClick={()=>{dialog.current.close();selectVariety({id:detail.record.id,name:detail.record.name});}}>查看该品种自己的系谱</button></>}</div><footer><span>按 Esc 或点击窗口外关闭</span><button className="secondary-button" onClick={()=>dialog.current.close()}>关闭窗口</button></footer></dialog>
  </section>;
}
