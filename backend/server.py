"""Local-only FastAPI bridge. Public mode exposes synthetic UI, never account files."""
from __future__ import annotations
import ipaddress
import json
import math
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from common import load_env, now, write_private
from agents import Report

ROOT = Path(__file__).resolve().parent
load_env(ROOT / '.env')
MODE = os.getenv('APP_MODE', 'local')
if MODE not in ('local', 'public'):
    raise RuntimeError('APP_MODE must be local or public')
DATA = Path(os.getenv('EQUITY_DATA_DIR') or ROOT / 'data').expanduser().resolve()
DIST = ROOT.parent / 'frontend' / ('dist-public' if MODE == 'public' else 'dist')
api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
job = None
job_lock = threading.Lock()
MAX_BYTES = 2_000_000


def fail(message, code=400):
    raise HTTPException(code, message)


@api.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({'error': exc.detail}, status_code=exc.status_code)


@api.middleware('http')
async def boundary(request: Request, call_next):
    path = request.url.path
    private = path.startswith('/api/') and path not in ('/api/config', '/api/health')
    if private:
        if MODE != 'local':
            return JSONResponse({'error':'공개 시연에서는 실제 자료 API를 사용할 수 없습니다.'}, status_code=403)
        try:
            local_client = ipaddress.ip_address(request.client.host).is_loopback
        except (ValueError, AttributeError):
            local_client = False
        if not local_client or request.url.hostname not in ('localhost', '127.0.0.1', '::1'):
            return JSONResponse({'error':'개인 API는 이 컴퓨터에서만 사용할 수 있습니다.'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error':'외부 사이트 요청을 차단했습니다.'}, status_code=403)
        if request.method not in ('GET', 'HEAD'):
            origin = request.headers.get('origin')
            if origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'error':'같은 화면에서 다시 요청하세요.'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


def numeric(v):
    if isinstance(v, bool):
        raise ValueError('boolean is not a number')
    result = float(v)
    if not math.isfinite(result) or result < 0:
        raise ValueError('invalid number')
    return result


def timestamp(v):
    if not isinstance(v, str):
        raise ValueError('timestamp must be text')
    dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timezone required')
    return dt.isoformat()


def snapshot(raw):
    result = {k:raw[k] for k in ('scope','stream_status') if k in raw}
    result.update(as_of=timestamp(raw['as_of']), valuation_at=timestamp(raw.get('valuation_at', raw['as_of'])),
                  calculated_equity_value_krw=numeric(raw['calculated_equity_value_krw']), warnings=raw.get('warnings', []))
    positions = []
    for p in raw['positions']:
        if p['currency'] not in ('USD','KRW'):
            raise ValueError('currency')
        row = {k:str(p.get(k,'')) for k in ('symbol','name','market','currency','price_quality','price_source')}
        row.update(quantity=numeric(p['quantity']), price=numeric(p['price']),
                   value_krw=numeric(p.get('value_krw',p.get('val_krw'))),
                   price_at=timestamp(p['price_at']) if p.get('price_at') else None)
        row['weight_pct'] = row['value_krw']/result['calculated_equity_value_krw']*100 if result['calculated_equity_value_krw'] else 0
        positions.append(row)
    total = sum(p['value_krw'] for p in positions)
    if len(positions)>100 or len({p['symbol'] for p in positions})!=len(positions) or abs(total-result['calculated_equity_value_krw'])>max(1,total*1e-8):
        raise ValueError('position totals or symbols')
    result['positions'] = positions
    return result


def report(raw):
    if not isinstance(raw, dict):
        raise ValueError('analysis_status.json is not a report')
    result = {'created_at':timestamp(raw['created_at']), 'complete':raw['complete'], 'warnings':raw.get('warnings',[])}
    if not isinstance(result['complete'], bool):
        raise ValueError('complete must be boolean')
    sources=[]
    for s in raw['sources']:
        u=urlsplit(s['url'])
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
            raise ValueError('unsafe source link')
        sources.append({k:s[k] for k in ('id','provider','title','content','url','kind','observed_at','retrieved_at','quality') if k in s})
    ids={s['id'] for s in sources}
    if len(ids)!=len(sources) or len(sources)>500:
        raise ValueError('source IDs')
    for key in ('portfolio','macro','synthesis'):
        stage=raw[key]
        if stage['status'] not in ('ok','failed'):
            raise ValueError('stage status')
        parsed=Report.model_validate(stage['report']).model_dump()
        for f in parsed['findings']+parsed['conflicts']:
            if not f['evidence_ids'] or any(i not in ids for i in f['evidence_ids']):
                raise ValueError('missing evidence ID')
        result[key]={'status':stage['status'],'report':parsed}
    if result['complete'] and any(result[k]['status']!='ok' for k in ('portfolio','macro','synthesis')):
        raise ValueError('inconsistent completion')
    result['sources']=sources
    return result


def read_json(path):
    if path.stat().st_size>MAX_BYTES:
        raise ValueError('file too large')
    return json.loads(path.read_text(encoding='utf-8'))


def current_workspace():
    sfile=DATA/'latest_portfolio.json'
    candidates=list((DATA/'reports').glob('*.json'))
    rfile=max(candidates,key=lambda p:p.name) if candidates else None
    status=DATA/'analysis_status.json'
    if status.exists():
        name=read_json(status).get('report')
        if isinstance(name,str) and Path(name).name==name:
            candidate=DATA/'reports'/Path(name).with_suffix('.json')
            if candidate.exists():
                rfile=candidate
    imported=DATA/'imported_report.json'
    if imported.exists() and (rfile is None or imported.stat().st_mtime>rfile.stat().st_mtime):
        rfile=imported
    try:
        s=snapshot(read_json(sfile)) if sfile.exists() else None
        r=report(read_json(rfile)) if rfile else None
    except (ValueError, KeyError, TypeError, OSError):
        fail('자료 형식을 확인하세요. 상태 파일 대신 reports 폴더의 보고서 JSON을 사용하세요.',422)
    times=[p.stat().st_mtime for p in (sfile,rfile) if p and p.exists()]
    return dict(snapshot=s, report=r, updated_at=datetime.fromtimestamp(max(times)).astimezone().isoformat() if times else None,
                heartbeat=now(), worker_status='local_server', has_token=False, job=job)


@api.get('/api/config')
def config():
    return {'mode':MODE}


@api.get('/api/health')
def health():
    return {'ok':True}


@api.get('/api/workspace')
def workspace():
    return current_workspace()


@api.post('/api/workspace')
async def import_workspace(request: Request):
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>MAX_BYTES:
            fail('합계 2MB 이하의 JSON을 선택하세요.',413)
    try:
        packet=json.loads(raw)
        if not isinstance(packet,dict):
            raise ValueError('packet must be object')
        s=snapshot(packet['snapshot']) if packet.get('snapshot') is not None else None
        r=report(packet['report']) if packet.get('report') is not None else None
        if s is None and r is None:
            raise ValueError()
    except (ValueError,KeyError,TypeError,AttributeError):
        fail('포트폴리오 또는 보고서 형식이 맞지 않습니다. analysis_status.json은 보고서가 아닙니다.',422)
    with job_lock:
        if job and job['status']=='running':
            fail('분석 완료 후 가져오세요.',409)
        if s is not None:
            write_private(DATA/'latest_portfolio.json',s)
        if r is not None:
            write_private(DATA/'imported_report.json',r)
    return {'ok':True}


def run_job(current):
    try:
        result=subprocess.run([sys.executable,str(ROOT/'app.py'),'live','--output',str(DATA)],cwd=ROOT,timeout=3600)
        state=current_workspace().get('report')
        current['status']='complete' if result.returncode==0 and state and state['complete'] else 'partial' if result.returncode==0 else 'failed'
        if result.returncode:
            current['error']='분석 실패: VS Code 터미널과 .env 설정을 확인하세요.'
    except Exception:
        current.update(status='failed',error='실행 실패 또는 60분 제한 초과. 터미널을 확인하세요.')
    finally:
        current['updated_at']=now()


@api.post('/api/jobs',status_code=202)
def analyze():
    global job
    with job_lock:
        if job and job['status']=='running':
            fail('이미 분석 중입니다.',409)
        if not all(os.getenv(k) for k in ('TOSS_CLIENT_ID','TOSS_CLIENT_SECRET','LLM_MODEL')):
            fail('backend/.env에 토스 인증정보와 LLM_MODEL을 설정하고 서버를 다시 실행하세요.',409)
        job=dict(id=uuid.uuid4().hex,status='running',created_at=now(),updated_at=now(),error=None)
        threading.Thread(target=run_job,args=(job,),daemon=True).start()
        return job


@api.get('/{path:path}')
def ui(path:str):
    if path in ('','dashboard','connect','project'):
        target=DIST/'index.html'
    elif path=='favicon.svg' or path.startswith('assets/'):
        target=(DIST/path).resolve()
        if not target.is_relative_to(DIST.resolve()):
            fail('Not found',404)
    else:
        fail('Not found',404)
    if not target.is_file():
        fail('화면 빌드가 없습니다. frontend에서 pnpm build를 실행하세요.',404)
    return FileResponse(target)


if __name__=='__main__':
    import uvicorn
    uvicorn.run(api,host='127.0.0.1',port=8000,proxy_headers=False)
