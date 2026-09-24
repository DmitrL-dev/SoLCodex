"""Verify the dedicated 8/8/8 selector and its fail-closed approval gate."""

import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

try:
    from scripts import select_quiet_screen_sample as selector
except ModuleNotFoundError:
    import select_quiet_screen_sample as selector


def fixture():
    lineages, reviews = [], []
    for index in range(24):
        family = ('state', 'build', 'api')[index // 8]
        repo = f'example/repo{index:02d}'
        identifier = f'example__repo{index:02d}-1'
        rank = f'{index + 1:064x}'
        lineages.append({'repositories':[repo], 'exposure_witnesses':[],
                         'selection_eligible':True,'lineage_rank_sha256':rank,
                         'candidates':[{'instance_id':identifier,'repo':repo,
                                        'language':'python','license':'MIT',
                                        'task_rank_sha256':f'{index + 100:064x}'}]})
        reviews.append({'lineage_rank_sha256':rank,
                        'tasks':[{'instance_id':identifier,
                                  'eligible_families':[family],
                                  'exclusion_reason':None}]})
    return ({'schema':'solcodex.lineage-rank.v1',
             'public_map_approval_checked':True,'lineages':lineages},
            {'schema':'solcodex.task-review.v1','lineages':reviews})


class QuietSelectorTests(unittest.TestCase):
    def test_cli_approved_synthetic_path_writes_private_counts(self):
        ranked,review=fixture()
        ranked.update(candidate_ranking_sha256='a'*64,
                      lineage_map_sha256='b'*64,
                      exposure_manifest_sha256=selector.digest(selector.EXPOSURE_PATH.read_bytes()),
                      lineage_ranker_sha256=selector.digest(selector.RANKER_PATH.read_bytes()))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            ranking,labels,output,sample_approval,map_approval=(root/name for name in
                ('ranking.json','review.json','out.json','sample-approval.json','map-approval.json'))
            ranking.write_text(json.dumps(ranked))
            labels.write_text(json.dumps(review))
            sample_approval.write_text(json.dumps({
                'schema':'solcodex.quiet-screen-sample-approval.v1','status':'approved',
                'lineage_ranking_sha256':selector.digest(ranking.read_bytes()),
                'task_review_sha256':selector.digest(labels.read_bytes()),
                'selector_sha256':selector.digest(Path(selector.__file__).read_bytes()),
                'core_selector_sha256':selector.digest(selector.CORE_PATH.read_bytes()),
                'metadata_helper_sha256':selector.digest(selector.HELPER_PATH.read_bytes()),
                'rules_sha256':selector.digest(selector.RULES_PATH.read_bytes()),
                'independent_curators':['one','two']}))
            map_approval.write_text(json.dumps({
                'schema':'solcodex.lineage-map-approval.v1','status':'approved',
                'candidate_ranking_sha256':ranked['candidate_ranking_sha256'],
                'lineage_map_sha256':ranked['lineage_map_sha256'],
                'exposure_manifest_sha256':ranked['exposure_manifest_sha256'],
                'lineage_ranker_sha256':ranked['lineage_ranker_sha256'],
                'independent_reviewers':['three','four']}))
            argv=['select_quiet_screen_sample.py','--lineage-ranking',str(ranking),
                  '--task-review',str(labels),'--out',str(output)]
            printed=io.StringIO()
            with mock.patch.object(sys,'argv',argv), \
                 mock.patch.object(selector,'APPROVAL_PATH',sample_approval), \
                 mock.patch.object(selector,'MAP_APPROVAL_PATH',map_approval), \
                 contextlib.redirect_stdout(printed):
                selector.main()
            result=json.loads(output.read_text())
            self.assertEqual(result['selected_count'],24)
            self.assertEqual(result['schema'],'solcodex.quiet-screen-sample.v1')
            self.assertFalse(result['screen_run_authorized'])
            self.assertEqual(output.stat().st_mode & 0o777,0o600)
            self.assertNotIn('example__repo',printed.getvalue())
            self.assertEqual(json.loads(printed.getvalue())['selected_count'],24)
            self.assertEqual(result['metadata_helper_sha256'],
                             selector.digest(selector.HELPER_PATH.read_bytes()))
            with self.assertRaises(ValueError):
                selector.verify_current_map_approval(ranked,json.loads(map_approval.read_text()),
                                                     '0'*64,ranked['lineage_ranker_sha256'])

    def test_exact_24_unique_lineages_and_unapproved_run(self):
        ranked, review = fixture()
        result = selector.select_quiet_sample(ranked,review)
        self.assertEqual(result['schema'],'solcodex.quiet-screen-sample.v1')
        self.assertEqual(result['family_counts'],{'state':8,'build':8,'api':8})
        self.assertEqual(result['selected_count'],24)
        self.assertEqual(result['reviewed_lineages'],24)
        self.assertEqual(len({r['repo'] for r in result['selected']}),24)
        self.assertFalse(result['screen_run_authorized'])
        self.assertNotIn('confirmation_run_authorized',result)
        missing=copy.deepcopy(review)
        missing['lineages'].pop()
        with self.assertRaises(ValueError):
            selector.select_quiet_sample(ranked,missing)

    def test_approval_requires_distinct_curators_and_all_pins(self):
        lineage_bytes,review_bytes=b'lineages',b'review'
        args=(lineage_bytes,review_bytes,'a'*64,'b'*64,'c'*64,'d'*64)
        approval={'schema':'solcodex.quiet-screen-sample-approval.v1',
                  'status':'approved',
                  'lineage_ranking_sha256':selector.digest(lineage_bytes),
                  'task_review_sha256':selector.digest(review_bytes),
                  'selector_sha256':'a'*64,'core_selector_sha256':'b'*64,
                  'metadata_helper_sha256':'c'*64,'rules_sha256':'d'*64,
                  'independent_curators':['curator-one','curator-two']}
        selector.verify_approval(*args,approval)
        for changed_args,changed_approval in (
                (args,dict(approval,status='pending')),
                ((b'changed',)+args[1:],approval),
                (args,dict(approval,core_selector_sha256='d'*64)),
                (args,dict(approval,metadata_helper_sha256='e'*64)),
                (args,dict(approval,independent_curators=['same','same'])),
                (args,dict(approval,independent_curators=['Curator',' curator ']))):
            with self.subTest(changed=changed_approval),self.assertRaises(ValueError):
                selector.verify_approval(*changed_args,changed_approval)

    def test_cli_pending_approval_never_writes_sample(self):
        ranked,review=fixture()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            ranking,labels,output=(root/name for name in ('ranking.json','review.json','out.json'))
            ranking.write_text(json.dumps(ranked))
            labels.write_text(json.dumps(review))
            argv=['select_quiet_screen_sample.py','--lineage-ranking',str(ranking),
                  '--task-review',str(labels),'--out',str(output)]
            with mock.patch.object(sys,'argv',argv),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    selector.main()
            self.assertEqual(stopped.exception.code,2)
            self.assertFalse(output.exists())


if __name__=='__main__':
    unittest.main()
