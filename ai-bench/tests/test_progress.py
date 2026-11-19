import io
import unittest
from progress import Progress

class ProgressTests(unittest.TestCase):
    def test_resume_and_eta_only_new_work(self):
        now=[0]; out=io.StringIO()
        p=Progress('classification',10,completed=6,errors=1,stream=out,clock=lambda:now[0])
        p.start('12345'); now[0]=10; p.finish('bug')
        self.assertIn('7/10 (70.0%)',out.getvalue())
        self.assertIn('current=12345',out.getvalue())
        self.assertIn('ETA~00:00:30',out.getvalue())
        self.assertIn('resumed=6',out.getvalue())
    def test_error_is_completed_not_success(self):
        out=io.StringIO(); p=Progress('RAG',1,stream=out)
        p.start('RAG-001'); p.finish('HTTP_ERROR',error=True)
        self.assertIn('1/1 (100.0%)',out.getvalue())
        self.assertIn('HTTP_ERROR | errors=1',out.getvalue())
    def test_empty_and_fully_resumed(self):
        for n in [0,2]:
            out=io.StringIO(); p=Progress('test',n,completed=n,stream=out)
            self.assertIn('ALL_SAVED',out.getvalue())
            self.assertIn('ETA~00:00:00',out.getvalue())
            with self.assertRaises(ValueError): p.start('extra')
    def test_no_early_increment(self):
        out=io.StringIO(); p=Progress('test',2,stream=out)
        p.start('a'); self.assertEqual(p.completed,0)
        with self.assertRaises(ValueError): p.start('b')
        p.finish(); self.assertEqual(p.completed,1)
        with self.assertRaises(ValueError): p.finish()
