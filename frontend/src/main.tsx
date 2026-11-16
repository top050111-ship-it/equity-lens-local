import React,{useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ResearchConsole} from './components/research-console';
import {Connections} from './components/connections';
import {Project} from './components/project';
import {AppShell} from './components/app-shell';
import './styles.css';

function App(){
 const [mode,setMode]=useState(import.meta.env.VITE_PUBLIC_DEMO_ONLY==='true'?'public':'loading');
 useEffect(()=>{if(mode!=='loading')return;fetch('/api/config').then(r=>{if(!r.ok)throw Error();return r.json()}).then(d=>setMode(d.mode==='local'?'local':'public')).catch(()=>setMode('offline'));},[mode]);
 const path=location.pathname;
 if(path==='/')return <ResearchConsole demo/>;
 if(path==='/project')return <Project/>;
 if(mode==='loading')return <AppShell><p role="status">연결 상태 확인 중…</p></AppShell>;
 if(mode!=='local')return <AppShell><section className="panel settings-panel"><h1>{mode==='public'?'공개 시연 전용':'Python 서버 연결 필요'}</h1><p>{mode==='public'?'실제 포트폴리오·파일 가져오기·분석 실행은 로컬 앱에서만 사용할 수 있습니다.':'backend 폴더에서 python server.py를 실행하고 새로고침하세요.'}</p><a href="/">시연 콘솔로 돌아가기 →</a></section></AppShell>;
 if(path==='/dashboard')return <ResearchConsole user="내 컴퓨터 · 로컬 전용"/>;
 if(path==='/connect')return <Connections user="내 컴퓨터 · 로컬 전용"/>;
 return <AppShell><h1>페이지를 찾을 수 없습니다.</h1><a href="/">홈으로</a></AppShell>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
