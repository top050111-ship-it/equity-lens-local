"""Offline regression tests. Fixtures exercise logic, not real model accuracy."""
from __future__ import annotations
from copy import deepcopy
from datetime import date
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import evidence, decimal, now
from financials import (duration_basis,sec_records,dart_records,deduplicate_records,
    financial_evidence,compute_metrics,number)
from official_sources import (SEC,DART,PublicCache,OfficialError,document_text,excerpt_sources,
    zip_documents,sec_cik,collect_official)
from deep_research import (Insight,ResearchDraft,source_budget,review_draft,run_deep_research,
    validate_deep_result,numeric_tokens)
from agents import (Finding,Report,EvidenceReview,ReviewDecision,EvidenceQuote,review_report,
    checked_sources,validate_references)


def record(metric='revenue',value='100',start='2026-01-01',end='2026-03-31',basis='quarter',unit='USD',**extra):
    return dict(symbol='TEST',metric=metric,value=value,start=start,end=end,basis=basis,unit=unit,
        scope='consolidated',tag='us-gaap:'+metric,filed='2026-05-01',provider='SEC',
        accession='0000000001-26-000001',cik='1',evidence_id='Eoriginal',**extra)


def sec_payload(rows,tag='Revenues'):
    return {'cik':1,'facts':{'us-gaap':{tag:{'units':{'USD':rows}}}}}


def sec_row(value=100,start='2026-01-01',end='2026-03-31',filed='2026-05-01',**kwargs):
    return dict(val=value,start=start,end=end,filed=filed,form='10-Q',accn='0000000001-26-000001',**kwargs)


def dart_row(metric='매출액',sj='IS',value='200',**extra):
    return dict(account_nm=metric,sj_div=sj,thstrm_amount=value,thstrm_add_amount='500',
        frmtrm_amount='900',frmtrm_q_amount='150',frmtrm_add_amount='400',currency='KRW',
        rcept_no='20261116000001',corp_code='00000001',**extra)


def insight(eid,observation='매출 100 USD가 기록되었습니다.',**extra):
    data=dict(topic='growth',title='가상 매출 논점',kind='inference',observation=observation,
        interpretation='현금 회수 여부를 함께 확인해야 합니다.',mechanism='수요가 이어지면 매출과 현금 유입으로 연결될 가능성이 있습니다.',
        horizon='다음 보고기간의 이행 여부에 따라 달라집니다.',countercase='현금 회수가 지연될 수 있습니다.',
        monitor='다음 보고서의 영업현금흐름을 확인합니다.',impact='uncertain',evidence_ids=[eid])
    data.update(extra);return Insight(**data)


class FixtureModel:
    backend='fixture';model='fixture'
    def __init__(self,reject=False,macro_fail=False,bad_quote=False):
        self.reject=reject;self.macro_fail=macro_fail;self.bad_quote=bad_quote;self.macro_payload=None
    def research(self,payload):
        s=next(x for x in payload['sources'] if x['kind']=='financial')
        return ResearchDraft(insights=[insight(s['id'])])
    def analyze(self,role,payload):
        if role=='macro':
            self.macro_payload=deepcopy(payload)
            if self.macro_fail:raise ValueError('fixture macro fail')
        ids=payload.get('source_groups')
        chosen=[ids['portfolio'][0],ids['macro'][0]] if ids else [payload['sources'][0]['id']]
        return Report(summary='',findings=[Finding(subject=role,claim='[TEST] 가상 기업 자료와 가상 거시 지표를 연결한 조건부 해석입니다. 다음 보고기간의 현금흐름을 확인합니다.',
            kind='scenario',evidence_ids=chosen,confidence='medium',counterpoint='경로가 실현되지 않을 수 있습니다.',revisit_when='다음 공시를 확인합니다.')],
            conflicts=[],watchlist=[],limitations=[])
    def review(self,role,payload):
        sm={s['id']:s for s in payload['sources']}
        return EvidenceReview(decisions=[ReviewDecision(item_id=i['item_id'],supported=not self.reject,
            portfolio_linked=role=='synthesis',duplicate_of='',reason='고정 대역 판정; 의미 검증 아님',
            quotes=[EvidenceQuote(evidence_id=e,field='content',quote='없는 구절 XXXXX' if self.bad_quote else sm[e]['content']) for e in i['evidence_ids']]) for i in payload['items']])


def bundles():
    s=evidence('가상 자료','TEST 가상 재무','https://example.com/company','TEST revenue=100 USD',now(),kind='financial',symbols=['TEST'])
    m=evidence('가상 자료','가상 금리','https://example.com/macro','가상의 금리 관측 자료이며 실제 데이터 아님',now(),kind='indicator')
    position=dict(symbol='TEST',name='가상 기업',market='NASDAQ',currency='USD',weight_pct=100,price_quality='가상',quantity='PRIVATE')
    snapshot={'as_of':now(),'positions':[position],'warnings':[]}
    dossier=dict(symbol='TEST',asset_type='company',sector='general',records=[],metrics=[],warnings=[],source_ids=[s['id']])
    return snapshot,{'sources':[s],'warnings':[],'dossiers':{'TEST':dossier}}, {'sources':[m],'warnings':[]}


class FinancialTests(unittest.TestCase):
    def test_quarter_and_annual(self):
        self.assertEqual(duration_basis('2026-01-01','2026-03-31'),'quarter')
        self.assertEqual(duration_basis('2026-01-01','2026-12-31'),'annual')
    def test_short_stub_rejected(self):self.assertIsNone(duration_basis('2026-01-01','2026-01-31'))
    def test_numbers(self):
        self.assertEqual(number('(1,234)'),'‑1234'.replace('‑','-'))
        self.assertIsNone(number(True));self.assertIsNone(number('NaN'))
    def test_sec_excludes_future_filing(self):
        rows,_=sec_records(sec_payload([sec_row(filed='2027-01-01')]),'TEST',cutoff='2026-11-16')
        self.assertEqual(rows,[])
    def test_sec_latest_restatement(self):
        rows,_=sec_records(sec_payload([sec_row(),sec_row(value=120,filed='2026-06-01')]),'TEST',cutoff='2026-11-16')
        self.assertEqual(rows[0]['value'],'120')
    def test_sec_conflicting_same_filing_excluded(self):
        rows,warnings=sec_records(sec_payload([sec_row(),sec_row(value=120)]),'TEST',cutoff='2026-11-16')
        self.assertEqual(rows,[]);self.assertTrue(warnings)
    def test_sec_wrong_currency_excluded(self):
        rows,_=sec_records(sec_payload([sec_row()]),'TEST','KRW',cutoff='2026-11-16');self.assertEqual(rows,[])
    def test_sec_uses_actual_dates_not_fiscal_labels(self):
        r=sec_row();r.update(fy=2027,fp='FY')
        rows,_=sec_records(sec_payload([r]),'TEST',cutoff='2026-11-16');self.assertEqual(rows[0]['basis'],'quarter')
    def test_dart_quarter_ytd_are_separate(self):
        rows,_=dart_records([dart_row()],'TEST',2026,'11014','CFS')
        current=[r for r in rows if r['end']=='2026-09-30']
        self.assertEqual({(r['basis'],r['value']) for r in current},{('quarter','200'),('ytd','500')})
    def test_dart_cf_is_cumulative(self):
        rows,_=dart_records([dart_row('영업활동현금흐름','CF')],'TEST',2026,'11014','CFS')
        self.assertEqual([(r['basis'],r['start'],r['value']) for r in rows],[('ytd','2026-01-01','200')])
    def test_dart_bs_comparison_is_year_end(self):
        rows,_=dart_records([dart_row('자산총계','BS')],'TEST',2026,'11014','CFS')
        self.assertIn('2025-12-31',[r['end'] for r in rows]);self.assertNotIn('2026-06-30',[r['end'] for r in rows])
    def test_dart_duplicate_conflict_excluded(self):
        rows,notes=dart_records([dart_row(),dart_row(value='999')],'TEST',2026,'11014','CFS')
        self.assertFalse(rows);self.assertTrue(notes)
    def test_no_cross_period_margin(self):
        rows=[record('operating_income','20'),record('revenue','100',basis='ytd')]
        metrics,_=compute_metrics(rows,'TEST');self.assertFalse(metrics)
    def test_no_cross_currency_ratio(self):
        rows=[record('operating_income','20'),record('revenue','100',unit='KRW')]
        self.assertFalse(compute_metrics(rows,'TEST')[0])
    def test_no_cross_scope_ratio(self):
        a=record('operating_income','20');b=record();b['scope']='OFS'
        self.assertFalse(compute_metrics([a,b],'TEST')[0])
    def test_margin_decimal(self):
        metrics,_=compute_metrics([record('operating_income','25'),record('revenue','200')],'TEST')
        self.assertEqual(metrics[0]['value'],'12.50')
    def test_fcf_ppe_proxy(self):
        metrics,_=compute_metrics([record('operating_cashflow','-10'),record('ppe_purchases','20')],'TEST')
        self.assertEqual(metrics[0]['name'],'fcf_ppe_proxy');self.assertEqual(metrics[0]['value'],'-30.00')
    def test_negative_ppe_not_silently_abs(self):
        self.assertFalse(compute_metrics([record('operating_cashflow','10'),record('ppe_purchases','-20')],'TEST')[0])
    def test_negative_net_income_no_cash_conversion(self):
        metrics,_=compute_metrics([record('operating_cashflow','10'),record('net_income','-20')],'TEST')
        self.assertFalse(any(m['name']=='cash_conversion' for m in metrics))
    def test_yoy(self):
        a=record(value='120');b=record(value='100',start='2025-01-01',end='2025-03-31')
        metrics,_=compute_metrics([a,b],'TEST');self.assertEqual(next(m for m in metrics if m['name']=='revenue_yoy')['value'],'20.00')
    def test_yoy_same_tag_required(self):
        a=record();b=record(start='2025-01-01',end='2025-03-31');b['tag']='different'
        self.assertFalse(compute_metrics([a,b],'TEST')[0])
    def test_no_qoq_over_gap(self):
        a=record(start='2026-07-01',end='2026-09-30');b=record()
        self.assertFalse(any(m['name']=='revenue_qoq' for m in compute_metrics([a,b],'TEST')[0]))
    def test_qoq_contiguous(self):
        a=record(value='120',start='2026-04-01',end='2026-06-30');b=record()
        self.assertEqual(next(m for m in compute_metrics([a,b],'TEST')[0] if m['name']=='revenue_qoq')['value'],'20.00')
    def test_zero_denominator_not_zero_growth(self):
        a=record();b=record(value='0',start='2025-01-01',end='2025-03-31')
        self.assertFalse(compute_metrics([a,b],'TEST')[0])
    def test_financial_citation_preserves_value(self):
        rows=[record(value='100000000000000')]
        sources=financial_evidence(rows,'TEST','1')
        self.assertIn('100000000000000',sources[0]['content']);self.assertEqual(rows[0]['evidence_id'],sources[0]['id'])
        self.assertLessEqual(len(sources[0]['content']),1600)
    def test_dedup_latest(self):
        a=record();b=deepcopy(a);b.update(value='120',filed='2026-06-01')
        self.assertEqual(deduplicate_records([a,b])[0][0]['value'],'120')


class SourceTests(unittest.TestCase):
    def test_hidden_text_removed(self):
        text=document_text(b'<html><head>hidden head</head><body><p>Visible revenue</p><script>hidden script</script><ix:hidden>hidden xbrl</ix:hidden><p>Visible cash</p></body></html>')
        self.assertIn('Visible revenue',text);self.assertNotIn('hidden',text)
    def test_inline_text_not_glued(self):
        self.assertEqual(document_text(b'<p>Revenue <b>100</b> USD</p>'),'Revenue 100 USD')
    def test_excerpt_offsets(self):
        raw=('<p>Revenue '+('cash flow strong '*100)+'</p>').encode()
        text=document_text(raw);sources=excerpt_sources(raw,symbol='TEST',provider='SEC',title='10-Q',url='https://example.com',filed='2026-11-01',event_key='test')
        for s in sources:self.assertEqual(text[s['excerpt_start']:s['excerpt_end']],s['content'])
    def test_pdf_explicit_gap(self):
        with self.assertRaises(OfficialError):document_text(b'%PDF-1.7')
    def test_invalid_zip(self):
        with self.assertRaises(OfficialError):zip_documents(b'<result>API ERROR</result>')
    def test_zip_bounds(self):
        data=io.BytesIO()
        with ZipFile(data,'w') as z:z.writestr('doc.xml','a'*100)
        with self.assertRaises(OfficialError):zip_documents(data.getvalue(),max_total=50)
    def test_cik_validation(self):
        self.assertEqual(sec_cik('1'),'0000000001')
        with self.assertRaises(OfficialError):sec_cik('../1')
    def test_sec_missing_identity(self):
        with patch.dict(os.environ,{'SEC_USER_AGENT':''}):
            with self.assertRaises(OfficialError):SEC(PublicCache())
    def test_dart_missing_key(self):
        with patch.dict(os.environ,{'DART_API_KEY':''}):
            with self.assertRaises(OfficialError):DART(PublicCache())
    def test_cache_no_secret(self):
        with tempfile.TemporaryDirectory() as d, patch('official_sources.request',return_value={'status':'000','list':[]}) as call:
            c=PublicCache(d);c.get('https://example.com',provider='DART',params={'crtfc_key':'SECRET','corp_code':'1'})
            self.assertNotIn('SECRET',''.join(p.read_text() for p in Path(d).glob('*')))
            c.get('https://example.com',provider='DART',params={'crtfc_key':'SECRET','corp_code':'1'})
            self.assertEqual(call.call_count,1)
    def test_api_error_not_cached(self):
        with tempfile.TemporaryDirectory() as d,patch('official_sources.request',return_value={'status':'020'}):
            with self.assertRaises(OfficialError):PublicCache(d).get('https://example.com',provider='DART')
            self.assertFalse(list(Path(d).glob('*')))
    def test_fund_does_not_call_company_api(self):
        p={'symbol':'SOXL','market':'AMEX','currency':'USD'}
        with patch('official_sources.SEC') as api:
            out=collect_official({'positions':[p]}, {})
            api.assert_not_called();self.assertEqual(out['dossiers']['SOXL']['asset_type'],'fund')
    def test_conflicting_ids_rejected(self):
        s=evidence('T','title','https://example.com','test content');b=deepcopy(s);b['content']='changed'
        with self.assertRaises(ValueError):checked_sources([s,b])


class PipelineTests(unittest.TestCase):
    def test_numeric_gate_blocks_even_optimistic_reviewer(self):
        snap,p,m=bundles();s=p['sources'][0]
        draft=ResearchDraft(insights=[insight(s['id'],observation='매출 999 USD가 기록되었습니다.')])
        kept,audit,_=review_draft(FixtureModel(),draft,{'positions':snap['positions'],'sources':[s]})
        self.assertFalse(kept);self.assertIn('수치',audit[0]['reason'])
    def test_numeric_gate_allows_formatting(self):
        self.assertEqual(numeric_tokens('매출 1,234.00 USD'),{decimal('1234')})
    def test_absent_quote_rejected(self):
        snap,p,m=bundles();s=p['sources'][0]
        kept,_,_=review_draft(FixtureModel(bad_quote=True),ResearchDraft(insights=[insight(s['id'])]),{'positions':snap['positions'],'sources':[s]})
        self.assertFalse(kept)
    def test_semantic_rejection_preserved(self):
        snap,p,m=bundles();result=run_deep_research(FixtureModel(reject=True),snap,p,m)
        self.assertFalse(result['complete']);self.assertFalse(result['research_v2']['companies'][0]['insights'])
    def test_end_to_end_schema(self):
        snap,p,m=bundles();model=FixtureModel();r=run_deep_research(model,snap,p,m)
        self.assertTrue(r['complete']);self.assertEqual(r['research_v2']['quality']['accepted_insights'],1)
        validate_deep_result(r['research_v2'],{s['id'] for s in r['sources']})
    def test_macro_isolation(self):
        snap,p,m=bundles();model=FixtureModel();run_deep_research(model,snap,p,m)
        self.assertNotIn('positions',model.macro_payload);self.assertNotIn('PRIVATE',json.dumps(model.macro_payload))
    def test_macro_failure_retains_company(self):
        snap,p,m=bundles();r=run_deep_research(FixtureModel(macro_fail=True),snap,p,m)
        self.assertFalse(r['complete']);self.assertEqual(len(r['research_v2']['companies'][0]['insights']),1)
    def test_budget_balances_types(self):
        rows=[evidence('T',str(i),'https://example.com/'+str(i),'quote data',kind='quote') for i in range(20)]
        rows.append(evidence('T','official','https://example.com/financial','official financial data',kind='financial'))
        selected,b=source_budget(rows,3,10000)
        self.assertIn('financial',[s['kind'] for s in selected]);self.assertEqual(b['omitted'],18)
    def test_budget_keeps_full_content(self):
        s=evidence('T','long','https://example.com','x'*1500,kind='financial')
        selected,b=source_budget([s],5,2000);self.assertEqual(selected[0]['content'],s['content'])
    def test_invalid_deep_reference_rejected(self):
        snap,p,m=bundles();r=run_deep_research(FixtureModel(),snap,p,m);r['research_v2']['companies'][0]['insights'][0]['evidence_ids']=['INVALID']
        with self.assertRaises(ValueError):validate_deep_result(r['research_v2'],{s['id'] for s in r['sources']})
    def test_report_render_and_changes(self):
        from research_render import render_deep_report,update_changes
        snap,p,m=bundles();r=run_deep_research(FixtureModel(),snap,p,m)
        with tempfile.TemporaryDirectory() as d:update_changes(r,Path(d))
        text=render_deep_report(r,snap)
        self.assertIn('누락 점검',text);self.assertIn('매출 100',text);self.assertNotIn('PRIVATE',text)
    def test_local_server_roundtrip(self):
        import server
        snap,p,m=bundles();r=run_deep_research(FixtureModel(),snap,p,m)
        parsed=server.report(r)
        self.assertEqual(parsed['research_v2']['quality']['accepted_insights'],1)
    def test_demo_unchanged(self):
        from app import demo
        with tempfile.TemporaryDirectory() as d:
            demo(Path(d));self.assertTrue((Path(d)/'latest_report.md').exists())
    def test_bad_citation_invalidates_whole_claim(self):
        snap,p,m=bundles();s=p['sources'][0]
        f=Finding(subject='test',claim='claim',kind='fact',evidence_ids=[s['id'],'INVALID'],confidence='low',counterpoint='',revisit_when='')
        r=validate_references(Report(summary='',findings=[f],conflicts=[],watchlist=[],limitations=[]),[s])
        self.assertFalse(r.findings)


if __name__=='__main__':unittest.main()
