import { useEffect, useLayoutEffect, useId, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Search, GitBranch, LoaderCircle, X, Plus, Minus, Maximize, Download, ArrowLeft } from "lucide-react";
import { request } from "./api";
import { visiblePedigree, layoutFanPedigree, catalogCircleLayout, expandLevels, fitView, zoomView, fanLabel } from "./pedigreeLayout";
import PedigreeFan from './PedigreeFan';
import "./PedigreeWorkspace.css";

const kinds={variety:"品种 / 材料",cross:"杂交组合",text:"来源文字节点",unknown:"类型未注明"};
const number=value=>Number(value||0).toLocaleString();
const safeLink=value=>/^https?:\/\//i.test(value||"")?value:undefined;
const emptyLayout={nodes:[],edges:[],width:380,height:260,labels:[]};

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
  const [highlight,setHighlight]=useState(null);
  const [inspection,setInspection]=useState('');
  const [selectedNode,setSelectedNode]=useState(null);
  const [detail,setDetail]=useState(null);
  const [detailBusy,setDetailBusy]=useState(false);
  const [detailError,setDetailError]=useState("");
  const requests=useRef({});
  const stage=useRef(null);
  const svg=useRef(null);
  const scroll=useRef(null);
  const drag=useRef(null);
  const dialog=useRef(null);
  const detailBody=useRef(null);
  const backdropPressed=useRef(false);
  const titleId=useId();
  const visible=useMemo(()=>visiblePedigree(graph,expanded,showAll),[graph,expanded,showAll]);
  const layout=useMemo(()=>graph?layoutFanPedigree(graph,visible):selectedVariety?emptyLayout:catalogCircleLayout(catalog,group,list?.records||[]),[graph,visible,catalog,group,list,selectedVariety]);
  const parentIds=useMemo(()=>new Set((graph?.edges||[]).map(e=>String(e.child_id))),[graph]);
  const compactNames=useMemo(()=>graph?layout.nodes.flatMap((node,index)=>{
    const label=fanLabel(node,view.scale);
    return !label.lines.length||label.truncated?[{node,index:index+1,numbered:!label.lines.length}]:[];
  }):[],[graph,layout,view.scale]);

  function controller(kind) { requests.current[kind]?.abort(); const next=new AbortController(); requests.current[kind]=next; return next; }
  async function browse(next={keyword,group,only:onlyPedigree,page:1}) {
    const control=controller("list"); setListBusy(true); setList(null); setError("");
    try {
      const params=new URLSearchParams({q:next.keyword||"",group:next.group||"",only_pedigree:String(next.only),page:String(next.page),page_size:"12"});
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
  useLayoutEffect(()=>{
    if(!graph){setView(fitView(layout,viewport));return;}
    // Whole-fan overview first. Sector labels compensate for zoom and omit
    // text only where it cannot fit; full names remain in inspection/details.
    setView({scale:Math.min(1,fitView(layout,viewport).scale),x:0,y:0});setHighlight(null);setInspection('');
    const frame=requestAnimationFrame(()=>scroll.current?.scrollTo({left:0,top:0}));
    return ()=>cancelAnimationFrame(frame);
  },[layout,viewport,graph]);
  useEffect(()=>{
    const element=svg.current;
    const wheel=e=>{
      if(scroll.current?.dataset.tree==="true" && !e.ctrlKey && !e.metaKey)return;
      e.preventDefault(); const bounds=element.getBoundingClientRect();
      if(scroll.current?.dataset.tree==="true"){
        const factor=Math.exp(-Math.max(-100,Math.min(100,e.deltaY))*.003);
        setView(current=>({...current,scale:Math.max(.08,Math.min(3,current.scale*factor))}));
      }else setView(current=>zoomView(current,Math.exp(-Math.max(-100,Math.min(100,e.deltaY))*.003),{x:e.clientX-bounds.left,y:e.clientY-bounds.top}));
    };
    element.addEventListener("wheel",wheel,{passive:false}); return ()=>element.removeEventListener("wheel",wheel);
  },[]);

  async function selectVariety(record) {
    const control=controller("graph"); requests.current.detail?.abort(); dialog.current?.close();
    setSelectedVariety(record); setGraph(null);setGraphBusy(true);setError("");setShowAll(false);
    try {
      const response=await request(`/api/research/pedigree/varieties/${record.id}/graph`,{signal:control.signal});
      if(!control.signal.aborted){setGraph(response);setExpanded(expandLevels(response,2));}
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
    setExpanded(current=>{const next=new Set(current);next.has(node.sourceId)?next.delete(node.sourceId):next.add(node.sourceId);return next;});
  }
  function locateReference(node) {
    const target=layout.nodes.find(n=>n.id===node.referenceTo);
    if(!target)return;
    setHighlight(target.id);drag.current=null;
    scroll.current.scrollTo({left:Math.max(0,target.x*view.scale-viewport.width/2),top:Math.max(0,target.y*view.scale-viewport.height/2)});
    svg.current.querySelector(`[data-card-id="${target.id}"]`)?.focus({preventScroll:true});
  }
  function zoom(factor) {
    if(!graph){setView(v=>zoomView(v,factor,{x:viewport.width/2,y:viewport.height/2}));return;}
    const area=scroll.current,scale=Math.max(.08,Math.min(3,view.scale*factor));
    const left=(area.scrollLeft+viewport.width/2)*scale/view.scale-viewport.width/2;
    const top=(area.scrollTop+viewport.height/2)*scale/view.scale-viewport.height/2;
    setView({scale,x:0,y:0});requestAnimationFrame(()=>area.scrollTo({left:Math.max(0,left),top:Math.max(0,top)}));
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
  function nodeAction(node,keyboard=false) {
    if(keyboard)drag.current=null;
    if(drag.current?.moved)return;
    if(node.id==="catalog")return chooseGroup("");
    if(node.group)return chooseGroup(node.group);
    if(node.kind==="catalog-variety")return selectVariety({id:node.variety_id,name:node.name});
    if(node.referenceTo)return locateReference(node);
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
    <div className="pg-layout"><aside className="pg-list"><div className="pg-list-heading"><strong>{list?`匹配 ${number(list.total)} 个品种`:"品种目录"}</strong>{listBusy&&<LoaderCircle className="spin" size={15}/>}</div><p>选择品种查看系谱。目录分组不是亲缘关系。</p><div className="pg-list-records">{list?.records.map(record=><button type="button" key={record.id} aria-pressed={selectedVariety?.id===record.id} onClick={()=>selectVariety(record)}><strong>{record.name}</strong><span>{record.variety_type||"类型未记载"} · {record.edge_count?`${record.node_count}个已存节点` : (record.pedigree_status==="pending_verification"?"系谱待原站校验":"原站未展示亲本")}</span></button>)}{!listBusy&&list&&!list.records.length&&<p>未找到符合条件的品种，可减少条件或改用别名检索。</p>}</div><div className="pg-list-pages"><button disabled={!list||list.page<=1||listBusy} onClick={()=>browse({keyword:list.keyword,group:list.group,only:list.only,page:list.page-1})}>上一页</button><span>{list?.page||1} / {Math.max(1,Math.ceil((list?.total||0)/(list?.page_size||12)))}</span><button disabled={!list?.has_more||listBusy} onClick={()=>browse({keyword:list.keyword,group:list.group,only:list.only,page:list.page+1})}>下一页</button></div></aside>
      <div className="pg-canvas-panel"><div className="pg-toolbar"><div><strong>{selectedVariety?.name||"品种库概览"}</strong><span>{graph?`扇形祖先谱 · ${visible.nodes.length} / ${graph.node_count} 个来源节点，${visible.edges.length} 条来源关系；${layout.referenceCount} 个引用节点（不重复计数）` : "点击四大分类，环绕浏览品种；点击品种查看扇形祖先谱"}</span></div><div className="pg-actions">{selectedVariety&&<button className="secondary-button" onClick={()=>{drag.current=null;openDetail({name:selectedVariety.name,kind:"variety",variety_id:selectedVariety.id,source_url:graph?.variety.source_url});}}>品种档案</button>}{graph&&<><button className="primary-button" onClick={()=>{setExpanded(new Set(graph.nodes.map(n=>String(n.id))));setShowAll(true);}}>一键展开全部</button><button className="secondary-button" onClick={()=>{setExpanded(expandLevels(graph,3));setShowAll(false);}}>展开3层</button><button className="secondary-button" onClick={()=>{setExpanded(new Set());setShowAll(false);}}>收起全部</button></>}<button className="secondary-button" onClick={()=>directory("")} disabled={!selectedVariety&&!group}><ArrowLeft size={14}/>目录</button></div></div>
        {graph?.warnings.length>0&&<div className="pg-warnings">{graph.warnings.map((warning,index)=><p key={index}>{warning}</p>)}</div>}
        <div className="pg-stage" ref={stage} aria-busy={graphBusy}>
          {graphBusy&&<div className="pg-loading" role="status"><LoaderCircle className="spin" size={22}/>正在读取服务器系谱…</div>}
          {!graph&&selectedVariety&&!graphBusy&&<div className="pg-loading" role="status"><span>系谱未读取成功，不展示推测关系。</span><button className="secondary-button" onClick={()=>selectVariety(selectedVariety)}>重试</button></div>}
          <div ref={scroll} className={`pg-scroll${graph?" pg-scroll-tree":""}`} data-tree={Boolean(graph)} tabIndex={graph?0:undefined} aria-label={graph?"系谱画布，可横向和纵向滚动":undefined}>
          <svg ref={svg} width={graph?Math.max(viewport.width,layout.width*view.scale):"100%"} height={graph?Math.max(viewport.height,layout.height*view.scale):"100%"} viewBox={graph?undefined:`0 0 ${viewport.width} ${viewport.height}`} role="group" aria-label={graph?`${graph.variety.name}的亲本祖先系谱图`:"品种目录分类图（不是亲缘关系）"} onPointerDown={e=>{if(e.button!==0)return;drag.current={x:e.clientX,y:e.clientY,moved:false};if(e.target===e.currentTarget)e.currentTarget.setPointerCapture(e.pointerId);}} onPointerMove={e=>{if(!drag.current||!e.buttons)return;const dx=e.clientX-drag.current.x,dy=e.clientY-drag.current.y;if(Math.abs(dx)+Math.abs(dy)>2)drag.current.moved=true;if(drag.current.moved&&!e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.setPointerCapture(e.pointerId);if(drag.current.moved){if(graph){scroll.current.scrollLeft-=dx;scroll.current.scrollTop-=dy;}else setView(v=>({...v,x:v.x+dx,y:v.y+dy}));}drag.current.x=e.clientX;drag.current.y=e.clientY;}} onPointerUp={e=>{if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);}} onPointerCancel={()=>{drag.current=null;}}>
            <g className="pg-world" transform={graph?`scale(${view.scale})`:`translate(${view.x},${view.y}) scale(${view.scale})`}>
              {graph?<PedigreeFan layout={layout} scale={view.scale} expanded={expanded} parentIds={parentIds} highlight={highlight} onNode={nodeAction} onToggle={node=>{if(!drag.current?.moved)toggleNode(node);}} onInspect={setInspection}/>:<>
                {layout.edges.map(edge=><path key={edge.id} d={`M${edge.from.x},${edge.from.y} L${edge.to.x},${edge.to.y}`} fill="none" stroke="#bdd0c3" strokeWidth="1.3"/>)}
                {layout.nodes.map(node=><g key={node.id} transform={`translate(${node.x},${node.y})`} role="button" tabIndex={0} aria-label={`${node.name}，打开`} onClick={()=>nodeAction(node)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();nodeAction(node,true);}}} className="pg-catalog-node"><title>{node.name} · {node.subtitle}</title><circle r={node.radius} fill={node.is_root?'#195f4b':'#b77732'} stroke="#fff" strokeWidth="3"/>{node.kind==='catalog-variety'?<><text textAnchor="middle" y="5" fill="#fff" fontSize={Math.max(13,13/view.scale)}>{node.name.slice(0,2)}</text><text textAnchor="middle" y={node.radius+18/view.scale} fill="#345b47" fontSize={Math.max(12,12/view.scale)}>{node.name.length>8?node.name.slice(0,7)+'…':node.name}</text></>:<text textAnchor="middle" fill="#fff" fontSize={Math.max(13,13/view.scale)}>{(node.name.length>7?[node.name.slice(0,6),node.name.slice(6)]:[node.name]).map((line,i,lines)=><tspan key={i} x="0" y={(i-(lines.length-1)/2)*16/view.scale+5/view.scale}>{line}</tspan>)}</text>}<text textAnchor="middle" y={node.radius+(node.kind==='catalog-variety'?36:20)/view.scale} fill="#577866" fontSize={Math.max(11,11/view.scale)}>{node.subtitle}</text></g>)}
              </>}
            </g>
          </svg></div><div className="pg-zoom"><button title="放大" aria-label="放大图谱" onClick={()=>zoom(1.25)}><Plus size={17}/></button><button className="pg-scale-reset" title="恢复100%可读尺寸" aria-label="恢复100%可读尺寸" onClick={()=>zoom(1/view.scale)}>{Math.round(view.scale*100)}%</button><button title="缩小" aria-label="缩小图谱" onClick={()=>zoom(.8)}><Minus size={17}/></button><button title="适应窗口（概览）" aria-label="图谱适应窗口" onClick={()=>{setView(graph?{scale:fitView(layout,viewport).scale,x:0,y:0}:fitView(layout,viewport));scroll.current?.scrollTo({top:0,left:0});}}><Maximize size={17}/></button><button title="导出当前可见图谱" aria-label="导出当前可见图谱" onClick={downloadGraph}><Download size={17}/></button></div>
          <div className="pg-help">{graph?"内圈：亲本；外圈：更早祖先 · 放大查看密集区 · 紫色 ↗ 定位共同祖先":"滚轮缩放 · 拖动平移 · 目录连线仅表示分类包含"}</div>
        </div>
        {graph&&<div className="pg-inspection">{inspection||'悬停或聚焦扇区查看完整名称与关系；点击查看资料。'}</div>}
        {compactNames.length>0&&<div className="pg-compact-names"><strong>窄扇区完整名称</strong><p>图中数字对应下列名称；放大可直接查看扇区文字。</p><div>{compactNames.map(({node,index,numbered})=><button key={node.id} onClick={()=>nodeAction(node,true)}>{numbered?`${index}. `:''}{node.name||'名称未记载'}{node.referenceTo?' ↗':''}</button>)}</div></div>}
        <div className="pg-legend">{graph?<><span>浅绿：母本分支</span><span>浅黄：父本分支</span><span>灰蓝：关系未注明，不推断父母本</span><span>灰色：来源文字</span><span>紫色：共同祖先引用，不是新材料</span><span>扇区位置表示来源层级，面积不表示遗传贡献</span><span>祖先层级不是 F1 / F2 世代</span>{graph.snapshot?.crawled_at&&<span>系谱采集：{new Date(graph.snapshot.crawled_at).toLocaleDateString("zh-CN")}</span>}</>:<span>四大类环绕浏览；每页最多12个品种。分类目录不表示遗传关系，点击中心返回全部类别。</span>}</div>
      </div>
    </div>
    <dialog ref={dialog} className="pg-detail" aria-labelledby={titleId} onClose={()=>{requests.current.detail?.abort();backdropPressed.current=false;}} onPointerDown={e=>{backdropPressed.current=outside(e);}} onClick={e=>{if(backdropPressed.current&&outside(e))dialog.current.close();backdropPressed.current=false;}}><header><h2 id={titleId}>{selectedNode?.name||"节点资料"}</h2><button aria-label="关闭节点详情" onClick={()=>dialog.current.close()}><X size={18}/></button></header><div className="pg-detail-body" ref={detailBody} tabIndex={0} role="region" aria-label="节点详情，可滚动">{selectedNode&&<><p>{kinds[selectedNode.kind]||"来源节点"} · {selectedNode.variety_id?"已有关联品种档案":"未关联完整品种档案"}</p>{selectedNode.disconnected&&<p className="pg-warning">此节点未连接到当前品种，不认定为其祖先。</p>}{!selectedNode.variety_id&&<p>来源系谱记录了这个名称，但没有可确认关联的品种档案。不会根据同名自动拼接性状或亲缘关系。</p>}{selectedNode.was_expandable&&!selectedNode.expansion_succeeded&&<p className="pg-warning">原页面允许展开该节点，但采集时未成功；不能解释为没有更早祖先。</p>}{safeLink(selectedNode.source_url||graph?.snapshot?.source_url)&&<a href={safeLink(selectedNode.source_url||graph?.snapshot?.source_url)} target="_blank" rel="noreferrer">查看原始系谱来源</a>}</>}{detailBusy&&<p role="status">正在读取品种档案…</p>}{detailError&&<p className="pg-error" role="alert">{detailError}</p>}{detail&&<><h3>关联品种档案</h3><dl><dt>名称</dt><dd>{detail.record.name}</dd><dt>品种类型</dt><dd>{detail.record.variety_type||"未记载"}</dd><dt>亲本来源</dt><dd>{detail.record.parentage||"未记载"}</dd><dt>育种单位</dt><dd>{detail.record.breeder||"未记载"}</dd></dl><div className="assistant-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{table:({children})=><div className="pg-table-scroll"><table>{children}</table></div>,a:({href,children})=><a href={href} target="_blank" rel="noreferrer">{children}</a>}}>{detail.content}</ReactMarkdown></div><details><summary>查看原文与来源</summary>{detail.evidence?.map((e,i)=><section key={i}><h4>{e.title}</h4>{safeLink(e.url)&&<a href={safeLink(e.url)} target="_blank" rel="noreferrer">查看原始品种网页</a>}{e.excerpts?.map((excerpt,index)=><div key={index}><strong>{excerpt.title}</strong><p>{excerpt.text}</p></div>)}</section>)}</details><button className="primary-button" onClick={()=>{dialog.current.close();selectVariety({id:detail.record.id,name:detail.record.name});}}>查看该品种自己的系谱</button></>}</div><footer><span>按 Esc 或点击窗口外关闭</span><button className="secondary-button" onClick={()=>dialog.current.close()}>关闭窗口</button></footer></dialog>
  </section>;
}
