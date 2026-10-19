"""Offline regression tests for evidence gating; no external LLM or broker calls."""
import copy
import unittest
from typing import Literal, cast
from unittest.mock import patch
from agents import (Report, Finding, Conflict, EvidenceReview, ReviewDecision, EvidenceQuote,
                    Model, safe_analyze, run_agents, checked_sources, validate_references, has_symbol)
from common import evidence, now

P = evidence('fixture', 'ACME contract growth', 'https://example.com/p', 'ACME orders increased in Q2.')
M = evidence('fixture', 'Higher long-term yields', 'https://example.com/m', 'Ten-year yields rose during Q2.')
SOURCES = [P, M]


def finding(claim='ACME orders increased in Q2.', ids: list[str] | None = None,
            kind: Literal['fact', 'inference', 'scenario'] = 'fact'):
    return Finding(subject='ACME', claim=claim, kind=kind,
           evidence_ids=ids if ids is not None else [cast(str, P['id'])], confidence='high',
                   counterpoint='추가 자료에서는 다른 해석이 가능할 수 있습니다.',
                   revisit_when='다음 분기 수주 공시를 확인합니다.')


def report(findings=None, conflicts=None):
    return Report(summary='REMOVED CLAIM MUST NOT SURVIVE',
                  findings=findings if findings is not None else [finding()],
                  conflicts=conflicts or [], watchlist=['UNREVIEWED WATCHLIST'], limitations=[])


class Stub:
    backend = 'ollama'
    def __init__(self, draft=None, edit=None):
        self.draft = draft
        self.edit = edit
        self.payloads = []
    def local_parallel(self):
        return False
    def analyze(self, role, payload):
        self.payloads.append((role, copy.deepcopy(payload)))
        if self.draft is not None:
            return copy.deepcopy(self.draft)
        if role == 'macro':
            return report([finding('Ten-year yields rose during Q2.', [cast(str, M['id'])])])
        if role == 'synthesis':
            return report([finding('[ACME] 企業受注と金利 → 中期利益と短期割引率の相殺の可能性',
                                   [cast(str, P['id']), cast(str, M['id'])], 'scenario')])
        return report()
    def review(self, role, payload):
        self.payloads.append((role + '_review', copy.deepcopy(payload)))
        sources = {s['id']: s for s in payload['sources']}
        decisions = [ReviewDecision(
            item_id=x['item_id'], supported=True, portfolio_linked=(role == 'synthesis'),
            duplicate_of='', reason='fixture decision, not a real semantic judgment',
            quotes=[EvidenceQuote(evidence_id=eid, field='content', quote=sources[eid]['content'])
                    for eid in x['evidence_ids']]) for x in payload['items']]
        if self.edit:
            self.edit(decisions)
        return EvidenceReview(decisions=decisions)


def payload(**extra):
    return {'sources': copy.deepcopy(SOURCES), 'positions': [{'symbol': 'ACME'}],
            'source_groups': {'portfolio': [P['id']], 'macro': [M['id']]}, **extra}


class QualityTests(unittest.TestCase):
    def test_valid_claim_and_summary_rebuilt(self):
        out = safe_analyze(Stub(report()), 'portfolio', payload())
        self.assertEqual(out['status'], 'ok')
        self.assertNotIn('REMOVED', out['report']['summary'])
        self.assertNotIn('UNREVIEWED', str(out['report']['watchlist']))
        self.assertEqual(out['report']['findings'][0]['confidence'], 'medium')

    def test_one_invalid_id_drops_entire_finding(self):
        r = validate_references(report([finding(ids=[cast(str, P['id']), 'BAD'])]), SOURCES)
        self.assertEqual(r.findings, [])
        self.assertNotIn('REMOVED', r.summary)

    def test_supported_false_removes_unrelated_citation(self):
        def edit(ds): ds[0].supported = False
        out = safe_analyze(Stub(report(), edit), 'portfolio', payload())
        self.assertEqual(out['status'], 'failed')
        self.assertEqual(out['report']['findings'], [])

    def test_fabricated_quote_rejected(self):
        def edit(ds): ds[0].quotes[0].quote = 'This never appeared in the source.'
        out = safe_analyze(Stub(report(), edit), 'portfolio', payload())
        self.assertEqual(out['status'], 'failed')

    def test_every_cited_source_needs_quote(self):
        def edit(ds): ds[0].quotes = ds[0].quotes[:1]
        out = safe_analyze(Stub(report([finding(ids=[cast(str, P['id']), cast(str, M['id'])])]), edit), 'portfolio', payload())
        self.assertEqual(out['status'], 'failed')

    def test_missing_verdict_fails_closed(self):
        def edit(ds): ds.clear()
        out = safe_analyze(Stub(report(), edit), 'portfolio', payload())
        self.assertEqual(out['status'], 'failed')
        self.assertEqual(out['report']['findings'], [])

    def test_duplicate_verdict_fails_closed(self):
        def edit(ds): ds.append(ds[0])
        self.assertEqual(safe_analyze(Stub(report(), edit), 'portfolio', payload())['status'], 'failed')

    def test_unknown_verdict_fails_closed(self):
        def edit(ds): ds[0].item_id = 'F999'
        self.assertEqual(safe_analyze(Stub(report(), edit), 'portfolio', payload())['status'], 'failed')

    def test_semantic_duplicate_removed(self):
        def edit(ds): ds[1].duplicate_of = 'F0'
        out = safe_analyze(Stub(report([finding(), finding('Q2 order intake at ACME grew.')]), edit),
                           'portfolio', payload())
        self.assertEqual(len(out['report']['findings']), 1)
        self.assertTrue(any('중복' in x for x in out['report']['limitations']))

    def test_exact_duplicate_removed(self):
        out = safe_analyze(Stub(report([finding(), finding()])), 'portfolio', payload())
        self.assertEqual(len(out['report']['findings']), 1)

    def test_distinct_claims_preserved(self):
        out = safe_analyze(Stub(report([finding(), finding('ACME orders fell in Q1.')])), 'portfolio', payload())
        self.assertEqual(len(out['report']['findings']), 2)

    def test_rejected_duplicate_representative_does_not_hide_valid_claim(self):
        def edit(ds):
            ds[0].supported = False
            ds[1].duplicate_of = 'F0'
        out = safe_analyze(Stub(report([finding(), finding('Different supported claim')]), edit),
                           'portfolio', payload())
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(len(out['report']['findings']), 1)

    def test_synthesis_needs_both_source_groups(self):
        draft = report([finding('[ACME] 단기·중기 경로', [cast(str, P['id'])], 'scenario')])
        self.assertEqual(safe_analyze(Stub(draft), 'synthesis', payload())['status'], 'failed')

    def test_one_source_cannot_be_both_sides(self):
        draft = report([finding('[ACME] 단기·중기 경로', [cast(str, P['id'])], 'scenario')])
        p = payload(source_groups={'portfolio': [P['id']], 'macro': [P['id']]})
        self.assertEqual(safe_analyze(Stub(draft), 'synthesis', p)['status'], 'failed')

    def test_synthesis_needs_actual_holding(self):
        draft = report([finding('[OTHER] 단기·중기 경로', [cast(str, P['id']), cast(str, M['id'])], 'scenario')])
        self.assertEqual(safe_analyze(Stub(draft), 'synthesis', payload())['status'], 'failed')

    def test_synthesis_link_semantic_rejection(self):
        def edit(ds): ds[0].portfolio_linked = False
        draft = report([finding('[ACME] 단기·중기 경로', [cast(str, P['id']), cast(str, M['id'])], 'scenario')])
        self.assertEqual(safe_analyze(Stub(draft, edit), 'synthesis', payload())['status'], 'failed')

    def test_reviewer_failure_never_releases_draft(self):
        stub = Stub(report())
        def broken(role, payload): raise TimeoutError('secret request contents')
        stub.review = broken
        out = safe_analyze(stub, 'portfolio', payload())
        self.assertEqual(out['status'], 'failed')
        self.assertNotIn('secret', str(out))
        self.assertEqual(out['report']['findings'], [])

    def test_conflicts_are_semantically_reviewed(self):
        c = Conflict(issue='Conflict', portfolio_view='growth', macro_view='rates',
                     assessment='uncertain', evidence_ids=[cast(str, P['id']), cast(str, M['id'])])
        def edit(ds): ds[-1].supported = False
        out = safe_analyze(Stub(report(conflicts=[c]), edit), 'synthesis', payload())
        self.assertEqual(out['report']['conflicts'], [])

    def test_conflicting_source_id_raises(self):
        bad = {**P, 'content': 'different content'}
        with self.assertRaises(ValueError): checked_sources([P, bad])
        self.assertEqual(len(checked_sources([P, copy.deepcopy(P)])), 1)

    def test_symbol_boundaries(self):
        self.assertFalse(has_symbol('DATA', 'A'))
        self.assertTrue(has_symbol('[A]', 'A'))
        self.assertFalse(has_symbol('[10059300]', '005930'))

    def test_full_pipeline_and_macro_isolation(self):
        model = Stub()
        snapshot = {'as_of': now(), 'positions': [dict(symbol='ACME', name='ACME', market='NASDAQ',
                    currency='USD', weight_pct=100, price_quality='fixture')]}
        p = {'sources': [P], 'warnings': []}
        m = {'sources': [M], 'warnings': [], 'positions': [{'symbol': 'LEAK'}], 'account': 'secret'}
        out = run_agents(model, snapshot, p, m)
        self.assertTrue(out['complete'])
        self.assertEqual(len(model.payloads), 6)
        for role, packet in model.payloads:
            if role.startswith('macro'):
                self.assertNotIn('positions', packet)
                self.assertNotIn('account', packet)
        self.assertEqual(len(p['sources']), 1)

    def test_failed_half_prevents_synthesis(self):
        def edit(ds): ds[0].supported = False
        model = Stub(edit=edit)
        snapshot = {'as_of': now(), 'positions': []}
        out = run_agents(model, snapshot, {'sources': [P], 'warnings': []}, {'sources': [M], 'warnings': []})
        self.assertFalse(out['complete'])
        synthesis = cast(dict[str, object], out['synthesis'])
        self.assertEqual(synthesis['status'], 'failed')
        self.assertEqual(cast(dict[str, object], synthesis['report'])['findings'], [])
        self.assertFalse(any(role == 'synthesis' for role, _ in model.payloads))

    def test_existing_server_report_validator_accepts_new_output(self):
        # Execute the exact uploaded server validator without importing FastAPI.
        import ast
        from datetime import datetime
        from urllib.parse import urlsplit
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'server.py').read_text()
        tree = ast.parse(source)
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('report', 'timestamp')]
        namespace = {'datetime': datetime, 'urlsplit': urlsplit, 'Report': Report}
        exec(compile(ast.Module(body=[cast(ast.stmt, n) for n in nodes], type_ignores=[]), 'server.py', 'exec'), namespace)
        model = Stub()
        snapshot = {'as_of': now(), 'positions': [dict(symbol='ACME', name='ACME', market='NASDAQ',
                    currency='USD', weight_pct=100, price_quality='fixture')]}
        out = run_agents(model, snapshot, {'sources': [P], 'warnings': []},
                         {'sources': [M], 'warnings': []})
        accepted = namespace['report'](out)
        self.assertTrue(accepted['complete'])
        self.assertEqual(len(accepted['synthesis']['report']['findings']), 1)

    def test_real_ollama_transport_uses_review_schema(self):
        import os
        with patch.dict(os.environ, {'LLM_BACKEND': 'ollama', 'LLM_MODEL': 'fixture'}):
            model = Model()
        with patch('agents.request', return_value={'done': True, 'message': {'content': '{"decisions":[]}'}}) as req:
            reviewed = model.review('portfolio', {'sources': [P], 'items': [{'item_id': 'F0'}]})
        self.assertEqual(reviewed.decisions, [])
        self.assertIn('decisions', req.call_args.kwargs['body']['format']['properties'])


if __name__ == '__main__':
    unittest.main()

