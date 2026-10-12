"""Freeze genuinely distinct model inputs and capability/operation graphs."""
import ast
import hashlib
import itertools
import json
import random
from pathlib import Path

from build_business_corpus import add_caps
from build_related_corpus import case
from som_core import composed as c
from som_core.v1 import V1Error, load_blocks, assemble, resolve_prompt_refs

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "som-core/data/composed"


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def graph(p):
    return c.canonical({"capabilities":{k:v for k,v in p["capabilities"].items() if k not in {"value_field","group_field"}}, "queries":[c.related.query_kind(p["resources"][r]) for r in ("parent","child")], "operations":sorted((o["scope"],o["action"]) for o in p["operations"])})


def main():
    OUT.mkdir(exist_ok=False)
    library = load_blocks(ROOT / "som-code-python/data/blocks/composed")
    seen_inputs, seen_structures, seen_graphs = set(), set(), set()
    groups = {"regression":[], "heldout":[], "dev":[], "train":[]}
    meta = {k:[] for k in groups}
    def add(p, name, app):
        c.validate(p)
        feature, structure, g = c.features(p), c.signature(p), graph(p)
        if name != "regression" and (feature in seen_inputs or structure in seen_structures or g in seen_graphs): return False
        d = c.reference(p,library); c.validate_plan(p,d)
        files = assemble(resolve_prompt_refs(p,d),library)
        for path, code in files.items():
            if path.endswith('.py'): ast.parse(code)
        assert c.decode(c.encode(d)) == d
        seen_inputs.add(feature); seen_structures.add(structure); seen_graphs.add(g)
        groups[name].append({"prompt":p,"dsl":d})
        meta[name].append({"app":app,"input_sha256":hashlib.sha256(feature.encode()).hexdigest(), "structure_sha256":hashlib.sha256(structure.encode()).hexdigest(), "graph_sha256":hashlib.sha256(g.encode()).hexdigest(), "operations":len(p['operations']), "output_blocks":sum(len(f['blocks']) for f in d['files']), "source_bytes":sum(len(x.encode()) for x in files.values())})
        return True
    for line in (ROOT/'som-core/data/business-service/requirements.jsonl').read_text().splitlines():
        p=c.from_legacy(json.loads(line)['prompt']); add(p,'regression',p['package'])
    combinations=list(itertools.product(('postgresql','mongodb'),('jwt','firebase','google_oauth'),('none','s3','gcs'),('json_export','csv','gzip_export','decimal_summary','statistics')))
    serial=400000
    # Sizes bound training work; app acceptance is based on coverage, not quotas.
    for name, count in [('heldout',120),('dev',120),('train',1200)]:
        while len(groups[name])<count:
            serial+=1; rng=random.Random(serial+17)
            app = serial % (10 if name=='heldout' else 8)
            p=case(app,serial%5,serial,training=True)
            if not add_caps(p,*combinations[serial%len(combinations)]): continue
            try:
                p=c.from_legacy(p)
                if serial%8:
                    n=10 if serial%4==0 else rng.randint(1,10)
                    p['operations']=rng.sample(p['operations'],n)
                add(p,name,app)
            except V1Error:
                continue
    for name,rows in groups.items():
        (OUT/(name+'.jsonl')).write_text(''.join(c.canonical(r)+'\n' for r in rows))
    atoms=set(); pairs=set()
    for row in groups['train']:
        p=row['prompt']; choices=[f'{k}:{p["capabilities"][k]}' for k in ('database','auth','storage','workflow')]
        atoms.update(f'{o["scope"]}:{o["action"]}' for o in p['operations'])
        pairs.update(itertools.combinations(choices,2))
    expected_pairs=set()
    for combo in combinations:
        expected_pairs.update(itertools.combinations([f'{k}:{v}' for k,v in zip(('database','auth','storage','workflow'),combo)],2))
    assert pairs==expected_pairs, expected_pairs-pairs
    assert len(atoms)==11
    checks={}
    for a,b in itertools.combinations(groups,2):
        # Existing regression variants may share a graph with one another.
        for key in ('input_sha256','structure_sha256','graph_sha256'):
            overlap={m[key] for m in meta[a]} & {m[key] for m in meta[b]}
            checks[f'{a}/{b}/{key}']=len(overlap)
            assert not overlap
    manifest={'status':'frozen-before-training','seed':17,'counts':{k:{'services':len(v),'apps':len({m['app'] for m in meta[k]}),'operations':sum(m['operations'] for m in meta[k]),'unique_inputs':len({m['input_sha256'] for m in meta[k]}),'unique_structures':len({m['structure_sha256'] for m in meta[k]}),'unique_graphs':len({m['graph_sha256'] for m in meta[k]})} for k,v in groups.items()},'split_overlap':checks,'training_atoms':sorted(atoms),'training_capability_pairs':len(pairs),'files':{p.name:digest(p) for p in OUT.glob('*.jsonl')},'contract_sha256':c.contract_hash(),'catalog_sha256':digest(c.CATALOG),'blocks_sha256':digest(ROOT/'som-code-python/data/blocks/composed/blocks.json'),'cases':meta}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:v for k,v in manifest.items() if k not in {'cases','files'}}))


if __name__=='__main__': main()
