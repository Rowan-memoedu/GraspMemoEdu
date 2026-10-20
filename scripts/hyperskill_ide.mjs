/** The official Hyperskill plugin's Solve in IDE REST entry point.
 * https://github.com/hyperskill/hyperskill-plugin/blob/main/intellij-plugin/hs-core/src/org/hyperskill/academy/learning/stepik/hyperskill/HyperskillRestService.kt
 * Only the plugin creates the project, owns files, and submits Check results.
 */
import fs from 'node:fs/promises';
import {spawn} from 'node:child_process';

export function officialIDE({executable,properties,fetcher=fetch,
  ports=Array.from({length:21},(_,i)=>63342+i),start,wait=ms=>new Promise(r=>setTimeout(r,ms))}={}) {
  const headers={Origin:'https://hyperskill.org'};
  const discover=async()=>{
    const candidates=await Promise.all(ports.map(async port=>{
      try {
        const url=`http://127.0.0.1:${port}/api/hyperskill`;
        const response=await fetcher(url+'/info',{headers,redirect:'error',signal:AbortSignal.timeout(1200)});
        if(!response.ok)return null;
        const info=await response.json();
        return typeof info.edutools==='string'&&/IntelliJ IDEA/i.test(info.version||'')?url:null;
      } catch {return null;}
    }));
    return candidates.find(Boolean);
  };
  return async({step,user,language})=>{
    if(!Number.isSafeInteger(step)||step<=0||!Number.isSafeInteger(user)||user<=0||language!=='java')
      throw Error('IDEA 练习身份或语言无效。');
    let endpoint=await discover();
    if(!endpoint) {
      if(start)await start();
      else {
        if(!executable)throw Error('请先启动装有 Hyperskill 官方插件的 IDEA。');
        await fs.access(executable);
        if(properties)await fs.access(properties);
        await new Promise((resolve,reject)=>{
          const child=spawn(executable,[],{windowsHide:true,stdio:'ignore',detached:true,
            env:{...process.env,...(properties?{IDEA_PROPERTIES:properties}:{})}});
          child.once('error',reject);child.once('spawn',()=>{child.unref();resolve();});
        });
      }
      for(let i=0;i<30&&!endpoint;i++) {await wait(1500);endpoint=await discover();}
    }
    if(!endpoint)throw Error('尚未找到 IDEA 的 Hyperskill 官方插件，请确认插件已启用。');
    const query=new URLSearchParams({step_id:String(step),language,user_id:String(user)});
    const response=await fetcher(`${endpoint}?${query}`,{headers,redirect:'error',signal:AbortSignal.timeout(90000)});
    if(!response.ok)throw Error('Hyperskill 官方插件未能打开本题，请查看 IDEA 中的提示。');
  };
}
