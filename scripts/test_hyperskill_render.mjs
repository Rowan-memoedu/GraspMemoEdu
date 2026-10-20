/** Read-only rendering checks against a real translated image-bearing source. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {embedSourceImages} from './hyperskill_media.mjs';
import {renderSourceHtml} from './render_mathacademy.mjs';
const [translatedFile,output]=process.argv.slice(2);
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
await fs.mkdir(output,{recursive:true});
const translated=JSON.parse(await fs.readFile(translatedFile));
const nested='<h4>第一节</h4><p>段落</p><h6>子节</h6><pre><code class="language-java">int x = 1_000 * 2;\n\tif (x &lt; 3) return;</code></pre><ul><li>外层<ul><li>内层</li></ul></li></ul><h4>第二节</h4><p>结尾</p>';
const media=await embedSourceImages([translated.step.block.text,nested],{
  directory:path.join(output,'assets'),manifest:path.join(path.dirname(translatedFile),'assets.json'),
  base_url:translated.source_url,
});
assert.ok(Object.keys(media.assets).length>0,'Independent source must contain a real image');
const bodies=media.html.map(source=>renderSourceHtml(source).html);
await fs.writeFile(path.join(output,'rendered.json'),JSON.stringify({bodies}));
const server=http.createServer(async(req,res)=>{
  try {
    const url=new URL(req.url,'http://127.0.0.1');
    if(url.pathname==='/') {
      res.setHeader('Content-Type','text/html;charset=utf-8');
      res.end(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/topic-content.css">
        <main style="max-width:850px;margin:auto;padding:20px"><h1>正文显示验证</h1>
        <div class="courseContent" id="source">${bodies[Number(url.searchParams.get('case')||0)]}</div></main>
        <script type="module">import {enhanceSourceContent} from '/topic-content.js';
          const root=document.getElementById('source');
          window.originalCodes=[...root.querySelectorAll('pre')].map(n=>n.textContent);
          window.originalText=root.textContent;
          window.imageCount=root.querySelectorAll('img').length;
          enhanceSourceContent(root); enhanceSourceContent(root);
          window.ready=true;
        </script>`);
      return;
    }
    const file=path.resolve(repo,'web',decodeURIComponent(url.pathname).slice(1));
    if(!file.startsWith(path.join(repo,'web')+path.sep))throw Error('scope');
    res.setHeader('Content-Type',/\.m?js$/.test(file)?'text/javascript':'text/css');res.end(await fs.readFile(file));
  }catch{res.statusCode=404;res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const req=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=req('playwright'),browser=await chromium.launch({channel:'chrome',headless:true}),errors=[];
try {
  const page=await browser.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  for(const scenario of [0,1])for(const width of [320,375,414,768,1280]) {
    await page.setViewportSize({width,height:900});
    await page.goto(`http://127.0.0.1:${server.address().port}/?case=${scenario}`);
    await page.waitForFunction(()=>window.ready);
    await page.waitForFunction(()=>[...document.images].every(i=>i.complete));
    await page.evaluate(()=>Promise.all([...document.images].map(i=>i.decode())));
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const result=await page.evaluate(()=>{
      const source=document.getElementById('source'),copy=source.cloneNode(true);
      copy.querySelectorAll('.sourceCodeLanguage').forEach(n=>n.remove());
      return {codeUnchanged:JSON.stringify([...source.querySelectorAll('pre')].map(n=>n.textContent))===JSON.stringify(window.originalCodes),
        textUnchanged:copy.textContent===window.originalText,
        imageCount:source.querySelectorAll('img').length,
        images:[...source.querySelectorAll('img')].every(i=>i.naturalWidth>0),
        originalImages:window.imageCount,overflow:document.documentElement.scrollWidth>innerWidth,
        nested:source.querySelectorAll('.sourceOutlineSection .sourceOutlineSection').length,
        codeBackground:getComputedStyle(source.querySelector('pre')?.parentElement||source).backgroundColor};
    });
    assert.equal(result.codeUnchanged,true);assert.equal(result.textUnchanged,true);
    assert.equal(result.imageCount,result.originalImages);assert.equal(result.images,true);
    assert.equal(result.overflow,false,`Overflow at ${width}px`);
    if(scenario===1)assert.equal(result.nested,1);
    if(width===375||width===1280)await page.screenshot({path:path.join(output,`${scenario?'hierarchy':'image-source'}-${width}.png`),fullPage:true});
    if(scenario===0&&width===375)await page.locator('#source img').first().screenshot({path:path.join(output,'image-detail.png')});
  }
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(output,'receipt.json'),JSON.stringify({source:translated.source_url,scenarios:10,
    widths:[320,375,414,768,1280],sourceTextUnchanged:true,codeUnchanged:true,imagesLoaded:true,pageErrors:errors},null,2));
  console.log('PASS original text/code, real cached images, nested headings, idempotence, 5 viewport widths');
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
