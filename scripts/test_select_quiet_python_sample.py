"""Synthetic end-to-end checks for Python-scoped screen selection."""

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
from scripts import rank_quiet_python_lineages as scoped
from scripts import select_quiet_python_sample as selector
from scripts.test_project_quiet_python_candidates import candidate


def encode(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode()


def synthetic_inputs():
    rows = [candidate(f'example/repo{index:02d}', 1, 'python')
            for index in range(24)]
    rows.sort(key=lambda row: (row['rank_sha256'], row['instance_id']))
    source = {'schema': 'solcodex.candidate-rank.v1',
              'candidate_source': projection.SOURCE,
              'candidate_revision': projection.REVISION,
              'selection_seed': projection.SEED,
              'export_manifest_checks_passed': True, 'candidates': rows}
    projected = projection.project(source)
    projected.update(source_candidate_sha256=scoped.PINNED_RANK_SHA256,
                     projection_code_sha256=scoped.digest(scoped.PROJECTION_PATH.read_bytes()),
                     dependency_sha256={name: scoped.digest((scoped.SCRIPTS / name).read_bytes())
                                        for name in scoped.DEPENDENCIES})
    projection_bytes = encode(projected)
    aggregate = {'schema': 'solcodex.quiet-python-projection-aggregate.v1',
                 'candidate_source': projection.SOURCE,
                 'candidate_revision': projection.REVISION,
                 'private_projection_sha256': scoped.digest(projection_bytes),
                 'source_candidate_sha256': scoped.PINNED_RANK_SHA256,
                 'projection_code_sha256': projected['projection_code_sha256'],
                 'dependency_sha256': projected['dependency_sha256'],
                 'source_candidate_count': 24,
                 'projected_candidate_count': 24,
                 'projected_direct_repository_count': 24}
    groups = [{'repositories': [f'example/repo{index:02d}'],
               'exposure_witnesses': []} for index in range(24)]
    lineage_map = {'schema': 'solcodex.lineage-map.v1', 'lineages': groups}
    evidence = {'schema': 'solcodex.quiet-python-ancestry-evidence.v1',
                'lineages': [
                    {'repositories': group['repositories'], 'resolution': 'resolved',
                     'external_repositories': [], 'exposure_witnesses': [],
                     'evidence_references': [f'review/source-{index:02d}']}
                    for index, group in enumerate(groups)]}
    exposure = {'schema': 'solcodex.development-exposure.v1', 'lineages': []}
    raw = {'projection': projection_bytes, 'aggregate': encode(aggregate),
           'map': encode(lineage_map), 'evidence': encode(evidence),
           'exposure': encode(exposure)}
    dependencies = {name: scoped.digest((scoped.SCRIPTS / name).read_bytes())
                    for name in scoped.DEPENDENCIES}
    map_approval = {
        'schema': 'solcodex.quiet-python-lineage-map-approval.v1',
        'status': 'approved',
        'source_candidate_ranking_sha256': scoped.PINNED_RANK_SHA256,
        'projected_candidate_sha256': scoped.digest(raw['projection']),
        'projection_code_sha256': scoped.digest(scoped.PROJECTION_PATH.read_bytes()),
        'projection_dependency_sha256': scoped.digest(json.dumps(
            dependencies, sort_keys=True, separators=(',', ':')).encode()),
        'lineage_map_sha256': scoped.digest(raw['map']),
        'ancestry_evidence_sha256': scoped.digest(raw['evidence']),
        'exposure_manifest_sha256': scoped.digest(raw['exposure']),
        'core_lineage_ranker_sha256': scoped.digest(scoped.CORE_PATH.read_bytes()),
        'scoped_lineage_ranker_sha256': scoped.digest(Path(scoped.__file__).read_bytes()),
        'independent_reviewers': ['ancestry A', 'ancestry B']}
    raw['map_approval'] = encode(map_approval)
    replayed = selector.replay_scoped_ranking(
        raw['projection'], raw['map'], raw['evidence'], raw['exposure'],
        raw['map_approval'], raw['aggregate'])
    raw['ranking'] = encode(replayed)
    review = {'schema': 'solcodex.task-review.v1', 'lineages': []}
    for index, group in enumerate(replayed['lineages']):
        family = ('state', 'build', 'api')[index // 8]
        review['lineages'].append({
            'lineage_rank_sha256': group['lineage_rank_sha256'],
            'tasks': [{'instance_id': group['candidates'][0]['instance_id'],
                       'eligible_families': [family], 'exclusion_reason': None}]})
    raw['review'] = encode(review)
    return raw


def approved_sample(raw):
    return {
        'schema': 'solcodex.quiet-python-sample-approval.v1',
        'status': 'approved',
        'lineage_ranking_sha256': scoped.digest(raw['ranking']),
        'task_review_sha256': scoped.digest(raw['review']),
        'projected_candidate_sha256': scoped.digest(raw['projection']),
        'lineage_map_sha256': scoped.digest(raw['map']),
        'ancestry_evidence_sha256': scoped.digest(raw['evidence']),
        'map_approval_sha256': scoped.digest(raw['map_approval']),
        'projection_aggregate_sha256': scoped.digest(raw['aggregate']),
        'selector_sha256': scoped.digest(Path(selector.__file__).read_bytes()),
        'quiet_helper_sha256': scoped.digest(selector.QUIET_HELPER_PATH.read_bytes()),
        'core_selector_sha256': scoped.digest(selector.CORE_SELECTOR_PATH.read_bytes()),
        'rules_sha256': scoped.digest(selector.RULES_PATH.read_bytes()),
        'screen_proposal_sha256': scoped.digest(selector.PROPOSAL_PATH.read_bytes()),
        'independent_curators': ['task A', 'task B']}


class QuietPythonSelectorTests(unittest.TestCase):
    def test_pending_then_approved_cli_replays_full_ranking(self):
        with mock.patch.object(scoped, 'EXPECTED_SOURCE_COUNT', 24), \
             mock.patch.object(scoped, 'EXPECTED_CANDIDATE_COUNT', 24), \
             mock.patch.object(scoped, 'EXPECTED_REPOSITORY_COUNT', 24):
            raw = synthetic_inputs()
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                paths = {name: root / f'{name}.json' for name in raw}
                for name, value in raw.items():
                    paths[name].write_bytes(value)
                paths['approval'] = root / 'approval.json'
                output = root / 'sample.json'
                approval = approved_sample(raw)
                paths['approval'].write_bytes(encode({**approval, 'status': 'pending'}))
                argv = ['select_quiet_python_sample.py', '--projected-candidates',
                        str(paths['projection']), '--lineage-map', str(paths['map']),
                        '--ancestry-evidence', str(paths['evidence']),
                        '--lineage-ranking', str(paths['ranking']),
                        '--task-review', str(paths['review']), '--out', str(output)]
                with mock.patch.object(sys, 'argv', argv), \
                     mock.patch.object(selector, 'APPROVAL_PATH', paths['approval']), \
                     mock.patch.object(selector, 'MAP_APPROVAL_PATH', paths['map_approval']), \
                     mock.patch.object(selector, 'AGGREGATE_PATH', paths['aggregate']), \
                     mock.patch.object(selector, 'EXPOSURE_PATH', paths['exposure']):
                    with contextlib.redirect_stderr(io.StringIO()), \
                         self.assertRaises(SystemExit):
                        selector.main()
                    self.assertFalse(output.exists())
                    paths['approval'].write_bytes(encode(approval))
                    printed = io.StringIO()
                    with contextlib.redirect_stdout(printed):
                        selector.main()
                    result = json.loads(output.read_bytes())
                    self.assertEqual(result['schema'], 'solcodex.quiet-python-screen-sample.v1')
                    self.assertEqual(result['selected_count'], 24)
                    self.assertEqual(result['family_counts'],
                                     {'state': 8, 'build': 8, 'api': 8})
                    self.assertTrue(result['scoped_lineage_map_approval_checked'])
                    self.assertFalse(result['screen_run_authorized'])
                    self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                    self.assertNotIn('example__repo', printed.getvalue())
                    altered = copy.deepcopy(json.loads(paths['ranking'].read_bytes()))
                    altered['lineages'][0]['selection_eligible'] = False
                    raw['ranking'] = encode(altered)
                    paths['ranking'].write_bytes(raw['ranking'])
                    paths['approval'].write_bytes(encode(approved_sample(raw)))
                    argv[-1] = str(root / 'replay-mismatch.json')
                    with contextlib.redirect_stderr(io.StringIO()), \
                         self.assertRaises(SystemExit):
                        selector.main()
                    self.assertFalse((root / 'replay-mismatch.json').exists())

    def test_sample_approval_needs_distinct_curators_and_current_rules(self):
        with mock.patch.object(scoped, 'EXPECTED_SOURCE_COUNT', 24), \
             mock.patch.object(scoped, 'EXPECTED_CANDIDATE_COUNT', 24), \
             mock.patch.object(scoped, 'EXPECTED_REPOSITORY_COUNT', 24):
            raw = synthetic_inputs()
        approval = approved_sample(raw)
        args = (raw['ranking'], raw['review'], raw['projection'], raw['map'],
                raw['evidence'], raw['map_approval'], raw['aggregate'])
        selector.verify_sample_approval(*args, approval)
        for changed in (
                {**approval, 'status': 'pending'},
                {**approval, 'rules_sha256': '0' * 64},
                {**approval, 'screen_proposal_sha256': '0' * 64},
                {**approval, 'quiet_helper_sha256': '0' * 64},
                {**approval, 'independent_curators': ['same', ' same ']}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                selector.verify_sample_approval(*args, changed)
        with tempfile.TemporaryDirectory() as directory:
            changed_proposal = Path(directory) / 'changed-proposal.md'
            changed_proposal.write_bytes(selector.PROPOSAL_PATH.read_bytes() + b'\nchanged\n')
            with mock.patch.object(selector, 'PROPOSAL_PATH', changed_proposal), \
                 self.assertRaises(ValueError):
                selector.verify_sample_approval(*args, approval)


if __name__ == '__main__':
    unittest.main()
