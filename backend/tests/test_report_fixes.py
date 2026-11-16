"""Regression cases from the first real report; no network or real LLM required."""
from __future__ import annotations
import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import evidence
from agents import (Model, Report, Finding, review_report, safe_analyze, ContextBudgetError, error_code)
from deep_research import analyze_issuer, run_deep_research
from evidence_grounding import observation_choices, numeric_issues, indicator_issues
from test_research import FixtureModel, bundles
from official_sources import collect_official
from collectors import _yahoo_fetch, yahoo, reset_yahoo_cache


def fred_source(sid='DGS2'):
    return evidence('FRED', '2-Year Treasury', 'https://fred.stlouisfed.org/series/' + sid,
        json.dumps({'series_id':sid, 'frequency':'Daily', 'units':'Percent',
            'observations':[{'date':'2026-11-12','value':'4.86'},
                            {'date':'2026-11-10','value':'4.73'},
                            {'date':'2026-11-09','value':'4.56'}]}),
        kind='indicator', observation_period='2026-11-12')


def local_model():
    model = Model.__new__(Model)
    model.backend = 'ollama'; model.model = 'fixture'; model.telemetry = []
    return model


def response(data):
    return {'done':True, 'done_reason':'stop', 'message':{'content':json.dumps(data,ensure_ascii=False)}}


class ReportFixTests(unittest.TestCase):
    def test_period_metadata_is_valid_evidence_for_dates(self):
        s = evidence('T','financial','https://example.com','revenue 100 USD',
                     kind='financial',period_start='2026-07-01',period_end='2026-09-30',basis='quarter')
        self.assertFalse(numeric_issues('2026-07-01 ~ 2026-09-30 매출 100 USD', [s]))
        self.assertTrue(numeric_issues('2026-10-19 매출 100 USD', [s]))

    def test_unknown_amount_is_still_rejected(self):
        s=fred_source()
        self.assertTrue(numeric_issues('금리 9.99%', [s]))

    def test_report_date_value_swap_rejected(self):
        self.assertTrue(indicator_issues('2026-11-12 금리 4.73%', [fred_source()]))
        self.assertFalse(indicator_issues('2026-11-12 금리 4.86%', [fred_source()]))

    def test_report_three_month_trend_from_daily_points_rejected(self):
        self.assertTrue(indicator_issues('3개월 상승 추세', [fred_source()]))

    def test_report_pce_is_not_real_consumption(self):
        self.assertTrue(indicator_issues('실질 소비 지수', [fred_source('PCEPILFE')]))

    def test_positive_boolean_cannot_override_negative_reason(self):
        snap,p,_=bundles(); model=FixtureModel()
        original_review=model.review
        def contradictory(role,payload):
            reviewed=original_review(role,payload)
            reviewed.decisions[0].reason='직접적인 지지가 없음. 따라서 supported=false로 판정.'
            return reviewed
        model.review=contradictory
        draft=model.analyze('portfolio',{'sources':p['sources']})
        checked,audit=review_report(model,'portfolio',draft,{'sources':p['sources'],'positions':snap['positions']})
        self.assertFalse(checked.findings);self.assertFalse(audit[0]['accepted'])

    def test_quote_only_fund_never_calls_research(self):
        snap,p,_=bundles(); s=p['sources'][0];s['kind']='quote'
        dossier={'asset_type':'fund','sector':'fund','records':[],'source_ids':[s['id']]}
        with patch.object(FixtureModel,'research') as call:
            detail,stage=analyze_issuer(FixtureModel(),snap['positions'][0],dossier,[s],{})
        call.assert_not_called();self.assertEqual(stage['status'],'failed')

    def test_new_research_observations_are_restored_by_code(self):
        snap,p,_=bundles(); choices=observation_choices(p['sources'])
        data={'insights':[{'topic':'growth',
            'observation_ids':[choices[0]['observation_id']], 'interpretation':'수요 지속 여부를 확인한다.',
            'mechanism':'수요는 현금 회수로 이어질 수 있다.',
            'countercase':'회수가 지연될 수 있다.', 'impact':'uncertain'}]}
        with patch.dict(os.environ,{'OLLAMA_NUM_CTX':'16384','OLLAMA_NUM_PREDICT':'4800'}), patch('agents.request',return_value=response(data)):
            draft=local_model().research({'sources':p['sources'],'observation_choices':choices})
        self.assertEqual(draft.insights[0].observation,choices[0]['text'])
        self.assertEqual(draft.insights[0].evidence_ids,[p['sources'][0]['id']])

    def test_unknown_selected_observation_never_published(self):
        snap,p,_=bundles();choices=observation_choices(p['sources'])
        bad={'insights':[{'topic':'growth','observation_ids':['DOES_NOT_EXIST'],
            'interpretation':'x','mechanism':'x','countercase':'x','impact':'uncertain'}]}
        with patch.dict(os.environ,{'OLLAMA_NUM_CTX':'16384','OLLAMA_NUM_PREDICT':'4800'}), patch('agents.request',return_value=response(bad)), self.assertRaises(ValueError):
            local_model().research({'sources':p['sources'],'observation_choices':choices})

    def test_macro_observations_keep_exact_date_and_values(self):
        s=fred_source()
        answer={'findings':[{'observation_id':'O0','interpretation':'장기 현금흐름의 할인율 부담이 될 수 있다.',
            'counterpoint':'성장 기대도 확인해야 한다.'}]}
        with patch.dict(os.environ,{'OLLAMA_NUM_CTX':'16384','OLLAMA_NUM_PREDICT':'4800'}), patch('agents.request',return_value=response(answer)):
            draft=local_model().analyze('macro',{'sources':[s]})
        self.assertIn('2026-11-12 = 4.86',draft.findings[0].claim)
        self.assertIn('2026-11-10 = 4.73',draft.findings[0].claim)

    def test_macro_is_capped_before_review(self):
        snap,p,_=bundles()
        model=FixtureModel();original=model.analyze; seen=[]
        def analyze(role,payload):
            report=original(role,payload)
            base=report.findings[0]
            report.findings=[base.model_copy(update={'subject':'각기다른논점'+chr(65+i),'claim':base.claim+chr(65+i)}) for i in range(8)]
            return report
        original_review=model.review
        def review(role,payload):
            seen.extend(payload['items']);return original_review(role,payload)
        model.analyze=analyze;model.review=review
        safe_analyze(model,'macro',{'sources':p['sources']})
        self.assertEqual(len(seen),4)

    def test_synthesis_receives_accepted_claim_text(self):
        snap,p,m=bundles();model=FixtureModel();original=model.analyze;seen=[]
        def analyze(role,payload):
            if role=='synthesis':seen.append(copy.deepcopy(payload))
            return original(role,payload)
        model.analyze=analyze
        run_deep_research(model,snap,p,m)
        self.assertIn('claim',seen[0]['portfolio_agent']['findings'][0])
        self.assertIn('claim',seen[0]['macro_agent']['findings'][0])

    def test_yahoo_news_failure_keeps_valid_quote(self):
        chart={'chart':{'result':[{'meta':{'regularMarketPrice':10,'regularMarketTime':1794826800,'currency':'USD'}}]}}
        with patch('collectors.request',return_value=chart) as call, patch('collectors.rss',side_effect=TimeoutError):
            sources,warnings=_yahoo_fetch('TEST',8)
        self.assertEqual(sources[0]['kind'],'quote');self.assertTrue(warnings)
        self.assertEqual(call.call_count,1)

    def test_yahoo_empty_chart_is_explicit_failure(self):
        with patch('collectors.request',return_value={'chart':{'result':None}}):
            sources,warnings=_yahoo_fetch('TEST',0)
        self.assertFalse(sources);self.assertTrue(warnings)

    def test_refresh_quote_is_reused_when_collecting_news(self):
        reset_yahoo_cache()
        with patch('collectors._yahoo_worker',side_effect=[([{'kind':'quote'}],[]),([],['news unavailable'])]) as worker:
            yahoo('TEST',0); result=yahoo('TEST',8)
        self.assertTrue(worker.call_args.kwargs['news_only'])
        self.assertEqual(result[0],[{'kind':'quote'}])
        reset_yahoo_cache()

    def test_known_fund_uses_official_page_without_company_api(self):
        snap,_,_=bundles();snap['positions'][0]['symbol']='URA'
        with patch('official_sources.PublicCache') as cache, patch('official_sources.excerpt_sources',return_value=[]) as excerpts, patch('official_sources.SEC') as sec:
            cache.return_value.get.return_value=b'official page'
            collect_official(snap,{})
        sec.assert_not_called();self.assertIn('globalxetfs.com/funds/ura',excerpts.call_args.kwargs['url'])

    def test_explicit_empty_documents_respected(self):
        snap,_,_=bundles();snap['positions'][0]['symbol']='URA'
        with patch('official_sources.PublicCache') as cache:
            collect_official(snap,{'instruments':{'URA':{'documents':[]}}})
        cache.return_value.get.assert_not_called()

    def test_error_diagnostics_distinguish_context_and_truncation(self):
        self.assertEqual(error_code(ContextBudgetError()),'MODEL_CONTEXT_BUDGET')
        self.assertEqual(error_code(ValueError('local model output truncated; increase OLLAMA_NUM_PREDICT')),'MODEL_OUTPUT_TRUNCATED')


if __name__=='__main__':unittest.main()
