// <stdin>
function collectDocumentBacklinks(pages) {
  const documents = {}, nodes = /* @__PURE__ */ new Map(), roots = /* @__PURE__ */ new Map(), edges = [];
  for (const { slug, document: doc } of pages) {
    if (slug === "index") continue;
    const root = doc.querySelector(".reading-outline");
    if (!root) continue;
    for (const node of root.querySelectorAll(".outline-node")) {
      const owner = node.closest('[data-rem-type="document"],[data-rem-type="dailyDocument"]');
      if (!owner) continue;
      nodes.set(node.id, { owner: owner.id, slug });
      if (owner === node) {
        const label = node.querySelector(":scope > .node-content");
        documents[node.id] = { title: label?.dataset.nodeLabel ?? label?.textContent ?? "", href: "/blog/" + slug + "#" + node.id, links: [] };
        if (!node.parentElement.closest(".outline-node")) roots.set(slug, node.id);
      }
      if (node.hasAttribute("data-parent-reference") || node.dataset.publicationState === "draft") continue;
      for (const a of node.querySelectorAll(":scope > .node-content a.internal[href]")) edges.push({ source: owner.id, slug, href: a.getAttribute("href") });
    }
  }
  const outgoing = {};
  for (const { source, slug, href } of edges) {
    const url = new URL(href, "https://website.invalid/blog/" + slug);
    if (url.origin !== "https://website.invalid") continue;
    const targetSlug = url.pathname.replace(/^\/blog\//, "").replace(/\.html$/, "").replace(/\/$/, "");
    const targetNode = url.hash ? nodes.get(decodeURIComponent(url.hash.slice(1))) : null;
    const target = url.hash ? targetNode?.slug === targetSlug ? targetNode.owner : null : roots.get(targetSlug);
    if (!target || target === source || !documents[target]) continue;
    if (!documents[target].links.includes(source)) documents[target].links.push(source);
    (outgoing[slug] ??= /* @__PURE__ */ new Set()).add(targetSlug);
  }
  return { documents, outgoing: Object.fromEntries(Object.entries(outgoing).map(([slug, targets]) => [slug, [...targets]])) };
}
function renderBacklinks(doc, data) {
  const root = doc.querySelector(".reading-outline");
  const right = doc.querySelector(".sidebar.right");
  if (!root || !right) return;
  let panel = right.querySelector(":scope > .backlinks:not([data-document-attachment-sidebar])");
  if (!panel) {
    panel = doc.createElement("div");
    panel.className = "backlinks";
    const heading = doc.createElement("h3");
    heading.textContent = "\u53CD\u5411\u94FE\u63A5";
    panel.append(heading);
    right.append(panel);
  }
  let list = panel.querySelector("ul");
  if (!list) {
    list = doc.createElement("ul");
    list.className = "overflow";
    panel.append(list);
  }
  const active = doc.getElementById(decodeURIComponent(doc.defaultView.location.hash.slice(1)));
  const owner = active && root.contains(active) ? active.closest('[data-rem-type="document"],[data-rem-type="dailyDocument"]') : doc.getElementById(root.dataset.documentTitleSource);
  for (const child of [...list.children]) if (!child.classList.contains("overflow-end")) child.remove();
  const entries = data.documents[owner?.id]?.links ?? [];
  for (const id of entries) {
    const item = doc.createElement("li"), link = doc.createElement("a");
    link.className = "internal";
    link.href = data.documents[id].href;
    link.textContent = data.documents[id].title;
    item.append(link);
    list.insertBefore(item, list.querySelector(".overflow-end"));
  }
  if (!entries.length) {
    const item = doc.createElement("li");
    item.textContent = "\u6682\u65E0\u53CD\u5411\u94FE\u63A5";
    list.insertBefore(item, list.querySelector(".overflow-end"));
  }
  panel.dataset.documentBacklinks = owner?.id ?? "";
}
function mountDocumentBacklinks(doc, data) {
  renderBacklinks(doc, data);
  const script = doc.createElement("script");
  script.textContent = "(()=>{const data=" + JSON.stringify(data).replace(/</g, "\\u003c") + ";const render=" + renderBacklinks.toString() + ';const update=()=>render(document,data);addEventListener("hashchange",update);addEventListener("popstate",update);update();})();';
  doc.body.append(script);
}
export {
  collectDocumentBacklinks,
  mountDocumentBacklinks,
  renderBacklinks
};
