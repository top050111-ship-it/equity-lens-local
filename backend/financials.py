# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Period-safe financial normalization. All arithmetic uses Decimal, never an LLM.
No TTM synthesis, automatic annualization, consensus or valuation estimates.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta
import json
import re
from common import decimal, evidence

# Ordered, exact standard concepts. Do not infer a company-extension's meaning.
SEC_TAGS = {
    'revenue': ['us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
                'us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax',
                'us-gaap:Revenues', 'us-gaap:SalesRevenueNet', 'ifrs-full:Revenue'],
    'operating_income': ['us-gaap:OperatingIncomeLoss', 'ifrs-full:ProfitLossFromOperatingActivities'],
    'net_income': ['us-gaap:ProfitLoss', 'us-gaap:NetIncomeLoss', 'ifrs-full:ProfitLoss'],
    'gross_profit': ['us-gaap:GrossProfit', 'ifrs-full:GrossProfit'],
    'operating_cashflow': ['us-gaap:NetCashProvidedByUsedInOperatingActivities',
                           'ifrs-full:CashFlowsFromUsedInOperatingActivities'],
    'ppe_purchases': ['us-gaap:PaymentsToAcquirePropertyPlantAndEquipment',
                      'ifrs-full:PurchaseOfPropertyPlantAndEquipment'],
    'cash': ['us-gaap:CashAndCashEquivalentsAtCarryingValue', 'ifrs-full:CashAndCashEquivalents'],
    'assets': ['us-gaap:Assets', 'ifrs-full:Assets'],
    'liabilities': ['us-gaap:Liabilities', 'ifrs-full:Liabilities'],
    'equity': ['us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
               'us-gaap:StockholdersEquity', 'ifrs-full:Equity'],
    'current_assets': ['us-gaap:AssetsCurrent', 'ifrs-full:CurrentAssets'],
    'current_liabilities': ['us-gaap:LiabilitiesCurrent', 'ifrs-full:CurrentLiabilities'],
    'inventory': ['us-gaap:InventoryNet', 'ifrs-full:Inventories'],
}
INSTANT = {'cash', 'assets', 'liabilities', 'equity', 'current_assets', 'current_liabilities', 'inventory'}
LABELS = {'revenue': '매출액', 'operating_income': '영업이익', 'net_income': '당기순이익',
          'gross_profit': '매출총이익', 'operating_cashflow': '영업현금흐름',
          'ppe_purchases': '유형자산 취득 현금유출', 'cash': '현금및현금성자산',
          'assets': '자산총계', 'liabilities': '부채총계', 'equity': '자본총계',
          'current_assets': '유동자산', 'current_liabilities': '유동부채', 'inventory': '재고자산'}
DART_NAMES = {
    'revenue': ['매출액', '수익(매출액)', '영업수익'],
    'operating_income': ['영업이익', '영업이익(손실)', '영업손익'],
    'net_income': ['당기순이익', '당기순이익(손실)', '분기순이익', '분기순이익(손실)',
                   '반기순이익', '반기순이익(손실)'],
    'gross_profit': ['매출총이익', '매출총이익(손실)'],
    'operating_cashflow': ['영업활동현금흐름', '영업활동으로인한현금흐름', '영업활동으로부터의현금흐름'],
    'ppe_purchases': ['유형자산의취득', '유형자산취득'],
    'cash': ['현금및현금성자산'], 'assets': ['자산총계'], 'liabilities': ['부채총계'],
    'equity': ['자본총계'], 'current_assets': ['유동자산'],
    'current_liabilities': ['유동부채'], 'inventory': ['재고자산'],
}


def iso(value):
    return date.fromisoformat(str(value)[:10])


def number(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(',', '')
    if text in ('', '-', '.', 'N/A'):
        return None
    if text.startswith('(') and text.endswith(')'):
        text = '-' + text[1:-1]
    try:
        return str(decimal(text))
    except ValueError:
        return None


def duration_basis(start, end):
    days = (iso(end) - iso(start)).days + 1
    if 70 <= days <= 105:
        return 'quarter'
    if 150 <= days <= 205 or 240 <= days <= 300:
        return 'ytd'
    if 330 <= days <= 380:
        return 'annual'
    return None


def sec_records(payload, symbol, currency='USD', cutoff=None):
    cutoff = cutoff or date.today().isoformat()
    candidates, warnings = defaultdict(list), []
    for metric, tags in SEC_TAGS.items():
        for priority, full_tag in enumerate(tags):
            taxonomy, tag = full_tag.split(':', 1)
            units = payload.get('facts', {}).get(taxonomy, {}).get(tag, {}).get('units', {})
            for row in units.get(currency, []):
                try:
                    end, filed = iso(row['end']).isoformat(), iso(row['filed']).isoformat()
                    if filed > cutoff or end > cutoff or row.get('form') not in ('10-K','10-Q','20-F','40-F','10-K/A','10-Q/A','20-F/A','40-F/A'):
                        continue
                    start = row.get('start')
                    basis = 'instant' if metric in INSTANT and not start else duration_basis(start, end) if start else None
                    value = number(row.get('val'))
                    accession = str(row.get('accn', ''))
                    if not basis or value is None or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession):
                        continue
                    record = dict(symbol=symbol, metric=metric, value=value, unit=currency,
                                  start=start or '', end=end, basis=basis, scope='consolidated',
                                  tag=full_tag, filed=filed, accession=accession, provider='SEC')
                    candidates[(metric, start or '', end, basis, currency)].append((priority, record))
                except (ValueError, TypeError, KeyError):
                    continue
    records = []
    for key, items in candidates.items():
        # Prefer exact concept priority, then the latest filing for that concept.
        priority = min(x[0] for x in items)
        same = [r for p, r in items if p == priority]
        latest = max(r['filed'] for r in same)
        same = [r for r in same if r['filed'] == latest]
        if len({decimal(r['value']) for r in same}) != 1:
            warnings.append(f'{symbol} {key[0]} {key[2]}: 동일 기간·공시일의 상충 값 제외')
            continue
        records.append(sorted(same, key=lambda r: r['accession'])[-1])
    return records, warnings


def clean_name(value):
    return re.sub(r'\s+', '', value or '')


def dart_records(rows, symbol, year, code, scope, currency='KRW'):
    """DART IS/CIS amount=3 months; add_amount=YTD. CF amount=YTD.
    Comparative CF is deliberately not guessed from ambiguous prior-year labels.
    Non-December fiscal years are rejected by the caller.
    """
    month = {'11013': 3, '11012': 6, '11014': 9, '11011': 12}[code]
    end = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    start_q = date(year, month - 2, 1)
    start_y = date(year, 1, 1)
    chosen, warnings = {}, []
    for metric in SEC_TAGS:
        accepted = []
        for row in rows:
            sj = row.get('sj_div')
            if sj not in (('BS',) if metric in INSTANT else ('CF',) if metric in ('operating_cashflow','ppe_purchases') else ('IS','CIS')):
                continue
            if row.get('currency') != currency:
                continue
            account_id = str(row.get('account_id', '')).replace('ifrs-full_', 'ifrs-full:')
            allowed_ids = {t for t in SEC_TAGS[metric] if t.startswith('ifrs-full:')}
            # Operating income is a DART extension of IFRS.
            if metric == 'operating_income':
                allowed_ids.add('dart:OperatingIncomeLoss')
                account_id = account_id.replace('dart_', 'dart:')
            score = 0 if account_id in allowed_ids else 1 if clean_name(row.get('account_nm')) in DART_NAMES[metric] else None
            if score is not None:
                accepted.append((score, row))
        if not accepted:
            continue
        best = min(p for p, _ in accepted)
        matched = [r for p, r in accepted if p == best]
        fields = ('thstrm_amount','thstrm_add_amount','frmtrm_amount','frmtrm_q_amount','frmtrm_add_amount')
        if len({tuple(decimal(n) if (n:=number(r.get(k))) is not None else None for k in fields) for r in matched}) != 1:
            warnings.append(f'{symbol} {metric}: 일치 계정이 여러 개이고 금액이 달라 제외')
            continue
        chosen[metric] = matched[0]
    result = []
    for metric, row in chosen.items():
        rcp = str(row.get('rcept_no', ''))
        if not re.fullmatch(r'\d{14}', rcp):
            continue
        filed = f'{rcp[:4]}-{rcp[4:6]}-{rcp[6:8]}'
        def add(field, start, finish, basis):
            value = number(row.get(field))
            if value is not None:
                result.append(dict(symbol=symbol, metric=metric, value=value, unit=currency,
                                   start=start.isoformat() if start else '', end=finish.isoformat(),
                                   basis=basis, scope=scope, tag=row.get('account_id') or row.get('account_nm'),
                                   filed=filed, accession=rcp, provider='DART', amount_field=field))
        if metric in INSTANT:
            add('thstrm_amount', None, end, 'instant')
            # Prior BS is the preceding fiscal year-end, not preceding quarter.
            add('frmtrm_amount', None, date(year-1,12,31), 'instant')
        elif metric in ('operating_cashflow','ppe_purchases'):
            add('thstrm_amount', start_y, end, 'annual' if month == 12 else 'ytd' if month > 3 else 'quarter')
        elif month == 12:
            add('thstrm_amount', start_y, end, 'annual')
            add('frmtrm_amount', date(year-1,1,1), date(year-1,12,31), 'annual')
        else:
            add('thstrm_amount', start_q, end, 'quarter')
            add('frmtrm_q_amount', date(year-1,month-2,1), end.replace(year=year-1), 'quarter')
            if month > 3:
                add('thstrm_add_amount', start_y, end, 'ytd')
                add('frmtrm_add_amount', date(year-1,1,1), end.replace(year=year-1), 'ytd')
    return result, warnings


def deduplicate_records(records):
    groups, result, warnings = defaultdict(list), [], []
    for r in records:
        groups[tuple(r[k] for k in ('symbol','metric','unit','start','end','basis','scope'))].append(r)
    for key, rows in groups.items():
        latest = max(r['filed'] for r in rows)
        rows = [r for r in rows if r['filed'] == latest]
        if len({decimal(r['value']) for r in rows}) != 1:
            warnings.append(f'{key[0]} {key[1]} {key[4]}: 상충 재무값 제외')
        else:
            result.append(rows[-1])
    return result, warnings


def financial_evidence(records, symbol, cik=None):
    """Group short facts by exact filing and period. Each value gets a real citation."""
    groups, sources = defaultdict(list), []
    for r in records:
        groups[tuple(r[k] for k in ('provider','accession','start','end','basis','scope','unit'))].append(r)
    for (provider, acc, start, end, basis, scope, unit), rows in sorted(groups.items(), key=lambda kv: kv[0][3], reverse=True):
        url = ('https://www.sec.gov/Archives/edgar/data/' + str(int(cik)) + '/' + acc.replace('-','') + '/' + acc + '-index.html') if provider == 'SEC' else 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + acc
        # Split groups to keep content under common.evidence's 1600-character bound.
        for offset in range(0,len(rows),2):
            batch = rows[offset:offset+2]
            body = dict(symbol=symbol, period_start=start or None, period_end=end, basis=basis,
                        scope=scope, unit=unit, filed=batch[0]['filed'], accession=acc,
                        period_label=('3개월 단일분기' if basis=='quarter' else ('누적 '+str((iso(end).month-iso(start).month+1))+'개월' if basis=='ytd' else '연간' if basis=='annual' else '기말 시점')),
                        facts=[{**{k:r.get(k) for k in ('metric','value','tag','amount_field')},
                                'display': (str((decimal(r['value'])/decimal('100000000')).quantize(decimal('0.01')))+' 억 '+unit) if unit=='KRW' else (str((decimal(r['value'])/decimal('1000000')).quantize(decimal('0.01')))+' million '+unit)} for r in batch])
            content = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
            if len(content) > 1500:
                raise ValueError('financial evidence chunk too large')
            s = evidence(provider, f'{symbol} 재무수치 {end} {basis} {offset//2+1}', url,
                         content, batch[0]['filed']+'T00:00:00+00:00', kind='financial', symbols=[symbol],
                         primary=True, content_scope='API 재무수치; 공시일은 날짜 정밀도, 현재 조회 수정치 포함',
                         period_end=end, period_start=start, basis=basis, accession=acc)
            sources.append(s)
            for r in batch:
                r['evidence_id'] = s['id']
    return sources


def compute_metrics(records, symbol):
    """Compare only matching currency, consolidation, duration and concepts.
    Up to 8-day fiscal-calendar movement is permitted for 52/53-week issuers.
    """
    output, sources = [], []
    rows = sorted(records, key=lambda r:(r['end'],r['start']), reverse=True)
    def compatible(a,b, same_period=True):
        return (a['unit'],a['scope'],a['basis']) == (b['unit'],b['scope'],b['basis']) and (
            not same_period or (a['start'],a['end']) == (b['start'],b['end']))
    def pair(a,metric):
        return next((b for b in rows if b['metric']==metric and compatible(a,b)), None)
    def emit(name,label,a,b,value,unit,formula):
        inputs = [{k:r.get(k) for k in ('metric','value','unit','start','end','basis','scope','tag','evidence_id')} for r in (a,b)]
        record = dict(name=name,label=label,value=str(value.quantize(decimal('0.01'))),unit=unit,
                      start=a['start'],end=a['end'],basis=a['basis'],scope=a['scope'],formula=formula,inputs=inputs)
        content=json.dumps(record,ensure_ascii=False,separators=(',',':'))
        s=evidence('EQUITY LENS · Decimal 계산',f'{symbol} {label} {a["end"]} {a["basis"]}',
                   'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+a['accession'] if a['provider']=='DART' else
                   'https://www.sec.gov/Archives/edgar/data/' + a.get('cik','0') + '/' + a['accession'].replace('-','') + '/' + a['accession']+'-index.html',
                   content,a['filed']+'T00:00:00+00:00',kind='calculation',symbols=[symbol],primary=False,
                   content_scope='원자료를 코드로 계산; 공식기관 발표 지표가 아님',period_end=a['end'],
                   input_evidence_ids=list(dict.fromkeys(r['evidence_id'] for r in (a,b))))
        if len(content)>1500:
            raise ValueError('calculation evidence too large')
        record['evidence_id']=s['id']; output.append(record); sources.append(s)
    latest={}
    for a in rows:
        latest.setdefault((a['metric'],a['basis'],a['unit'],a['scope']),a)
    for a in latest.values():
        for num,den,name,label in [
            ('operating_income','revenue','operating_margin','영업이익률'),
            ('gross_profit','revenue','gross_margin','매출총이익률'),
            ('net_income','revenue','net_margin','순이익률'),
            ('operating_cashflow','net_income','cash_conversion','영업현금흐름/순이익'),
            ('current_assets','current_liabilities','current_ratio','유동비율'),
            ('liabilities','equity','liabilities_equity','부채총계/자본총계')]:
            if a['metric']!=num:
                continue
            b=pair(a,den)
            if b and decimal(b['value'])>0:
                emit(name,label,a,b,decimal(a['value'])/decimal(b['value'])*100,'%',num+'/'+den+'*100')
        if a['metric']=='operating_cashflow':
            b=pair(a,'ppe_purchases')
            if b and decimal(b['value'])>=0:
                emit('fcf_ppe_proxy','FCF 참고치(영업CF-유형자산취득)',a,b,decimal(a['value'])-decimal(b['value']),a['unit'],
                     'operating_cashflow-ppe_purchases; 무형자산 취득 등 제외, 회사 공표 FCF와 다를 수 있음')
        if a['metric']!='revenue' or a['basis']=='instant':
            continue
        for mode,low,high in [('yoy',357,374),('qoq',70,110)]:
            if mode=='qoq' and a['basis']!='quarter':
                continue
            peers=[]
            for b in rows:
                if b['metric']!='revenue' or not compatible(a,b,False) or b['tag']!=a['tag']:
                    continue
                gap=(iso(a['end'])-iso(b['end'])).days
                length_a=(iso(a['end'])-iso(a['start'])).days
                length_b=(iso(b['end'])-iso(b['start'])).days
                contiguous = abs((iso(a['start'])-iso(b['end'])).days-1)<=1
                if low<=gap<=high and abs(length_a-length_b)<=8 and (mode!='qoq' or contiguous):
                    peers.append(b)
            if peers:
                b=max(peers,key=lambda x:x['end'])
                if decimal(b['value'])>0:
                    emit('revenue_'+mode,'매출 '+('전년 동기' if mode=='yoy' else '전분기')+' 대비 증감률',a,b,
                         (decimal(a['value'])/decimal(b['value'])-1)*100,'%', '(current_revenue/prior_revenue-1)*100')
    return output,sources


def financial_summaries(records,symbol):
    """Compact latest-period data for small-model context; original facts remain linked."""
    latest={}
    for r in records:
        k=(r['basis'],r['scope'],r['unit'])
        latest[k]=max(latest.get(k,''),r['end'])
    grouped=defaultdict(list)
    for r in records:
        if r['end']==latest[(r['basis'],r['scope'],r['unit'])]:
            grouped[tuple(r[k] for k in ('start','end','basis','scope','unit'))].append(r)
    sources=[]
    for (start,end,basis,scope,unit),rows in grouped.items():
        for offset in range(0,len(rows),6):
            batch=rows[offset:offset+6]
            inputs=list(dict.fromkeys(r['evidence_id'] for r in batch))
            content=json.dumps(dict(symbol=symbol,start=start,end=end,basis=basis,scope=scope,unit=unit,
                facts=[{'metric':r['metric'],'value':r['value'],'filed':r['filed']} for r in batch],
                note='공식 API 값을 코드로 재배열; 원자료의 공시일·계정·출처를 별도 보존. 빈 계정은 0이 아님.'),ensure_ascii=False,separators=(',',':'))
            if len(content)>1500:raise ValueError('summary evidence too large')
            original_provider=batch[0]['provider']
            url=('https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+batch[0]['accession'] if original_provider=='DART' else
                 'https://www.sec.gov/Archives/edgar/data/'+str(int(batch[0]['cik']))+'/'+batch[0]['accession'].replace('-','')+'/'+batch[0]['accession']+'-index.html')
            sources.append(evidence('EQUITY LENS · 공식 재무 재구성',f'{symbol} 최신 {basis} 재무 묶음 {offset//6+1}',url,content,
                max(r['filed'] for r in batch)+'T00:00:00+00:00',kind='financial_summary',symbols=[symbol],primary=False,
                period_end=end,period_start=start,basis=basis,input_evidence_ids=inputs,
                content_scope='공식 재무수치 재배열; 원자료 API 계정·기간을 별도 보존'))
    return sources
