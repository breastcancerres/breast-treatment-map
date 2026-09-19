"""Behavioural checks for medical status integrity and repeatable updates."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from core import (new_state,proof_for,apply_approval,apply_trial,normalize_trial,
                  source_error,discover_fda,add_publication)

CATALOG=json.loads((ROOT/'data/catalog.json').read_text())
RULE=next(s for s in CATALOG['approvalSources'] if s['id']=='fda-imlu-combo-2026')
DOCUMENT=(ROOT/'tests/fixtures/imlunestrant-combo.html').read_text()
NOW='2026-09-19T10:00:00Z'


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.state=new_state('2026-09-19')

    def apply(self,document=DOCUMENT,rule=RULE):
        return apply_approval(self.state,rule,proof_for(rule,document),NOW)

    def test_real_announcement_moves_only_intended_combination(self):
        self.state['approvals']['imlu-monotherapy']={
            'status':'approved','jurisdiction':'US','sourceId':'fda-imlu-mono-2025',
            'confirmedOn':'2025-09-25','effectiveOn':'2025-09-25','needsReview':False}
        baseline=copy.deepcopy(self.state['approvals']['imlu-monotherapy'])
        self.assertTrue(self.apply())
        self.assertIn('imlu-abemaciclib',self.state['approvals'])
        self.assertNotIn('imlu-adjuvant',self.state['approvals'])
        self.assertEqual(baseline,self.state['approvals']['imlu-monotherapy'])

    def test_replaying_real_announcement_is_idempotent(self):
        self.apply()
        approvals=copy.deepcopy(self.state['approvals'])
        events=copy.deepcopy(self.state['events'])
        self.apply()
        self.assertEqual(self.state['approvals'],approvals)
        self.assertEqual(self.state['events'],events)
        self.assertEqual(len(events),1)

    def test_undetected_population_change_cannot_grant_approval(self):
        self.assertFalse(self.apply(DOCUMENT.replace('ESR1','BRCA1')))
        self.assertFalse(self.state['approvals'])
        self.assertTrue(next(iter(self.state['events'].values()))['reviewRequired'])

    def test_document_change_preserves_history_but_flags_review(self):
        self.apply()
        self.apply(DOCUMENT.replace('at least one line','at least two lines'))
        approved=self.state['approvals']['imlu-abemaciclib']
        self.assertEqual(approved['effectiveOn'],'2026-09-18')
        self.assertTrue(approved['needsReview'])
        self.apply()
        self.assertTrue(self.state['approvals']['imlu-abemaciclib']['needsReview'])

    def test_fetch_failure_does_not_remove_approval(self):
        self.apply()
        before=copy.deepcopy(self.state['approvals'])
        source_error(self.state,'fda:'+RULE['id'],RULE['url'],NOW,'HTTP 503')
        self.assertEqual(self.state['approvals'],before)
        self.assertEqual(self.state['sources']['fda:'+RULE['id']]['status'],'error')

    def test_completed_or_positive_sounding_trial_is_not_an_approval(self):
        trial={'id':'NCT05514054','phase':['PHASE3'],'status':'COMPLETED',
               'title':'Positive phase III results','hasResults':True}
        definition=next(t for t in CATALOG['trials'] if t['id']==trial['id'])
        apply_trial(self.state,trial,definition,NOW)
        self.assertFalse(self.state['approvals'])
        self.assertTrue(self.state['trials'][trial['id']]['hasResults'])

    def test_publication_deduplication_does_not_infer_clinical_success(self):
        paper={'pmid':'00000001','title':'Positive phase III results','trialIds':['NCT05514054'],
               'drugIds':['imlunestrant'],'publicationDate':'2026 Sep'}
        add_publication(self.state,copy.deepcopy(paper),NOW)
        add_publication(self.state,copy.deepcopy(paper),NOW)
        self.assertEqual(len(self.state['publications']),1)
        self.assertEqual(len(self.state['events']),1)
        self.assertFalse(self.state['approvals'])

    def test_unrecognised_fda_document_is_queued_not_promoted(self):
        doc='<a href="/drugs/new-imlunestrant-early-breast-cancer">FDA approves imlunestrant for early breast cancer</a>'
        discover_fda(self.state,CATALOG,doc,NOW)
        discover_fda(self.state,CATALOG,doc,NOW)
        self.assertEqual(len(self.state['events']),1)
        self.assertFalse(self.state['approvals'])

    def test_restriction_event_flags_affected_drug(self):
        self.apply()
        doc='<a href="/drugs/new-withdrawal">FDA withdraws imlunestrant breast cancer indication</a>'
        discover_fda(self.state,CATALOG,doc,NOW)
        self.assertTrue(self.state['approvals']['imlu-abemaciclib']['needsReview'])

    def test_untrusted_domain_cannot_supply_an_approval(self):
        rule=dict(RULE,url='https://example.org/fake')
        with self.assertRaises(ValueError):
            self.apply(rule=rule)

    def test_wrong_registry_identity_rejected(self):
        raw={'protocolSection':{'identificationModule':{'nctId':'NCT00000000'},
             'designModule':{'phases':['PHASE3']},'statusModule':{'overallStatus':'COMPLETED'}}}
        with self.assertRaises(ValueError):
            normalize_trial(raw,'NCT05514054')

    def test_future_event_is_not_published_early(self):
        self.state['asOf']='2026-09-17'
        self.assertFalse(self.apply())
        self.assertFalse(self.state['approvals'])


if __name__=='__main__':
    unittest.main(verbosity=2)
