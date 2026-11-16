"""Actual partial-run failure patterns: small-model contracts, truncation, and blocked ETF pages."""
from __future__ import annotations
import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agents import Model, CompactMacroFinding
from common import NetworkError, evidence
from evidence_grounding import numeric_issues
from deep_research import CompactInsight, run_deep_research
from official_sources import collect_official, OfficialError
from research_tools import replay_report
from test_report_fixes import local_model, response, fred_source
from test_research import bundles, FixtureModel
from test_transfer import full

ENV={'OLLAMA_NUM_CTX':'16384','OLLAMA_NUM_PREDICT':'4800','SEC_USER_AGENT':'Fixture tests@local.example'}


def compact_insight(**overrides):
    item={'topic':'growth','observation_ids':['O0'],
          'interpretation':'관측의 지속 여부를 확인해야 합니다.',
          'mechanism':'매출과 현금 회수로 전달될 수 있습니다.',
          'countercase':'현금 회수가 지연될 수 있습니다.','impact':'uncertain'}
    # Mock transport text is a fixture, not an investment judgment.
    item.update(overrides);return item


class PartialRecoveryTests(unittest.TestCase):
    def test_real_run_style_invented_numeric_monitor_has_no_wire_field(self):
        fields=CompactMacroFinding.model_fields
        self.assertNotIn('revisit_when',fields)
        item=CompactMacroFinding(observation_id='O0',interpretation='금리가 5.0%를 넘을 때',counterpoint='다를 수 있습니다.')
        self.assertTrue(numeric_issues(item.interpretation,[fred_source()]))

    def test_invented_year_in_issuer_narrative_is_rejected(self):
        item=CompactInsight(**compact_insight(mechanism='2027년에 매출이 늘어납니다.'))
        self.assertTrue(numeric_issues(item.mechanism,[fred_source()]))

    def test_long_small_model_output_is_constrained(self):
        with self.assertRaises(ValidationError):CompactInsight(**compact_insight(countercase='가'*601))

    def test_macro_repair_is_bounded_and_preserves_actual_observed_values(self):
        source=fred_source();bad={'findings':[{'observation_id':'O0','interpretation':'금리는 4.86%다.'}]}
        good={'findings':[{'observation_id':'O0','interpretation':'금리 상승은 조달비용 부담으로 전달될 수 있습니다.', 'counterpoint':'성장 개선이 부담을 상쇄할 수 있습니다.'}]}
        with patch.dict(os.environ,ENV),patch('agents.request',side_effect=[response(bad),response(good)]) as req:
            report=local_model().analyze('macro',{'sources':[source]})
        self.assertEqual(req.call_count,2)
        self.assertIn('2026-11-12 = 4.86',report.findings[0].claim)
        self.assertNotIn('5.0',report.findings[0].revisit_when)
        self.assertEqual(report.findings[0].kind,'inference')

    def test_issuer_truncation_gets_one_short_correction(self):
        snap,p,_=bundles()
        from evidence_grounding import observation_choices
        truncated={'done':True,'done_reason':'length','message':{'content':'{"insights":['}}
        with patch.dict(os.environ,ENV),patch('agents.request',side_effect=[truncated,response({'insights':[compact_insight()]})]) as req:
            draft=local_model().research({'positions':snap['positions'],'sources':p['sources'],
                'observation_choices':observation_choices(p['sources'])})
        self.assertEqual(req.call_count,2)
        self.assertEqual(req.call_args.kwargs['body']['options']['num_predict'],4800)
        self.assertNotIn('2027',draft.insights[0].monitor)

    def test_repeated_invalid_compact_output_fails_closed_after_two_calls(self):
        invalid=response({'findings':[{'observation_id':'O0','interpretation':'금리 설명'}]})
        with patch.dict(os.environ,ENV),patch('agents.request',return_value=invalid) as req,self.assertRaises(ValidationError):
            local_model().analyze('macro',{'sources':[fred_source()]})
        self.assertEqual(req.call_count,2)

    def test_http_failure_is_not_retried_as_format_repair(self):
        with patch.dict(os.environ,ENV),patch('agents.request',side_effect=NetworkError(503)) as req,self.assertRaises(NetworkError):
            local_model().analyze('macro',{'sources':[fred_source()]})
        self.assertEqual(req.call_count,1)

    def test_issuer_json_contract_limits_output_to_two_findings(self):
        from evidence_grounding import observation_choices
        snap,p,_=bundles()
        with patch.dict(os.environ,ENV),patch('agents.request',return_value=response({'insights':[]})) as req:
            local_model().research({'positions':snap['positions'],'sources':p['sources'],'observation_choices':observation_choices(p['sources'])})
        body=req.call_args.kwargs['body'];schema=body['format']
        self.assertEqual(schema['properties']['insights']['maxItems'],2)
        self.assertNotIn('monitor',schema['$defs']['CompactInsight']['properties'])
        self.assertNotIn('horizon',schema['$defs']['CompactInsight']['properties'])

    def test_rss_headline_is_not_preferred_over_indicator(self):
        headline=evidence('Bank of Japan','Speech title','https://example.com/speech','Speech title')
        with patch.dict(os.environ,ENV),patch('agents.request',return_value=response({'findings':[]})) as req:
            local_model().analyze('macro',{'sources':[headline,fred_source()]})
        payload=json.loads(req.call_args.kwargs['body']['messages'][1]['content'])
        self.assertEqual(len(payload['observation_choices']),1)
        self.assertNotIn('Speech title',str(payload))

    def fund_case(self,symbol,identity=None):
        snap,_,_=bundles();snap['positions'][0]['symbol']=symbol
        expected='Global X Uranium ETF' if symbol=='URA' else 'Schwab U.S. Dividend Equity ETF'
        text=f'<p>{identity or expected} Ticker: {symbol}. Investment objective: this fixture fund follows an index and does not promise future investment returns.</p>'
        return snap,text.encode()

    def test_etf_primary_403_uses_identity_checked_sec_document(self):
        snap,raw=self.fund_case('SCHD')
        def fetch(url,**kwargs):
            if 'schwabassetmanagement' in url:raise OfficialError('IR','HTTP_403')
            self.assertEqual(kwargs['provider'],'SEC')
            self.assertIn('tests@local.example',kwargs['headers']['User-Agent'])
            return raw
        with patch.dict(os.environ,ENV),patch('official_sources.PublicCache') as cache,patch('official_sources.SEC') as company_api:
            cache.return_value.get.side_effect=fetch
            result=collect_official(snap,{})
        company_api.assert_not_called()
        self.assertTrue(result['dossiers']['SCHD']['source_ids'])
        self.assertEqual(result['sources'][0]['document_date'],'2026-02-27')
        self.assertIn('현재 편입비중 자료가 아님',result['sources'][0]['content_scope'])

    def test_etf_primary_429_uses_sec_document(self):
        snap,raw=self.fund_case('URA')
        with patch.dict(os.environ,ENV),patch('official_sources.PublicCache') as cache:
            cache.return_value.get.side_effect=[OfficialError('IR','HTTP_429'),raw]
            result=collect_official(snap,{})
        self.assertTrue(result['dossiers']['URA']['source_ids'])
        self.assertTrue(all(s['provider']=='SEC' for s in result['sources']))

    def test_wrong_fund_document_is_never_accepted(self):
        snap,raw=self.fund_case('URA',identity='Other Fund')
        with patch.dict(os.environ,ENV),patch('official_sources.PublicCache') as cache:
            cache.return_value.get.side_effect=[OfficialError('IR','HTTP_429'),raw]
            result=collect_official(snap,{})
        self.assertFalse(result['dossiers']['URA']['source_ids'])
        self.assertIn('FUND_DOCUMENT_IDENTITY_MISMATCH',str(result['warnings']))

    def test_rejected_issuer_draft_is_saved_as_unverified_diagnostic(self):
        snap,p,m=bundles();result=run_deep_research(FixtureModel(reject=True),snap,p,m)
        company=result['research_v2']['companies'][0]
        self.assertFalse(company['diagnostic_draft']['verified'])
        self.assertTrue(company['diagnostic_draft']['report']['insights'])
        self.assertFalse(company['insights']);self.assertFalse(result['complete'])

    def test_replay_reuses_sources_without_collection_or_broker(self):
        snap,p,m=bundles();previous=run_deep_research(FixtureModel(),snap,p,m)
        raw=full();raw['positions'][0]['symbol']='TEST'
        with patch('official_sources.collect_official',side_effect=AssertionError('network collection')),patch('broker.Toss',side_effect=AssertionError('broker')):
            result,valued=replay_report(previous,raw,['TEST'],FixtureModel())
        self.assertTrue(result['complete'])
        self.assertEqual(result['research_v2']['replay']['symbols'],['TEST'])
        self.assertIn('새 시세·공시·뉴스를 수집하지',str(result['warnings']))

    def test_replay_rejects_unknown_symbol_before_model(self):
        snap,p,m=bundles();previous=run_deep_research(FixtureModel(),snap,p,m)
        with self.assertRaises(ValueError):replay_report(previous,full(),['UNKNOWN'],FixtureModel())

    def test_replay_does_not_turn_failed_ai_review_into_complete(self):
        snap,p,m=bundles();previous=run_deep_research(FixtureModel(),snap,p,m)
        raw=full();raw['positions'][0]['symbol']='TEST'
        result,_=replay_report(previous,raw,['TEST'],FixtureModel(reject=True))
        self.assertFalse(result['complete'])

    def test_compact_synthesis_restores_source_observations_and_ticker(self):
        snap,p,m=bundles();left=p['sources'][0]['id'];right=m['sources'][0]['id']
        data={'findings':[{'company_evidence_id':left,'macro_evidence_id':right,
            'mechanism':'금리 부담이 기업의 조달비용에 영향을 줄 수 있습니다.',
            'counterpoint':'현금 회수 개선이 상쇄할 수 있습니다.','horizon':'both'}]}
        payload={'positions':snap['positions'],'sources':p['sources']+m['sources'],
                 'source_groups':{'portfolio':[left],'macro':[right]},'compact_contract':True}
        with patch.dict(os.environ,ENV),patch('agents.request',return_value=response(data)):
            report=local_model().analyze('synthesis',payload)
        self.assertIn('[TEST]',report.findings[0].claim)
        self.assertIn('100',report.findings[0].claim)
        self.assertEqual(report.findings[0].evidence_ids,[left,right])


if __name__=='__main__':unittest.main()
