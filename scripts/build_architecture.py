#!/usr/bin/env python3
"""Render the editable draw.io source with native mxGraph and AWS4 service shapes."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import cairosvg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/architecture"
VENDOR = OUT / "vendor"

RENDER = r"""({xml, width, height}) => {
    const element = document.createElement('div');
    element.style.cssText = `position:relative;width:${width}px;height:${height}px;background:white`;
    document.body.appendChild(element);
    const graph = new mxGraph(element);
    graph.setEnabled(false);
    graph.setHtmlLabels(false);
    graph.view.setTranslate(0, 0);
    const source = mxUtils.parseXml(xml);
    new mxCodec(source).decode(source.documentElement, graph.getModel());
    graph.getView().validate();
    const doc = mxUtils.createXmlDocument();
    const svg = doc.createElementNS(mxConstants.NS_SVG, 'svg');
    svg.setAttribute('xmlns', mxConstants.NS_SVG);
    svg.setAttribute('xmlns:xlink', mxConstants.NS_XLINK);
    svg.setAttribute('width', width);
    svg.setAttribute('height', height);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    doc.appendChild(svg);
    const background = doc.createElementNS(mxConstants.NS_SVG, 'rect');
    background.setAttribute('width', width);
    background.setAttribute('height', height);
    background.setAttribute('fill', '#ffffff');
    svg.appendChild(background);
    const canvas = new mxSvgCanvas2D(svg);
    canvas.textEnabled = true;
    new mxImageExport().drawState(graph.getView().getState(graph.model.root), canvas);
    const overflows = [];
    let icons = 0;
    for (const cell of graph.getChildVertices(graph.getDefaultParent())) {
        const state = graph.getView().getState(cell);
        if (state.style.shape === 'mxgraph.aws4.resourceIcon') {
            if (!mxStencilRegistry.getStencil(state.style.resIcon)) throw Error('Unknown AWS stencil');
            if (state.style.strokeColor !== '#FFFFFF') throw Error('AWS glyph must have white foreground');
            icons++;
        }
        if (state.text && state.text.boundingBox && cell.value) {
            const bounds = state.text.boundingBox;
            if (bounds.width > state.width + 3 || bounds.height > state.height + 3) {
                overflows.push({id: cell.id, text: cell.value, width: bounds.width, height: bounds.height,
                                allowedWidth: state.width, allowedHeight: state.height});
            }
        }
    }
    const result = {svg: mxUtils.getXml(svg), icons, overflows};
    graph.destroy();
    element.remove();
    return result;
}"""


def main():
    provenance = json.loads((VENDOR / "provenance.json").read_text())
    for name, expected in provenance["renderer"]["files"].items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == expected, name
    source = OUT / "aperture-aws.drawio"
    diagrams = ET.parse(source).getroot()
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 2000, "height": 1300})
        # Vendor scripts and stencils are local; exporting never contacts AWS or draw.io.
        page.route("**/*", lambda route: route.abort())
        page.set_content('<!doctype html><html lang="en"><body style="margin:0"></body></html>')
        page.evaluate("window.mxLoadResources=false;window.mxLoadStylesheets=false;window.mxBasePath='';")
        page.add_script_tag(path=str(VENDOR / "mxClient.min.js"))
        page.add_script_tag(path=str(VENDOR / "mxAWS4.js"))
        page.evaluate("""xml => {
            const doc = mxUtils.parseXml(xml);
            for (const shape of doc.documentElement.children) {
                const name = 'mxgraph.aws4.' + shape.getAttribute('name').replace(/ /g, '_');
                mxStencilRegistry.addStencil(name, new mxStencil(shape));
            }
        }""", (VENDOR / "aws4-icons.xml").read_text())
        for diagram in diagrams:
            model = diagram.find("mxGraphModel")
            width, height = int(model.get("pageWidth")), int(model.get("pageHeight"))
            result = page.evaluate(RENDER, {"xml": ET.tostring(model, encoding="unicode"),
                                            "width": width, "height": height})
            assert result["icons"] > 0, f"No rendered AWS icons: {diagram.get('id')}"
            assert "foreignObject" not in result["svg"], "Export must contain portable SVG text"
            if result["overflows"]:
                raise AssertionError(json.dumps({"page": diagram.get("id"), "overflows": result["overflows"]}, indent=2))
            slug = diagram.get("id")
            destination = OUT / f"{slug}.svg"
            destination.write_text(result["svg"] + "\n")
            cairosvg.svg2png(bytestring=result["svg"].encode(), write_to=str(OUT / f"{slug}.png"), scale=2)
            results.append({"page": slug, "width": width, "height": height,
                            "nativeAwsIcons": result["icons"], "textOverflows": 0,
                            "svgSha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                            "pngSha256": hashlib.sha256((OUT / f"{slug}.png").read_bytes()).hexdigest()})
            print(f"{slug}: {result['icons']} native AWS icons, no text overflow")
        browser.close()
    (OUT / "manifest.json").write_text(json.dumps({
        "source": source.name, "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "style": "Original Chinese AWS layout, translated and updated in English; native draw.io AWS4 shapes",
        "renderer": "mxGraph 4.2.2 + draw.io mxAWS4 / Chromium / CairoSVG",
        "pages": results,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
