"""From-scratch full structural sequence training. No test-driven selection."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import time

from . import composed as c
from .v1 import load_blocks


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    import mlx.core as mx
    import mlx.optimizers as optim
    from mlx import nn
    from mlx.utils import tree_flatten
    from mlx_lm.models.gpt2 import Model, ModelArgs
    from mlx_lm.models.cache import make_prompt_cache
    from tokenizers import Tokenizer, models, pre_tokenizers, trainers, decoders, Regex

    if args.dev is None or args.legacy is None:
        raise ValueError("block_plan needs --dev and --legacy")
    if args.output.exists():
        raise ValueError("new training output must not exist")
    library = load_blocks(args.blocks.parent)
    c.validate_library(library)
    def read(path):
        rows=[]
        for line in path.read_text().splitlines():
            row=json.loads(line); c.validate_plan(row['prompt'],row['dsl'])
            rows.append((c.features(row['prompt']), c.encode(row['dsl'])))
        if not rows: raise ValueError("empty corpus")
        return rows
    train, dev = read(args.data), read(args.dev)
    symbols=sorted({symbol for a,b in train for symbol in re.findall(r'<[^<>\s]+>',a+b)})
    tok=Tokenizer(models.WordLevel(unk_token='<unk>'))
    tok.pre_tokenizer=pre_tokenizers.Split(Regex(r'<[^<>\s]+>'),behavior='isolated')
    tok.decoder=decoders.Fuse()
    tok.train_from_iterator((a+b for a,b in train),trainers.WordLevelTrainer(vocab_size=1024,special_tokens=['<unk>',*symbols]))
    # WordLevelTrainer can leave sparse IDs when special symbols also occur in
    # its input. Export one dense model vocabulary, with no AddedToken remapping.
    vocabulary=['<unk>',*sorted(set(tok.get_vocab())-{'<unk>'})]
    vocabulary += [f'<unused:{i}>' for i in range(1024-len(vocabulary))]
    tok=Tokenizer(models.WordLevel(vocab={s:i for i,s in enumerate(vocabulary)},unk_token='<unk>'))
    tok.pre_tokenizer=pre_tokenizers.Split(Regex(r'<[^<>\s]+>'),behavior='isolated')
    tok.decoder=decoders.Fuse()
    assert Tokenizer.from_str(tok.to_str()).get_vocab()==tok.get_vocab()
    assert tok.get_vocab_size()==1024
    examples=[]; development=[]
    for source, target in ((train,examples),(dev,development)):
        for left,right in source:
            x,y=tok.encode(left).ids,tok.encode(right).ids
            if max(len(x),len(y))>2048 or 0 in x+y: raise ValueError("context overflow or unknown structural symbol")
            assert tok.decode(y,skip_special_tokens=False)==right
            target.append((x,y))
    cfg=dict(model_type='gpt2',n_ctx=4096,n_embd=64,n_head=4,n_layer=2,n_positions=4096,layer_norm_epsilon=1e-5,vocab_size=1024,max_new_tokens=2048,output_mode='block_plan')
    random.seed(17); mx.random.seed(17); rng=random.Random(17)
    model=Model(ModelArgs(**{k:v for k,v in cfg.items() if k not in {'max_new_tokens','output_mode'}}))
    optimizer=optim.Adam(learning_rate=3e-4)
    args.output.mkdir(parents=True)
    tok.save(str(args.output/'tokenizer.json'))
    (args.output/'config.json').write_text(c.canonical(cfg)+'\n')
    shutil.copy2(c.CATALOG,args.output/'contracts.json')
    shutil.copy2(c.CONTRACT_PATH,args.output/'contract.md')
    (args.output/'codec.json').write_text(c.canonical({'version':'som.block-plan.v1','input_limit':2048,'output_limit':2048,'symbols':symbols,'token_ids':tok.get_vocab(),'decode':'lossless; no repair; explicit end required'})+'\n')
    shutil.copytree(args.legacy,args.output/'legacy')
    shutil.copy2(c.ROOT/'som-code-python/data/blocks/v5/blocks.json',args.output/'legacy/blocks.json')
    def loss(model, ids, mask):
        logits=model(ids[:,:-1]); per=nn.losses.cross_entropy(logits,ids[:,1:],reduction='none')
        return (per*mask).sum()/mask.sum()
    grad=nn.value_and_grad(model,loss)
    history=[]; started=time.monotonic(); completed=0
    def evaluate():
        model.eval(); groups=defaultdict(list)
        for index,(x,y) in enumerate(development): groups[len(x)].append((index,x,y))
        results=[None]*len(development)
        for group in groups.values():
            for start in range(0,len(group),16):
                batch=group[start:start+16]; cache=make_prompt_cache(model)
                logits=model(mx.array([b[1] for b in batch],dtype=mx.int32),cache=cache)[:,-1]
                active=set(range(len(batch)))
                for position in range(max(len(b[2]) for b in batch)):
                    selected=mx.argmax(logits,axis=-1); mx.eval(selected)
                    values=selected.tolist()
                    for j in list(active):
                        expected=batch[j][2][position]
                        if values[j]!=expected:
                            results[batch[j][0]]={'correct':False,'first_error':position,'expected':tok.id_to_token(expected),'selected':tok.id_to_token(values[j])}; active.remove(j)
                        elif position+1==len(batch[j][2]):
                            results[batch[j][0]]={'correct':True,'tokens':position+1}; active.remove(j)
                    if not active: break
                    logits=model(selected[:,None],cache=cache)[:,-1]
        model.train()
        result={'step':completed,'correct':sum(r['correct'] for r in results),'total':len(results),'results':results}
        history.append(result)
        print('development='+c.canonical({k:v for k,v in result.items() if k!='results'}),flush=True)
        return result['correct']==result['total']
    def save(perfect=False):
        weights=dict(tree_flatten(model.parameters())); mx.eval(weights)
        mx.save_safetensors(str(args.output/'model.safetensors'),weights)
        names=['tokenizer.json','config.json','model.safetensors','contracts.json','contract.md','codec.json']
        manifest={'dsl_version':'som.dsl.v2','output_mode':'block_plan','training_steps':completed,'elapsed_seconds':time.monotonic()-started,'seed':17,
            'blocks_sha256':sha(args.blocks),'files':{n:sha(args.output/n) for n in names},'legacy_manifest_sha256':sha(args.output/'legacy/manifest.json'),
            'data_sha256':sha(args.data),'development_sha256':sha(args.dev),'selection':'earliest perfect full-sequence development checkpoint',
            'development_perfect':perfect,'development_checks':history,'examples':len(examples),'unique_inputs':len({a for a,b in train}),
            'max_input_tokens':max(len(x) for x,y in examples),'max_output_tokens':max(len(y) for x,y in examples),'parameter_count':sum(v.size for v in weights.values())}
        (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    model.train(); save()
    for _ in range(args.steps):
        if time.monotonic()-started>=args.hours*3600: break
        batch=[examples[rng.randrange(len(examples))] for _ in range(8)]
        size=max(len(x)+len(y) for x,y in batch)
        ids=[]; masks=[]
        for x,y in batch:
            ids.append(x+y+[0]*(size-len(x)-len(y)))
            masks.append([0]*(len(x)-1)+[1]*len(y)+[0]*(size-len(x)-len(y)))
        value,grads=grad(model,mx.array(ids,dtype=mx.int32),mx.array(masks,dtype=mx.float32))
        optimizer.update(model,grads); mx.eval(model.parameters(),optimizer.state,value)
        completed+=1
        if completed%100==0: print(f'step={completed} loss={float(value):.6f} elapsed_s={time.monotonic()-started:.1f}',flush=True)
        if completed%1000==0:
            perfect=evaluate(); save(perfect)
            if perfect: break
    else:
        pass
    if completed%1000: save(False)
    print(c.canonical({'output':str(args.output),'steps':completed,'elapsed_seconds':time.monotonic()-started,'development_perfect':bool(history and history[-1]['correct']==len(development))}),flush=True)
