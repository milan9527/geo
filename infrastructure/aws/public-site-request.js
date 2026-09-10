// Public pages, including "/", have explicit SSR cache behaviors and bypass
// this default S3 behavior. Keep the home rewrite for older distributions;
// arbitrary paths must never become indexable home pages.
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri === "/") {
    request.uri = "/index.html";
    return request;
  }
  if (uri === "/index.html") {
    return {
      statusCode: 301,
      statusDescription: "Moved Permanently",
      headers: { location: { value: "/" } }
    };
  }
  var page = uri.replace(/\/+$/, "");
  if (uri !== page && [
    "/about", "/methodology", "/editorial-policy", "/corrections"
  ].indexOf(page) !== -1) {
    return {
      statusCode: 301,
      statusDescription: "Moved Permanently",
      headers: { location: { value: page } }
    };
  }
  // Static assets and search-engine ownership files are served directly by S3.
  var leaf = uri.substring(uri.lastIndexOf("/") + 1);
  if (leaf.indexOf(".") !== -1) return request;
  return {
    statusCode: 404,
    statusDescription: "Not Found",
    headers: {
      "content-type": { value: "text/html; charset=utf-8" },
      "cache-control": { value: "public, max-age=60" },
      "x-robots-tag": { value: "noindex, nofollow" }
    },
    body: '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow"><title>页面未找到 · Aperture Intelligence</title><main><h1>页面未找到</h1><p>内容可能尚未发布、已经更新或移动。</p><a href="/">返回首页</a></main></html>'
  };
}
