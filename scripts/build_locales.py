#!/usr/bin/env python3
"""Compile reviewed UI labels into shared server/browser locale dictionaries."""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.bilingual import model_json


class Texts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts = []

    def handle_data(self, data):
        self.texts.append(data)

    def handle_starttag(self, tag, attrs):
        self.texts.extend(value for name, value in attrs
                          if name in {"title", "placeholder", "aria-label", "alt", "content"} and value)


def collect():
    literals = []
    for name in ["app", "homepage", "database", "growth", "publication_protection", "article_redirects"]:
        tree = ast.parse((ROOT / "backend" / f"{name}.py").read_text())
        literals.extend(node.value for node in ast.walk(tree)
                        if isinstance(node, ast.Constant) and isinstance(node.value, str))
    javascript = """
const ts=require('./agent_runtime/node_modules/typescript'),fs=require('fs');
const values=[];
for(const path of ['frontend/public/app.js','frontend/public/growth.js','frontend/admin/app.js']){
 const source=ts.createSourceFile(path,fs.readFileSync(path,'utf8'),ts.ScriptTarget.Latest,true);
 function visit(node){
  if(ts.isStringLiteral(node)||ts.isNoSubstitutionTemplateLiteral(node)||
     node.kind===ts.SyntaxKind.TemplateHead||node.kind===ts.SyntaxKind.TemplateMiddle||
     node.kind===ts.SyntaxKind.TemplateTail) values.push(node.text);
  ts.forEachChild(node,visit);
 }
 visit(source);
}
console.log(JSON.stringify(values));
"""
    literals.extend(json.loads(subprocess.check_output(["node", "-e", javascript], cwd=ROOT)))
    literals.extend((ROOT / f"frontend/{site}/index.html").read_text() for site in ["public", "admin"])
    values = set()
    for literal in literals:
        if not re.search(r"[\u3400-\u9fff]", literal):
            continue
        parser = Texts()
        parser.feed(literal)
        for text in parser.texts:
            # Literal boundaries surround dynamic placeholders in both Python and JS.
            for part in re.split(r"\{[^{}]*\}|[\n\r]", text):
                part = part.strip()
                if re.search(r"[\u3400-\u9fff]", part) and len(part) <= 5000:
                    values.add(part)
    return sorted(values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    destination = ROOT / "backend/locales/en.json"
    existing = json.loads(destination.read_text()) if destination.exists() else {}
    values = [value for value in collect() if value not in existing]
    batches, batch, size = [], [], 0
    for value in values:
        if batch and (size + len(value) > 6000 or len(batch) >= 70):
            batches.append(batch)
            batch, size = [], 0
        batch.append(value)
        size += len(value)
    if batch:
        batches.append(batch)
    print(json.dumps({"missingStrings": len(values), "batches": len(batches)}), flush=True)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def translate(items):
        result = model_json("""Translate every Chinese UI label, message or content preview into concise,
natural English for a technical research website and its admin console. Preserve all facts,
numbers, punctuation needed around variable placeholders, URLs, markup and code fragments.
Some strings are fragments before or after a dynamic value; keep them usable as fragments.
Do not add facts or explanations. Return a JSON object {"translations":[English string,...]}
with exactly one string for every input, in the same order.
""" + json.dumps(items, ensure_ascii=False))
        texts = result.get("translations")
        if not isinstance(texts, list) or len(texts) != len(items) or not all(isinstance(s, str) and s.strip() for s in texts):
            raise ValueError("Incomplete UI translation batch")
        return dict(zip(items, texts))

    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(translate, batch) for batch in batches]
        for index, future in enumerate(as_completed(futures), 1):
            try:
                existing.update(future.result())
                destination.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps({"completedBatches": index, "strings": len(existing)}), flush=True)
            except Exception as error:
                errors.append(str(error))
                print("ERROR " + str(error), flush=True)
    for site in ["public", "admin"]:
        (ROOT / f"frontend/{site}/locales.js").write_text(
            "window.APERTURE_EN = " + json.dumps(existing, ensure_ascii=False).replace("<", "\\u003c") + ";\n")
    if errors:
        raise SystemExit("Some locale batches failed; rerun to resume.")


if __name__ == "__main__":
    main()
