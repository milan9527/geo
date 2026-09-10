#!/usr/bin/env python3
"""Install the public-only CloudFront routing fix without changing admin routing."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import boto3


FUNCTION_NAME = "geo-intelligence-public-routing"
SOURCE = Path(__file__).resolve().parents[1] / "infrastructure/aws/public-site-request.js"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution-id", default=os.environ.get("GEO_PUBLIC_DISTRIBUTION_ID"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.distribution_id:
        parser.error("--distribution-id or GEO_PUBLIC_DISTRIBUTION_ID is required")
    client = boto3.client("cloudfront")
    wrapper = client.get_distribution_config(Id=args.distribution_id)
    config = wrapper["DistributionConfig"]
    associations = config["DefaultCacheBehavior"].get("FunctionAssociations", {}).get("Items", [])
    # Do not silently replace an unrelated viewer-request function.
    allowed = {"geo-intelligence-spa-rewrite", FUNCTION_NAME}
    for association in associations:
        if association["EventType"] == "viewer-request":
            name = association["FunctionARN"].rsplit("/", 1)[-1]
            if name not in allowed:
                raise RuntimeError(f"Unexpected viewer-request function: {name}")
    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "distribution": args.distribution_id,
        "aliases": config.get("Aliases", {}).get("Items", []),
        "function": FUNCTION_NAME,
        "effect": "Unknown public page paths return 404; /index.html redirects to /",
    }))
    if not args.apply:
        return
    function_config = {
        "Comment": "Aperture public canonical home and real 404 responses",
        "Runtime": "cloudfront-js-2.0",
    }
    try:
        current = client.describe_function(Name=FUNCTION_NAME, Stage="DEVELOPMENT")
    except client.exceptions.NoSuchFunctionExists:
        function = client.create_function(
            Name=FUNCTION_NAME, FunctionConfig=function_config, FunctionCode=SOURCE.read_bytes()
        )
    else:
        function = client.update_function(
            Name=FUNCTION_NAME, IfMatch=current["ETag"],
            FunctionConfig=function_config, FunctionCode=SOURCE.read_bytes(),
        )
    # Validate the deployed runtime before publishing or associating it.
    for uri, status, rewritten in [
        ("/", None, "/index.html"), ("/index.html", 301, None),
        ("/missing-search-check", 404, None), ("/missing-search-check/", 404, None),
        ("/about/", 301, None), ("/BingSiteAuth.xml", None, "/BingSiteAuth.xml"),
        ("/google2fca4b1360d4ff6f.html", None, "/google2fca4b1360d4ff6f.html"),
        ("/app.js", None, "/app.js"),
    ]:
        result = client.test_function(
            Name=FUNCTION_NAME, IfMatch=function["ETag"], Stage="DEVELOPMENT",
            EventObject=json.dumps({
                "version": "1.0", "context": {"eventType": "viewer-request"},
                "viewer": {"ip": "192.0.2.1"},
                "request": {"method": "GET", "uri": uri, "querystring": {},
                            "headers": {"host": {"value": "example.com"}}, "cookies": {}},
            }).encode(),
        )["TestResult"]
        if result.get("FunctionErrorMessage"):
            raise RuntimeError(result["FunctionErrorMessage"])
        output = json.loads(result["FunctionOutput"])
        output = output.get("request", output.get("response", output))
        if output.get("statusCode") != status or (rewritten and output.get("uri") != rewritten):
            raise RuntimeError(f"CloudFront routing check failed for {uri}: {output}")
    published = client.publish_function(Name=FUNCTION_NAME, IfMatch=function["ETag"])
    arn = published["FunctionSummary"]["FunctionMetadata"]["FunctionARN"]
    # Refetch the config so concurrent changes are protected by the current ETag.
    wrapper = client.get_distribution_config(Id=args.distribution_id)
    config = wrapper["DistributionConfig"]
    associations = config["DefaultCacheBehavior"].get("FunctionAssociations", {}).get("Items", [])
    for item in associations:
        if item["EventType"] == "viewer-request" and item["FunctionARN"].rsplit("/", 1)[-1] not in allowed:
            raise RuntimeError("Viewer-request function changed during deployment")
    associations = [item for item in associations if item["EventType"] != "viewer-request"]
    associations.append({"EventType": "viewer-request", "FunctionARN": arn})
    config["DefaultCacheBehavior"]["FunctionAssociations"] = {
        "Quantity": len(associations), "Items": associations,
    }
    client.update_distribution(
        Id=args.distribution_id, IfMatch=wrapper["ETag"], DistributionConfig=config
    )
    print("Public routing published; CloudFront propagation is in progress.")


if __name__ == "__main__":
    main()
