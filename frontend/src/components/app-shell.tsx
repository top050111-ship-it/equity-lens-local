"use client";
import {ChartNoAxesCombined,LayoutDashboard,Plug,BookOpen,ArrowUpRight,ShieldCheck,FlaskConical,LogOut} from "lucide-react";
import {Sidebar,SidebarContent,SidebarFooter,SidebarHeader,SidebarInset,SidebarMenu,SidebarMenuItem,SidebarMenuButton,SidebarProvider,SidebarTrigger} from "@/components/ui/sidebar";

export function AppShell({children,active="demo",user}:{children:React.ReactNode,active?:string,user?:string}){
 const isPublic=import.meta.env.VITE_PUBLIC_DEMO_ONLY==="true";
 const items=[{id:"demo",href:"/",title:"시연 콘솔",icon:FlaskConical},{id:"dashboard",href:"/dashboard",title:"내 포트폴리오",icon:LayoutDashboard},{id:"connect",href:"/connect",title:"데이터 연결",icon:Plug},{id:"project",href:"/project",title:"프로젝트 소개",icon:BookOpen}].filter(x=>!isPublic||["demo","project"].includes(x.id));
 return <SidebarProvider style={{"--sidebar-width":"15rem"} as React.CSSProperties}>
  <Sidebar className="border-0"><SidebarHeader className="px-6 pt-8 pb-9"><a href="/" className="brand"><span className="brand-mark"><ChartNoAxesCombined size={24}/></span><span>Equity<span className="font-normal">Lens</span><small>INVESTMENT RESEARCH</small></span></a></SidebarHeader>
  <SidebarContent className="px-3"><div className="nav-caption">RESEARCH WORKSPACE</div><SidebarMenu>{items.map(x=><SidebarMenuItem key={x.id}><SidebarMenuButton asChild isActive={active===x.id} className="h-12 px-4 text-[14px] mb-1"><a href={x.href}><x.icon size={18}/><span>{x.title}</span></a></SidebarMenuButton></SidebarMenuItem>)}</SidebarMenu>
   <div className="sidebar-note"><span className="micro-label">THREE PERSPECTIVES</span><p>기업을 깊게.<br/>시장을 넓게.<br/>판단은 함께.</p><div className="flex gap-2"><span>01</span><span>02</span><span>03</span></div></div>
  </SidebarContent>
  <SidebarFooter className="px-6 py-6"><div className="text-[13px] text-slate-300 flex items-center gap-2 mb-3"><ShieldCheck size={15}/>공개 시연과 로컬 자료 분리</div>{user?<><div className="text-sm truncate">{user}</div><a className="text-sm text-slate-400 flex gap-2 mt-2" href="/" target="_top"><LogOut size={14}/>시연으로</a></>:<a href={isPublic?"/project":"/dashboard"} target="_top" className="text-sm flex items-center justify-between">{isPublic?"프로젝트 소개":"로컬 공간 열기"}<ArrowUpRight size={16}/></a>}</SidebarFooter></Sidebar>
  <SidebarInset className="min-w-0 bg-transparent"><div className="topbar"><div className="flex items-center gap-3"><SidebarTrigger/><span className="text-sm text-slate-500">Workspace <span className="mx-2 text-slate-300">/</span><span className="text-slate-800">{items.find(x=>x.id===active)?.title}</span></span></div><span className="topbar-tag">EQUITY LENS <span className="text-slate-400"> / </span> RESEARCH v1</span></div><main className="workspace-main">{children}</main></SidebarInset>
 </SidebarProvider>;
}
