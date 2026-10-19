"""Structured generation contracts; no network or real AI calls."""
import copy
import os
import unittest
from unittest.mock import patch
from agents import (Model, Report, LinkedReport, LinkedFinding, SpanReview, SpanDecision, source_spans,
                    safe_analyze, review_report, EvidenceReview, ReviewDecision, EvidenceQuote)
from tests.test_agents_quality import P, M, Stub, report, finding, payload


class CitationContractTests(unittest.TestCase):
    def model(self):
        with patch.dict(os.environ,{'LLM_BACKEND':'ollama','LLM_MODEL':'fixture'}):
            return Model()

    def test_selected_original_english_text_is_restored_verbatim(self):
        m=self.model()
        raw=SpanReview(decisions=[SpanDecision(item_id='F0',supported=True,portfolio_linked=False,
            duplicate_of='',reason='fixture',span_ids=['Q0C'])])
        with patch.object(m,'_generate',return_value=raw) as call:
            out=m.review('macro',{'sources':[P],'items':[{'item_id':'F0'}]})
        self.assertEqual(out.decisions[0].quotes[0].quote,P['content'])
        self.assertEqual(out.decisions[0].quotes[0].evidence_id,P['id'])
        wire=call.call_args.args[1]
        self.assertNotIn('content',wire['sources'][0])
        self.assertEqual(wire['source_spans'][1]['text'],P['content'])
        self.assertEqual(call.call_args.kwargs['schema_override']['$defs']['SpanDecision']['properties']['span_ids']['items']['enum'],['Q0T','Q0C'])

    def test_unknown_span_rejected(self):
        m=self.model()
        raw=SpanReview(decisions=[SpanDecision(item_id='F0',supported=True,portfolio_linked=False,
            duplicate_of='',reason='fixture',span_ids=['NOT_A_SPAN'])])
        with patch.object(m,'_generate',return_value=raw),self.assertRaises(ValueError):
            m.review('macro',{'sources':[P],'items':[{'item_id':'F0'}]})

    def test_no_source_text_rejected_without_model(self):
        m=self.model()
        with patch.object(m,'_generate',side_effect=AssertionError('no model')):
            out=m.review('macro',{'sources':[{'id':'EMPTY','title':'','content':''}], 'items':[{'item_id':'F0'}]})
        self.assertFalse(out.decisions[0].supported)

    def test_wrong_source_span_still_fails_finding_gate(self):
        m=Stub(report())
        def review(role,payload):
            return EvidenceReview(decisions=[ReviewDecision(item_id='F0',supported=True,
                portfolio_linked=False,duplicate_of='',reason='fixture',
                quotes=[EvidenceQuote(evidence_id=M['id'] or '',field='content',quote=M['content'] or '')])])
        m.review=review
        self.assertEqual(safe_analyze(m,'portfolio',payload())['status'],'failed')

    def linked(self,left=None,right=None):
        item=finding('[ACME] 단기 할인율과 중기 사업 변화의 조건부 연결',kind='scenario').model_dump()
        item.pop('evidence_ids')
        item.update(company_evidence_id=left or P['id'],macro_evidence_id=right or M['id'])
        return LinkedReport(summary='fixture',findings=[LinkedFinding(**item)],conflicts=[],watchlist=[],limitations=[])

    def test_synthesis_explicit_pair_becomes_existing_public_report(self):
        m=self.model()
        with patch.object(m,'_generate',return_value=self.linked()) as call:
            out=m.analyze('synthesis',payload())
        self.assertIsInstance(out,Report)
        self.assertEqual(out.findings[0].evidence_ids,[P['id'],M['id']])
        props=call.call_args.kwargs['schema_override']['$defs']['LinkedFinding']['properties']
        self.assertEqual(props['company_evidence_id']['enum'],[P['id']])
        self.assertEqual(props['macro_evidence_id']['enum'],[M['id']])
        self.assertNotIn('company_evidence_id',out.findings[0].model_dump())

    def test_source_id_from_wrong_group_rejected(self):
        m=self.model()
        with patch.object(m,'_generate',return_value=self.linked(left=M['id'])),self.assertRaises(ValueError):
            m.analyze('synthesis',payload())

    def test_same_id_cannot_fill_both_groups(self):
        m=self.model()
        p=payload(source_groups={'portfolio':[P['id']],'macro':[P['id']]})
        with patch.object(m,'_generate',return_value=self.linked(right=P['id'])),self.assertRaises(ValueError):
            m.analyze('synthesis',p)

    def test_missing_group_does_not_force_fake_pair(self):
        m=self.model()
        with patch.object(m,'_generate',side_effect=AssertionError('no call')),self.assertRaises(ValueError):
            m.analyze('synthesis',payload(source_groups={'portfolio':[P['id']],'macro':[]}))

    def test_model_invented_limitations_not_published(self):
        draft=report()
        draft.limitations=['모든 자료는 과거 빈티지다. FAKE_UNVERIFIED_LIMITATION']
        out=safe_analyze(Stub(draft),'portfolio',payload(warnings=['실제 수집 경고'],coverage='원문 요약'))
        self.assertEqual(out['status'],'ok')
        self.assertNotIn('FAKE_UNVERIFIED_LIMITATION',str(out['report']))
        self.assertIn('실제 수집 경고',out['report']['limitations'])
        self.assertFalse(out['diagnostic_draft']['verified'])

    def test_new_contract_transport_uses_schema_and_keeps_single_attempt(self):
        import json
        m=self.model()
        raw={'decisions':[dict(item_id='F0',supported=True,portfolio_linked=False,
            duplicate_of='',reason='fixture',span_ids=['Q0C'])]}
        with patch('agents.request',return_value={'done':True,'message':{'content':json.dumps(raw)}}) as req:
            out=m.review('macro',{'sources':[P],'items':[{'item_id':'F0'}]})
        self.assertEqual(out.decisions[0].quotes[0].quote,P['content'])
        self.assertEqual(req.call_args.kwargs['attempts'],1)


if __name__=='__main__':unittest.main()
