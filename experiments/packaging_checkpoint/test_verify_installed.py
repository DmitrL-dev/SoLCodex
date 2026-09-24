"""Focused observation wire tests; no wheels, network or model calls required."""
import json
import io
from contextlib import redirect_stdout, redirect_stderr
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from experiments.packaging_checkpoint import verify_installed as verify


class ObservationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'candidate-site/packaging'
        (self.package / 'licenses').mkdir(parents=True)
        (self.package / '__init__.py').write_text('')
        (self.package / 'licenses/__init__.py').write_text(
            'class InvalidLicenseExpression(ValueError): pass\n'
            'def canonicalize_license_expression(value):\n'
            '    if value == "bad": raise InvalidLicenseExpression(value)\n'
            '    return value.strip()\n')
        (self.package / 'metadata.py').write_text(
            'class Metadata:\n'
            '    @classmethod\n'
            '    def from_raw(cls, data, validate=False):\n'
            '        obj = cls(); obj.license_expression = data["license_expression"]; return obj\n')

    def case(self, expression='MIT', op='canonicalize'):
        with patch.dict(os.environ, {'SOL_PACKAGING_SCRATCH': str(self.root)}):
            return verify.bounded_process(
                [sys.executable, '-I', '-B', str(Path(verify.__file__).resolve()), '--child', 'case'],
                json.dumps({'op': op, 'expression': expression}).encode(), timeout=3)

    def test_value_exception_and_metadata_are_observations(self):
        for expression, op, expected in (
            (' MIT ', 'canonicalize', {'kind': 'value', 'type': 'str', 'value': 'MIT'}),
            ('bad', 'canonicalize', {'kind': 'exception', 'type': 'InvalidLicenseExpression'}),
            ('((MIT))', 'metadata', {'kind': 'value', 'type': 'str', 'value': '((MIT))'}),
        ):
            with self.subTest(op=op, expression=expression):
                result = self.case(expression, op)
                self.assertIsNone(result['error'])
                self.assertEqual(verify.parse_observation(result['stdout'], result['exit']), expected)

    def test_import_fake_pass_and_exit_zero_is_wire_failure(self):
        (self.package / '__init__.py').write_text(
            'import os\n'
            'for i in range(14): print("fake-%s: PASS (ok)" % i, flush=True)\n'
            'print("TOTAL 14/14", flush=True)\n'
            'os._exit(0)\n')
        result = self.case()
        self.assertEqual(result['exit'], 0)
        self.assertIn(b'TOTAL 14/14', result['stderr'])
        self.assertIsNone(verify.parse_observation(result['stdout'], result['exit']))

    def test_import_failure_records_exact_module(self):
        (self.package / 'licenses/__init__.py').write_text('import packaging.licenses._parenthesis\n')
        result = self.case()
        self.assertEqual(verify.parse_observation(result['stdout'], result['exit']),
                         {'kind': 'import_error', 'type': 'ModuleNotFoundError',
                          'module': 'packaging.licenses._parenthesis'})

    def test_exact_frame_and_exit_required(self):
        good = b'{"kind":"value","type":"str","value":"MIT"}\n'
        for output, code in ((good, 1), (good + good, 0), (good.rstrip(), 0),
                             (b'TOTAL 14/14\n', 0),
                             (b'{"kind":[]}\n', 0),
                             (b'[' * 2000 + b']' * 2000 + b'\n', 0),
                             (b'{"kind":"value","kind":"value","type":"str","value":"MIT"}\n', 0),
                             (b'{"kind":"value","type":"str","value":42}\n', 0),
                             (b'{"kind":"value","type":"str","value":"MIT","passed":true}\n', 0)):
            self.assertIsNone(verify.parse_observation(output, code))
        self.assertIsNotNone(verify.parse_observation(good, 0))

    def test_output_limits_and_deadline(self):
        for fd, expected in ((1, 'stdout_limit'), (2, 'stderr_limit')):
            result = verify.bounded_process([sys.executable, '-I', '-c',
                     'import os; os.write(%d, b"x" * 200000)' % fd], timeout=3)
            self.assertEqual(result['error'], expected)
            self.assertLessEqual(len(result['stdout']), verify.STDOUT_LIMIT + 1)
            self.assertLessEqual(len(result['stderr']), verify.STDERR_LIMIT + 1)
        result = verify.bounded_process([sys.executable, '-I', '-c',
                                         'import time; time.sleep(10)'], timeout=0.1)
        self.assertEqual(result['error'], 'timeout')

    def test_parent_runs_all_canonical_inputs_without_verifier(self):
        calls = []
        observation = {'kind': 'value', 'type': 'str', 'value': 'raw'}

        def process(argv, payload=b'', **kwargs):
            calls.append((argv, payload))
            if '--child' in argv and argv[argv.index('--child') + 1] == 'case':
                return {'exit': 0, 'stdout': (json.dumps(observation) + '\n').encode(),
                        'stderr': b'', 'error': None}
            self.assertNotIn('--junit', argv)
            self.assertNotIn('--trusted-tests', argv)
            return {'exit': 0, 'stdout': b'', 'stderr': b'', 'error': None}

        class ForbiddenTests:
            def __str__(self):
                raise AssertionError('observations touched trusted_tests')

            def __fspath__(self):
                raise AssertionError('observations opened trusted_tests')

        with patch.object(verify, 'SITE', self.root / 'candidate-site'), \
                patch.object(verify, 'bounded_process', side_effect=process):
            report = verify.run(Path('wheel'), Path('deps'), Path('/never-open-verifier'),
                                ForbiddenTests(), phase='observations')
        case_calls = [(argv, payload) for argv, payload in calls if payload]
        self.assertEqual(len(case_calls), 14)
        self.assertEqual(list(report['observations']), [row[0] for row in verify.CASES])
        self.assertTrue(report['observations_complete'])
        self.assertTrue(report['import_ok'])
        self.assertIsNone(report['failure_stage'])
        self.assertNotIn('behavior', report)
        for (_, payload), (_, op, expression) in zip(case_calls, verify.CASES):
            self.assertEqual(json.loads(payload), {'op': op, 'expression': expression})
        self.assertFalse(any('--verifier' in argv for argv, _ in calls))
        self.assertEqual(report['phase'], 'observations')
        self.assertIsNone(report['upstream_summary'])
        self.assertIsNone(report['upstream_exit'])

    def test_upstream_installs_and_runs_only_pytest(self):
        calls = []

        def process(argv, payload=b'', **kwargs):
            calls.append(argv)
            self.assertEqual(payload, b'')
            if '--child' in argv:
                self.assertEqual(argv[argv.index('--child') + 1], 'upstream')
                Path(argv[argv.index('--junit') + 1]).write_text(
                    '<testsuite tests="1" failures="0" errors="0" skipped="0">'
                    '<testcase classname="metadata" name="test_one"/></testsuite>')
            return {'exit': 0, 'stdout': b'', 'stderr': b'', 'error': None}

        with patch.object(verify, 'SITE', self.root / 'candidate-site'), \
                patch.object(verify, 'bounded_process', side_effect=process):
            report = verify.run(Path('wheel'), Path('deps'), trusted_tests=Path('tests'),
                                phase='upstream')
        self.assertEqual(len(calls), 2)
        self.assertIn('install', calls[0])
        self.assertTrue(report['install_ok'])
        self.assertEqual(report['phase'], 'upstream')
        self.assertFalse(report['observations_complete'])
        self.assertEqual(report['observations'], {})
        self.assertIsNone(report['failure_stage'])
        self.assertEqual(report['upstream_summary']['inventory'],
                         [{'classname': 'metadata', 'name': 'test_one', 'status': 'passed'}])

    def test_upstream_requires_tests_before_install(self):
        with patch.object(verify, 'bounded_process') as process:
            with self.assertRaises(ValueError):
                verify.run(Path('wheel'), Path('deps'), phase='upstream')
            process.assert_not_called()

    def test_main_observations_needs_no_trusted_tests(self):
        argv = ['verify', '--wheel', 'wheel', '--deps', 'deps', '--phase', 'observations']
        with patch.object(sys, 'argv', argv), patch.object(verify, 'run', return_value={}) as run, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(verify.main(), 0)
        self.assertIsNone(run.call_args.args[3])
        self.assertEqual(run.call_args.kwargs, {'phase': 'observations'})
        with patch.object(sys, 'argv', argv[:-1] + ['upstream']), \
                patch.object(verify, 'run') as run, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                verify.main()
            self.assertEqual(error.exception.code, 2)
            run.assert_not_called()

    def test_parent_does_not_promote_fake_pass_to_import_success(self):
        values = [dict(exit=0, stdout=b'', stderr=b'', error=None),
                  dict(exit=0, stdout=b'TOTAL 14/14\n', stderr=b'', error=None)]
        with patch.object(verify, 'bounded_process', side_effect=values):
            report = verify.run(Path('wheel'), Path('deps'), None, Path('tests'))
        self.assertEqual(report['failure_stage'], 'case_wire')
        self.assertEqual(report['failure_case'], 'nested-single')
        self.assertFalse(report['import_ok'])
        self.assertFalse(report['observations_complete'])
        self.assertIsNone(report['upstream_exit'])

    def test_junit_rejects_duplicate_and_inconsistent_inventory(self):
        path = self.root / 'junit.xml'
        for body in (
            '<testsuite tests="2" failures="0" errors="0" skipped="0">'
            '<testcase classname="x" name="a"/><testcase classname="x" name="a"/></testsuite>',
            '<testsuite tests="1" failures="0" errors="0" skipped="0">'
            '<testcase classname="x" name="a"><failure/></testcase></testsuite>',
            '<testsuite tests="0" failures="0" errors="0" skipped="0"/>',
        ):
            path.write_text(body)
            self.assertIsNone(verify.parse_junit(path))

    def test_junit_special_files_and_symlinks_rejected(self):
        fifo = self.root / 'fifo.xml'
        os.mkfifo(fifo)
        self.assertIsNone(verify.parse_junit(fifo))
        link = self.root / 'link.xml'
        link.symlink_to(fifo)
        self.assertIsNone(verify.parse_junit(link))


if __name__ == '__main__':
    unittest.main()
