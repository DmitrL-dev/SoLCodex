"""Synthetic checks for the scoped Python ancestry ranking gate."""

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
from scripts.test_project_quiet_python_candidates import candidate, fixture
from scripts.rank_confirm_lineages import rank_lineages


def synthetic_projection():
    report = projection.project(fixture())
    report.update(source_candidate_sha256=scoped.PINNED_RANK_SHA256,
                  projection_code_sha256=scoped.digest(scoped.PROJECTION_PATH.read_bytes()),
                  dependency_sha256={name: scoped.digest((scoped.SCRIPTS / name).read_bytes())
                                     for name in scoped.DEPENDENCIES})
    raw = (json.dumps(report, sort_keys=True) + '\n').encode()
    aggregate = {
        'schema': 'solcodex.quiet-python-projection-aggregate.v1',
        'candidate_source': projection.SOURCE,
        'candidate_revision': projection.REVISION,
        'private_projection_sha256': scoped.digest(raw),
        'source_candidate_sha256': scoped.PINNED_RANK_SHA256,
        'projection_code_sha256': report['projection_code_sha256'],
        'dependency_sha256': report['dependency_sha256'],
        'source_candidate_count': 3,
        'projected_candidate_count': 2,
        'projected_direct_repository_count': 1}
    return raw, aggregate


def ancestry_fixture():
    lineage_map = {'schema': 'solcodex.lineage-map.v1', 'lineages': [
        {'repositories': ['a/repo'], 'exposure_witnesses': ['x/exposed']}]}
    evidence = {'schema': 'solcodex.quiet-python-ancestry-evidence.v1',
                'lineages': [{'repositories': ['a/repo'], 'resolution': 'resolved',
                              'external_repositories': ['x/exposed'],
                              'exposure_witnesses': ['x/exposed'],
                              'evidence_references': ['review/source-1']}]}
    return lineage_map, evidence


def approved(projection_bytes, map_bytes, evidence_bytes, exposure_bytes):
    dependencies = {name: scoped.digest((scoped.SCRIPTS / name).read_bytes())
                    for name in scoped.DEPENDENCIES}
    return {
        'schema': 'solcodex.quiet-python-lineage-map-approval.v1',
        'status': 'approved',
        'source_candidate_ranking_sha256': scoped.PINNED_RANK_SHA256,
        'projected_candidate_sha256': scoped.digest(projection_bytes),
        'projection_code_sha256': scoped.digest(scoped.PROJECTION_PATH.read_bytes()),
        'projection_dependency_sha256': scoped.digest(json.dumps(
            dependencies, sort_keys=True, separators=(',', ':')).encode()),
        'lineage_map_sha256': scoped.digest(map_bytes),
        'ancestry_evidence_sha256': scoped.digest(evidence_bytes),
        'exposure_manifest_sha256': scoped.digest(exposure_bytes),
        'core_lineage_ranker_sha256': scoped.digest(scoped.CORE_PATH.read_bytes()),
        'scoped_lineage_ranker_sha256': scoped.digest(Path(scoped.__file__).read_bytes()),
        'independent_reviewers': ['curator A', 'curator B']}


class QuietPythonLineageTests(unittest.TestCase):
    def test_projection_validates_order_scope_and_all_code_pins(self):
        raw, aggregate = synthetic_projection()
        with mock.patch.object(scoped, 'EXPECTED_SOURCE_COUNT', 3), \
             mock.patch.object(scoped, 'EXPECTED_CANDIDATE_COUNT', 2), \
             mock.patch.object(scoped, 'EXPECTED_REPOSITORY_COUNT', 1):
            report, source = scoped.verify_projection(raw, aggregate)
            self.assertEqual(len(report['candidates']), 2)
            self.assertEqual(len(source['candidates']), 2)
            for mutated in (
                    {**aggregate, 'private_projection_sha256': '0' * 64},
                    {**aggregate, 'dependency_sha256': {}}):
                with self.assertRaises(ValueError):
                    scoped.verify_projection(raw, mutated)
            bad = copy.deepcopy(report)
            bad['candidates'][0]['patch'] = 'future fix'
            altered = (json.dumps(bad, sort_keys=True) + '\n').encode()
            with self.assertRaises(ValueError):
                scoped.verify_projection(altered, {**aggregate,
                    'private_projection_sha256': scoped.digest(altered)})

    def test_approval_rejects_pending_changed_evidence_and_one_reviewer(self):
        projection_bytes, _ = synthetic_projection()
        map_bytes = b'{"schema":"solcodex.lineage-map.v1"}'
        evidence_bytes = b'{"schema":"solcodex.quiet-python-ancestry-evidence.v1"}'
        exposure_bytes = b'{"schema":"solcodex.development-exposure.v1"}'
        approval = approved(projection_bytes, map_bytes, evidence_bytes,
                            exposure_bytes)
        scoped.verify_approval(projection_bytes, map_bytes, evidence_bytes,
                               exposure_bytes, approval)
        for changed in (
                {**approval, 'status': 'pending'},
                {**approval, 'ancestry_evidence_sha256': '0' * 64},
                {**approval, 'lineage_map_sha256': '0' * 64},
                {**approval, 'exposure_manifest_sha256': '0' * 64},
                {**approval, 'core_lineage_ranker_sha256': '0' * 64},
                {**approval, 'independent_reviewers': ['same', 'same']}):
            with self.assertRaises(ValueError):
                scoped.verify_approval(projection_bytes, map_bytes, evidence_bytes,
                                       exposure_bytes, changed)

    def test_external_exposed_witness_excludes_scoped_lineage(self):
        raw, aggregate = synthetic_projection()
        with mock.patch.object(scoped, 'EXPECTED_SOURCE_COUNT', 3), \
             mock.patch.object(scoped, 'EXPECTED_CANDIDATE_COUNT', 2), \
             mock.patch.object(scoped, 'EXPECTED_REPOSITORY_COUNT', 1):
            _, source = scoped.verify_projection(raw, aggregate)
        lineage_map = {'schema': 'solcodex.lineage-map.v1', 'lineages': [
            {'repositories': ['a/repo'], 'exposure_witnesses': ['x/exposed']}]}
        result = rank_lineages(source, lineage_map, {'x/exposed'})
        self.assertEqual(result['candidate_count'], 2)
        self.assertEqual(result['excluded_exposure_lineages'], 1)
        self.assertFalse(result['lineages'][0]['selection_eligible'])
        with self.assertRaises(ValueError):
            rank_lineages(source, {'schema': 'solcodex.lineage-map.v1',
                                   'lineages': []}, {'x/exposed'})

    def test_evidence_requires_complete_resolved_consistent_groups(self):
        lineage_map, evidence = ancestry_fixture()
        scoped.verify_evidence(evidence, lineage_map, {'a/repo'}, {'x/exposed'})
        for bad in (
                {'schema': evidence['schema'], 'lineages': []},
                {'schema': evidence['schema'], 'lineages': [{}]},
                {'schema': evidence['schema'], 'lineages': [
                    {**evidence['lineages'][0], 'resolution': 'unresolved'}]},
                {'schema': evidence['schema'], 'lineages': [
                    {**evidence['lineages'][0], 'evidence_references': []}]},
                {'schema': evidence['schema'], 'lineages': [
                    {**evidence['lineages'][0], 'exposure_witnesses': []}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                scoped.verify_evidence(bad, lineage_map, {'a/repo'}, {'x/exposed'})

    def test_two_python_repositories_share_external_upstream(self):
        ranked = fixture()
        ranked['candidates'] = [candidate('a/repo', 1, 'python'),
                                candidate('b/repo', 1, 'python'),
                                candidate('c/repo', 1, 'go')]
        ranked['candidates'].sort(key=lambda row: (row['rank_sha256'],
                                                    row['instance_id']))
        projected = projection.project(ranked)
        source = {**ranked, 'candidates': projected['candidates']}
        lineage_map = {'schema': 'solcodex.lineage-map.v1', 'lineages': [
            {'repositories': ['a/repo', 'b/repo'], 'exposure_witnesses': []}]}
        evidence = {'schema': 'solcodex.quiet-python-ancestry-evidence.v1',
                    'lineages': [{'repositories': ['a/repo', 'b/repo'],
                                  'resolution': 'resolved',
                                  'external_repositories': ['upstream/core'],
                                  'exposure_witnesses': [],
                                  'evidence_references': ['review/common-upstream']}]}
        scoped.verify_evidence(evidence, lineage_map, {'a/repo', 'b/repo'}, set())
        result = rank_lineages(source, lineage_map, set())
        self.assertEqual(result['lineage_count'], 1)
        self.assertEqual(result['candidate_count'], 2)

    def test_cli_pending_then_approved_private_output_without_ids(self):
        projection_bytes, aggregate = synthetic_projection()
        lineage_map, evidence = ancestry_fixture()
        map_bytes = (json.dumps(lineage_map) + '\n').encode()
        evidence_bytes = (json.dumps(evidence) + '\n').encode()
        exposure_bytes = (json.dumps({'schema': 'solcodex.development-exposure.v1',
                                      'lineages': [{'repositories': ['x/exposed']}]}) + '\n').encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {name: root / name for name in
                     ('projection.json', 'aggregate.json', 'map.json', 'evidence.json',
                      'exposure.json', 'approval.json', 'ranked.json')}
            for name, raw in (('projection.json', projection_bytes),
                              ('aggregate.json', json.dumps(aggregate).encode()),
                              ('map.json', map_bytes), ('evidence.json', evidence_bytes),
                              ('exposure.json', exposure_bytes)):
                paths[name].write_bytes(raw)
            approval = approved(projection_bytes, map_bytes, evidence_bytes,
                                exposure_bytes)
            paths['approval.json'].write_text(json.dumps({**approval, 'status': 'pending'}))
            argv = ['rank_quiet_python_lineages.py', '--projected-candidates',
                    str(paths['projection.json']), '--lineage-map', str(paths['map.json']),
                    '--ancestry-evidence', str(paths['evidence.json']),
                    '--out', str(paths['ranked.json'])]
            patches = (mock.patch.object(scoped, 'EXPECTED_SOURCE_COUNT', 3),
                       mock.patch.object(scoped, 'EXPECTED_CANDIDATE_COUNT', 2),
                       mock.patch.object(scoped, 'EXPECTED_REPOSITORY_COUNT', 1),
                       mock.patch.object(scoped, 'AGGREGATE_PATH', paths['aggregate.json']),
                       mock.patch.object(scoped, 'APPROVAL_PATH', paths['approval.json']),
                       mock.patch.object(scoped, 'EXPOSURE_PATH', paths['exposure.json']),
                       mock.patch.object(sys, 'argv', argv))
            with contextlib.ExitStack() as stack:
                for patch in patches:
                    stack.enter_context(patch)
                with contextlib.redirect_stderr(io.StringIO()), \
                     self.assertRaises(SystemExit):
                    scoped.main()
                self.assertFalse(paths['ranked.json'].exists())
                paths['approval.json'].write_text(json.dumps(approval))
                printed = io.StringIO()
                with contextlib.redirect_stdout(printed):
                    scoped.main()
                report = json.loads(paths['ranked.json'].read_bytes())
                self.assertEqual(paths['ranked.json'].stat().st_mode & 0o777, 0o600)
                self.assertEqual(report['excluded_exposure_lineages'], 1)
                self.assertFalse(report['lineages'][0]['selection_eligible'])
                self.assertNotIn('a__repo', printed.getvalue())
                self.assertFalse(report['sample_selected'])
                with contextlib.redirect_stderr(io.StringIO()), \
                     self.assertRaises(SystemExit):
                    scoped.main()
                argv[-1] = str(root / 'tampered.json')
                for name, changed in (
                        ('evidence.json', evidence_bytes + b' '),
                        ('map.json', map_bytes + b' '),
                        ('exposure.json', exposure_bytes + b' ')):
                    original = paths[name].read_bytes()
                    paths[name].write_bytes(changed)
                    with self.subTest(name=name), \
                         contextlib.redirect_stderr(io.StringIO()), \
                         self.assertRaises(SystemExit):
                        scoped.main()
                    self.assertFalse((root / 'tampered.json').exists())
                    paths[name].write_bytes(original)


if __name__ == '__main__':
    unittest.main()
