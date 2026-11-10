import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
if(!process.env.HYPERSKILL_TRIAL_ROOT)throw Error('HYPERSKILL_TRIAL_ROOT is required');
const root=path.resolve(process.env.HYPERSKILL_TRIAL_ROOT);
const sample=JSON.parse(await fs.readFile(path.join(root,'sample.json'),'utf8'));
const dir=path.join(root,'public-steps');await fs.mkdir(dir,{recursive:true});
let count=0;
for(const q of sample.questions){
  const file=path.join(dir,q.id+'.json');
  try{await fs.access(file);count++;continue;}catch{}
  const r=await fetch('https://hyperskill.org/api/steps/'+q.id,{redirect:'error',signal:AbortSignal.timeout(30000)});
  if(!r.ok)throw Error('Public step read HTTP '+r.status+' at '+q.id);
  const step=(await r.json()).steps?.[0];if(step?.id!==q.id||step.block?.name!=='code')throw Error('Step identity/type mismatch');
  await fs.writeFile(file,JSON.stringify({observed_at:new Date().toISOString(),authenticated:false,step},null,2));
  count++;if(count%10===0)console.log(JSON.stringify({public_steps:count}));
  await new Promise(r=>setTimeout(r,250));
}
console.log(JSON.stringify({public_steps:count,complete:count===100}));
