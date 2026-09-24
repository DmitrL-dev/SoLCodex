"""Verify exact metadata-only Python projection and private publication."""

import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from scripts import project_quiet_python_candidates as projection


def candidate(repo, issue, language):
    instance_id = repo.replace('/', '__') + '-' + str(issue)
    return {'instance_id': instance_id, 'repo': repo, 'language': language,
            'license': 'MIT', 'rank_sha256': projection.sha(
                (projection.SEED + '\n' + instance_id).encode()),
            'lineage_review': 'required'}


def fixture():
    rows = [candidate('a/repo', 1, 'python'),
            candidate('a/repo', 2, 'python'),
            candidate('b/repo', 1, 'go')]
    rows.sort(key=lambda row: (row['rank_sha256'], row['instance_id']))
    return {'schema': 'solcodex.candidate-rank.v1',
            'candidate_source': projection.SOURCE,
            'candidate_revision': projection.REVISION,
            'selection_seed': projection.SEED,
            'export_manifest_checks_passed': True,
            'candidates': rows}


class QuietPythonProjectionTests(unittest.TestCase):
    def test_only_python_preserves_frozen_task_order(self):
        ranked = fixture()
        report = projection.project(ranked)
        self.assertEqual(report['source_candidate_count'], 3)
        self.assertEqual(report['projected_candidate_count'], 2)
        self.assertEqual(report['projected_direct_repository_count'], 1)
        self.assertEqual([row['instance_id'] for row in report['candidates']],
                         [row['instance_id'] for row in ranked['candidates']
                          if row['language'] == 'python'])
        self.assertFalse(report['sample_selection_authorized'])

    def test_outcome_payload_duplicate_bad_hash_and_order_fail_closed(self):
        ranked = fixture()
        cases = []
        payload = copy.deepcopy(ranked)
        payload['candidates'][0]['patch'] = 'future fix'
        cases.append(payload)
        duplicate = copy.deepcopy(ranked)
        duplicate['candidates'].append(copy.deepcopy(duplicate['candidates'][0]))
        cases.append(duplicate)
        wrong_hash = copy.deepcopy(ranked)
        wrong_hash['candidates'][0]['rank_sha256'] = '0' * 64
        cases.append(wrong_hash)
        wrong_order = copy.deepcopy(ranked)
        wrong_order['candidates'].reverse()
        cases.append(wrong_order)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                projection.project(value)

    def test_cli_requires_pinned_source_and_writes_private_no_replace(self):
        ranked = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root/'ranked.json', root/'python.json'
            source.write_text(json.dumps(ranked))
            argv = ['project_quiet_python_candidates.py', '--candidate-ranking',
                    str(source), '--out', str(output)]
            with mock.patch.object(sys, 'argv', argv), \
                 contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                projection.main()
            self.assertFalse(output.exists())
            printed = io.StringIO()
            with mock.patch.object(sys, 'argv', argv), \
                 mock.patch.object(projection, 'PINNED_RANK_SHA256',
                                   projection.sha(source.read_bytes())), \
                 contextlib.redirect_stdout(printed):
                projection.main()
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text())['projected_candidate_count'], 2)
            self.assertEqual(set(json.loads(output.read_text())['dependency_sha256']),
                             set(projection.DEPENDENCIES))
            self.assertNotIn('a__repo', printed.getvalue())
            with mock.patch.object(sys, 'argv', argv), \
                 contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                projection.main()


if __name__ == '__main__':
    unittest.main()
