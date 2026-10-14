"""Source-proven direct fragments. Never merge by name or infer parent roles."""
import hashlib
from collections import defaultdict,deque
from sqlalchemy import text


def material_key(source_id,name):
    if not source_id or not str(source_id).isdigit() or int(source_id)<=0:
        raise ValueError('Material identity requires a positive source ID')
    return 'v:'+str(source_id)+':'+hashlib.sha256(name.encode()).hexdigest()[:24]


def fragment(records,index=0):
    root=records[index]
    if not root['source_id'] or root['pending_expansion']:
        return None
    owner=material_key(root['source_id'],root['name'])
    children=defaultdict(list)
    for i,node in enumerate(records):
        if node['parent'] is not None:children[node['parent']].append(i)
    nodes=[];edges=[];references=[]
    def visit(i,parent=None,position=0):
        node=records[i]
        if i!=index and node['source_id']:
            key=material_key(node['source_id'],node['name'])
            references.append(key)
        else:
            if node['pending_expansion']:
                raise ValueError('Unnamed intermediate branch is not expanded')
            key=owner if i==index else 'x:'+hashlib.sha256(f'{owner}|{len(nodes)}|{node["name"]}'.encode()).hexdigest()[:40]
        nodes.append(dict(key=key,name=node['name'],source_id=node['source_id'],source_url=node['url']))
        if parent is not None:edges.append(dict(child=parent,parent=key,position=position))
        if i==index or not node['source_id']:
            for pos,child in enumerate(children[i]):visit(child,key,pos)
    try:visit(index)
    except ValueError:return None
    return dict(root=owner,nodes=nodes,edges=edges,references=list(dict.fromkeys(references)))


def semantic_signature(value):
    # URLs and disclosure/expand buttons are not biological relationships.
    payload=dict(nodes=[{k:n[k] for k in ('key','name','source_id')} for n in value['nodes']],edges=value['edges'])
    import json
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def assemble(root,facts,limit=5000):
    nodes={};edges=[];missing=set();conflicts=set();expanded=set();queue=deque([root])
    while queue:
        key=queue.popleft()
        if key in expanded:continue
        expanded.add(key)
        fact=facts.get(key)
        if not fact:
            missing.add(key);continue
        if fact.get('status')!='verified_direct':
            conflicts.add(key);continue
        value=fact['fragment']
        for item in value['nodes']:
            node=dict(item)
            node.update(id=node['key'],kind='variety' if node['source_id'] else 'cross' if '/' in node['name'] else 'text',
                        variety_id=None,is_root=node['key']==root,was_expandable=bool(node['source_id']),expansion_succeeded=True,recorded_depth=0)
            if not node['name'].strip() or node['name'].strip() in ('0','?','？'):
                node['raw_name']=node['name'];node['name']='亲本名称待核实';node['kind']='unknown';conflicts.add(node['key'])
            nodes.setdefault(node['key'],node)
        if len(nodes)>limit:raise ValueError('Direct graph exceeds safe display bound; not silently truncated')
        for edge in value['edges']:
            edges.append(dict(id=f'direct:{len(edges)}',child_id=edge['child'],parent_id=edge['parent'],role='parent',display_order=edge['position']))
        queue.extend(value['references'])
    adjacency=defaultdict(list)
    for i,edge in enumerate(edges):adjacency[edge['child_id']].append((edge['parent_id'],i))
    # Identify closing edges without infinite ancestry expansion. Keep all raw evidence in facts.
    blocked=set();color={};active=[];positions={};cycle_nodes=set()
    if root in nodes:
        stack=[(root,iter(adjacency[root]))];color[root]=1;positions[root]=0;active.append(root)
        while stack:
            key,iterator=stack[-1]
            item=next(iterator,None)
            if item is None:
                color[key]=2;positions.pop(key,None);active.pop();stack.pop();continue
            parent,edge_index=item
            if color.get(parent)==1:
                blocked.add(edge_index);cycle_nodes.update(active[positions[parent]:]);continue
            if color.get(parent,0)==0:
                color[parent]=1;positions[parent]=len(active);active.append(parent);stack.append((parent,iter(adjacency[parent])))
    if cycle_nodes:
        # Do not present any internal relationship of a contradictory source cycle as valid.
        blocked.update(i for i,e in enumerate(edges) if e['child_id'] in cycle_nodes and e['parent_id'] in cycle_nodes)
    visible=[e for i,e in enumerate(edges) if i not in blocked]
    warnings=[]
    if missing:warnings.append(f'尚有 {len(missing)} 个材料的直接亲本待采集，当前不能称为完整祖先系谱。')
    if conflicts:warnings.append(f'存在 {len(conflicts)} 个来源冲突或名称待核实节点，未强行续接。')
    if blocked:warnings.append(f'原站存在祖先回环，{len(blocked)} 条回环关系未作为有效亲缘关系展示；原始证据已保留。')
    for key in missing|conflicts:
        if key in nodes:nodes[key]['expansion_succeeded']=False
    for key in cycle_nodes:
        if key in nodes:nodes[key]['source_status']='source_cycle'
    depths={root:0};pending=deque([root]);visible_adjacency=defaultdict(list)
    for e in visible:visible_adjacency[e['child_id']].append(e['parent_id'])
    while pending:
        key=pending.popleft()
        for parent in visible_adjacency[key]:
            if parent not in depths:depths[parent]=depths[key]+1;pending.append(parent)
    for key,node in nodes.items():node['recorded_depth']=depths.get(key,0)
    return dict(nodes=list(nodes.values()),edges=visible,root_ids=[root] if root in nodes else [],
                warnings=warnings,fully_expanded=not(missing or conflicts or blocked),
                missing_count=len(missing),conflict_count=len(conflicts),blocked_relation_count=len(blocked))


def load_graph(session,source_id):
    job=session.execute(text('SELECT root_key,collected_at FROM agent_query.ricedata__rice_pedigree_direct_job WHERE source_id=:id AND status=\'complete\''),{'id':str(source_id)}).mappings().first()
    if not job:return None
    records=session.execute(text('''WITH RECURSIVE dependencies(key) AS (
        SELECT CAST(:root AS text) UNION
        SELECT jsonb_array_elements_text(f.fragment->'references')
        FROM dependencies d JOIN agent_query.ricedata__rice_pedigree_direct_fact f ON f.fact_key=d.key
        WHERE f.status='verified_direct'
    ) SELECT f.fact_key,f.fragment,f.status FROM agent_query.ricedata__rice_pedigree_direct_fact f
    JOIN dependencies d ON d.key=f.fact_key LIMIT 5001'''),{'root':job['root_key']}).mappings().all()
    if len(records)>5000:raise ValueError('Source dependencies exceed safe graph bound')
    graph=assemble(job['root_key'],{r['fact_key']:dict(r) for r in records})
    graph['collected_at']=job['collected_at']
    return graph
