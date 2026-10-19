"""Offline transfer, historical research, failure and API integration regression tests."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
import app
import server
import research_runtime as runtime
import site_worker
from common import now, NetworkError, decimal, evidence, write_private, load_env
from broker import Toss, value_snapshot, LiveQuotes
from holdings_cache import (CachedToss, HoldingsCacheError, positive_snapshot, read_snapshot_file,
                            research_snapshot, research_file)
from tests.test_agents_quality import Stub, P, M

ROOT = Path(__file__).resolve().parents[1]
OLD = '2026-01-01T00:00:00+00:00'


def full():
    return {'as_of': OLD, 'scope': '과거 실계좌 조회 원본',
            'fx': {'midRate':'1400','validFrom':OLD,'validUntil':'2026-01-02T00:00:00+00:00'},
            'broker_equity_value_krw':'1400000',
            'positions':[dict(symbol='ACME',name='Sample',market='NASDAQ',currency='USD',
                quantity='10',average_price='90',price='100',price_at=OLD,price_source='Toss REST')]}


def empty():
    return dict(as_of=now(),positions=[],fx=None,broker_equity_value_krw='0',scope='계좌 주식 없음')


def config():
    return dict(holdings_refresh_seconds=60,report_interval_seconds=3600,news_lookback_hours=48,
                yahoo_overrides={},macro_symbols=[],macro_feeds=[],fred_series=[])


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'TOSS_CLIENT_ID':'fixture-client', 'TOSS_CLIENT_SECRET':'fixture-secret',
                    'TOSS_ACCOUNT_SEQ':'account-A','EQUITY_HOLDINGS_MODE':'live'})
        self.env.start()
        self.cache = self.folder/'last_nonempty_portfolio.json'
        self.broker = CachedToss(self.cache)
        # Any unmocked real HTTP request fails immediately; tests must remain offline.
        self.net = patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('unmocked network prohibited'))
        self.net.start()

    def tearDown(self):
        self.net.stop()
        self.env.stop()
        self.temp.cleanup()

    def seed(self, raw=None):
        with patch.object(Toss, 'snapshot', return_value=raw or full()):
            return self.broker.snapshot()

    def quotes(self, path, params=None, **kwargs):
        if path.endswith('/prices'):
            return [dict(symbol='ACME',lastPrice='110',timestamp=now())]
        if path.endswith('/exchange-rate'):
            return {'midRate':'1450','validUntil':'2099-01-01T00:00:00+00:00'}
        raise AssertionError(path)

    def fallback(self, getter=None):
        with patch.object(Toss,'snapshot',return_value=empty()), patch.object(self.broker,'get',side_effect=getter or self.quotes):
            return self.broker.snapshot()

    def test_normal_save_has_no_credentials(self):
        self.assertEqual(self.seed()['holdings_source'],'live')
        text=self.cache.read_text()
        self.assertNotIn('fixture-secret',text)
        self.assertNotIn('fixture-client',text)
        self.assertNotIn('account-A',text)
        self.assertEqual(self.cache.stat().st_mode & 0o777,0o600)

    def test_empty_new_price_same_quantity_and_original_time(self):
        self.seed()
        out=value_snapshot(self.fallback())
        self.assertEqual(out['positions'][0]['quantity'],'10')
        self.assertEqual(out['positions'][0]['price'],'110')
        self.assertEqual(out['as_of'],OLD)
        self.assertEqual(out['calculated_equity_value_krw'],'1595000')
        self.assertTrue(any('실제 보유 내역이 아닙니다' in x for x in out['warnings']))

    def test_empty_never_overwrites_normal_cache(self):
        self.seed()
        before=self.cache.read_bytes()
        self.fallback();self.fallback()
        self.assertEqual(before,self.cache.read_bytes())

    def test_restart_reuses_snapshot(self):
        self.seed()
        self.broker=CachedToss(self.cache)
        self.assertEqual(len(self.fallback()['positions']),1)

    def test_no_cache_fails_explicitly(self):
        with self.assertRaises(HoldingsCacheError):self.fallback()

    def test_auth_failure_is_not_empty(self):
        self.seed()
        with patch.object(Toss,'snapshot',side_effect=NetworkError(401)):
            with self.assertRaises(NetworkError):self.broker.snapshot()

    def test_different_account_refused(self):
        self.seed();self.broker.account='account-B'
        with self.assertRaises(HoldingsCacheError):self.fallback()

    def test_different_client_same_account_refused(self):
        self.seed();self.broker.client_id='another-client'
        with self.assertRaises(HoldingsCacheError):self.fallback()

    def test_frozen_baseline_survives_partial_transfer(self):
        original=full()
        original['positions'].append({**original['positions'][0], 'symbol':'OTHER','quantity':'5'})
        self.seed(original)
        frozen=self.broker.freeze()
        before=frozen.read_bytes()
        self.seed(full())  # OTHER was transferred; a nonempty live residual is cached.
        self.assertEqual(len(read_snapshot_file(self.cache)[0]['positions']),1)
        self.assertEqual(len(self.fallback()['positions']),2)
        self.assertEqual(before,frozen.read_bytes())

    def test_frozen_needs_explicit_replace(self):
        self.seed();self.broker.freeze()
        with self.assertRaises(HoldingsCacheError):self.broker.freeze()
        self.broker.freeze(replace=True)

    def test_backup_recovery_after_corruption(self):
        self.seed();self.seed()
        self.cache.write_text('{BROKEN')
        self.assertEqual(len(self.fallback()['positions']),1)
        self.assertTrue(any('백업' in x for x in self.fallback()['snapshot_warnings']))
        self.assertEqual(research_file(self.folder).suffix,'.bak')

    def test_checksum_detects_accidental_edit(self):
        self.seed()
        data=json.loads(self.cache.read_text())
        data['snapshot']['positions'][0]['quantity']='999'
        self.cache.write_text(json.dumps(data))
        with self.assertRaises(HoldingsCacheError):read_snapshot_file(self.cache)

    def test_corrupt_frozen_does_not_silently_use_partial(self):
        self.seed();frozen=self.broker.freeze();frozen.write_text('{}')
        with self.assertRaises(HoldingsCacheError):self.fallback()

    def test_version_one_cache_readable(self):
        key=hashlib.sha256(b'toss-holdings:account-A').hexdigest()
        write_private(self.cache,dict(version=1,account_key=key,snapshot=full()))
        self.assertEqual(len(self.fallback()['positions']),1)

    def test_bad_numeric_and_future_time_refused(self):
        for field,value in [('quantity',True),('price','NaN'),('price','-1'),('price_at','2099-01-01T00:00:00+00:00')]:
            raw=full();raw['positions'][0][field]=value
            with self.subTest(field=field,value=value), self.assertRaises(HoldingsCacheError):positive_snapshot(raw)

    def test_zero_quantity_is_empty(self):
        self.seed();zero=full();zero['positions'][0]['quantity']='0'
        with patch.object(Toss,'snapshot',return_value=zero),patch.object(self.broker,'get',side_effect=self.quotes):
            self.assertEqual(self.broker.snapshot()['holdings_source'],'cached')

    def test_live_parser_does_not_evaluate_missing_price_fallback(self):
        item=dict(symbol='ACME',name='Sample',currency='USD',quantity='10',averagePurchasePrice='90',marketValue={'amount':'1000'})
        def get(path,*args,**kwargs):
            return {'/api/v1/holdings':{'items':[item],'marketValue':{'amount':{'krw':'1400000'}}},
                    '/api/v1/stocks':[{'symbol':'ACME','market':'NASDAQ'}],
                    '/api/v1/prices':[{'symbol':'ACME','lastPrice':'100','timestamp':now()}],
                    '/api/v1/exchange-rate':{'midRate':'1400','validUntil':'2099-01-01T00:00:00+00:00'}}[path]
        raw=Toss()
        with patch.object(raw,'get',side_effect=get):out=raw.snapshot()
        self.assertEqual(out['positions'][0]['price'],'100')

    def test_empty_toss_parser_without_market_value(self):
        raw=Toss()
        with patch.object(raw,'get',return_value={'items':[]}):self.assertEqual(raw.snapshot()['positions'],[])

    def test_prices_failure_preserves_old_timestamp(self):
        self.seed()
        def broken(*args,**kwargs):raise NetworkError(500)
        out=self.fallback(broken)
        self.assertEqual(out['positions'][0]['price_at'],OLD)
        self.assertEqual(out['positions'][0]['price'],'100')
        self.assertEqual(out['fx']['midRate'],'1400')

    def test_future_quote_not_labeled_current(self):
        self.seed()
        def future(path,params=None,**kwargs):
            if path.endswith('/prices'):return [dict(symbol='ACME',lastPrice='900',timestamp='2099-01-01T00:00:00+00:00')]
            return self.quotes(path,params,**kwargs)
        self.assertEqual(self.fallback(future)['positions'][0]['price'],'100')

    def test_research_mode_needs_no_toss_credentials(self):
        self.seed()
        with patch.dict(os.environ,{'TOSS_CLIENT_ID':'','TOSS_CLIENT_SECRET':''}), patch.object(Toss,'__init__',side_effect=AssertionError('Toss must not initialize')):
            source=runtime.ResearchPortfolio(self.folder,config(),refresh=False)
            out=source.snapshot()
        self.assertEqual(out['holdings_source'],'research')
        self.assertIsNone(out['account_checked_at'])

    def test_research_snapshot_cannot_replace_live_cache(self):
        self.seed()
        research=research_snapshot(self.cache)
        with self.assertRaises(HoldingsCacheError):self.broker._save(research)

    def test_demo_cannot_be_imported(self):
        raw=full();raw['scope']='가상 샘플'
        path=self.folder/'demo.json';write_private(path,raw)
        with self.assertRaises(HoldingsCacheError):self.broker.import_snapshot(path)

    def test_explicit_legacy_import(self):
        path=self.folder/'old.json';write_private(path,full())
        self.broker.import_snapshot(path)
        self.assertEqual(len(self.fallback()['positions']),1)

    def test_public_quote_refresh_validates_currency(self):
        self.seed()
        source=research_snapshot(self.cache)
        def wrong(symbol,count):
            s=evidence('Yahoo','fixture','https://example.com/q',json.dumps({'regularMarketPrice':900,'currency':'EUR'}),now(),kind='quote')
            return [s],[]
        with patch('research_runtime.yahoo',side_effect=wrong):out=runtime.refresh_public_prices(source,config())
        self.assertEqual(out['positions'][0]['price'],'100')
        self.assertEqual(out['fx']['midRate'],'1400')

    def test_public_quote_valid_currency_and_source(self):
        self.seed()
        def good(symbol,count):
            data={'regularMarketPrice':1450 if symbol=='KRW=X' else 120,'currency':'KRW' if symbol=='KRW=X' else 'USD'}
            return [evidence('Yahoo','fixture','https://example.com/'+symbol,json.dumps(data),now(),kind='quote')],[]
        with patch('research_runtime.yahoo',side_effect=good):out=runtime.refresh_public_prices(research_snapshot(self.cache),config())
        self.assertEqual(out['positions'][0]['price'],'120')
        self.assertTrue(out['positions'][0]['price_source'].startswith('Yahoo'))
        self.assertEqual(out['fx']['midRate'],'1450')

    def test_full_research_report_saved_without_toss(self):
        self.seed()
        with patch('app.Model',return_value=Stub()), patch('research_runtime.portfolio_bundle',return_value={'sources':[P],'warnings':[]}), patch('research_runtime.macro_bundle',return_value={'sources':[M],'warnings':[]}), patch.object(Toss,'__init__',side_effect=AssertionError('no Toss')):
            app.live(config(),self.folder,False,False,source_mode='cached',skip_price_refresh=True)
        status=json.loads((self.folder/'analysis_status.json').read_text())
        path=self.folder/'reports'/Path(status['report']).with_suffix('.json')
        result=json.loads(path.read_text())
        self.assertTrue(result['complete'])
        self.assertEqual(result['portfolio_context']['holdings_source'],'research')
        self.assertIn('현재 토스 잔고가 아닙니다',(self.folder/'latest_report.md').read_text())

    def test_collector_failure_still_saves_partial_report(self):
        valued=value_snapshot(full())
        with patch('research_runtime.portfolio_bundle',side_effect=RuntimeError('secret')),patch('research_runtime.macro_bundle',side_effect=NetworkError(500)):
            out=runtime.collect_research(Stub(),valued,config())
        self.assertFalse(out['complete'])
        path=app.save_report(out,valued,self.folder)
        self.assertTrue(path.exists())
        self.assertNotIn('secret',str(out))

    def test_ollama_length_stop_does_not_release_truncated_json(self):
        from agents import Model
        with patch.dict(os.environ,{'LLM_BACKEND':'ollama','LLM_MODEL':'fixture'}):model=Model()
        with patch('agents.request',return_value={'done':True,'done_reason':'length','message':{'content':'{}'}}) as req:
            with self.assertRaises(ValueError):model.analyze('portfolio',{'sources':[]})
        self.assertEqual(req.call_args.kwargs['attempts'],1)

    def test_source_budget_omissions_are_explicit(self):
        bundle={'sources':[P]*205,'warnings':[]}
        out=runtime.cap_sources(bundle,200)
        self.assertEqual(len(out['sources']),200)
        self.assertEqual(len(bundle['sources']),205)
        self.assertTrue(any('5개' in x for x in out['warnings']))

    def test_pipeline_error_becomes_explicit_partial(self):
        with patch('research_runtime.portfolio_bundle',return_value={'sources':[],'warnings':[]}),patch('research_runtime.macro_bundle',return_value={'sources':[],'warnings':[]}),patch('research_runtime.run_agents',side_effect=ValueError('secret')):
            out=runtime.collect_research(Stub(),value_snapshot(full()),config())
        self.assertFalse(out['complete'])
        self.assertTrue(out['warnings'])
        self.assertNotIn('secret',str(out))

    def test_cli_cached_snapshot_only_no_credentials(self):
        self.seed()
        env={k:v for k,v in os.environ.items() if not k.startswith(('TOSS_','LLM_','OPENAI_','EQUITY_'))}
        completed=subprocess.run([sys.executable,str(ROOT/'app.py'),'cached','--snapshot-only','--skip-price-refresh','--output',str(self.folder)],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertIn('research',completed.stdout)

    def test_snapshot_only_freezes_without_model(self):
        with patch.object(Toss,'snapshot',return_value=full()),patch('app.Model',side_effect=AssertionError('no model')):
            app.live(config(),self.folder,False,False,snapshot_only=True,freeze_holdings=True)
        self.assertTrue((self.folder/'frozen_portfolio.json').exists())

    def test_atomic_concurrent_writes_remain_valid_private_json(self):
        path=self.folder/'atomic.json'
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i:write_private(path,{'i':i,'body':'x'*10000}),range(20)))
        self.assertIn('i',json.loads(path.read_text()))
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertEqual(len(list(self.folder.glob('*.tmp'))),0)

    def test_news_same_url_different_content_has_distinct_id(self):
        a=evidence('RSS','Title','https://example.com/article','old')
        b=evidence('RSS','Title','https://example.com/article','new')
        self.assertNotEqual(a['id'],b['id'])

    def test_env_quotes_and_comments(self):
        path=self.folder/'.env';path.write_text('TRANSFER_TEST_VAR="value with spaces" # note\nTRANSFER_TEST_EMPTY= # note\n')
        with patch.dict(os.environ,{},clear=False):
            load_env(path)
            self.assertEqual(os.environ['TRANSFER_TEST_VAR'],'value with spaces')
            self.assertEqual(os.environ['TRANSFER_TEST_EMPTY'],'')

    def test_server_roundtrip_keeps_research_provenance_and_audit(self):
        self.seed()
        valued=value_snapshot(research_snapshot(self.cache))
        from agents import run_agents
        raw=run_agents(Stub(),valued,{'sources':[P],'warnings':valued['warnings']},{'sources':[M],'warnings':[]})
        app.save_report(raw,valued,self.folder)
        normal=server.report(raw)
        self.assertEqual(normal['portfolio_context']['holdings_source'],'research')
        self.assertTrue(normal['portfolio']['quality_audit'])
        self.assertEqual(server.snapshot(valued)['holdings_source'],'research')
        self.assertIn('현재 증권계좌 잔고 아님',site_worker.site_packet(valued)['scope'])

    def test_server_cached_job_starts_without_toss_keys(self):
        self.seed()
        old_data,old_job,old_mode=server.DATA,server.job,server.MODE
        server.DATA,server.job,server.MODE=self.folder,None,'local'
        try:
            thread=Mock()
            with patch.dict(os.environ,{'EQUITY_HOLDINGS_MODE':'cached','LLM_MODEL':'fixture','TOSS_CLIENT_ID':'','TOSS_CLIENT_SECRET':''}),patch.object(server,'threading',SimpleNamespace(Thread=thread)):
                client=TestClient(server.api,base_url='http://127.0.0.1:8000',client=('127.0.0.1',32000))
                response=client.post('/api/jobs',headers={'Origin':'http://127.0.0.1:8000'})
            self.assertEqual(response.status_code,202,response.text)
            self.assertEqual(response.json()['mode'],'cached')
            thread.return_value.start.assert_called_once()
        finally:server.DATA,server.job,server.MODE=old_data,old_job,old_mode

    def test_server_old_report_never_counts_as_new_success(self):
        self.seed()
        valued=value_snapshot(full())
        from agents import run_agents
        raw=run_agents(Stub(),valued,{'sources':[P],'warnings':[]},{'sources':[M],'warnings':[]})
        app.save_report(raw,valued,self.folder)
        current={'id':'new-run','mode':'cached','status':'running'}
        with patch.object(server,'DATA',self.folder),patch('server.subprocess.run',return_value=SimpleNamespace(returncode=0)):
            server.run_job(current)
        self.assertEqual(current['status'],'failed')

    def test_server_new_matching_run_counts_as_success(self):
        valued=value_snapshot(full())
        def execute(*args,**kwargs):
            from agents import run_agents
            raw=run_agents(Stub(),valued,{'sources':[P],'warnings':[]},{'sources':[M],'warnings':[]})
            with patch.dict(os.environ,kwargs['env']):app.save_report(raw,valued,self.folder)
            return SimpleNamespace(returncode=0)
        current={'id':'new-matching-run','mode':'cached','status':'running'}
        with patch.object(server,'DATA',self.folder),patch('server.subprocess.run',side_effect=execute):server.run_job(current)
        self.assertEqual(current['status'],'complete')

    def test_site_worker_cached_source_and_collection(self):
        self.seed()
        valued=value_snapshot(research_snapshot(self.cache))
        with patch('site_worker.collect_research',return_value={'created_at':now(),'complete':False,'warnings':[],
            'sources':[],**{k:{'status':'failed','report':{'summary':'fixture','findings':[],'conflicts':[],'watchlist':[],'limitations':[]}} for k in ('portfolio','macro','synthesis')}}):
            result=site_worker.collect_analysis(Stub(),valued,config(),self.folder)
        self.assertEqual(result['portfolio_context']['holdings_source'],'research')
        self.assertTrue((self.folder/'latest_report.md').exists())


if __name__=='__main__':unittest.main()
