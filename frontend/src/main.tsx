// Copyright (C) 2026 Seungbeom Hong
// SPDX-License-Identifier: AGPL-3.0-or-later

/** @jsx React.createElement */
// @ts-expect-error React is provided by the runtime environment.
import React,* as react from 'react';
// @ts-expect-error React DOM is provided by the runtime environment.
import {createRoot} from 'react-dom/client';
import {ResearchConsole} from './components/research-console';
import {Connections} from './components/connections';
import {Project} from './components/project';
import {AppShell} from './components/app-shell';
// @ts-expect-error CSS is handled by the bundler at runtime.
import './styles.css';

function App(){
 const [mode,setMode]=react.useState((import.meta as ImportMeta & {env?:{VITE_PUBLIC_DEMO_ONLY?:string}}).env?.VITE_PUBLIC_DEMO_ONLY==='true'?'public':'loading');
 react.useEffect(()=>{if(mode!=='loading')return;fetch('/api/config').then(r=>{if(!r.ok)throw Error();return r.json()}).then(d=>setMode(d.mode==='local'?'local':'public')).catch(()=>setMode('offline'));},[mode]);
 const path=location.pathname;
if(path==='/')return React.createElement(ResearchConsole,{demo:true});
if(path==='/project')return React.createElement(Project);
 if(mode==='loading')return React.createElement(AppShell,null,React.createElement('p',{role:'status'},'연결 상태 확인 중…'));
if(mode!=='local')return React.createElement(AppShell,null,React.createElement('section',{className:'panel settings-panel'},React.createElement('h1',null,mode==='public'?'공개 시연 전용':'Python 서버 연결 필요'),React.createElement('p',null,mode==='public'?'실제 포트폴리오·파일 가져오기·분석 실행은 로컬 앱에서만 사용할 수 있습니다.':'backend 폴더에서 python server.py를 실행하고 새로고침하세요.'),React.createElement('a',{href:'/'},'시연 콘솔로 돌아가기 →')));
if(path==='/dashboard')return React.createElement(ResearchConsole,{user:'내 컴퓨터 · 로컬 전용'});
if(path==='/connect')return React.createElement(Connections,{user:'내 컴퓨터 · 로컬 전용'});
 return React.createElement(AppShell,null,
  React.createElement('h1',null,'페이지를 찾을 수 없습니다.'),
  React.createElement('a',{href:'/'},'홈으로')
 );
}
createRoot(document.getElementById('root')!).render(
 React.createElement(React.StrictMode,null,React.createElement(App))
);
