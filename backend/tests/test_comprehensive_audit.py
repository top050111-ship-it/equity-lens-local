"""Failure diagnostics, source provenance, report commits and valuation regressions.

All model/HTTP calls here are fixed doubles. Live Qwen evidence is documented
separately; passing these tests is never reported as real-model accuracy.
"""
from __future__ import annotations
import copy
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
from fastapi.testclient import TestClient

import app
import server
import research_runtime
from agents import Model, Report, CompactMacroDraft, error_code
from broker import value_snapshot,LiveQuotes
from common import NetworkError, evidence, load_config, now, output_lock, stamp, write_private
from deep_research import run_deep_research, validate_deep_result, analyze_issuer, ResearchDraft
from evidence_grounding import QualitativeText, numeric_issues, indicator_issues, financial_period_issues, evidence_scope_issues
from financials import sec_records, deduplicate_records, dart_records
from holdings_cache import CachedToss
from model_runtime import ModelOutputError, transport_schema
from research_tools import replay_report
from test_report_fixes import local_model, response, fred_source
from test_research import FixtureModel, bundles, record, sec_payload, sec_row, dart_row, insight
from test_transfer import full, config

ENV={'OLLAMA_NUM_CTX':'16384','OLLAMA_NUM_PREDICT':'4800','OLLAMA_TIMEOUT':'900'}


class ModelDiagnosticsTests(unittest.TestCase):
    def test_missing_review_decision_is_not_a_valid_completed_review(self):
        model=local_model()
        with patch.dict(os.environ,ENV),patch('agents.request',return_value=response({'decisions':[]})),self.assertRaises(ModelOutputError):
            model.review('macro',{'sources':[fred_source()],'items':[{'item_id':'F0'}]})
        self.assertEqual(model.telemetry[-1]['error_code'],'MODEL_SELECTION_INVALID')

    def test_native_enum_violation_is_logged_before_public_conversion(self):
        model=local_model()
        bad=response({'findings':[{'observation_id':'NOT_A_SOURCE','interpretation':'조건부 해석입니다.','counterpoint':'다를 수 있습니다.'}]})
        with patch.dict(os.environ,ENV),patch('agents.request',return_value=bad),self.assertRaises(ModelOutputError):
            model.analyze('macro',{'sources':[fred_source()]})
        self.assertEqual(len(model.telemetry),2)
        self.assertEqual(model.telemetry[-1]['error_code'],'MODEL_SELECTION_INVALID')
        self.assertIn('NOT_A_SOURCE',model.telemetry[-1]['unverified_output'])

    def test_invalid_model_setting_is_identified_without_sending_input(self):
        model=local_model()
        with patch.dict(os.environ,{**ENV,'OLLAMA_NUM_PREDICT':'oops'}),patch('agents.request') as req,self.assertRaises(ValueError) as caught:
            model._generate('JSON',{},Report,'portfolio')
        req.assert_not_called();self.assertEqual(error_code(caught.exception),'MODEL_SETTINGS_INVALID')
        self.assertIn('OLLAMA_NUM_PREDICT',model.telemetry[0]['configuration_error'])

    def test_native_server_failure_detail_is_saved_only_when_opted_in(self):
        model=local_model()
        with patch.dict(os.environ,ENV),patch('agents.request',side_effect=NetworkError(500,detail='runner exited')),self.assertRaises(NetworkError):
            model._generate('JSON',{},Report,'portfolio')
        self.assertEqual(model.telemetry[0]['local_server_error'],'runner exited')

    def test_schema_failure_keeps_each_attempt_and_raw_output(self):
        model=local_model()
        broken={'done':True,'done_reason':'stop','prompt_eval_count':123,'eval_count':480,
                'message':{'content':'{"findings":[]}\n</think>\n{"findings":[]}'}}
        with patch.dict(os.environ,ENV),patch('agents.request',return_value=broken),self.assertRaises(ValidationError):
            model.analyze('macro',{'sources':[fred_source()]})
        self.assertEqual(len(model.telemetry),2)
        for event in model.telemetry:
            self.assertEqual(event['error_code'],'MODEL_SCHEMA_INVALID')
            self.assertEqual(event['output_tokens'],480)
            self.assertIn('</think>',event['unverified_output'])
            self.assertEqual(event['validation_errors'][0]['type'],'json_invalid')

    def test_truncation_preserves_usage_and_never_parses_partial_json(self):
        model=local_model()
        truncated={'done':True,'done_reason':'length','eval_count':4800,'message':{'content':'{"findings":['}}
        with patch.dict(os.environ,ENV),patch('agents.request',return_value=truncated),self.assertRaises(ModelOutputError):
            model._generate('JSON',{},Report,'portfolio')
        event=model.telemetry[0]
        self.assertEqual(event['error_code'],'MODEL_OUTPUT_TRUNCATED')
        self.assertEqual(event['output_tokens'],4800)
        self.assertEqual(event['done_reason'],'length')

    def test_failed_network_is_logged_without_credentials(self):
        model=local_model()
        with patch.dict(os.environ,ENV),patch('agents.request',side_effect=NetworkError(503)),self.assertRaises(NetworkError):
            model._generate('JSON',{},Report,'portfolio')
        self.assertEqual(model.telemetry[0]['error_code'],'MODEL_HTTP_503')
        self.assertNotIn('Authorization',json.dumps(model.telemetry))

    def test_qwen_uses_non_greedy_sampling_and_answer_only(self):
        model=local_model();model.model='qwen3:4b'
        with patch.dict(os.environ,{**ENV,'OLLAMA_THINK':'true'}),patch('agents.request',return_value=response({'findings':[]})) as req:
            model.analyze('macro',{'sources':[fred_source()]})
        body=req.call_args.kwargs['body']
        self.assertIs(body['think'],False)
        self.assertEqual(body['options']['temperature'],0.7)
        self.assertEqual(body['options']['top_p'],0.8)
        self.assertEqual(body['options']['num_predict'],4800)
        self.assertIn('JSON',body['messages'][0]['content'])

    def test_wire_schema_does_not_force_digit_substitution(self):
        schema={'type':'string','pattern':'^[^0-9]*$','minLength':1,'maxLength':140}
        self.assertEqual(transport_schema(schema),{'type':'string'})
        self.assertIn('pattern',schema)

    def test_unicode_digits_cannot_bypass_numeric_rejection(self):
        for text in ('금리 ９.９９%','금리 ٩.٩٩%','금리 9.99%'):
            with self.subTest(text=text):self.assertTrue(numeric_issues(text,[fred_source()]))
        self.assertFalse(numeric_issues('금리 ٤.٨٦%',[fred_source()]))

    def test_unicode_date_value_swap_is_checked(self):
        self.assertTrue(indicator_issues('２０２６-１１-１２ 금리 ４.７３%',[fred_source()]))

    def test_failed_response_is_saved_before_next_call(self):
        model=local_model()
        with tempfile.TemporaryDirectory() as d:
            model.diagnostic_path=Path(d)/'calls.jsonl'
            with patch.dict(os.environ,ENV),patch('agents.request',side_effect=NetworkError(500)),self.assertRaises(NetworkError):
                model._generate('JSON',{},Report,'portfolio')
            event=json.loads(model.diagnostic_path.read_text())
            self.assertEqual(event['status'],'failed')
            self.assertEqual(model.diagnostic_path.stat().st_mode&0o777,0o600)

    def test_malformed_usage_does_not_hide_empty_output_error(self):
        model=local_model()
        with patch.dict(os.environ,ENV),patch('agents.request',return_value={'done':True,'usage':[],'message':None}),self.assertRaises(ModelOutputError) as caught:
            model._generate('JSON',{},Report,'portfolio')
        self.assertEqual(error_code(caught.exception),'MODEL_EMPTY_OUTPUT')

    def test_context_rejection_is_recorded_without_a_request(self):
        model=local_model()
        with patch.dict(os.environ,{**ENV,'OLLAMA_NUM_CTX':'4096'}),patch('agents.request') as req,self.assertRaises(ValueError):
            model._generate('JSON',{},Report,'portfolio')
        req.assert_not_called()
        self.assertEqual(model.telemetry[0]['error_code'],'MODEL_CONTEXT_BUDGET')

    def test_keyboard_interrupt_is_not_retried(self):
        model=local_model()
        with patch.dict(os.environ,ENV),patch('agents.request',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt):
            model.analyze('macro',{'sources':[fred_source()]})
        self.assertEqual(len(model.telemetry),1)
        self.assertEqual(model.telemetry[0]['status'],'cancelled')


class ReplayAndCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.snap,self.p,self.m=bundles()
        self.result=run_deep_research(FixtureModel(),self.snap,self.p,self.m)

    def tearDown(self):self.temp.cleanup()

    def raw_snapshot(self):
        snap=full();snap['positions'][0]['symbol']='TEST';return snap

    def test_other_issuer_financials_never_become_macro_on_repeated_subset_replay(self):
        other=evidence('SEC','OTHER financial','https://example.com/other','OTHER revenue 999 USD',kind='financial',symbols=['OTHER'])
        self.result['sources'].append(other)
        self.result['research_v2'].pop('source_groups') # old file without provenance
        model=FixtureModel()
        result,valued=replay_report(self.result,self.raw_snapshot(),['TEST'],model)
        self.assertNotIn(other['id'],{s['id'] for s in model.macro_payload['sources']})
        model2=FixtureModel();replay_report(result,self.raw_snapshot(),['TEST'],model2)
        self.assertNotIn(other['id'],{s['id'] for s in model2.macro_payload['sources']})

    def test_partial_scope_is_not_relabelled_full_on_second_replay(self):
        self.result['research_v2']['replay']={'origin_symbols':['TEST','OTHER']}
        result,valued=replay_report(self.result,self.raw_snapshot(),['TEST'],FixtureModel())
        self.assertFalse(result['research_v2']['replay']['full_portfolio'])
        result,_=replay_report(result,self.raw_snapshot(),['TEST'],FixtureModel())
        self.assertFalse(result['research_v2']['replay']['full_portfolio'])

    def test_replay_call_history_belongs_to_current_run_only(self):
        model=FixtureModel();model.telemetry=[{'role':'old-run'}]
        result,_=replay_report(self.result,self.raw_snapshot(),['TEST'],model)
        self.assertEqual(result['research_v2']['model_calls'],[])

    def test_render_failure_does_not_update_baseline_or_report_pointer(self):
        app.save_report(self.result,self.snap,self.root)
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('app.render_report',side_effect=ValueError('render failed')),self.assertRaises(ValueError):
            app.save_report(copy.deepcopy(self.result),self.snap,self.root)
        after={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_markdown_linkage_preserves_its_scenario_status(self):
        text=app.render_report(self.result,self.snap)
        self.assertIn('조건부 시나리오',text)
        self.assertIn('실제 발생했다고 확인한 것은 아닙니다',text)

    def test_malformed_previous_baseline_does_not_break_report_saving(self):
        for previous in ([],123,{'events':None},{'events':[{}]}):
            write_private(self.root/'research_previous.json',previous)
            self.assertTrue(app.save_report(copy.deepcopy(self.result),self.snap,self.root).exists())

    def test_same_output_writer_is_locked_but_separate_outputs_work(self):
        with output_lock(self.root/'a'):
            with self.assertRaises(ValueError):
                with output_lock(self.root/'a'):pass
            with output_lock(self.root/'b'):pass

    def test_pipeline_failure_preserves_collected_sources(self):
        with patch('research_runtime.portfolio_bundle',return_value=self.p),patch('research_runtime.macro_bundle',return_value=self.m),patch('research_runtime.run_agents',side_effect=ValueError):
            result=research_runtime.collect_research(FixtureModel(),self.snap,config())
        self.assertFalse(result['complete']);self.assertEqual(len(result['sources']),2)
        self.assertEqual(result['diagnostics']['pipeline_error'],'ValueError')

    def test_deep_report_rejects_duplicate_companies_and_false_quality_counts(self):
        bad=copy.deepcopy(self.result['research_v2']);bad['companies']*=2
        with self.assertRaises(ValueError):validate_deep_result(bad,{s['id'] for s in self.result['sources']})
        bad=copy.deepcopy(self.result['research_v2']);bad['quality']['accepted_insights']=999
        with self.assertRaises(ValueError):validate_deep_result(bad,{s['id'] for s in self.result['sources']})


class BoundaryAndFinanceTests(unittest.TestCase):
    def test_financial_numbers_do_not_ground_new_contract_and_backlog_facts(self):
        _,p,_=bundles()
        self.assertTrue(evidence_scope_issues('고정가격 계약의 수주잔고가 확대되었습니다.',p['sources']))
        self.assertFalse(evidence_scope_issues('현금 유입이 지연된다면 조달 부담을 검토합니다.',p['sources']))

    def test_business_terms_are_allowed_with_an_actual_cited_excerpt(self):
        source=evidence('SEC','contract','https://example.com/contract','Fixed-price contracts and backlog are described here.',kind='filing_excerpt')
        self.assertFalse(evidence_scope_issues('고정가격 계약의 수주잔고를 확인합니다.',[source]))

    def test_rate_signal_is_not_confused_with_rate_stimulus(self):
        source=fred_source('FEDFUNDS')
        self.assertTrue(evidence_scope_issues('실효금리 상승이 경제활동을 촉진해 매출이 늘어날 수 있다.',[source]))
        self.assertFalse(evidence_scope_issues('경제활동의 강세가 금리 상승에 반영될 수 있다.',[source]))
        self.assertFalse(evidence_scope_issues('금리 상승이 경제활동을 촉진하지 않는다.',[source]))

    def test_deterministic_content_repair_is_once_and_keeps_rejected_attempt(self):
        snap,p,_=bundles();model=local_model();eid=p['sources'][0]['id']
        bad=ResearchDraft(insights=[insight(eid,mechanism='고정가격 계약의 수주잔고가 확대되었습니다.')])
        good=ResearchDraft(insights=[insight(eid)])
        with patch.object(model,'research',side_effect=[bad,good]) as generate,patch.object(model,'review',side_effect=FixtureModel().review):
            detail,stage=analyze_issuer(model,snap['positions'][0],p['dossiers']['TEST'],p['sources'],{})
        self.assertEqual(generate.call_count,2);self.assertEqual(stage['status'],'ok')
        self.assertIn('previous_attempt',detail['diagnostic_draft'])
        self.assertTrue(any(a['item_id'].startswith('A0_') and not a['accepted'] for a in detail['quality_audit']))

    def test_repeated_unsupported_business_content_stays_partial_without_looping(self):
        snap,p,_=bundles();model=local_model();eid=p['sources'][0]['id']
        bad=ResearchDraft(insights=[insight(eid,mechanism='고정가격 계약의 수주잔고가 확대되었습니다.')])
        with patch.object(model,'research',return_value=bad) as generate,patch.object(model,'review') as review:
            detail,stage=analyze_issuer(model,snap['positions'][0],p['dossiers']['TEST'],p['sources'],{})
        self.assertEqual(generate.call_count,2);review.assert_not_called();self.assertEqual(stage['status'],'failed')
        self.assertFalse(detail['insights'])

    def test_ytd_cannot_be_relabelled_as_first_quarter(self):
        source=evidence('SEC','cash flow','https://example.com/cf',json.dumps({'start':'2026-01-01','end':'2026-09-30','basis':'ytd','facts':[]}),kind='financial_summary')
        self.assertTrue(financial_period_issues('2026년 1분기 영업현금흐름입니다.',[source]))
        self.assertFalse(financial_period_issues('2026년 3분기 누적 영업현금흐름입니다.',[source]))

    def test_calendar_quarter_label_must_match_source(self):
        source=evidence('SEC','sales','https://example.com/sales',json.dumps({'start':'2026-07-01','end':'2026-09-30','basis':'quarter','facts':[]}),kind='financial_summary')
        self.assertTrue(financial_period_issues('2026년 1분기 매출입니다.',[source]))
        self.assertFalse(financial_period_issues('2026년 3분기 매출입니다.',[source]))

    def test_non_calendar_fiscal_quarter_is_not_guessed(self):
        source=evidence('SEC','sales','https://example.com/sales',json.dumps({'start':'2026-02-01','end':'2026-04-30','basis':'quarter','facts':[]}),kind='financial_summary')
        self.assertFalse(financial_period_issues('2026년 1분기 매출입니다.',[source]))

    def test_invalid_numeric_claim_is_rejected_without_semantic_call(self):
        from agents import review_report,Finding
        source=fred_source();model=Mock()
        draft=Report(summary='',findings=[Finding(subject='금리',claim='금리는 9.99%입니다.',kind='fact',evidence_ids=[source['id']],confidence='low',counterpoint='다를 수 있습니다.',revisit_when='다음 발표를 확인합니다.')],conflicts=[],watchlist=[],limitations=[])
        kept,audit=review_report(model,'macro',draft,{'sources':[source]})
        model.review.assert_not_called();self.assertFalse(kept.findings);self.assertFalse(audit[0]['accepted'])
    def test_boolean_is_not_a_timestamp(self):
        self.assertIsNone(stamp(True));self.assertIsNone(stamp(False))

    def test_duplicate_position_cannot_change_portfolio_weights(self):
        snap=full();snap['positions']*=2
        with self.assertRaises(ValueError):value_snapshot(snap)

    def test_financial_numeric_format_differences_are_not_conflicts(self):
        rows,notes=deduplicate_records([record(value='100'),record(value='100.0')])
        self.assertEqual(len(rows),1);self.assertFalse(notes)
        rows,notes=sec_records(sec_payload([sec_row(value=100),sec_row(value=100.0)]),'TEST',cutoff='2026-11-16')
        self.assertEqual(len(rows),1);self.assertFalse(notes)

    def test_dart_equivalent_amount_formats_are_accepted(self):
        rows,notes=dart_records([dart_row(value='200'),dart_row(value='200.0')],'TEST',2026,'11014','CFS')
        self.assertTrue(rows);self.assertFalse(notes)

    def test_missing_quote_timestamp_does_not_replace_valid_cached_price(self):
        broker=CachedToss.__new__(CachedToss);snap=full();snap['snapshot_warnings']=[]
        broker.get=lambda path,params: [{'symbol':'ACME','lastPrice':'120'}] if path.endswith('prices') else snap['fx']
        out=broker._refresh_prices(snap)
        self.assertEqual(out['positions'][0]['price'],'100')
        self.assertEqual(out['positions'][0]['price_at'],full()['positions'][0]['price_at'])

    def test_expired_fx_does_not_replace_saved_reference(self):
        broker=CachedToss.__new__(CachedToss);snap=full();snap['snapshot_warnings']=[]
        old=copy.deepcopy(snap['fx'])
        broker.get=lambda path,params: [] if path.endswith('prices') else {**old,'midRate':'9999'}
        out=broker._refresh_prices(snap)
        self.assertEqual(out['fx'],old)

    def test_config_rejects_nan_boolean_and_negative_budgets(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json'
            for change in ({'holdings_refresh_seconds':float('nan')},{'holdings_refresh_seconds':True},
                           {'research':{'evidence_char_budget':-1}}):
                path.write_text(json.dumps({**config(),**change}))
                with self.assertRaises(ValueError):load_config(path)

    def test_server_preserves_financial_provenance(self):
        snap,p,m=bundles();result=run_deep_research(FixtureModel(),snap,p,m)
        result['sources'][0].update(period_start='2026-01-01',period_end='2026-03-31',basis='quarter',primary=True,
                                    event_key='SEC:filing',input_evidence_ids=[],document_date='2026-05-01')
        parsed=server.report(result)
        for key in ('period_start','period_end','basis','primary','event_key','input_evidence_ids','document_date'):
            self.assertEqual(parsed['sources'][0][key],result['sources'][0][key])

    def test_empty_ok_stages_do_not_imply_complete(self):
        snap,p,m=bundles();result=run_deep_research(FixtureModel(),snap,p,m)
        result['macro']['report']['findings']=[]
        with self.assertRaises(ValueError):server.report(result)

    def test_imported_json_does_not_download_stale_markdown(self):
        with tempfile.TemporaryDirectory() as d,patch.object(server,'DATA',Path(d)),patch.object(server,'MODE','local'):
            app.demo(Path(d))
            client=TestClient(server.api,base_url='http://127.0.0.1:8000',client=('127.0.0.1',32100))
            self.assertEqual(client.get('/api/report/markdown').status_code,200)
            write_private(Path(d)/'imported_report.json',{'unused':'download boundary only'})
            self.assertEqual(client.get('/api/report/markdown').status_code,409)

    def test_long_fred_metadata_keeps_valid_structured_json(self):
        from collectors import fred
        meta={'seriess':[{'title':'Long metadata','units':'Percent '+('x'*1800),'frequency':'Daily'}]}
        rows={'observations':[{'date':'2026-11-12','value':'4.86'},{'date':'2026-11-10','value':'4.73'}]}
        with patch('collectors.request',side_effect=[meta,rows]):out=fred('DGS2','fixture')
        self.assertGreater(len(out[0]['content']),1600)
        self.assertEqual(json.loads(out[0]['content'])['observations'][0]['value'],'4.86')


class WebsocketFrameTests(unittest.IsolatedAsyncioTestCase):
    async def test_plaintext_heartbeat_and_malformed_frames_do_not_reconnect(self):
        broker=Mock();broker.token.return_value='fixture-token';stream=LiveQuotes(broker)
        snap=full();stream.update_targets(snap)
        frames=iter(['PONG','PING','[]','not json',json.dumps({'type':'message','topic':None}),
                     json.dumps({'type':'subscriptions','rejected':None}),
                     json.dumps({'type':'message','topic':'trade:us:ACME','data':{'price':'101','timestamp':now(),'currency':'USD'}})])
        ws=Mock();ws.send=AsyncMock()
        async def recv():
            try:return next(frames)
            except StopIteration:stream.stop.set();return 'PONG'
        ws.recv=recv
        context=Mock();context.__aenter__=AsyncMock(return_value=ws);context.__aexit__=AsyncMock(return_value=False)
        with patch('websockets.asyncio.client.connect',return_value=context) as connect:
            await stream._run()
        connect.assert_called_once();self.assertEqual(stream.prices['ACME']['price'],'101')
        self.assertIn('PONG',[call.args[0] for call in ws.send.call_args_list])


if __name__=='__main__':unittest.main()
