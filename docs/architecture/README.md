# English AWS diagrams

[Editable draw.io source](aperture-aws.drawio) · [System explanation](../architecture.md)

The overview and payment sequence follow the [original Chinese diagrams](https://github.com/milan9527/geo/blob/a2de0e830f363177b0138409d93aebc512de4237/docs/architecture/aperture-aws.drawio)' layout, AWS Cloud boundaries, native AWS4 icons and service-category colors. English labels reflect the current implementation: reviewed bilingual publication, persistent URLs, an external payment facilitator and Base Sepolia testnet USDC.

| Page | PNG | SVG |
| --- | --- | --- |
| AWS overview | [Image](aws-overview.png) | [Vector](aws-overview.svg) |
| Publication safeguards | [Image](publication-pipeline.png) | [Vector](publication-pipeline.svg) |
| x402 payment and delivery | [Image](x402-payment-flow.png) | [Vector](x402-payment-flow.svg) |

The draw.io file contains editable shapes, labels and connectors. Its sixteen native AWS icon instances cover EventBridge Scheduler, Lambda, Bedrock, CloudFront, Application Load Balancer, Fargate, Aurora, S3 and SQS. External readers, sources, facilitators, the blockchain and wallets remain outside the AWS service boundaries.

## Edit and export

Open `aperture-aws.drawio` in diagrams.net or draw.io Desktop. Enable the AWS architecture shape library when adding services. Preserve `html=0` for portable SVG text and the existing page IDs, which determine the output filenames. Adding a service beyond the nine included stencils also requires adding its definition to the pinned local subset and updating the provenance hash.

Run from the repository root:

```bash
.venv/bin/pip install -r docs/presentation/requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/build_architecture.py
.venv/bin/python scripts/build_presentation.py
```

The exporter renders the actual draw.io model with mxGraph and draw.io's AWS4 shape implementation in Chromium. SVG output keeps the native service glyph paths; PNG uses the same SVG. Export does not rewrite the draw.io source and does not require network access. Native label bounds are checked for overflow. The [export manifest](manifest.json) links the source and each output by SHA-256.

Re-render the PPTX to PDF and run artifact verification as described in the [presentation guide](../presentation/README.md). Verification rejects stale diagrams embedded in the deck.

## Rendering assets

The documentation-only [vendor directory](vendor/provenance.json) pins mxGraph 4.2.2, draw.io's AWS4 shape renderer and nine unmodified AWS4 stencil definitions. The stencil subset is extracted from the draw.io revision recorded in that file; hashes are checked before export. These assets are not included in the website or API.

Sources: [draw.io](https://github.com/jgraph/drawio), [mxGraph](https://github.com/jgraph/mxgraph), and the [AWS Architecture Icons](https://aws.amazon.com/architecture/icons/) library. The repository license for the rendering sources is included as [Apache 2.0](vendor/drawio-LICENSE.txt). AWS service names and icons identify the corresponding AWS services.
