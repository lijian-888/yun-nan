"""Conservative, source-evidenced alias repair and occurrence comparison."""
import copy
import hashlib
import re
from collections import Counter, defaultdict
from app.pedigree_direct import material_key


def explicit_alias(canonical, alias):
    """Only exact base labels or explicitly listed parenthetical synonyms."""
    if canonical == alias:
        return True
    match = re.fullmatch(r'([^（）()]+)[（(]([^（）()]+)[）)]', canonical)
    if not match:
        return False
    return alias == match[1].strip() or alias in {
        part.strip() for part in re.split(r'[;；、,，]', match[2]) if part.strip()
    }

def alias_fragment(value, source_id, alias):
    root = value['nodes'][0]
    if str(root['source_id']) != str(source_id) or not explicit_alias(root['name'], alias):
        return None
    if any(n.get('source_limited') for n in value['nodes']):
        return None
    result = copy.deepcopy(value)
    key = material_key(source_id, alias)
    remap = {value['root']: key}
    # Intermediate nodes remain contextual; don't merge them across aliases.
    for n in value['nodes'][1:]:
        if not n['source_id']:
            remap[n['key']] = 'x:' + hashlib.sha256(f'{key}|{n["key"]}'.encode()).hexdigest()[:40]
    result['root'] = key
    for n in result['nodes']:
        n['key'] = remap.get(n['key'], n['key'])
    result['nodes'][0]['name'] = alias
    for e in result['edges']:
        for field in ('child', 'parent'):
            e[field] = remap.get(e[field], e[field])
    result['references'] = [remap.get(k, k) for k in result['references']]
    return result


def graph_paths(graph, max_occurrences=30000):
    names = {n['id']: n['name'] for n in graph['nodes']}
    adjacency = defaultdict(list)
    for e in graph['edges']:
        adjacency[e['child_id']].append(e['parent_id'])
    paths = Counter()
    stack = [(k, (), frozenset()) for k in graph['root_ids']]
    visits = 0
    while stack:
        key, path, seen = stack.pop()
        if key in seen:
            raise ValueError('Source relationship cycle')
        visits += 1
        if visits > max_occurrences:
            raise ValueError('Occurrence comparison bound exceeded; not silently truncated')
        path = path + (names[key],)
        paths[path] += 1
        stack.extend((p, path, seen | {key}) for p in adjacency[key])
    return paths


def validate_source_records(records):
    for i, n in enumerate(records):
        if n['pending_expansion']:
            raise ValueError('Source collection incomplete')
        if not n['name'].strip() or n['name'].strip() in ('0', '?', '？'):
            raise ValueError('Source parent name unknown; not complete')
        parent = n['parent']
        seen = {str(n['source_id'])} if n['source_id'] else set()
        while parent is not None:
            ancestor = records[parent]
            identity = str(ancestor['source_id'])
            if ancestor['source_id'] and identity in seen:
                raise ValueError('Source ancestor identity repeats on same path; review cycle')
            if ancestor['source_id']:
                seen.add(identity)
            parent = ancestor['parent']
