import { fanLabel } from './pedigreeLayout';

const roles={female:'♀ 母本',male:'♂ 父本',parent:'亲本 / 来源',unknown:'关系未注明'};
export default function PedigreeFan({layout,scale,expanded,parentIds,highlight,onNode,onToggle,onInspect}) {
  return <>
    {layout.labels.map((label,index)=><text key={index} x={label.x} y={label.y} fill="#59796b" fontSize={Math.max(12,12/scale)}>{label.label}</text>)}
    {layout.nodes.map((node,index)=>{
      const labels=fanLabel(node,scale),isRoot=node.depth===0,canExpand=parentIds.has(node.sourceId)&&!node.referenceTo;
      const fill=node.cycle?'#ffebe2':node.referenceTo?'#eee8f7':isRoot?'#195f4b':node.kind==='text'?'#eef2ef':node.lineage==='female'?'#e4f0e8':node.lineage==='male'?'#f7e7cf':'#e8edf0';
      const description=`${node.name} · ${isRoot?node.disconnected?'未连接来源节点':'当前品种':roles[node.relation]||roles.unknown} · 祖先第${node.depth}层${node.referenceTo?node.cycle?' · 循环异常引用':' · 共同祖先引用':''}`;
      const degrees=node.angle*180/Math.PI;
      const angle=labels.radial?(degrees<-90?degrees+180:degrees):degrees+90;
      const size=labels.font,stroke=highlight===node.id?'#7553b4':node.cycle?'#c27755':'#fff';
      function keyboard(event,action){if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();action();}}
      return <g key={node.id} data-card-id={node.id} data-source-id={node.sourceId} data-reference-to={node.referenceTo} data-edge-id={node.edgeId} className="pg-fan-node" role="button" tabIndex={0} aria-label={`${node.name}，${node.referenceTo?'定位已展开位置':'查看资料'}`} onClick={()=>onNode(node)} onKeyDown={e=>keyboard(e,()=>onNode(node,true))} onPointerEnter={()=>onInspect(description)} onFocus={()=>onInspect(description)}>
        <title>{description}{canExpand?'；使用展开按钮追溯更早祖先':''}</title>
        {isRoot?<circle cx={node.x} cy={node.y} r={node.outer} fill={fill} stroke={stroke} strokeWidth={2/scale}/>:<path d={node.path} fill={fill} stroke={stroke} strokeWidth={Math.min(3,2/scale)} strokeLinejoin="round"/>}
        {labels.lines.length>0&&<g transform={`translate(${node.x},${node.y})${isRoot?'':` rotate(${angle})`}`} pointerEvents="none"><text textAnchor="middle" fill={isRoot?'#fff':node.referenceTo?'#725698':'#254e3c'} fontSize={size} fontWeight="500">{labels.lines.map((line,index)=><tspan key={index} x="0" y={(index-(labels.lines.length-1)/2)*size*1.3+size*.35}>{line}</tspan>)}</text></g>}
        {!labels.lines.length&&<text x={node.x} y={node.y+4/scale} textAnchor="middle" fontSize={11/scale} fill={isRoot?'#fff':'#254e3c'} pointerEvents="none">{index+1}</text>}
        {node.referenceTo&&node.depth>0&&node.outer*(node.end-node.start)*scale>20&&<text x={node.cx+(node.outer-13)*Math.cos(node.angle)} y={node.cy+(node.outer-13)*Math.sin(node.angle)} fill="#725698" textAnchor="middle" fontSize={Math.max(12,12/scale)} pointerEvents="none">↗</text>}
        {canExpand&&((isRoot&&node.outer*scale>25)||(!isRoot&&scale>=.55&&node.outer*(node.end-node.start)*scale>38))&&<g role="button" tabIndex={0} aria-label={`${expanded.has(node.sourceId)?'收起':'展开'}${node.name}的亲本`} aria-expanded={expanded.has(node.sourceId)} onClick={e=>{e.stopPropagation();onToggle(node);}} onKeyDown={e=>keyboard(e,()=>onToggle(node,true))}>
          <circle cx={isRoot?node.x:node.cx+(node.outer-12)*Math.cos(node.angle)} cy={isRoot?node.y+node.outer-12:node.cy+(node.outer-12)*Math.sin(node.angle)} r={Math.max(9,9/scale)} fill="#fff" stroke="#8daa99" strokeWidth={1/scale}/>
          <text x={isRoot?node.x:node.cx+(node.outer-12)*Math.cos(node.angle)} y={(isRoot?node.y+node.outer-12:node.cy+(node.outer-12)*Math.sin(node.angle))+4/scale} textAnchor="middle" fill="#32654e" fontSize={Math.max(14,14/scale)} pointerEvents="none">{expanded.has(node.sourceId)?'−':'+'}</text>
        </g>}
      </g>;
    })}
  </>;
}
