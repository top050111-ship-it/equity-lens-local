import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const temp=path.join(root,'src/lib',`data.contracts.${process.pid}.mjs`);
try {
 const source=fs.readFileSync(path.join(root,'src/lib/data.ts'),'utf8');
 fs.writeFileSync(temp,ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText);
 const {reportSchema,snapshotSchema}=await import(pathToFileURL(temp));
 const at='2026-11-16T00:00:00Z';
 const sourceRow={id:'Efixture',provider:'fixture',title:'fixture',content:'fixture only',url:'https://example.com',kind:'financial',period_end:'2026-09-30',primary:true};
 const stage={status:'failed',report:{summary:'fixture',findings:[],conflicts:[],watchlist:[],limitations:[]}};
 const report={created_at:at,complete:false,portfolio:stage,macro:stage,synthesis:stage,sources:[sourceRow],
               research_v2:{companies:[],quality:{companies:0}},warnings:[]};
 const parsed=reportSchema.parse(report);
 assert.deepEqual(parsed.research_v2,report.research_v2);
 assert.equal(parsed.sources[0].period_end,sourceRow.period_end);
 const large=structuredClone(report);
 while(large.sources.length<1000)large.sources.push({...sourceRow,id:'Efixture'+large.sources.length});
 assert.equal(reportSchema.parse(large).sources.length,1000);
 const emptyComplete=structuredClone(report);emptyComplete.complete=true;
 for(const key of ['portfolio','macro','synthesis'])emptyComplete[key].status='ok';
 assert.throws(()=>reportSchema.parse(emptyComplete));
 assert.throws(()=>snapshotSchema.parse({as_of:at,calculated_equity_value_krw:'NaN',positions:[]}));
 console.log('Frontend contracts: 5 passed (deep metadata, provenance, 1000 sources, empty completion, non-finite rejection)');
} finally {fs.rmSync(temp,{force:true});}
