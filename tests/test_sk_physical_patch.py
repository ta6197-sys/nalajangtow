"""Source-pattern patcher contracts, not a substitute for actual CP-SAT integration tests."""
import ast
import inspect
import unittest

from scripts.apply_step3_integration import PATCHES, one


class TestSourcePatches(unittest.TestCase):
    def test_01_required_files_patched(self):
        self.assertEqual(set(PATCHES), {
            'src/daily_planning/context.py',
            'src/daily_planning/exact_search.py',
            'src/daily_planning/evaluation.py',
            'src/daily_planning/execution.py',
            'src/daily_planning/operations.py',
            'src/daily_planning/optimization.py',
            'web_app.py',
        })

    def test_02_each_exact_patch_replaces_its_known_source_patterns(self):
        # Build a minimal snippet collection from each transform's declared
        # source patterns, then exercise the exact-match patch transform.
        for name, func in PATCHES.items():
            with self.subTest(name=name):
                node = ast.parse(inspect.getsource(func))
                before = []
                for call in ast.walk(node):
                    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                            and call.func.id == 'one'):
                        before.append(ast.literal_eval(call.args[1]))
                self.assertTrue(before)
                original = '# synthetic source-pattern test fixture\n' + '\n'.join(before)
                new = func(original)
                self.assertNotEqual(new, original)
                self.assertIn('sk_physical', new)

    def test_03_pattern_drift_refused(self):
        with self.assertRaisesRegex(ValueError, 'expected exact source pattern once'):
            one('some other revision', 'never matches', 'x', 'mock')

    def test_04_duplicate_pattern_refused(self):
        with self.assertRaisesRegex(ValueError, 'found 2'):
            one('xyz xyz', 'xyz', 'foo', 'mock')


if __name__ == '__main__':
    unittest.main()
