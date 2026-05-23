chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "extract_page") return false;
  const title = document.title || "";
  const text = collectVisibleText();
  const html = document.documentElement?.outerHTML || "";
  const metadata = {
    description: meta("description"),
    siteName: meta("og:site_name"),
    lang: document.documentElement?.lang || "",
  };
  sendResponse({ ok: true, title, text, readabilityText: text, html, metadata });
  return true;
});

function meta(name) {
  return (
    document.querySelector(`meta[name="${name}"]`)?.content ||
    document.querySelector(`meta[property="${name}"]`)?.content ||
    ""
  );
}

function collectVisibleText() {
  const blocked = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "SVG", "CANVAS"]);
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  const chunks = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const parent = node.parentElement;
    if (!parent || blocked.has(parent.tagName)) continue;
    const style = getComputedStyle(parent);
    if (style.display === "none" || style.visibility === "hidden") continue;
    const value = node.nodeValue?.replace(/\s+/g, " ").trim();
    if (value) chunks.push(value);
  }
  return chunks.join("\n");
}
