import tempfile
import unittest
from pathlib import Path
from src.daily_planning.integrated_input.loader import load_inputs, InputError, SCENARIO_FILES

class V3CrudePlusInputTest(unittest.TestCase):
    def test_missing_modified_workbook_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(InputError) as exc:load_inputs(folder,scenario='V3_CRUDE_PLUS_3')
            self.assertEqual(exc.exception.code,'MISSING_INPUT')
            self.assertEqual(str(exc.exception),SCENARIO_FILES['V3_CRUDE_PLUS_3'][0])
    def test_missing_modified_json_is_not_replaced_by_original(self):
        with tempfile.TemporaryDirectory() as folder:
            paths=SCENARIO_FILES['V3_CRUDE_PLUS_3'];(Path(folder)/paths[0]).write_bytes(b'not parsed');(Path(folder)/paths[2]).write_text('{}')
            with self.assertRaises(InputError) as exc:load_inputs(folder,scenario='V3_CRUDE_PLUS_3')
            self.assertEqual(str(exc.exception),paths[1])
    def test_invalid_scenario_cannot_silently_use_original(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(InputError) as exc:load_inputs(folder,scenario='UNKNOWN')
            self.assertEqual(exc.exception.code,'INVALID_SCHEMA')
