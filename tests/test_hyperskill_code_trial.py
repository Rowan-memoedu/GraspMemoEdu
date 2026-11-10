import importlib.util
import unittest
from pathlib import Path

PATH=Path(__file__).resolve().parents[1]/'scripts'/'hyperskill_code_trial'/'core.py'
spec=importlib.util.spec_from_file_location('trial_core',PATH);core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)

class CodeTrialBoundaryTests(unittest.TestCase):
    def test_line_ending_spaces_do_not_reject_template_output(self):
        self.assertEqual(core.normalize('1 2 3  \r\n'),core.normalize('1 2 3'))
        self.assertNotEqual(core.normalize('1  2 3'),core.normalize('1 2 3'))
        self.assertNotEqual(core.normalize('Hello, world!'),core.normalize('Hello, World!'))
    def test_exact_output_keeps_significant_spaces(self):
        self.assertNotEqual(core.normalize('a ','exact'),core.normalize('a','exact'))
    def test_placeholder_and_noop_harnesses_fail_before_judging(self):
        for code in ['class TrialHarness {static void run(){System.out.println("HS_TRIAL_OK");}}','class TrialHarness {static void run(){java.util placeholder;}}']:
            self.assertTrue(core.validate_bundle({'status':'ready','mode':'harness','harness_code':code}))
    def test_state_restore_helper_is_an_assertion_entry_point(self):
        code='class TrialHarness {static void run() throws Exception {TrialChecks.restores(() -> 1, x -> {}, () -> {}, () -> 1, "state");}}'
        self.assertEqual(core.validate_bundle({'status':'ready','mode':'harness','harness_code':code}),[])
    def test_io_entry_point_cannot_be_invented_for_a_method_task(self):
        b={'status':'ready','mode':'io','main_class':'Main','template_has_main':False,'cases':[{'input':'','output':'ok'}]}
        self.assertTrue(core.validate_bundle(b))
    def test_debugging_template_keeps_its_intended_main_entry(self):
        self.assertTrue(core.declares_main('class Main {public static vo1d main(String[] args) {}}'))
        self.assertTrue(core.declares_main('class Main {public static void main(String args[]) {}}'))
        self.assertTrue(core.declares_main('class Main {public static void main(String() args) {}}'))
        self.assertFalse(core.declares_main('class Inspector {static void report(Probe p,String name){}}'))
    def test_code_and_task_identity_are_checked(self):
        with self.assertRaises(ValueError):core.parse_output({'id':'3','output':'{"id":4}'})
    def test_no_path_escape_through_support_filename(self):
        b={'status':'ready','mode':'io','main_class':'Main','cases':[{'input':'','output':''}],'support_files':[{'name':'../Main.java','code':''}]}
        self.assertTrue(core.validate_bundle(b))
    def test_fixture_recovery_uses_declared_names_and_parameter_type(self):
        q={'template':'class Inspect {static void report(Probe p,String name){}}','existing_samples':[['<A> void alpha','1'],['<K, V> void beta','2']]}
        files=core.derive_fixture_sources(q)
        self.assertEqual(files[0]['name'],'Probe.java')
        self.assertIn('public <K, V> void beta()',files[0]['code'])
        self.assertNotIn('TestClass',files[0]['code'])
        q['existing_samples']=[['No input','Well done!']]
        self.assertEqual(core.derive_fixture_sources(q),[])

if __name__=='__main__':unittest.main()
