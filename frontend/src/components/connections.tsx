"use client";
import {useEffect,useState} from "react";
import {ArrowRight,FileUp,ShieldCheck,RefreshCw} from "lucide-react";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Toaster,toast} from "sonner";
import {AppShell} from "./app-shell";
import {blankWorkspace,formattedAt,packetSchema,type Workspace} from "@/lib/data";
export function Connections({user}:{user:string}){
 const [data,setData]=useState<Workspace>(blankWorkspace),[files,setFiles]=useState<File[]>([]),[busy,setBusy]=useState(false);
 async function refresh(){const r=await fetch("/api/workspace");const d=await r.json();if(!r.ok)throw Error(d.error);setData(d);}
 useEffect(()=>{refresh().catch(e=>toast.error(e.message));},[]);
 async function importFiles(){
  setBusy(true);
  try{
   const packet:Record<string,unknown>={};
   for(const file of files){
    if(file.size>2_000_000)throw Error("합계 2MB 이하 파일을 사용하세요.");
    const d=JSON.parse(await file.text());
    if(d.positions)packet.snapshot=d;
    else if(d.synthesis)packet.report=d;
    else if(d.snapshot||typeof d.report==="object")Object.assign(packet,{...(d.snapshot?{snapshot:d.snapshot}:{}),...(d.report?{report:d.report}:{})});
    else throw Error(file.name+": analysis_status.json이 아니라 reports 폴더의 보고서 JSON을 선택하세요.");
   }
   const clean=packetSchema.parse(packet);
   const r=await fetch("/api/workspace",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(clean)});
   const d=await r.json();if(!r.ok)throw Error(d.error);
   toast.success("저장했습니다. 내 포트폴리오에서 확인하세요.");setFiles([]);await refresh();
  }catch(e){toast.error(e instanceof Error?e.message:"가져오기 실패");}finally{setBusy(false);}
 }
 return <AppShell active="connect" user={user}><Toaster richColors/>
 <header className="page-heading"><div><div className="eyebrow">LOCAL CONNECTIONS</div><h1>데이터 연결</h1><p>파일 업로드 없이 기존 Python 결과를 자동으로 읽습니다.</p></div><Button asChild variant="outline"><a href="/dashboard">내 포트폴리오<ArrowRight size={16}/></a></Button></header>
 <div className="notice private-notice"><ShieldCheck size={18}/><span>개인 자료는 이 컴퓨터에서만 접근합니다. 공개 빌드는 가상 시연만 제공합니다.</span></div>
 <div className="connection-grid">
 <section className="panel settings-panel"><span className="step-number">01</span><h2>기존 데이터 폴더 연결</h2><p>backend/.env의 EQUITY_DATA_DIR에 기존 수집기의 data 폴더 절대 경로를 입력하고 FastAPI 서버를 다시 시작하세요. 비워두면 backend/data를 읽습니다.</p><div className="file-guide"><code>EQUITY_DATA_DIR=/Users/본인/Downloads/equity-lens-collector/data</code></div><p>app.py는 수집·분석을, server.py는 화면과 API를 담당합니다. 별도 프로그램이며 동시에 실행할 수 있습니다. 대시보드는 10초마다 결과를 확인합니다.</p><div className="saved-status">파일 기준 시각 {formattedAt(data.updated_at)}</div><Button className="mt-6" variant="outline" onClick={()=>refresh().then(()=>toast.success("최신 상태 확인")).catch(e=>toast.error(e.message))}><RefreshCw size={16}/>상태 확인</Button></section>
 <section className="panel settings-panel"><span className="step-number">02</span><h2>결과 파일 직접 가져오기</h2><p>latest_portfolio.json과 reports 폴더의 보고서 JSON을 선택하세요. 부분 완료 보고서도 가져올 수 있습니다.</p><label className="field-label" htmlFor="files">JSON 선택 · 합계 2MB 이하</label><Input id="files" type="file" accept=".json" multiple onChange={e=>setFiles(Array.from(e.target.files||[]))}/><p className="meta mt-3">.env, 인증정보, analysis_status.json은 업로드하지 마세요. 가져온 포트폴리오는 연결한 data 폴더의 현재 포트폴리오 파일을 갱신합니다.</p><Button className="mt-6" disabled={busy||!files.length} onClick={importFiles}><FileUp size={16}/>{busy?"저장 중…":"로컬에 가져오기"}</Button></section>
 </div>
 <section className="panel setup-guide"><div className="panel-heading"><div><h2>분석 실행과 자동 갱신</h2><p>Ollama와 토스 인증정보는 Python에서만 사용합니다.</p></div></div><div className="setup-grid"><div><h3>1. 화면 실행</h3><code>python server.py</code><p>http://127.0.0.1:8000 에서 완성된 화면을 확인합니다.</p></div><div><h3>2. 한 번 분석</h3><code>python app.py live</code><p>또는 내 포트폴리오의 ANALYZE를 누르세요. 실제 인증정보가 필요하며 계좌 조회와 모델 호출이 발생합니다.</p></div><div><h3>3. 주기적 수집</h3><code>python app.py live --watch --stream</code><p>별도 터미널에서 실행합니다. 같은 패키지에서는 중복 실행 잠금이 적용됩니다. 다른 폴더의 수집기는 먼저 종료하세요.</p></div></div></section>
 </AppShell>;
}
