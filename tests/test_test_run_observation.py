"""Failure-summary contracts, including actual unittest class failure and exit1."""
import io,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
if str(SCRIPTS) not in sys.path:sys.path.insert(0,str(SCRIPTS))
from test_run_observation import observe_test_result,write_observed_result

class OutcomePublicationTests(unittest.TestCase):
    def test_setup_class_error_publishes_missing_ids_and_process_exit_one(self):
        with tempfile.TemporaryDirectory() as temp:
            destination=Path(temp)/'observed.json'
            code=r'''
import unittest,io,sys
from test_run_observation import observe_test_result,write_observed_result
started=[];finished=[]
class Broken(unittest.TestCase):
 @classmethod
 def setUpClass(cls):raise RuntimeError('controlled setup failure')
 def test_a(self):pass
 def test_b(self):pass
class Good(unittest.TestCase):
 def test_ok(self):pass
class Track(unittest.TextTestResult):
 def startTest(self,t):started.append(t.id());super().startTest(t)
 def stopTest(self,t):finished.append(t.id());super().stopTest(t)
suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(Broken),unittest.defaultTestLoader.loadTestsFromTestCase(Good)])
def flatten(s):
 for item in s:
  if isinstance(item,unittest.TestSuite):yield from flatten(item)
  else:yield item.id()
ids=list(flatten(suite));result=unittest.TextTestRunner(stream=io.StringIO(),resultclass=Track).run(suite)
summary=observe_test_result(result,ids,started,finished)
write_observed_result(sys.argv[1],summary)
assert started==ids and finished==ids,'strict inventory still fails AFTER publication'
sys.exit(summary['intendedExitStatus'])
'''
            env=dict(os.environ,PYTHONPATH=str(SCRIPTS))
            result=subprocess.run([sys.executable,'-B','-c',code,str(destination)],env=env,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,1)
            summary=json.loads(destination.read_bytes())
            self.assertEqual(summary['collected'],3);self.assertEqual(summary['tests'],1)
            self.assertEqual(len(summary['missingIDs']),2);self.assertEqual(len(summary['setUpClassErrors']),1)
            self.assertFalse(summary['success']);self.assertEqual(summary['intendedExitStatus'],1)
            self.assertEqual(summary['nativeExitStatus'],'OBSERVED_SEPARATELY_BY_PARENT')

    def test_existing_summary_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'result.json';path.write_bytes(b'original')
            with self.assertRaises(FileExistsError):write_observed_result(path,{'forged':True})
            self.assertEqual(path.read_bytes(),b'original')
            self.assertEqual([p.name for p in Path(temp).iterdir()],['result.json'])

    def test_missing_or_reordered_invocations_never_become_success(self):
        class Result:
            testsRun=2;skipped=[];errors=[];failures=[]
            def wasSuccessful(self):return True
        for actual in (['a'],['b','a'],['a','a']):
            with self.subTest(actual=actual):
                value=observe_test_result(Result(),['a','b'],actual,actual)
                self.assertFalse(value['success']);self.assertEqual(value['intendedExitStatus'],1)