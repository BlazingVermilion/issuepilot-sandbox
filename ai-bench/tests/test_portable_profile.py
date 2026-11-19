"""Offline end-to-end CLI contract: new taxonomy, mapped dataset, other Ollama model."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class Portable(unittest.TestCase):
    def test_custom_model_ranking_and_taxonomy_through_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"issues.jsonl").write_text(json.dumps({"n":1,"report":"The glossary is outdated.","kind":"docs","messages":["DO_NOT_SEND_REFERENCE"]})+"\n")
            (root/"scenarios.jsonl").write_bytes((ROOT/"examples/local/scenarios.jsonl").read_bytes())
            profile={"classification":{"splits":{"validation":"issues.jsonl"},"fields":{"id":"n","text":"report","label":"kind"},"classes":{"docs":"Documentation error","usage":"Usage question","other":"Other request"}},"rag":{"scenarios":"scenarios.jsonl"}}
            file=root/"profile.json";file.write_text(json.dumps(profile))
            code=r"""
import sys, json
from pathlib import Path
root=Path(sys.argv[1])
import bench, retrieval
calls=[]
def http(base,path,payload=None,*args,**kwargs):
    if path=='/api/tags': return {'models':[{'name':'alternate:4b','digest':'d'*64}]}
    if path=='/api/show': return {'capabilities':['completion'],'model_info':{'alternate.context_length':65536}}
    if path=='/api/version': return {'version':'offline-test'}
    if path=='/api/ps': return {'models':[{'digest':'d'*64,'context_length':32768,'size_vram':0}]}
    assert path=='/api/chat' and payload['model']=='alternate:4b' and 'think' not in payload
    if 'format' not in payload: return {'done':True}
    schema=payload['format'];keys=schema['properties']
    prompt=payload['messages'][1]['content']
    assert 'DO_NOT_SEND_REFERENCE' not in prompt and 'reference_claims' not in prompt
    calls.append((list(keys),prompt))
    if 'category' in keys:
        assert set(keys['category']['enum'])=={'docs','usage','other','ABSTAIN'}
        answer={'category':'docs'}
    elif 'decision' in keys: answer={'decision':'BLOCK'}
    else: answer={'claims':[{'text':'The partial reply blocks closure.','citations':['IP-INFO-002']}]}
    return {'model':'alternate:4b','done':True,'done_reason':'stop','prompt_eval_count':80,'eval_count':8,'message':{'content':json.dumps(answer)}}
retrieval.http=http
def cmd(*args):
    sys.argv=['bench.py','--dataset-config',str(root/'profile.json'),*args];bench.main()
common=['--model','alternate:4b','--allow-cpu','--num-ctx','32768','--budget-tokenizer','byte']
cmd('check-data')
cmd('classify','--modes','none','--out',str(root/'class'),*common)
cmd('classify-report','--out',str(root/'class'))
cmd('rag','run','--modes','oracle','--strategy','direct','--out',str(root/'rag'),*common)
cmd('rag','report','--out',str(root/'rag'))
assert len(calls)==3 and (root/'rag/comparison.md').is_file()
c=json.loads((root/'class/comparison.json').read_text())['none']['overall']
assert c['accuracy']==1 and set(c['per_class'])=={'docs','usage','other'}
r=json.loads((root/'rag/oracle--direct/predictions.jsonl').read_text())
assert not r['error'] and r['output']['action']=='BLOCK',r.get('error')
m=json.loads((root/'class/none/manifest.json').read_text())['identity']
assert m['model_digest']=='d'*64 and m['model']=='alternate:4b'
"""
            r=subprocess.run([sys.executable,"-c",code,str(root)],cwd=ROOT,env={**os.environ,"PYTHONUTF8":"1"},capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    def test_local_splits_cannot_overlap_and_answer_column_cannot_be_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); data=d/"tickets.jsonl";data.write_text('{"issue_number": 5, "input": "A report.", "label":"bug"}\n')
            profile=d/"profile.json"; obj={"classification":{"splits":{"validation":data.name,"test":data.name}}}
            for message,fields in [("duplicate",{}),("not a label",{"text":"messages"})]:
                obj["classification"]["fields"]=fields;profile.write_text(json.dumps(obj))
                p=subprocess.run([sys.executable,"bench.py","--dataset-config",str(profile),"check-data"],cwd=ROOT,capture_output=True,text=True)
                self.assertNotEqual(p.returncode,0);self.assertIn(message,p.stderr)
