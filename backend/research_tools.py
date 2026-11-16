# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Collect official evidence without an LLM, or audit a saved real-model report."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from common import load_env, load_config, output_lock, now, write_private
from deep_research import validate_deep_result
from official_sources import collect_official
from holdings_cache import research_snapshot
import copy

ROOT=Path(__file__).resolve().parent


def replay_report(previous, snapshot, symbols=None, model=None):
    """Re-analyse a fixed public source snapshot without recollecting or contacting a broker."""
    from agents import Model
    from model_runtime import runtime_info
    from broker import value_snapshot
    from deep_research import run_deep_research
    companies = previous.get('research_v2', {}).get('companies', [])
    if not companies:
        raise ValueError('심층 보고서 JSON이 필요합니다.')
    available_symbols = {c['symbol'] for c in companies}
    origin = previous.get('research_v2',{}).get('replay',{}).get('origin_symbols',[])
    all_symbols = available_symbols | set(origin) | {p['symbol'] for p in snapshot.get('positions',[])}
    selected = set(symbols or available_symbols)
    if not selected <= available_symbols:
        raise ValueError('이전 보고서에 없는 재분석 종목입니다.')
    valued = value_snapshot(copy.deepcopy(snapshot))
    valued['positions'] = [p for p in valued['positions'] if p['symbol'] in selected]
    if {p['symbol'] for p in valued['positions']} != selected:
        raise ValueError('저장 포트폴리오와 재분석 종목이 일치하지 않습니다.')
    notes = ['저장된 보고서의 공개자료를 재사용한 분석; 새 시세·공시·뉴스를 수집하지 않았습니다.',
             '재분석 비중은 저장 포트폴리오 가격 기준이며 현재 비중이 아닙니다.']
    prior_subset=previous.get('research_v2',{}).get('replay',{}).get('full_portfolio') is False
    if selected != all_symbols or prior_subset:
        notes.append('일부 종목만 재분석했습니다. 전체 포트폴리오의 완료 결과가 아닙니다.')
    valued['warnings'] = list(dict.fromkeys(valued.get('warnings', []) + notes))
    company_ids = {eid for c in companies for eid in c.get('source_ids', [])}
    selected_ids = {eid for c in companies if c['symbol'] in selected for eid in c.get('source_ids',[])}
    groups = previous.get('research_v2',{}).get('source_groups',{})
    group_company=set(groups.get('portfolio',[]))
    group_macro=set(groups.get('macro',[]))
    def belongs(source):
        return source['id'] in company_ids|group_company or bool(set(source.get('symbols', [])) & all_symbols)
    public_sources = previous.get('sources', [])
    portfolio_sources = [copy.deepcopy(s) for s in public_sources if
                         s['id'] in selected_ids or bool(set(s.get('symbols',[]))&selected)]
    # Old subset replays retained other issuers' sources. Unclassified financial
    # documents must never become independent macro evidence by subtraction.
    company_kinds={'financial','financial_summary','calculation','filing_excerpt','filing_index','fund_document'}
    def legacy_macro(source):
        if source.get('kind') in company_kinds:return False
        labels=source.get('symbols',[])
        # For old files without groups, keep named macro index/FX/commodity
        # series and unlabelled official feeds. Unknown issuer news is omitted.
        return not labels or all(s.startswith('^') or s.endswith(('=X','=F')) or s=='DX-Y.NYB' for s in labels)
    macro_sources = [copy.deepcopy(s) for s in public_sources if not belongs(s) and
                     (s['id'] in group_macro if groups else legacy_macro(s))]
    fields = ('symbol','asset_type','sector','records','metrics','source_ids','questions','cik','corp_code','filings')
    dossiers = {}
    for c in companies:
        if c['symbol'] not in selected:continue
        d = {k:copy.deepcopy(c[k]) for k in fields if k in c}
        d['warnings'] = [w for w in c.get('warnings', []) if not any(term in w for term in
            (' 제외:', '심층 생성/', '검토를 통과한', '동일 모델의 별도', '문맥 예산으로'))]
        dossiers[c['symbol']] = d
    active_model = model or Model()
    first_call=len(getattr(active_model,'telemetry',[]))
    result = run_deep_research(active_model,valued,
        {'sources':portfolio_sources,'dossiers':dossiers,'warnings':notes},
        {'sources':macro_sources,'warnings':notes,'coverage':'이전 실행에서 수집한 독립 거시 자료'})
    result['model'] = {'backend':getattr(active_model,'backend','fixture'),'model':getattr(active_model,'model','fixture')}
    result['diagnostics']={'runtime':runtime_info()}
    result['research_v2']['model_calls'] = copy.deepcopy(getattr(active_model,'telemetry',[])[first_call:])
    if isinstance(getattr(active_model,'telemetry',None),list):del active_model.telemetry[:first_call]
    result['warnings'] = list(dict.fromkeys(result.get('warnings', []) + notes))
    result['research_v2']['replay'] = {'source_created_at':previous.get('created_at'),'symbols':sorted(selected),
                                     'origin_symbols':sorted(all_symbols),'full_portfolio':selected==all_symbols and not prior_subset}
    return result,valued


def evaluate_report(result):
    sources=result.get('sources',[]);by_id={s['id']:s for s in sources}
    errors=[]
    if len(by_id)!=len(sources):errors.append('중복 출처 ID')
    deep=result.get('research_v2')
    if not deep:raise ValueError('심층 보고서의 reports/*.json을 선택하세요.')
    try:validate_deep_result(deep,set(by_id))
    except ValueError as exc:errors.append(type(exc).__name__+': '+str(exc))
    audits=[]
    for c in deep['companies']:audits+=c.get('quality_audit',[])+c.get('macro_audit',[])
    audits+=result.get('macro',{}).get('quality_audit',[])
    for audit in audits:
        for q in audit.get('quotes',[]):
            s=by_id.get(q.get('evidence_id'))
            if not s or q.get('quote','') not in s.get(q.get('field'), ''):errors.append('원문에 없는 검토 인용문')
    accepted=sum(bool(a.get('accepted')) for a in audits)
    return {'at':now(),'structural_errors':list(dict.fromkeys(errors)),
        'companies':len(deep['companies']),
        'verified_companies':sum(c['status']=='ok' for c in deep['companies']),
        'linked_companies':sum(c.get('macro_status')=='ok' for c in deep['companies']),
        'reviewed_items':len(audits),'accepted_items':accepted,
        'rejected_items':len(audits)-accepted,
        'company_coverage':[{'symbol':c['symbol'],'accepted_insights':len(c.get('insights',[])),
            'unmet_topics':[r['label'] for r in c['coverage'] if r['status']!='검토 통과'],
            'ai_source_selection':c.get('source_budget')} for c in deep['companies']],
        'limits':['통과율은 정확도 점수가 아닙니다. 실제 의미·인과·중요도는 원문과 사람이 재검토해야 합니다.',
                  '이 진단은 JSON 구조·인용 구절 존재·주제 누락을 점검하며 투자 성과를 검증하지 않습니다.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    collect=sub.add_parser('collect',help='SEC/DART/지정 공식문서만 수집; LLM/토스 호출 없음')
    collect.add_argument('--holdings',required=True)
    collect.add_argument('--config',default=str(ROOT/'config.json'))
    collect.add_argument('--output',default=str(ROOT/'diagnostics'))
    evaluate=sub.add_parser('evaluate',help='저장된 실제 모델 보고서 품질 진단')
    evaluate.add_argument('--report',required=True)
    evaluate.add_argument('--output',default=str(ROOT/'diagnostics'))
    replay=sub.add_parser('replay',help='이전 보고서 JSON 자료로 재분석; 외부 수집/토스 호출 없음')
    replay.add_argument('--report',required=True)
    replay.add_argument('--holdings',required=True)
    replay.add_argument('--symbols',nargs='+')
    replay.add_argument('--output',default=str(ROOT/'data'/'replay'))
    args=parser.parse_args();load_env(ROOT/'.env')
    output=Path(args.output).expanduser().resolve()
    if args.command=='replay':
        from app import save_report
        previous=json.loads(Path(args.report).expanduser().read_text())
        with output_lock(output):
            from agents import Model
            active_model=Model()
            active_model.diagnostic_path=output/'diagnostics'/'replay_model_calls.jsonl'
            result,snapshot=replay_report(previous,research_snapshot(Path(args.holdings).expanduser()),args.symbols,active_model)
            path=save_report(result,snapshot,output)
        print('저장자료 재분석:',path)
        print('재분석 상태:','complete' if result['complete'] else 'partial')
        return 0 if result['complete'] else 2
    if args.command=='collect':
        snapshot=research_snapshot(Path(args.holdings).expanduser())
        config=load_config(args.config)
        bundle=collect_official(snapshot,config)
        write_private(output/'official_evidence.json',bundle)
        stats={s:dict(records=len(d['records']),metrics=len(d['metrics']),sources=len(d['source_ids']),warnings=d['warnings']) for s,d in bundle['dossiers'].items()}
        write_private(output/'collection_quality.json',{'at':now(),'companies':stats})
        print('수집 결과:',output/'collection_quality.json')
        return 0 if any(d['source_ids'] for d in bundle['dossiers'].values()) else 2
    result=json.loads(Path(args.report).read_text())
    report=evaluate_report(result)
    write_private(output/'quality_check.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('company_coverage',)},ensure_ascii=False,indent=2))
    return 1 if report['structural_errors'] else 2 if not report['verified_companies'] else 0


if __name__=='__main__':raise SystemExit(main())
