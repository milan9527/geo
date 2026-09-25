#!/usr/bin/env python3
"""Build matching English SVG, PNG, and editable draw.io architecture diagrams."""
from html import escape
from pathlib import Path
import xml.etree.ElementTree as ET

import cairosvg

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/architecture"
NAVY, TEAL, INK, MUTED = "#122330", "#0E9384", "#152A36", "#56697A"
ORANGE, PURPLE, BLUE, RED = "#C96917", "#7760C0", "#2478B8", "#B74444"


class Scene:
    def __init__(self, slug, title, subtitle, height=850):
        self.slug, self.title, self.width, self.height = slug, title, 1440, height
        self.svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="{height}" viewBox="0 0 1440 {height}">',
            '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" '
            'orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#56697A"/></marker></defs>',
        ]
        self.model = ET.Element("mxGraphModel", dx="1440", dy=str(height), grid="1",
                                gridSize="10", page="1", pageScale="1", pageWidth="1440",
                                pageHeight=str(height), background="#F4F7F9")
        self.root = ET.SubElement(self.model, "root")
        ET.SubElement(self.root, "mxCell", id="0")
        ET.SubElement(self.root, "mxCell", id="1", parent="0")
        self.counter = 1
        self.rect(0, 0, 1440, height, "#F4F7F9", "#F4F7F9", radius=0)
        self.text(48, 30, 1344, 48, title, 34, bold=True)
        self.text(48, 86, 1344, 42, subtitle, 20, MUTED)

    def cell(self, value, style, x, y, w, h):
        self.counter += 1
        cell = ET.SubElement(self.root, "mxCell", id=str(self.counter), value=value,
                             style=style, vertex="1", parent="1")
        ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h),
                      attrib={"as": "geometry"})
        return str(self.counter)

    def rect(self, x, y, w, h, fill="#FFFFFF", stroke="#D9E2E8", radius=12):
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
                        f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
        return self.cell("", f"rounded={int(radius > 0)};arcSize=10;whiteSpace=wrap;html=0;"
                         f"fillColor={fill};strokeColor={stroke};", x, y, w, h)

    def text(self, x, y, w, h, text, size=22, color=INK, bold=False, align="left"):
        lines = text.split("\n")
        assert len(lines) * size * 1.25 <= h + 4, (self.slug, text, h)
        anchor = "middle" if align == "center" else "start"
        tx = x + w / 2 if align == "center" else x
        parts = "".join(f'<tspan x="{tx}" y="{y + size + i * size * 1.25}">{escape(line)}</tspan>'
                        for i, line in enumerate(lines))
        self.svg.append(f'<text fill="{color}" font-family="Arial, sans-serif" font-size="{size}" '
                        f'font-weight="{700 if bold else 400}" text-anchor="{anchor}">{parts}</text>')
        self.cell(text, f"text;html=0;whiteSpace=wrap;overflow=hidden;fillColor=none;strokeColor=none;"
                  f"fontFamily=Arial;fontSize={size};fontColor={color};fontStyle={int(bold)};"
                  f"align={align};verticalAlign=top;spacing=0;", x, y, w, h)

    def card(self, x, y, w, h, title, body, color=TEAL, size=22):
        ident = self.rect(x, y, w, h)
        self.rect(x, y + 16, 5, h - 32, color, color, radius=0)
        title_lines = len(title.split("\n"))
        self.text(x + 20, y + 16, w - 36, title_lines * size * 1.25 + 4, title, size, bold=True)
        offset = 24 + title_lines * size * 1.25
        self.text(x + 20, y + offset, w - 36, h - offset - 12, body, 19, MUTED)
        return ident

    def edge(self, points, dashed=False):
        data = " ".join(f"{x},{y}" for x, y in points)
        dash = ' stroke-dasharray="7 6"' if dashed else ""
        self.svg.append(f'<polyline points="{data}" fill="none" stroke="{MUTED}" stroke-width="2"'
                        f'{dash} marker-end="url(#arrow)"/>')
        self.counter += 1
        cell = ET.SubElement(self.root, "mxCell", id=str(self.counter), edge="1", parent="1",
                             style=f"endArrow=block;html=0;rounded=0;strokeColor={MUTED};"
                                   f"strokeWidth=2;dashed={int(dashed)};")
        geom = ET.SubElement(cell, "mxGeometry", relative="1", attrib={"as": "geometry"})
        for point, role in [(points[0], "sourcePoint"), (points[-1], "targetPoint")]:
            ET.SubElement(geom, "mxPoint", x=str(point[0]), y=str(point[1]), attrib={"as": role})
        if len(points) > 2:
            arr = ET.SubElement(geom, "Array", attrib={"as": "points"})
            for x, y in points[1:-1]:
                ET.SubElement(arr, "mxPoint", x=str(x), y=str(y))

    def export(self, mxfile):
        svg = "\n".join(self.svg + ["</svg>"])
        (OUT / f"{self.slug}.svg").write_text(svg + "\n")
        cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT / f"{self.slug}.png"), scale=2)
        diagram = ET.SubElement(mxfile, "diagram", id=self.slug, name=self.title)
        diagram.append(self.model)


def overview():
    s = Scene("aws-overview", "Aperture on AWS",
              "Scheduled research, reviewed bilingual publication, and public delivery")
    s.text(48, 135, 900, 30, "RESEARCH & PUBLICATION", 18, TEAL, True)
    s.card(48, 185, 220, 140, "EventBridge\nScheduler", "UTC schedules", ORANGE)
    s.card(306, 185, 170, 140, "Lambda\nbridge", "Async dispatch", ORANGE)
    s.card(514, 165, 325, 180, "AgentCore Runtime",
           "Codex SDK + Bedrock\nBrowser + Code Interpreter\nEvidence collection", PURPLE)
    s.card(877, 185, 225, 140, "Review + publish", "Quality + dedup\nEnglish review", TEAL, 20)
    s.card(1155, 360, 237, 175, "Aurora\nPostgreSQL", "Data API\nContent + operations", BLUE)
    s.edge([(268, 255), (306, 255)])
    s.edge([(476, 255), (514, 255)])
    s.edge([(839, 255), (877, 255)])
    s.edge([(1102, 255), (1273, 255), (1273, 360)])
    s.text(48, 362, 900, 30, "SERVING & DISCOVERY", 18, TEAL, True)
    s.card(48, 415, 220, 125, "Readers & agents", "English + Chinese", size=21)
    s.card(306, 415, 200, 125, "CloudFront", "Public + admin", PURPLE)
    s.card(544, 415, 305, 125, "ALB + ECS Fargate", "Python SSR + JSON APIs\nAuthentication + x402", ORANGE)
    s.edge([(268, 477), (306, 477)])
    s.edge([(506, 477), (544, 477)])
    s.edge([(849, 477), (1155, 477)])
    s.text(914, 439, 190, 30, "Database access", 18, MUTED)
    s.card(306, 640, 200, 125, "Private S3", "Two frontend\nbuckets", BLUE)
    s.card(544, 640, 295, 135, "Traffic pipeline", "Edge logs / S3 / SQS\nLambda aggregation", BLUE)
    s.card(910, 640, 260, 135, "Indexing Lambda", "Cache invalidation\nBilingual IndexNow", TEAL)
    s.edge([(400, 540), (400, 640)])
    s.text(308, 588, 90, 26, "Assets", 17, MUTED)
    s.edge([(470, 540), (470, 590), (690, 590), (690, 640)])
    s.edge([(839, 690), (873, 690), (873, 575), (1210, 575), (1210, 535)])
    s.edge([(990, 325), (990, 350), (1420, 350), (1420, 707), (1170, 707)])
    s.text(1183, 615, 185, 55, "After\npublication", 18, MUTED)
    s.text(48, 803, 1344, 30, "Public research stays accessible. x402 demonstrates Base Sepolia testnet USDC payments.", 18, MUTED)
    return s


def publication():
    s = Scene("publication-pipeline", "Review before every new publication",
              "Scheduled runs and manual reviews share the same publication safeguards", 760)
    steps = [
        ("01  Evidence", "Registered sources\nArchived excerpts", BLUE),
        ("02  Draft", "Generate research\nRevise as needed", PURPLE),
        ("03  Quality", "Facts + citations\nIndependent audit", TEAL),
        ("04  Deduplicate", "Full public corpus\nCore overlap", TEAL),
        ("05  English", "Full translation\nSeparate review", TEAL),
        ("06  Commit", "Recheck versions\nDatabase guards", BLUE),
    ]
    for i, (title, body, color) in enumerate(steps):
        x = 48 + i * 226
        s.card(x, 205, 210, 145, title, body, color, 21)
        if i < 5:
            s.edge([(x + 210, 278), (x + 226, 278)])
    s.card(48, 490, 600, 150, "Keep under review",
           "Insufficient evidence, duplicate content, uncertain results,\nfailed translation, or changed inputs block publication.", RED, 24)
    s.edge([(605, 350), (605, 410), (347, 410), (347, 490)])
    s.edge([(831, 350), (831, 435), (430, 435), (430, 490)])
    s.edge([(1057, 350), (1057, 460), (510, 460), (510, 490)])
    s.card(780, 490, 608, 150, "Publish both editions",
           "English: /article/{slug}     Chinese: /zh/article/{slug}\nRefresh discovery files and notify IndexNow.", TEAL, 24)
    s.edge([(1283, 350), (1283, 490)])
    s.text(48, 688, 1344, 34, "Existing published URLs remain protected. New content cannot replace or unpublish an older page.", 21, MUTED)
    return s


def payments():
    s = Scene("x402-payment-flow", "Agent access with x402",
              "Seller-side sequence • Base Sepolia (eip155:84532) • default price: 0.002 testnet USDC", 880)
    s.card(80, 155, 300, 110, "Buyer agent", "Wallet + spending budget", PURPLE, 24)
    s.card(570, 155, 300, 110, "Aperture seller", "ECS API + x402 middleware", ORANGE, 24)
    s.card(1060, 155, 300, 110, "External facilitator", "Verification + settlement", TEAL, 24)
    for x in (230, 720, 1210):
        s.edge([(x, 270), (x, 805)], dashed=True)
    arrows = [
        (230, 720, 325, "1   GET paid resource", 265),
        (720, 230, 405, "2   HTTP 402 + PAYMENT-REQUIRED", 270),
        (230, 720, 490, "3   Signed retry + PAYMENT-SIGNATURE", 265),
        (720, 1210, 575, "4   Verify and settle", 760),
        (1210, 720, 660, "5   Settlement result", 760),
        (720, 230, 755, "6   HTTP 200 + PAYMENT-RESPONSE", 265),
    ]
    for start, end, y, label, tx in arrows:
        s.text(tx, y - 34, 450, 28, label, 21, INK, True)
        s.edge([(start, y), (end, y)])
    s.text(48, 830, 1344, 30, "Failed payment does not unlock the paid response. Public HTML and the open JSON route remain accessible.", 19, MUTED)
    return s


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mxfile = ET.Element("mxfile", host="app.diagrams.net", version="26.0.16",
                       agent="Aperture documentation builder", type="device")
    for scene in (overview(), publication(), payments()):
        scene.export(mxfile)
        print(scene.slug)
    ET.indent(mxfile)
    ET.ElementTree(mxfile).write(OUT / "aperture-aws.drawio", encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
