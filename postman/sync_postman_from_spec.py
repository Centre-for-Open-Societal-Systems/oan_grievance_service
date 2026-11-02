#!/usr/bin/env python3
# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Sync and validate Postman Collection against OpenAPI 3.1 specification.

Non-destructively scans openapi/openapi_v1.public.yaml, detects endpoints missing
from postman/oan_grievance_rest_collection.json, and scaffolds them with
proper URLs, headers, sample request bodies, and test assertions without
overwriting existing custom tests or environment variable flows.

Usage:
    # Audit / check mode for CI (exits 1 if endpoints missing):
    python postman/sync_postman_from_spec.py --check

    # Preview additions without modifying file:
    python postman/sync_postman_from_spec.py --dry-run

    # Sync missing endpoints into collection:
    python postman/sync_postman_from_spec.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
	import yaml
except ImportError:
	sys.exit("Error: PyYAML is required. Run 'pip install pyyaml'.")

# Default paths relative to app root
DEFAULT_SPEC = Path("openapi/openapi_v1.public.yaml")
DEFAULT_COLLECTION = Path("postman/oan_grievance_rest_collection.json")

# Endpoints maintained in dedicated sibling collections
EXCLUDED_TAGS = {"Dashboard Charts"}


def to_path_pattern(path_or_url: str | list[str]) -> str:
	"""Convert path or raw URL to a normalized pattern replacing any variable segment with '{}'."""
	if isinstance(path_or_url, list):
		parts = [p.strip() for p in path_or_url if p.strip()]
	else:
		raw = re.sub(r"^\{\{base_url\}\}", "", str(path_or_url).strip())
		parts = raw.strip("/").split("?")[0].split("/")

	norm: list[str] = []
	for p in parts:
		if not p:
			continue
		if (
			(p.startswith("{") and p.endswith("}"))
			or (p.startswith("{{") and p.endswith("}}"))
			or p.startswith(":")
		):
			norm.append("{}")
		else:
			norm.append(p)
	if norm and norm[0] == "api":
		norm = norm[1:]
	return "/".join(norm)


def openapi_path_to_postman(path: str) -> tuple[str, list[str]]:
	"""Convert OpenAPI path to Postman raw URL and path segments with {{variables}}."""
	parts = [p for p in path.strip("/").split("/") if p]
	if parts and parts[0] == "api":
		parts = parts[1:]
	postman_parts: list[str] = []
	for p in parts:
		if p.startswith("{") and p.endswith("}"):
			var = p[1:-1]
			postman_parts.append(f"{{{{{var}}}}}")
		else:
			postman_parts.append(p)
	raw = "{{base_url}}/" + "/".join(postman_parts)
	return raw, postman_parts


def extract_postman_endpoints(items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
	"""Recursively extract all (METHOD, path_pattern) from Postman items."""
	endpoints: dict[tuple[str, str], dict[str, Any]] = {}

	def _walk(nodes: list[dict[str, Any]]) -> None:
		for node in nodes:
			if "item" in node and isinstance(node["item"], list):
				_walk(node["item"])
			elif "request" in node:
				req = node["request"]
				method = req.get("method", "GET").upper()
				url_obj = req.get("url", {})
				if isinstance(url_obj, dict):
					path_parts = url_obj.get("path", [])
					pattern = (
						to_path_pattern(path_parts) if path_parts else to_path_pattern(url_obj.get("raw", ""))
					)
				else:
					pattern = to_path_pattern(url_obj)
				endpoints[(method, pattern)] = node

	_walk(items)
	return endpoints


def fix_mismatched_raw_urls(items: list[dict[str, Any]]) -> int:
	"""Fix existing items where url.path was updated but url.raw retained an old URL."""
	fixed = 0

	def _walk(nodes: list[dict[str, Any]]) -> None:
		nonlocal fixed
		for node in nodes:
			if "item" in node and isinstance(node["item"], list):
				_walk(node["item"])
			elif "request" in node:
				req = node["request"]
				url_obj = req.get("url", {})
				if isinstance(url_obj, dict) and "path" in url_obj and isinstance(url_obj["path"], list):
					expected_raw = "{{base_url}}/" + "/".join(url_obj["path"])
					current_raw = url_obj.get("raw", "")
					current_clean = current_raw.split("?")[0].rstrip("/")
					if expected_raw != current_clean and not current_clean.endswith(
						"/" + "/".join(url_obj["path"])
					):
						query_suffix = ("?" + current_raw.split("?", 1)[1]) if "?" in current_raw else ""
						url_obj["raw"] = expected_raw + query_suffix
						fixed += 1

	_walk(items)
	return fixed


def build_sample_body(schema: dict[str, Any], components: dict[str, Any], depth: int = 0) -> Any:
	"""Generate an illustrative JSON sample body from OpenAPI schema."""
	if not schema or depth > 5:
		return {}

	# Resolve $ref
	if "$ref" in schema:
		ref = schema["$ref"].replace("#/components/schemas/", "")
		return build_sample_body(components.get("schemas", {}).get(ref, {}), components, depth + 1)

	stype = schema.get("type", "object")
	if stype == "object":
		props = schema.get("properties", {})
		example: dict[str, Any] = {}
		required = set(schema.get("required", []))
		for k, v in props.items():
			# Prefer required fields, or key informative fields
			if "$ref" in v:
				ref = v["$ref"].replace("#/components/schemas/", "")
				resolved = components.get("schemas", {}).get(ref, {})
				example[k] = resolved.get("example", build_sample_body(resolved, components, depth + 1))
			elif "example" in v:
				example[k] = v["example"]
			elif v.get("enum"):
				example[k] = v["enum"][0]
			elif "default" in v and v["default"] is not None:
				example[k] = v["default"]
			elif v.get("type") == "string":
				if k in {"email"}:
					example[k] = "user@example.com"
				elif k in {"phone", "mobile"}:
					example[k] = "+251911000000"
				elif k in {"reason"}:
					example[k] = "Standard processing"
				elif k in {"body"}:
					example[k] = "Response body content."
				elif k in {"title"}:
					example[k] = "Sample Title"
				else:
					example[k] = "Sample value"
			elif v.get("type") == "integer":
				example[k] = 5 if k == "rating" else 1
			elif v.get("type") == "boolean":
				example[k] = True
			elif v.get("type") == "array":
				example[k] = []
			elif k in required:
				example[k] = "required"
		return example
	elif stype == "array":
		return []
	return schema.get("example", None)


def scaffold_request(
	method: str,
	path: str,
	operation: dict[str, Any],
	components: dict[str, Any],
) -> dict[str, Any]:
	"""Create a new scaffolded Postman request item with test assertions."""
	summary = operation.get("summary", f"{method} {path}")
	description = operation.get("description", summary)
	raw_url, path_parts = openapi_path_to_postman(path)

	# Query parameters
	query_params: list[dict[str, Any]] = []
	for p in operation.get("parameters", []):
		if p.get("in") == "query":
			query_params.append(
				{
					"key": p["name"],
					"value": str(p.get("schema", {}).get("default", "")),
					"description": p.get("description", ""),
					"disabled": not p.get("required", False),
				}
			)

	request_dict: dict[str, Any] = {
		"method": method,
		"header": [],
		"url": {
			"raw": raw_url,
			"host": ["{{base_url}}"],
			"path": path_parts,
		},
		"description": description,
	}

	if query_params:
		request_dict["url"]["query"] = query_params

	# Request body
	req_body = operation.get("requestBody", {})
	content = req_body.get("content", {})
	if "application/json" in content:
		request_dict["header"].append({"key": "Content-Type", "value": "application/json"})
		body_schema = content["application/json"].get("schema", {})
		sample = build_sample_body(body_schema, components)
		request_dict["body"] = {
			"mode": "raw",
			"raw": json.dumps(sample, indent=2),
		}

	# Test script
	expected_status = 201 if method == "POST" and "201" in operation.get("responses", {}) else 200
	test_script = [
		f'pm.test("Status code is {expected_status}", function () {{',
		f"    pm.response.to.have.status({expected_status});",
		"});",
		"",
		'pm.test("Response contains success envelope", function () {',
		"    var jsonData = pm.response.json();",
		"    pm.expect(jsonData).to.have.property('status');",
		"    pm.expect(jsonData.status).to.eql('success');",
		"});",
	]

	return {
		"name": summary,
		"event": [
			{
				"listen": "test",
				"script": {
					"exec": test_script,
					"type": "text/javascript",
				},
			}
		],
		"request": request_dict,
		"response": [],
	}


def determine_folder(path: str, operation: dict[str, Any]) -> str:
	"""Map an endpoint to an existing or dedicated folder name."""
	norm = re.sub(r"^/api/", "/", path)
	if not norm.startswith("/"):
		norm = "/" + norm

	if norm.startswith("/v1/response-templates"):
		return "Response Templates"
	if norm.startswith("/v1/change-requests"):
		return "Change Requests"
	if norm.startswith("/v1/attachments"):
		return "Attachments"
	if norm.startswith("/v1/submitters"):
		return "Profiles"
	if norm.startswith("/v1/category-assignments"):
		return "Category Assignments"
	if norm.startswith("/v1/sla"):
		return "SLA Settings"
	if norm.startswith("/v1/officers"):
		return "Officers"
	if norm.startswith("/v1/notification-templates"):
		return "Notification Templates"
	if norm.startswith("/v1/administrative-areas"):
		return "Public Reference Data"
	if norm.startswith("/v1/grievances") and "feedback" in norm:
		return "Grievances Core"

	tags = operation.get("tags", [])
	if tags:
		return tags[0]
	return "General"


def _log(msg: str = "") -> None:
	sys.stdout.write(f"{msg}\n")


def sync_collection(
	spec_path: Path,
	collection_path: Path,
	dry_run: bool = False,
	check_only: bool = False,
) -> int:
	"""Compare and sync OpenAPI spec with Postman collection."""
	if not spec_path.exists():
		sys.exit(f"Spec file not found: {spec_path}")
	if not collection_path.exists():
		sys.exit(f"Collection file not found: {collection_path}")

	with open(spec_path, encoding="utf-8") as f:  # nosemgrep: frappe-security-file-traversal
		spec = yaml.safe_load(f)

	with open(collection_path, encoding="utf-8") as f:  # nosemgrep: frappe-security-file-traversal
		collection = json.load(f)

	# Clean up any historical out-of-sync raw URLs
	raw_fixed = fix_mismatched_raw_urls(collection.get("item", []))
	if raw_fixed > 0:
		_log(f"Repaired {raw_fixed} requests with out-of-sync raw URLs.")

	components = spec.get("components", {})
	postman_endpoints = extract_postman_endpoints(collection.get("item", []))

	missing_endpoints: list[tuple[str, str, dict[str, Any]]] = []

	# Traverse OpenAPI paths
	for path, methods in spec.get("paths", {}).items():
		for method_name, operation in methods.items():
			method = method_name.upper()
			if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
				continue

			# Skip excluded tags (e.g. Dashboard Charts maintained in separate file)
			tags = set(operation.get("tags", []))
			if tags.intersection(EXCLUDED_TAGS):
				continue

			pattern = to_path_pattern(path)
			if (method, pattern) not in postman_endpoints:
				missing_endpoints.append((method, path, operation))

	if check_only:
		if missing_endpoints:
			_log(f"FAILED: Found {len(missing_endpoints)} endpoints missing from {collection_path}:")
			for m, p, op in missing_endpoints:
				_log(f"  - {m:6} {p:50} ({op.get('summary', '')})")
			return 1
		_log(f"SUCCESS: Postman collection is in full sync with {spec_path}!")
		return 0

	_log(f"Detected {len(missing_endpoints)} missing endpoints in Postman collection.")

	if not missing_endpoints and raw_fixed == 0:
		_log("Postman collection is already completely up to date.")
		return 0

	# Map folders
	folder_map: dict[str, dict[str, Any]] = {}
	for top_item in collection.get("item", []):
		if "item" in top_item:
			folder_map[top_item["name"]] = top_item

	added_count = 0
	for method, path, operation in missing_endpoints:
		folder_name = determine_folder(path, operation)
		new_request = scaffold_request(method, path, operation, components)

		_log(f"  + [{folder_name}] {method:6} {path} -> '{new_request['name']}'")

		if not dry_run:
			if folder_name not in folder_map:
				new_folder = {
					"name": folder_name,
					"item": [],
				}
				collection.setdefault("item", []).append(new_folder)
				folder_map[folder_name] = new_folder

			folder_map[folder_name]["item"].append(new_request)
			added_count += 1

	if dry_run:
		_log(f"\n[DRY RUN] Would add {len(missing_endpoints)} requests across folders. No files modified.")
	else:
		with open(collection_path, "w", encoding="utf-8") as f:  # nosemgrep: frappe-security-file-traversal
			json.dump(collection, f, indent=2, ensure_ascii=False)
			f.write("\n")
		_log(f"\nSuccessfully synced {added_count} endpoints to {collection_path}.")

	return 0


def main() -> None:
	parser = argparse.ArgumentParser(description="Sync Postman Collection from OpenAPI spec")
	parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC, help="Path to openapi_v1.public.yaml")
	parser.add_argument(
		"--collection", type=Path, default=DEFAULT_COLLECTION, help="Path to Postman JSON collection"
	)
	parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying file")
	parser.add_argument("--check", action="store_true", help="Audit mode: exit 1 if endpoints missing")

	args = parser.parse_args()
	sys.exit(sync_collection(args.spec, args.collection, dry_run=args.dry_run, check_only=args.check))


if __name__ == "__main__":
	main()
