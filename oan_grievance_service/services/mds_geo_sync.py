# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Sync Grievance Administrative Area from OpenG2P Master Data Service (MDS).

Reads the public geography catalogue endpoints:
- GET <mds_base_url>/public/geography
- GET <mds_base_url>/public/geography/units?page=<n>&page_size=1000
"""

from contextlib import contextmanager

import frappe
import requests
from frappe import _
from frappe.utils import cint, now_datetime
from frappe.utils.nestedset import rebuild_tree
from oan_auth_service.api import operational_log as oplog

DOCTYPE = "Grievance Administrative Area"
JOB_METHOD = "oan_grievance_service.services.mds_geo_sync.sync_from_mds"
JOB_QUEUE = "long"
JOB_TIMEOUT = 900
REQUEST_TIMEOUT = (10, 30)
PAGE_SIZE = 1000

_LEVEL_MAP = {
	"country": "Country",
	"region": "Region",
	"zone": "Zone",
	"woreda": "Woreda",
	"kebele": "Kebele",
	"county": "County",
	"sub-county": "Sub-County",
	"ward": "Ward",
	"state": "State",
	"district": "District",
	"taluka": "Taluka",
	"village": "Village",
}


class SyncInProgress(frappe.ValidationError):
	http_status_code = 409


def enqueue_sync(force: bool = False, after_commit: bool = False) -> str | None:
	"""Queue an MDS geography sync on the `long` background queue and return the job ID."""
	settings = frappe.get_cached_doc("Grievance Settings")
	if not (settings.mds_base_url or "").strip():
		frappe.throw(_("MDS API Base URL is not configured in Grievance Settings."), frappe.ValidationError)
	job = frappe.enqueue(
		JOB_METHOD,
		queue=JOB_QUEUE,
		timeout=JOB_TIMEOUT,
		job_id="grievance_mds_geo_sync",
		deduplicate=True,
		enqueue_after_commit=after_commit,
		force=force,
	)
	if after_commit:
		return None
	if job is None:
		frappe.throw(_("An MDS geography sync is already running."), SyncInProgress)
	return job.id


def enqueue_scheduled_sync() -> str | None:
	"""Queue an MDS geography sync when enabled and configured in Grievance Settings."""
	settings = frappe.get_cached_doc("Grievance Settings")
	if not cint(settings.mds_geo_sync_enabled) or not (settings.mds_base_url or "").strip():
		return None
	try:
		return enqueue_sync(force=False)
	except SyncInProgress:
		return None


def sync_from_mds(force: bool = False) -> dict:
	"""Fetch the latest published geography from MDS and upsert Grievance Administrative Area."""
	settings = frappe.get_doc("Grievance Settings")
	base_url = (settings.mds_base_url or "").strip().rstrip("/")
	if not base_url:
		frappe.throw(_("MDS API Base URL is not configured in Grievance Settings."), frappe.ValidationError)

	meta = _get_json(f"{base_url}/public/geography")
	remote_version = cint(meta.get("version") or 0)
	if not force and remote_version and cint(settings.mds_geo_version) == remote_version:
		return {"skipped": True, "version": remote_version}

	level_order = _level_order(meta.get("levels") or [])
	units = _fetch_all_units(base_url, version=remote_version or None)
	if not units:
		frappe.throw(_("MDS returned no geography units; aborting sync."), frappe.ValidationError)

	units.sort(key=lambda u: (level_order.get((u.get("level") or "").lower(), 99), u.get("unit_id") or ""))
	leaf_levels = _leaf_levels(meta.get("levels") or [])

	summary = _apply_units(units, leaf_levels)
	frappe.db.set_single_value(
		"Grievance Settings",
		{"mds_geo_version": remote_version, "mds_last_synced_at": now_datetime()},
		update_modified=False,
	)
	oplog.emit("mds_geo_sync.completed", version=remote_version, **summary)
	return {"skipped": False, "version": remote_version, **summary}


def _apply_units(units: list[dict], leaf_levels: set[str]) -> dict:
	existing = {
		row.code: row
		for row in frappe.get_all(
			DOCTYPE,
			filters={"code": ["is", "set"]},
			fields=[
				"name",
				"code",
				"area_name",
				"level_name",
				"parent_administrative_area",
				"is_active",
				"is_group",
			],
		)
	}
	world_name = frappe.db.get_value(DOCTYPE, {"area_name": "World", "depth": 0}, "name")

	created, updated, unchanged = 0, 0, 0
	tree_dirty = False

	with _bulk_tree_insert():
		for unit in units:
			code = (unit.get("unit_id") or "").strip()
			if not code:
				continue
			level_raw = (unit.get("level") or "").strip().lower()
			level_name = _LEVEL_MAP.get(level_raw, "Other")
			parent_code = (unit.get("parent_unit_id") or "").strip()
			parent_name = (
				existing[parent_code].name if parent_code and parent_code in existing else world_name
			)
			is_active = 1 if (unit.get("status") or "ACTIVE").upper() == "ACTIVE" else 0
			is_group = 0 if level_raw in leaf_levels else 1
			area_name = (unit.get("name") or code).strip()
			valid_from = _iso_date(unit.get("valid_from"))
			valid_to = _iso_date(unit.get("valid_to"))

			row = existing.get(code)
			if row:
				changed = (
					row.area_name != area_name
					or row.level_name != level_name
					or row.parent_administrative_area != parent_name
					or cint(row.is_active) != is_active
					or cint(row.is_group) != is_group
				)
				if not changed:
					unchanged += 1
					continue
				if row.parent_administrative_area != parent_name:
					tree_dirty = True
				doc = frappe.get_doc(DOCTYPE, row.name)
				doc.update(
					{
						"area_name": area_name,
						"level_name": level_name,
						"parent_administrative_area": parent_name,
						"is_active": is_active,
						"is_group": is_group,
						"valid_from": valid_from,
						"valid_to": valid_to,
					}
				)
				doc.save(ignore_permissions=True)
				existing[code] = doc
				updated += 1
			else:
				doc = frappe.get_doc(
					{
						"doctype": DOCTYPE,
						"code": code,
						"area_name": area_name,
						"level_name": level_name,
						"parent_administrative_area": parent_name,
						"is_active": is_active,
						"is_group": is_group,
						"valid_from": valid_from,
						"valid_to": valid_to,
					}
				).insert(ignore_permissions=True)
				existing[code] = doc
				tree_dirty = True
				created += 1

	if tree_dirty:
		rebuild_tree(DOCTYPE)

	return {"created": created, "updated": updated, "unchanged": unchanged, "total": len(units)}


def _fetch_all_units(base_url: str, version: int | None = None) -> list[dict]:
	units: list[dict] = []
	page = 1
	while True:
		params = {"page": page, "page_size": PAGE_SIZE}
		if version:
			params["version"] = version
		payload = _get_json(f"{base_url}/public/geography/units", params=params)
		batch = payload.get("units") or []
		units.extend(batch)
		total = cint(payload.get("total") or 0)
		if not batch or len(units) >= total:
			break
		page += 1
	return units


def _get_json(url: str, params: dict | None = None) -> dict:
	try:
		response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
	except requests.RequestException as exc:
		frappe.throw(_("MDS request failed: {0}").format(type(exc).__name__), frappe.ValidationError)
	if response.status_code != 200:
		frappe.throw(
			_("MDS returned HTTP {0} for {1}.").format(response.status_code, url),
			frappe.ValidationError,
		)
	try:
		return response.json()
	except ValueError:
		frappe.throw(_("MDS did not return valid JSON."), frappe.ValidationError)


def _level_order(levels: list[dict]) -> dict[str, int]:
	by_id = {lvl.get("level_id"): lvl for lvl in levels if lvl.get("level_id")}
	depths: dict[str, int] = {}

	def depth_of(level_id: str) -> int:
		if level_id in depths:
			return depths[level_id]
		lvl = by_id.get(level_id)
		if not lvl or not lvl.get("parent_level_id"):
			depths[level_id] = 0
			return 0
		depths[level_id] = depth_of(lvl["parent_level_id"]) + 1
		return depths[level_id]

	return {
		(lvl.get("level") or "").lower(): depth_of(lvl["level_id"]) for lvl in levels if lvl.get("level_id")
	}


def _leaf_levels(levels: list[dict]) -> set[str]:
	parents = {lvl.get("parent_level_id") for lvl in levels if lvl.get("parent_level_id")}
	return {(lvl.get("level") or "").lower() for lvl in levels if lvl.get("level_id") not in parents}


def _iso_date(value: str | None) -> str | None:
	if not value:
		return None
	return str(value)[:10]


@contextmanager
def _bulk_tree_insert():
	previous = getattr(frappe.flags, "ignore_nsm", False)
	frappe.flags.ignore_nsm = True
	try:
		yield
	finally:
		frappe.flags.ignore_nsm = previous
