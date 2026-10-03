import { fromHtml } from "hast-util-from-html";

const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const textOf = node => node.type === "text" ? node.value : (node.children ?? []).map(textOf).join("");

/** Transform only explicitly labelled, text-only Mermaid blocks.
 * No UI strings are added to the indexed source; runtime creates controls.
 * Returns the original decoded source in a readable pre if scripts cannot run.
 */
export function prepareMermaid(html) {
  const diagnostics = [];
  const output = html.replace(/<pre\b[^>]*>\s*<code\b([^>]*)>([\s\S]*?)<\/code>\s*<\/pre>/giu, (whole, attrs, encoded) => {
    const classes = attrs.match(/\bclass\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/iu);
    if (!(classes?.slice(1).find(Boolean) ?? "").split(/\s+/u).includes("language-mermaid")) return whole;
    const root = fromHtml(encoded.replace(/\r/gu,"&#13;"), {fragment:true});
    if (root.children.some(n=>n.type!=="text")) {
      diagnostics.push({status:"preserved",reason:"non-text-code-block"});
      return whole;
    }
    const source = textOf(root);
    diagnostics.push({status:"prepared",reason:"explicit-mermaid-code"});
    return '<figure class="local-mermaid"><div class="mermaid-output"></div><pre><code class="mermaid-source">'+escapeHtml(source).replace(/\r/gu,"&#13;")+'</code></pre></figure>';
  });
  return {html:output,diagnostics};
}

// Diagram-level settings must not override the site's local-only render policy.
export function sourceIssue(source) {
  if (!source.trim()) return "empty";
  if (source.length > 20000) return "too-large";
  if (/^\s*---(?:\r?\n|$)|%%\s*\{/u.test(source)) return "configuration";
  if (/<\s*(?:img|image|iframe|script|link|style|svg|foreignObject)\b|\b(?:img|image|icon|href|src)\s*[:=]|\bclick\s+|@import|\burl\s*\(|!\[[^\]]*\]\s*\(/iu.test(source)) return "external-or-active-content";
  return null;
}

const messages = {
  empty:"图表源码为空。",
  "too-large":"图表超过本地渲染的大小限制，源码已保留。",
  configuration:"此图包含尚未支持的图内配置，源码已保留。",
  "external-or-active-content":"此图包含外部资源或交互指令，当前仅显示源码。",
  syntax:"图表语法未通过解析，原始源码已保留。",
  library:"本地图表组件加载失败，原始源码已保留。",
  output:"图表输出包含未支持的内容，原始源码已保留。",
};

/** Browser entry. Bundle with esbuild locally; do not import Mermaid from a CDN.
 * An engine can be injected for tests or supplied by the host. Returned results
 * distinguish rendering from source preservation, without including source text.
 */
export async function mountMermaid(root = document, {engine, loadEngine = () => import("mermaid")} = {}) {
  const doc = root.ownerDocument ?? root;
  const hosts = [...root.querySelectorAll(".local-mermaid")].filter(h=>!h.dataset.mermaidState);
  if (!hosts.length) return [];
  const results = [];
  let library;
  try {
    library = engine ?? (await loadEngine()).default;
    library.initialize({
      startOnLoad:false, securityLevel:"strict", suppressErrorRendering:true,
      htmlLabels:false, maxTextSize:20000, maxEdges:250, theme:"neutral",
      fontFamily:'Arial, "Microsoft YaHei", sans-serif',
      flowchart:{htmlLabels:false,useMaxWidth:false},
      secure:["secure","securityLevel","startOnLoad","maxTextSize","maxEdges","suppressErrorRendering","htmlLabels","themeCSS"],
    });
  } catch { library = null; }
  for (const host of hosts) {
    if (host.dataset.mermaidState) continue;
    host.dataset.mermaidState = "pending";
    const code = host.querySelector("pre > code.mermaid-source");
    if (!code) {host.dataset.mermaidState="error";results.push({status:"error",reason:"missing-source"});continue;}
    const source = code.textContent, pre = code.parentElement;
    const details = doc.createElement("details"), summary = doc.createElement("summary");
    summary.textContent = "查看图表源码"; details.append(summary,pre);host.append(details);
    const status = doc.createElement("p");status.className="mermaid-status";status.setAttribute("role","status");
    status.textContent="正在绘制图表…";host.prepend(status);
    const fail = reason => {
      status.textContent=messages[reason];details.open=true;
      host.dataset.mermaidState="error";results.push({status:"error",reason});
    };
    const issue = sourceIssue(source);
    if (issue || !library) {fail(issue??"library");continue;}
    // A measurable detached-from-layout stage also works for folded ancestors.
    const stage = doc.createElement("div");stage.className="mermaid-measure-stage";
    Object.assign(stage.style,{position:"absolute",left:"-100000px",top:"0",width:"800px",visibility:"hidden",pointerEvents:"none"});
    stage.setAttribute("aria-hidden","true");doc.body.append(stage);
    try {
      await doc.fonts?.ready;
      let number = 1;
      while(doc.getElementById("local-mermaid-svg-"+number))number++;
      const {svg} = await library.render("local-mermaid-svg-"+number,source,stage);
      const parsed = new doc.defaultView.DOMParser().parseFromString(svg,"image/svg+xml");
      const diagram = parsed.documentElement;
      const unsafe = diagram.localName!=="svg" || parsed.querySelector("parsererror,script,foreignObject,image,iframe,object,embed") ||
        [...diagram.querySelectorAll("*"),diagram].some(n=>[...n.attributes].some(a=>
          /^on/iu.test(a.name) ||
          /(?:^|:)href$/iu.test(a.name) && !a.value.startsWith("#") ||
          a.name==="style" && /@import|url\(\s*["']?(?!#)/iu.test(a.value)
        ));
      if(unsafe) {fail("output");continue;}
      const box=(diagram.getAttribute("viewBox")??"").trim().split(/[\s,]+/u).map(Number);
      const width=box.length===4&&box[2]>0&&Number.isFinite(box[2])?box[2]:800;
      diagram.style.width=width+"px";diagram.style.maxWidth="none";diagram.style.height="auto";
      diagram.setAttribute("role","img");
      if(!diagram.hasAttribute("aria-label")&&!diagram.hasAttribute("aria-labelledby"))diagram.setAttribute("aria-label","Mermaid 图表");
      const output=host.querySelector(".mermaid-output");output.replaceChildren(doc.importNode(diagram,true));
      output.setAttribute("tabindex","0");output.setAttribute("aria-label","图表，可横向滚动查看");
      status.hidden=true;host.dataset.mermaidState="rendered";results.push({status:"rendered"});
    } catch {fail("syntax");} finally {stage.remove();}
  }
  return results;
}
