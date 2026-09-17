import { z } from "zod";

const short = z.string().max(300);
const prose = z.string().max(10000);
const numericText = z.string().trim().regex(/^[+]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/);
const number = z.union([z.number(), numericText]).transform(Number).pipe(z.number().finite().nonnegative());
const timestamp = z.string().refine(value => Number.isFinite(Date.parse(value)), "올바른 날짜·시각이 필요합니다.")
  .transform(value => new Date(value).toISOString());
const rawPositionSchema = z.object({
  symbol: short.min(1), name: short, market: short.default("UNKNOWN"), currency: z.enum(["KRW", "USD"]),
  quantity: number, price: number, price_at: timestamp.nullable().optional(),
  price_quality: short.default("시각 미확인"), price_source: short.default("가져온 자료"),
  value_krw: number, weight_pct: number.optional(),
});
export const positionSchema = z.preprocess(value => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const row = value as Record<string, unknown>;
  return row.value_krw === undefined && row.val_krw !== undefined
    ? { ...row, value_krw: row.val_krw }
    : row;
}, rawPositionSchema);
export const snapshotSchema = z.object({
  as_of: timestamp, valuation_at: timestamp.optional(), calculated_equity_value_krw: number,
  positions: z.array(positionSchema).max(100), warnings: z.array(prose).max(200).default([]),
  stream_status: short.optional(), scope: short.optional(),
}).superRefine((s, ctx) => {
  const sum = s.positions.reduce((n,p)=>n+p.value_krw,0);
  if(Math.abs(sum-s.calculated_equity_value_krw)>Math.max(1,sum*1e-8)) ctx.addIssue({code:"custom",message:"종목 평가액 합계와 총액이 다릅니다."});
  if(new Set(s.positions.map(p=>p.symbol)).size !== s.positions.length) ctx.addIssue({code:"custom",message:"종목 코드가 중복됩니다."});
}).transform(s => ({...s, positions:s.positions.map(p=>({...p,weight_pct:s.calculated_equity_value_krw ? p.value_krw/s.calculated_equity_value_krw*100 : 0}))}));
const ids = z.array(short).max(50);
const findingSchema=z.object({subject:short,claim:prose,kind:z.enum(["fact","inference","scenario"]),evidence_ids:ids,confidence:z.enum(["low","medium","high"]),counterpoint:prose,revisit_when:prose});
const conflictSchema=z.object({issue:short,portfolio_view:prose,macro_view:prose,assessment:prose,evidence_ids:ids});
const stageSchema=z.object({status:z.enum(["ok","failed"]),report:z.object({summary:prose,findings:z.array(findingSchema).max(50),conflicts:z.array(conflictSchema).max(25),watchlist:z.array(prose).max(50),limitations:z.array(prose).max(100)})});
const safeUrl=z.string().max(2000).url().refine(s=>{const u=new URL(s);return ["https:","http:"].includes(u.protocol)&&!u.username&&!u.password},"안전한 출처 주소가 필요합니다.");
export const reportSchema=z.object({
  created_at:timestamp,complete:z.boolean(),portfolio:stageSchema,macro:stageSchema,synthesis:stageSchema,
  sources:z.array(z.object({id:short,provider:short,title:short,content:prose,url:safeUrl,kind:short,observed_at:z.union([timestamp,z.number().finite(),z.null()]).optional(),retrieved_at:timestamp.optional(),quality:short.optional()})).max(500),
  warnings:z.array(prose).max(300).default([]),
}).superRefine((r,ctx)=>{
 const valid=new Set(r.sources.map(s=>s.id));
 if(valid.size!==r.sources.length) ctx.addIssue({code:"custom",message:"출처 ID가 중복됩니다."});
 for(const stage of [r.portfolio,r.macro,r.synthesis]) for(const f of [...stage.report.findings,...stage.report.conflicts]) if(!f.evidence_ids.length || f.evidence_ids.some(id=>!valid.has(id))) ctx.addIssue({code:"custom",message:"주장에 연결된 출처를 찾을 수 없습니다."});
 if(r.complete && [r.portfolio,r.macro,r.synthesis].some(s=>s.status!=="ok")) ctx.addIssue({code:"custom",message:"완료 상태가 일치하지 않습니다."});
});
export const packetSchema=z.object({snapshot:snapshotSchema.optional(),report:reportSchema.optional()}).refine(d=>d.snapshot||d.report,"포트폴리오 또는 보고서가 필요합니다.");
export type Snapshot=z.infer<typeof snapshotSchema>;
export type ResearchReport=z.infer<typeof reportSchema>;
export type Role="portfolio"|"macro"|"synthesis";
export type Job={id:string,status:string,created_at:string,updated_at:string,error:string|null};
export type Workspace={snapshot:Snapshot|null,report:ResearchReport|null,updated_at:string|null,heartbeat:string|null,worker_status:string|null,has_token:boolean,job:Job|null};
export const blankWorkspace:Workspace={snapshot:null,report:null,updated_at:null,heartbeat:null,worker_status:null,has_token:false,job:null};
export const labels:Record<Role,{name:string,sub:string,number:string}>={portfolio:{name:"포트폴리오 분석가",sub:"기업 · 뉴스 · 집중 위험",number:"01"},macro:{name:"글로벌 거시 분석가",sub:"금리 · 경기 · 환율",number:"02"},synthesis:{name:"종합 리서치 편집자",sub:"충돌 검토 · 최종 보고서",number:"03"}};
export function formattedAt(value?:string|null){return value?new Intl.DateTimeFormat("ko-KR",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",timeZone:"Asia/Seoul",hour12:false}).format(new Date(value))+" KST":"기록 없음";}
export function won(value:number){return "₩"+Math.round(value).toLocaleString("ko-KR");}
