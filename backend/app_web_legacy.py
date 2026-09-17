import importlib

try:
    st = importlib.import_module("streamlit")
except ModuleNotFoundError as exc:
    if exc.name == "streamlit":
        raise RuntimeError(
            "Streamlit is required to run this app. Install it with "
            "'python -m pip install streamlit'."
        ) from exc
    raise
import os
import json
import glob
import subprocess
import pandas as pd

# 페이지 기본 설정
st.set_page_config(page_title="Equity Lens - Research Workspace", page_icon="📈", layout="wide")

# 커스텀 CSS
st.markdown("""
<style>
    .report-brief { border-left: 4px solid #d90429; padding-left: 1rem; background-color: #f8f9fa; padding: 1rem; margin-bottom: 2rem; }
    .finding-tag { background-color: #e9ecef; padding: 0.2rem 0.5rem; border-radius: 4px; font-size: 0.8rem; color: #495057; }
    .counterpoint-text { color: #6c757d; font-size: 0.9rem; margin-bottom: 0.2rem; }
</style>
""", unsafe_allow_html=True)

# 데이터 로드 함수
def load_latest_json(filename):
    for folder in ['data/reports', 'data', 'demo-output/reports', 'demo-output']:
        if os.path.exists(folder):
            if filename == "report":
                files = glob.glob(os.path.join(folder, "*.json"))
                files = [f for f in files if "latest_portfolio" not in f and "status" not in f]
                if files:
                    latest_file = max(files, key=os.path.getctime)
                    with open(latest_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
            else:
                filepath = os.path.join(folder, filename)
                if os.path.exists(filepath):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        return json.load(f)
    return None

# --- 좌측 사이드바 (내비게이션) ---
with st.sidebar:
    st.markdown("## 📈 EquityLens\n**INVESTMENT RESEARCH**\n\n---")
    
    # 세션 상태로 메뉴 관리 (화면 전환용)
    menu = st.radio("RESEARCH WORKSPACE", ["시연 콘솔 (리서치)", "내 포트폴리오", "데이터 연결", "프로젝트 소개"])
    
    st.markdown("---")
    st.markdown("### THREE PERSPECTIVES\n**기업을 깊게.**\n\n**시장을 넓게.**\n\n**판단은 함께.**")
    st.markdown("---")
    
    st.markdown("### ⚙️ 실행 제어")
    run_mode = st.selectbox("모드 선택", ["demo", "live"])
    if st.button("🚀 에이전트 파이프라인 실행"):
        with st.spinner("AI 에이전트 데이터 수집 및 분석 중..."):
            try:
                subprocess.run(["python", "app.py", run_mode], capture_output=True, text=True, check=True)
                st.success("분석 완료!")
                st.rerun()
            except subprocess.CalledProcessError as e:
                st.error(f"오류 발생: {e.stderr}")

# ==========================================
# 1. 시연 콘솔 (AI 리서치 리포트 화면)
# ==========================================
if menu == "시연 콘솔 (리서치)":
    report_data = load_latest_json("report")
    if not report_data:
        st.info("아직 생성된 리포트가 없습니다. 좌측 사이드바에서 [에이전트 파이프라인 실행] 버튼을 눌러주세요.")
    else:
        # Streamlit's type checker does not narrow the optional JSON result here.
        assert report_data is not None
        col_main, col_news = st.columns([7, 3])
        with col_main:
            st.markdown("## 리서치 보고서")
            st.caption(f"작성 시각: {report_data.get('created_at', '알 수 없음')}")
            
            tab1, tab2, tab3 = st.tabs(["01 기업·포트폴리오", "02 거시경제", "03 종합 판단"])
            
            def render_agent_view(agent_key):
                assert report_data is not None
                agent_section = report_data.get(agent_key) or {}
                agent = agent_section.get('report') or {}
                if not agent:
                    st.warning("분석 결과가 없습니다.")
                    return
                st.markdown(f'<div class="report-brief"><strong>RESEARCH BRIEF</strong><br><br>{agent.get("summary", "")}</div>', unsafe_allow_html=True)
                for f in agent.get('findings', []):
                    st.markdown(f"#### {f.get('subject', '')} <span class='finding-tag'>{f.get('kind', '')}</span>", unsafe_allow_html=True)
                    st.write(f.get('claim', ''))
                    st.markdown(f"<div class='counterpoint-text'><strong>반대 근거:</strong> {f.get('counterpoint', '')}</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='counterpoint-text'><strong>재검토 조건:</strong> {f.get('revisit_when', '')}</div>", unsafe_allow_html=True)
                    with st.expander(f"🔗 근거 {len(f.get('evidence_ids', []))}개 보기"):
                        for eid in f.get('evidence_ids', []):
                            st.code(eid)
                    st.markdown("---")
                if agent.get('conflicts'):
                    st.markdown("#### ⚖️ 두 분석의 충돌 지점")
                    for c in agent['conflicts']:
                        st.markdown(f"**{c.get('issue', '')}**")
                        c1, c2 = st.columns(2)
                        c1.info(f"**기업 관점:**\n\n{c.get('portfolio_view', '')}")
                        c2.warning(f"**거시 관점:**\n\n{c.get('macro_view', '')}")
                        st.markdown(f"**판단:** {c.get('assessment', '')}")
                        st.markdown("---")
                if agent.get('watchlist'):
                    st.markdown("#### ✅ 다음 확인 사항")
                    for w in agent['watchlist']:
                        st.markdown(f"- {w}")

            with tab1: render_agent_view('portfolio')
            with tab2: render_agent_view('macro')
            with tab3: render_agent_view('synthesis')

        with col_news:
            st.markdown("### 뉴스와 근거")
            st.caption(f"수집된 원문 자료 {len(report_data.get('sources', []))}개")
            for s in report_data.get('sources', []):
                st.markdown(f"**{s.get('provider', '')}**\n[{s.get('title', '')}]({s.get('url', '')})")
                st.caption(f"ID: {s.get('id', '')}")
                st.divider()

# ==========================================
# 2. 내 포트폴리오 (계좌 및 보유 종목 조회)
# ==========================================
elif menu == "내 포트폴리오":
    st.markdown("## 💼 내 포트폴리오")
    portfolio_data = load_latest_json("latest_portfolio.json")
    
    if not portfolio_data:
        st.info("포트폴리오 데이터가 없습니다. 파이프라인을 먼저 실행해 주세요.")
    else:
        st.caption(f"기준 시각: {portfolio_data.get('valuation_at', '알 수 없음')} | {portfolio_data.get('scope', '')}")
        
        # 총 자산 요약
        total_value = float(portfolio_data.get('calculated_equity_value_krw', 0))
        st.metric(label="주식 평가액 참고치 (KRW)", value=f"₩ {total_value:,.0f}")
        
        # 데이터프레임 렌더링
        positions = portfolio_data.get('positions', [])
        if positions:
            df = pd.DataFrame(positions)
            df = df[['symbol', 'name', 'market', 'quantity', 'price', 'currency', 'weight_pct', 'price_quality']]
            df.columns = ['티커', '종목명', '시장', '수량', '현재가', '통화', '비중(%)', '시세 상태']
            st.dataframe(df, use_container_width=True)
            
        if portfolio_data.get('warnings'):
            st.warning("⚠️ 포트폴리오 경고 사항")
            for w in portfolio_data['warnings']:
                st.write(f"- {w}")

# ==========================================
# 3. 데이터 연결 (API 상태 및 설정)
# ==========================================
elif menu == "데이터 연결":
    st.markdown("## 🔌 데이터 연결 상태")
    st.markdown("AI 에이전트가 사용할 외부 API 및 키 연결 상태를 확인합니다.")
    
    env_exists = os.path.exists('.env')
    config_exists = os.path.exists('config.json')
    
    col1, col2 = st.columns(2)
    with col1:
        st.success("✅ 환경 변수 (.env) 로드 완료") if env_exists else st.error("❌ .env 파일 누락")
        st.success("✅ 설정 파일 (config.json) 로드 완료") if config_exists else st.error("❌ config.json 파일 누락")
    
    with col2:
        st.info("**연결된 서비스:**\n- Toss Securities Open API\n- Yahoo Finance (비공식)\n- FRED API (거시경제)\n- LLM Backend: Ollama / OpenAI")

# ==========================================
# 4. 프로젝트 소개 (About)
# ==========================================
elif menu == "프로젝트 소개":
    st.markdown("## 🚀 About Equity Lens")
    st.markdown("""
    **Equity Lens**는 다중 AI 에이전트(Multi-Agent) 아키텍처를 기반으로 작동하는 지능형 주식 리서치 파이프라인입니다.
    
    전통적인 금융 리서치 방법론에 최신 LLM(대규모 언어 모델) 기술을 결합하여, 거시 경제 데이터와 개별 기업의 뉴스 플로우를 자동으로 수집하고 분석합니다.
    
    - **Agent 1 (Portfolio):** 개별 기업의 사업·재무·경쟁 환경을 분석하고 비중 집중 위험을 평가합니다.
    - **Agent 2 (Macro):** 글로벌 금리, 물가, 환율, 원자재 등 거시 경제 지표를 독립적으로 검토합니다.
    - **Agent 3 (Synthesis):** 두 에이전트의 관점을 종합하여 충돌 지점을 발견하고, 최종 투자 시나리오 및 재검토 조건을 도출합니다.
    
    *Developed by Seungbeom Hong (Korea University Business School)*
    """)