import sys, unittest, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jsonschema, contracts, classification as c, storage, retrieval, lab

class Protocol(unittest.TestCase):
    def test_all_legal_states(self):
        for task, states in [('action',['ALLOW','BLOCK','NEED_FACTS','NO_POLICY']),('explanation',['EXPLANATION','NO_POLICY'])]:
            for state in states:
                value={'reason':'No policy.'} if state=='NO_POLICY' else {'claims':[{'text':'Evidence.', 'citations':['p']}]}
                out=contracts.normalize(state,value,['p'],task)
                contracts.validate_output(out,['p'],task)
                self.assertEqual(out['policy_answerable'],state!='NO_POLICY')
                self.assertEqual(out['action'],state if task=='action' and state!='NO_POLICY' else None)
    def test_wire_validator_agreement(self):
        for state in ['ALLOW','BLOCK','NEED_FACTS','EXPLANATION','NO_POLICY']:
            task='explanation' if state=='EXPLANATION' else 'action'
            for payload in [{}, {'claims':[]}, {'claims':[{'text':'x','citations':[]}]},
                            {'claims':[{'text':' ','citations':['p']}]}, {'claims':[{'text':'x','citations':['bad']}]},
                            {'reason':' '}, {'reason':'x','action':'ALLOW'}]:
                accepted=jsonschema.Draft202012Validator(contracts.payload_schema(['p'],state)).is_valid(payload)
                try: contracts.normalize(state,payload,['p'],task); normalized=True
                except jsonschema.ValidationError: normalized=False
                self.assertEqual(accepted,normalized)
    def test_no_citable_context(self):
        with self.assertRaisesRegex(ValueError,'ANSWER_WITHOUT_CONTEXT'): contracts.payload_schema([], 'ALLOW')
        self.assertFalse(contracts.normalize('NO_POLICY',{'reason':'Absent'},[],'action')['policy_answerable'])
    def test_task_mismatch(self):
        with self.assertRaises(jsonschema.ValidationError):
            contracts.normalize('ALLOW',{'claims':[{'text':'x','citations':['p']}]},['p'],'explanation')
    def test_fixed_denominators(self):
        rows=[dict(reference='bug',prediction='ERROR',calls=[],error={'kind':'HTTP'},latency_seconds=1),
              dict(reference='feature-request',prediction='ABSTAIN',calls=[],error=None,latency_seconds=2)]
        m=c.metrics(rows)
        self.assertEqual(m['accuracy'],0); self.assertEqual(m['macro_f1'],0)
        self.assertEqual(m['errors'],1); self.assertEqual(m['abstentions'],1)
    def test_input_allowlist(self):
        row={'input':'Problem', 'label':'SECRET_GOLD','messages':[{'content':'SECRET_ASSISTANT'}]}
        self.assertNotIn('SECRET',c.prompt(row['input'],{}))
    def test_classification_schema(self):
        for label in c.CLASSES+['ABSTAIN']: jsonschema.validate({'category':label},c.SCHEMA)
        with self.assertRaises(jsonschema.ValidationError): jsonschema.validate({'category':'question'},c.SCHEMA)
    def test_classification_resume(self):
        rows=[{'issue_number':1,'split':'validation','label':'bug','input':'crash'}]
        with tempfile.TemporaryDirectory() as d, patch.object(c,'source',return_value=({'splits':{'validation':1}},rows)):
            a=SimpleNamespace(modes=['rules'],split='all',limit=None,k=4,device='cpu',out=d,resume=False)
            c.run(a); a.resume=True; c.run(a)
            got=storage.read(Path(d)/'rules/summary.json')
            self.assertEqual(got['overall']['n'],1); self.assertEqual(got['overall']['accuracy'],1)
    def test_failed_decision(self):
        from test_lab import FakeClient
        from test_judge import args
        class Broken(FakeClient):
            def chat(self,*a,**kw): return {'decision':'wrong'},{}
        with tempfile.TemporaryDirectory() as d, patch.object(lab,'Ollama',Broken):
            lab.run(args(d))
            row=retrieval.jsonl(Path(d)/'bm25--direct/predictions.jsonl')[0]
            self.assertEqual(row['error']['stage'],'decision')
            self.assertIsNone(row['output']); self.assertEqual(len(row['calls']),1)

class ReportChecks(unittest.TestCase):
    def test_classification_report_and_corruption(self):
        rows=[{'issue_number':1,'split':'validation','label':'bug','input':'crash'}]
        with tempfile.TemporaryDirectory() as d, patch.object(c,'source',return_value=({'splits':{'validation':1}},rows)):
            a=SimpleNamespace(modes=['rules'],split='all',limit=None,k=4,device='cpu',out=d,resume=False)
            c.run(a); c.report(d)
            self.assertEqual(storage.read(Path(d)/'comparison.json')['rules']['overall']['n'],1)
            with open(Path(d)/'rules/predictions.jsonl','a') as f: f.write('\n')
            with self.assertRaisesRegex(ValueError,'checksum'): c.report(d)
    def test_classification_missing_arm(self):
        with tempfile.TemporaryDirectory() as d:
            retrieval.dump(Path(d)/'experiment.json',{'planned_arms':['none']})
            with self.assertRaisesRegex(ValueError,'Incomplete'): c.report(d)
    def test_normalized_schema_is_entire_validator(self):
        good={'policy_answerable':True,'action':'BLOCK','claims':[{'text':'x','citations':['p']}],'refusal_reason':''}
        for key,value in [('refusal_reason','wrong'),('claims',[]),('action',None),('policy_answerable',False)]:
            row={**good,key:value}
            self.assertFalse(jsonschema.Draft202012Validator(contracts.schema(['p'],'action')).is_valid(row))
            with self.assertRaises(jsonschema.ValidationError): contracts.validate_output(row,['p'],'action')
