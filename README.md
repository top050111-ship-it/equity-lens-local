# Equity Lens — 원본 UI의 로컬 실행 패키지

기존 chatgpt.site의 React·TypeScript·CSS 화면을 가져와, 최신 첨부 Python 코드와 FastAPI로 연결한 버전입니다. 비슷하게 다시 그린 Streamlit 화면이 아닙니다. 사이드바, 포트폴리오 도넛·표, 세 에이전트 탭, 종합 판단, 근거 패널, 보고서 다운로드, 모바일 레이아웃을 유지했습니다.

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
OLLAMA_NUM_CTX=24576
EQUITY_DATA_DIR=
```

```bash
python server.py
```

브라우저에서 **http://127.0.0.1:8000** 으로 접속합니다. `/`는 가상 시연, `/dashboard`는 실제 결과, `/connect`는 연결 안내, `/project`는 소개입니다. `index.html`을 Finder로 더블클릭하거나 VS Code Live Server로 열지 마세요.

## 2. 기존 app.py의 결과 연결 — 다시 분석할 필요 없음

기존 수집기와 새 패키지는 서로 다른 폴더입니다. 기본적으로 새 패키지는 `backend/data`를 읽습니다. 기존 결과를 그대로 보려면 `.env`에 다음처럼 지정하세요. 공백이 있는 경로도 가능하며, 절대 경로를 권장합니다.

```dotenv
EQUITY_DATA_DIR=/Users/hongseungbeom/Downloads/equity-lens-collector/data
```

`server.py`를 Ctrl+C로 종료했다가 다시 실행하고 `/dashboard`를 열면 됩니다. 기존 `app.py`가 이 폴더를 갱신하면 화면은 10초마다 읽습니다. 결과 파일을 웹사이트에 반복 업로드할 필요가 없습니다. `analysis_status.json`은 상태 정보이며 보고서 본문이 아닙니다.

연결 대상은 `latest_portfolio.json`과 `reports/*.json`입니다. `complete: false`도 부분 보고서로 표시합니다. 빈 폴더를 가상 데이터로 몰래 대체하지 않습니다.

직접 가져오기도 가능합니다. `/connect`에서 위 두 JSON을 선택합니다. 가져온 포트폴리오는 선택한 data 폴더의 `latest_portfolio.json`을 갱신하므로 원본 보존이 필요하면 먼저 백업하세요. `.env`는 업로드 금지입니다.

## 3. 새 분석 실행

화면의 ANALYZE 버튼은 `POST /api/jobs` → 같은 가상환경의 `python app.py live --output ...`를 백그라운드에서 실행합니다. 요청 중 브라우저가 멈추지 않으며 화면이 결과를 다시 읽습니다. 실제 토스 키와 사용 가능한 모델이 필요합니다. 작업은 60분 후 제한되며 중복 버튼 요청은 차단합니다.

명령으로 한 번 실행하려면 새 터미널에서:

```bash
cd backend
source .venv/bin/activate
python app.py live
```

다만 `EQUITY_DATA_DIR`는 연결 서버의 설정입니다. `app.py`를 직접 실행할 때도 같은 외부 폴더로 쓰려면 `--output /절대/경로/data`를 별도로 지정하세요.

계속 수집·분석하려면:

```bash
python app.py live --watch --stream
```

기본 뉴스 분석 간격은 `config.json`의 3600초, 보유 종목 조회 간격은 60초입니다. `server.py`만 실행했다고 자동 분석 스케줄러가 켜지는 것은 아닙니다. 반복 수집은 위 명령으로 따로 실행합니다. 반복 수집 중에는 ANALYZE를 누르지 마세요. 같은 패키지는 잠금으로 중복 실행을 막지만 서로 다른 폴더의 수집기는 잠금을 공유하지 않습니다.

Ollama 확인: `ollama list` / `curl http://127.0.0.1:11434/api/tags`. 8GB Mac에서는 기존에 사용한 4B·순차 실행 설정을 유지할 수 있습니다. 메모리가 부족하면 문맥을 16384로 낮춰볼 수 있지만 입력 잘림·보고서 품질 저하 가능성이 있습니다.

## 4. 화면 직접 편집 — 프론트엔드 전체 소스

Node.js 22.13 이상(22 LTS 권장), pnpm 11.25.0을 사용합니다. 원본의 dependency 목록과 lockfile을 유지했습니다. Next/Sites 관련 의존성 일부는 원본과의 호환을 위해 남아 있지만 로컬 실행은 Vite만 사용합니다.

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

ZIP의 `frontend/dist-public`은 공개 전용으로 이미 빌드되어 있습니다. 실제 종목·계좌·보고서·키를 포함하지 않습니다. 이 폴더의 내용만 Cloudflare Pages 같은 정적 호스팅에 올리면 Mac을 꺼도 방문자가 시연·소개를 볼 수 있습니다.

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

Render 무료 웹 서비스는 15분 동안 요청이 없으면 중단되며, 재시작 시 로컬 파일이 사라질 수 있습니다. 따라서 이 파일 저장형 수집기의 24시간 운영용으로 적합하지 않습니다. 무료 시연부터 공개하고 실제 리서치는 로컬에서 실행하는 구성을 권합니다.

공식 자료(2026-09-17 확인):

- [Cloudflare Pages](https://www.cloudflare.com/products/pages/)
- [Cloudflare 정적 사이트 배포](https://developers.cloudflare.com/pages/framework-guides/deploy-anything/)
- [Render 무료 서비스 제약](https://render.com/docs/free)
- [FastAPI 정적 파일 안내](https://fastapi.tiangolo.com/tutorial/static-files/)

## 6. 포함된 변경과 주의점

- 첨부 최신 `app(4).py` 등은 정상 모듈명으로 복사했습니다. 사용자의 분석 프롬프트와 수집 로직은 유지했습니다.
- `server.py`를 새로 추가했으며 app.py를 웹 서버로 억지로 바꾸지 않았습니다.
- Streamlit 버전은 `app_web_legacy.py`로 보존했습니다. 이 버전은 실행에 별도 streamlit/pandas가 필요하며 공개 배포 대상으로 사용하지 마세요. 새 UI는 이 파일을 사용하지 않습니다.
- 원본 UI의 “연결 대기”와 “보고서 완료”를 구분해 부분 보고서가 있어도 없는 것처럼 보이던 혼동을 수정했습니다.
- 공개 빌드, 로컬 전용 API 접근 검사, 동일 출처 쓰기 검사, 파일 크기 제한, 출처 URL 검사, 숫자 변환을 추가했습니다.
- API 키는 프론트엔드에 넣지 않습니다. `VITE_` 환경변수는 빌드에 포함되므로 비밀키를 절대 넣지 마세요.
- local 모드는 본인 OS 계정과 로컬 프로세스를 신뢰합니다. 다른 사람이 쓰는 공유 컴퓨터에서는 실행하지 마세요. 127.0.0.1 바인딩을 유지하고 임의 리버스 프록시·공개 터널에 연결하지 마세요.
- Python 서버를 종료하면 이 서버가 관리하는 작업 상태는 사라집니다. 분석 중 서버 재시작을 피하세요. 장기 다중 사용자 운영에는 별도 작업 큐가 필요합니다.
- Python 수집기의 파일 잠금은 macOS/Linux용입니다. Windows에서는 WSL2에서 실행하세요.
- 실제 토스 계좌 접속과 Ollama 추론은 이 패키지를 만드는 환경에서 실행하지 않았습니다. 모의 데이터 기반 통합 검증과 실계좌 검증은 다릅니다.

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
| 보고서 partial | UI 오류가 아닌 일부 에이전트 분석 실패; 단계별 한계·터미널 확인 |

오프라인 API 검증: `backend`에서 `python -m pip install httpx` 후 `python -m unittest test_server -v`.
