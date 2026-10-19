# Equity Lens v1.4.0 — 로컬 AI 투자 리서치 워크스페이스

React·TypeScript 기반 대시보드와 Python·FastAPI 기반 분석 엔진을 연결한 개인 투자 리서치 프로젝트입니다. 포트폴리오 차트·표, 세 에이전트 탭, 종합 분석, 근거 패널과 보고서 다운로드를 제공합니다.

## 프로젝트 개요

Equity Lens는 개인 포트폴리오와 시장 데이터를 세 개의 AI 에이전트가 서로 다른 관점에서 분석하는 로컬 투자 리서치 워크스페이스입니다.

- **기업·포트폴리오 분석가:** 기업 뉴스와 포트폴리오 집중 위험 분석
- **글로벌 거시 분석가:** 금리·경기·환율 등 거시환경 분석
- **종합 리서치 편집자:** 두 분석의 일치점과 충돌을 검토하고 최종 보고서 작성

기본 구성은 로컬 실행과 Ollama 추론을 사용합니다. 계좌·뉴스·시세 조회에는 외부 서비스가 사용되며, 외부 LLM을 선택하면 분석 입력이 해당 제공자에게 전송될 수 있습니다. 각 사용자는 자신의 인증정보를 설정하며 실제 인증정보·보유 내역·개인 보고서는 Git 추적에서 제외해야 합니다.

## v1.4.0 변경 사항

- **저장 포트폴리오 분석:** 토스 계좌 조회 없이 기존 보유 내역으로 분석하는 `cached` 모드 추가
- **빈 계좌 대응:** 계좌 조회에 성공했지만 보유 종목이 없으면 검증된 저장 포트폴리오 사용
- **이관 전 고정 보관:** 전체 포트폴리오를 별도로 보관하여 부분 이관 중 잔여 종목으로 덮어쓰는 문제 방지
- **데이터 검증:** 저장 데이터 무결성·수량·통화·시각 검사 및 캐시 복구 강화
- **분석 품질:** 중복 주장 제거, 주장과 근거의 의미 일치 검토, 원문 구간 선택을 통한 인용 검증
- **종합 분석:** 기업·거시 근거를 함께 사용하고 보유 종목과 연결하도록 검증 강화
- **실행 안정성:** 단계별 실패 기록, 부분 보고서 저장, 작업 식별 및 진단·회귀 테스트 추가

### 확인한 범위

개발자의 macOS 환경에서 자동 테스트 **83개가 통과**했고 실제 저장된 6종목 포트폴리오의 오프라인 복원을 확인했습니다. 자동 테스트는 모의 응답과 고정 입력을 사용하므로 실계좌 API 및 실제 모델 추론 전체의 성공을 보장하지 않습니다. 최신 인용 검증 수정 후 실제 모델로 모든 분석 단계가 완료되는지는 추가 검증 예정입니다.

의미 일치 검토도 모델의 판단을 포함하며 정확성을 보장하지 않습니다. 근거 부족·검증 탈락·외부 서비스 실패에 따라 `partial` 보고서가 생성될 수 있습니다.

## 주요 기술

- Python · FastAPI
- React · TypeScript · CSS
- Toss Securities Open API
- Ollama · Qwen3
- JSON Schema 기반 출력 검증

## 데모

프로젝트 실행 화면은 LinkedIn 게시물의 시연 영상에서 확인할 수 있습니다.



## 1. 가장 빠른 시작 — Mac / VS Code

Python 3.11 이상을 권장합니다. 압축을 풀어 `equity-lens-local` 폴더를 VS Code에서 열고, 터미널에서 실행하세요. 빌드된 화면이 포함되어 있어서 **일단 실행만 할 때는 Node.js가 필요 없습니다.**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

`.env`가 없다면 다음으로 만듭니다. 기존 `.env`가 있으면 덮어쓰지 않습니다.

```bash
test -f .env || cp env.example .env
chmod 600 .env
```

VS Code에서 `backend/.env`를 직접 수정합니다. 기존 수집기의 인증정보를 이 파일에 옮기되 채팅·GitHub에 올리지 마세요. Ollama가 이미 실행 중이면 `ollama serve`를 다시 실행하지 않습니다.

```dotenv
APP_MODE=local
TOSS_CLIENT_ID=본인의_ID
TOSS_CLIENT_SECRET=본인의_SECRET
TOSS_ACCOUNT_SEQ=
LLM_BACKEND=ollama
LLM_MODEL=qwen3:4b
OLLAMA_THINK=false
OLLAMA_PARALLEL_AGENTS=false
OLLAMA_NUM_PREDICT=3200
OLLAMA_NUM_CTX=16384
EQUITY_HOLDINGS_MODE=live
EQUITY_RESEARCH_FILE=
EQUITY_JOB_TIMEOUT=7200
EQUITY_DATA_DIR=
```

```bash
python server.py
```

브라우저에서 **http://127.0.0.1:8000** 으로 접속합니다. `/`는 가상 시연, `/dashboard`는 실제 결과, `/connect`는 연결 안내, `/project`는 소개입니다. `index.html`을 Finder로 더블클릭하거나 VS Code Live Server로 열지 마세요.

## 2. 기존 app.py의 결과 연결 — 다시 분석할 필요 없음

기본 저장 경로는 `backend/data`이며 CLI·서버·워커가 같은 설정을 사용합니다. 기존 결과를 그대로 보려면 `.env`에 다음처럼 지정하세요. 공백이 있는 경로도 가능하며, 절대 경로를 권장합니다.

```dotenv
EQUITY_DATA_DIR=/absolute/path/to/existing/data
```

`server.py`를 Ctrl+C로 종료했다가 다시 실행하고 `/dashboard`를 열면 됩니다. 기존 `app.py`가 이 폴더를 갱신하면 화면은 10초마다 읽습니다. 결과 파일을 웹사이트에 반복 업로드할 필요가 없습니다. `analysis_status.json`은 상태 정보이며 보고서 본문이 아닙니다.

연결 대상은 `latest_portfolio.json`과 `reports/*.json`입니다. `complete: false`도 부분 보고서로 표시합니다. 빈 폴더를 가상 데이터로 몰래 대체하지 않습니다.

직접 가져오기도 가능합니다. `/connect`에서 위 두 JSON을 선택합니다. 가져온 포트폴리오는 선택한 data 폴더의 `latest_portfolio.json`을 갱신하므로 원본 보존이 필요하면 먼저 백업하세요. `.env`는 업로드 금지입니다.

## 3. 분석 실행과 계좌 이관 후 개발

아래 명령은 **프로젝트 최상위 폴더**에서 가상환경을 활성화한 상태로 실행합니다. `app.py`는 `backend` 안에 있습니다. 앞의 설치 과정에서 `backend`로 이동했다면 먼저 `cd ..`를 실행하세요.

### 실계좌 분석

```bash
python backend/app.py live
```

계좌 조회 오류·인증 실패는 빈 계좌로 처리하지 않습니다. 계좌 조회가 성공하고 보유 종목이 없는 경우에만 저장된 포트폴리오로 대체합니다. 사용 가능한 저장 파일이 없거나 검증에 실패하면 명시적인 오류를 표시합니다.

### 이관 전 포트폴리오 고정 보관

주식을 옮기기 전에 전체 보유 종목이 있는 상태에서 한 번 실행합니다. AI는 호출하지 않습니다.

```bash
python backend/app.py live --snapshot-only --freeze-holdings
```

기본 경로의 `backend/data/frozen_portfolio.json`에 보관합니다. 기존 고정 보관본이 있으면 덮어쓰지 않습니다. `--replace-frozen`은 기존 보관본을 의도적으로 교체할 때만 함께 사용하세요. 고정 파일은 프로젝트 밖의 개인 저장 공간에도 백업하고 GitHub에는 올리지 마세요.

### 계좌 조회 없이 저장 포트폴리오 분석

```bash
python backend/app.py cached
```

토스 인증정보·토큰 발급·계좌 조회 없이 고정 보관본을 우선 사용합니다. 고정 보관본이 없으면 검증된 최근 비어 있지 않은 캐시를 사용합니다. 저장 데이터에는 무결성 검사를 적용하며, 손상된 고정 보관본을 다른 데이터로 조용히 대체하지 않습니다.

이 모드는 저장 시점의 **보유 수량**을 분석 기준으로 사용합니다. 이후 다른 증권사에서 발생한 매매는 자동 반영되지 않으며 다른 증권사 계좌 연동을 의미하지 않습니다. 공개 시세·환율 갱신을 시도하지만 실패하면 기존 값과 기준 시각을 유지하고 한계를 기록합니다. 뉴스 수집과 실제 AI 분석에는 네트워크 및 설정된 모델이 필요합니다.

저장 파일 복원만 오프라인으로 확인하려면:

```bash
python backend/app.py cached --snapshot-only --skip-price-refresh
```

별도 저장 파일을 명시하려면:

```bash
python backend/app.py cached --holdings /absolute/path/to/portfolio.json
```

### 웹 화면의 ANALYZE 버튼

버튼은 `POST /api/jobs`를 통해 같은 Python 환경의 분석 프로세스를 실행합니다. 기본값은 `live`이며, 계좌 조회 없이 실행하려면 `backend/.env`에 다음을 설정하고 서버를 재시작합니다.

```dotenv
EQUITY_HOLDINGS_MODE=cached
EQUITY_RESEARCH_FILE=
```

파일 경로를 비워 두면 기본 저장 파일을 선택합니다. 이 설정은 `site_worker.py`에도 적용됩니다. CLI에서는 `live` 또는 `cached` 인자가 모드를 정합니다. 작업 제한은 `EQUITY_JOB_TIMEOUT`으로 설정하며 기본값은 7200초입니다. 중복 분석 요청은 차단합니다.

### 반복 분석과 저장 경로

```bash
# 실계좌 조회 및 토스 시세 수신
python backend/app.py live --watch --stream

# 저장된 보유 수량을 기준으로 반복 분석
python backend/app.py cached --watch
```

`cached`에서는 `--stream`을 사용하지 않습니다. 기본 뉴스 분석 간격은 `config.json`에서 설정합니다. 서버 실행만으로 반복 분석이 시작되지는 않습니다. 반복 분석 중에는 ANALYZE 버튼을 누르지 마세요.

CLI·서버·워커는 `EQUITY_DATA_DIR`를 공유합니다. CLI의 `--output /absolute/path/to/data`는 해당 실행에서 우선 적용되므로 웹 화면과 연결하려면 같은 경로를 지정하세요.

Ollama 확인: `ollama list` 또는 `curl http://127.0.0.1:11434/api/tags`. 모델이 설치되어 있어야 하며, 8GB Mac에서는 4B 모델과 순차 실행을 기준으로 조정하세요. 큰 문맥·출력 설정은 메모리 사용량과 실행 시간을 늘립니다.

## 4. 화면 직접 편집 — 프론트엔드 전체 소스

기존 프런트엔드의 `package.json`과 lockfile에 맞는 Node.js·pnpm 버전을 사용합니다. 기존 패키지의 설치 기준은 Node.js 22.13 이상, pnpm 11.25.0입니다. 원본의 dependency 목록과 lockfile을 유지했습니다. Next/Sites 관련 의존성 일부는 원본과의 호환을 위해 남아 있지만 로컬 실행은 Vite만 사용합니다.

```bash
cd frontend
npm install -g pnpm@11.25.0
pnpm install --frozen-lockfile
pnpm dev
```

별도 터미널의 `backend` 폴더에서 `python server.py`가 실행 중이어야 합니다. 개발 화면은 **http://127.0.0.1:5173** 입니다. Vite가 `/api` 요청을 `127.0.0.1:8000`으로 전달하므로 CORS를 전체 허용할 필요가 없습니다.

| 수정할 내용 | 파일 |
|---|---|
| HTML 진입점·제목 | `frontend/index.html` |
| 페이지 선택·공개 모드 | `frontend/src/main.tsx` |
| 원본 색상·간격·반응형 CSS | `frontend/src/styles.css` |
| 리서치 콘솔·표·차트·탭·근거 | `frontend/src/components/research-console.tsx` |
| 사이드바·공통 레이아웃 | `frontend/src/components/app-shell.tsx` |
| 로컬 연결·JSON 가져오기 | `frontend/src/components/connections.tsx` |
| 프로젝트 소개 | `frontend/src/components/project.tsx` |
| JSON 규칙과 금액 표시 | `frontend/src/lib/data.ts` |
| 공개 가상 데이터 | `frontend/src/lib/demo.ts` |
| Python 연결 API | `backend/server.py` |
| 기존 수집·분석 프로그램 | `backend/app.py`, `agents.py`, `broker.py`, `collectors.py` |

UI 변경 후 Python 한 서버만으로 쓰려면 `frontend`에서 `pnpm build`를 실행하고 8000번 화면을 새로고침합니다. 기존 `dist`가 갱신됩니다. `pnpm preview`는 프론트엔드 미리보기용이며 개인 API 통합에는 `server.py` 또는 `pnpm dev`를 사용하세요.

## 5. 공개·무료 운영

**공개 화면을 운영하는 것과 24시간 실제 AI 분석을 돌리는 것은 다릅니다.**

### 가장 안전한 무료 공유: 공개 시연만 배포

`frontend/dist-public`은 공개 시연용 빌드 경로입니다. 배포 전에 공개용 빌드를 다시 만들고 실제 보유 내역·개인 보고서·인증정보가 포함되지 않았는지 확인하세요. 정적 호스팅에 배포한 시연 화면은 로컬 Mac을 꺼도 제공할 수 있습니다.

직접 수정한 뒤 공개용으로 다시 빌드:

```bash
cd frontend
pnpm build:public
```

Cloudflare Pages의 Direct Upload에서 새 프로젝트를 만들고 `dist-public` 폴더를 업로드합니다. `_redirects`가 포함되어 `/project` 직접 접속도 처리합니다. Git 연동 시 빌드 명령 `pnpm build:public`, 출력 `dist-public`, 루트 `frontend`로 설정합니다. **backend, .env, data 폴더는 업로드하지 마세요.** 공개 전용 UI에서 개인 메뉴는 숨기고 개인 경로 접근도 안내 화면으로 바꿨습니다.

FastAPI 자체를 공개 서버에 올릴 경우에는 **반드시 APP_MODE=public**으로 설정합니다. 이 모드는 개인 조회·가져오기·분석 API를 서버에서 403으로 차단합니다. UI 버튼만 숨기는 방식이 아닙니다. 예:

```bash
APP_MODE=public python -m uvicorn server:api --host 0.0.0.0 --port 8000 --workers 1 --no-proxy-headers
```

이는 가상 시연용입니다. 인터넷에서 실제 개인 데이터를 보고 싶다면 검증된 로그인·사용자별 권한·HTTPS·비밀키 보관·영구 저장소를 추가해야 합니다. 이 로컬 패키지는 다중 사용자 계좌 서비스를 구현하지 않으며, 인증 없이 로컬 API를 터널로 공개하면 안 됩니다. ChatGPT 로그인은 기존 chatgpt.site의 플랫폼 기능이므로 이식하지 않았습니다.

### 컴퓨터를 꺼도 실제 자동 분석까지 하려면

계속 켜져 있는 별도 서버에 Python 수집기와 Ollama를 설치하거나, 서버에서 외부 모델 API를 호출해야 합니다. 로컬 Mac의 Ollama는 Mac이 꺼지면 중단됩니다. 4B 모델도 메모리·연산이 필요하므로 일반적인 무료 웹 호스팅에서 항상 실행할 수 있다고 보장할 수 없습니다. 외부 모델을 사용하면 전달되는 데이터와 비용도 다시 검토해야 합니다.

호스팅 서비스의 실행 시간·메모리·영구 저장소 제한은 배포 시 해당 서비스의 공식 문서에서 확인하세요.

## 6. 저장소와 실행 시 주의점

- `NOTICE`의 저작권 표시와 `LICENSE`의 AGPL-3.0-or-later 안내를 유지합니다.
- `backend/.env`, 실제 보유 내역, 토큰·계좌 캐시, 개인 보고서는 공개하지 않습니다. 기본 실행 데이터는 `backend/data/`에 저장됩니다. 외부 경로를 사용하는 경우 그 경로의 파일을 별도로 Git에 추가하지 마세요.
- `.gitignore`는 이미 추적 중인 파일을 자동으로 제거하지 않습니다. 커밋 전에 `git ls-files -ci --exclude-standard`와 `git diff --cached`를 확인하세요. 이미 커밋한 비밀정보는 추적 해제만으로 과거 이력에서 없어지지 않습니다.
- `backend/env.example`에는 비밀정보 없는 설정 예시만 넣습니다. 프런트엔드 환경변수는 브라우저에 공개될 수 있으므로 인증키를 넣지 않습니다.
- Streamlit 화면은 `app_web_legacy.py`로 보존하며 별도 `requirements-legacy.txt`가 필요합니다. React UI는 이 파일을 사용하지 않습니다.
- 로컬 모드는 본인 OS 계정과 로컬 프로세스를 신뢰합니다. 개인 API를 인증 없는 공개 터널로 노출하지 마세요.
- 분석 중 서버 재시작을 피하세요. 장기 다중 사용자 운영에는 별도 인증·권한·작업 관리가 필요합니다.
- 파일 잠금은 macOS/Linux 기준이며 Windows에서는 WSL2를 사용하세요.
- 이 프로젝트는 투자 리서치 보조 도구입니다. 보고서의 출처·기준 시각·한계와 단계별 검증 결과를 함께 확인하세요.

## 7. 오류 해결

| 증상 | 확인 |
|---|---|
| `.venv/bin/activate` 없음 | backend 위치에서 가상환경부터 생성 |
| `.env` 없음 | 숨김 파일 표시 또는 `test -f .env` 확인; 예시에서 새로 생성 |
| Ollama address already in use | 이미 실행 중일 수 있음; tags API 확인 후 기존 서버 사용 |
| 실제 자료가 빈 화면 | EQUITY_DATA_DIR, reports 폴더, 서버 재시작 확인 |
| 5173에서 API 연결 실패 | 8000 Python 서버가 실행 중인지 확인 |
| API 403 | 공개 모드는 의도적으로 차단; 로컬 주소·Origin 확인 |
| ANALYZE 409 | .env 누락, 이미 분석 중, 반복 수집 잠금 확인 |
| 보고서 partial | 근거 검증 탈락·수집 문제·일부 분석 단계 실패 가능; 단계별 한계와 로그 확인 |

## 8. 진단 및 테스트

프로젝트 최상위 폴더에서 실행합니다.

```bash
python -m pip install -r backend/requirements-dev.txt
python backend/doctor.py --tests
```

테스트만 실행하려면 프로젝트 최상위 폴더에서 `backend`로 이동한 뒤 실행합니다.

```bash
cd backend
python -m unittest discover -s tests -v
```

진단은 파일 구성·문법·의존성·환경설정 형식을 확인하며 비밀값을 출력하지 않습니다. 테스트에는 저장 데이터 복구, 빈 계좌 대체, 계좌 조회 없는 연구 모드, 인용 계약 및 서버 동작 검증이 포함됩니다. 실제 계좌 API와 실제 모델 추론은 별도로 확인해야 합니다.

