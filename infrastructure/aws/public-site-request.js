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
    body: '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow"><title>Page not found · Aperture Intelligence</title><main><h1>Page not found</h1><p>This page may not have been published yet, or its address may have changed.</p><a href="/">Return home</a></main></html>'
  };
}
