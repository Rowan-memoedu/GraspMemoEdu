/** Media is embedded before sanitizing, through the existing RemNote converter. */
import {execFile} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const script=fileURLToPath(new URL('./hyperskill_media.py',import.meta.url));
export async function embedSourceImages(html, {
  directory='D:/AppData/GraspMemoEduHyperskillBridge/media', manifest,
  base_url='https://hyperskill.org/',
}={}) {
  if(!html.some(value=>/<img\b/i.test(value)))return {html,assets:{}};
  const python=process.env.HYPERSKILL_REMNOTE_PYTHON||
    'D:/AppData/HyperskillRemNoteWorkflow/venv/Scripts/python.exe';
  return new Promise((resolve,reject)=>{
    const child=execFile(python,[script],{timeout:240000,maxBuffer:64*1024*1024,
      windowsHide:true,encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'}},
      (error,stdout,stderr)=>{
        if(error)return reject(Error(`图片预处理失败：${stderr.slice(-800) || error.message}`));
        try{resolve(JSON.parse(stdout));}catch{reject(Error('图片预处理未返回有效内容。'));}
      });
    child.stdin.on('error',()=>{});
    child.stdin.end(JSON.stringify({html,directory,manifest,base_url}));
  });
}
