# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from oan_grievance_service.services import mds_geo_sync

_META_V5 = {
	"version": 5,
	"levels": [
		{"level": "country", "level_id": "l0", "parent_level_id": None},
		{"level": "region", "level_id": "l1", "parent_level_id": "l0"},
		{"level": "zone", "level_id": "l2", "parent_level_id": "l1"},
	],
}

_UNITS_V5 = {
	"total": 3,
	"units": [
		{
			"unit_id": "MDS-Z1",
			"name": "MDS Zone 1",
			"level": "zone",
			"parent_unit_id": "MDS-R1",
			"status": "ACTIVE",
			"valid_from": "2026-10-28T05:26:13+00:00",
			"valid_to": None,
		},
		{
			"unit_id": "MDS-ET",
			"name": "Ethiopia",
			"level": "country",
			"parent_unit_id": None,
			"status": "ACTIVE",
			"valid_from": "2026-10-28T05:26:13+00:00",
			"valid_to": None,
		},
		{
			"unit_id": "MDS-R1",
			"name": "MDS Region 1",
			"level": "region",
			"parent_unit_id": "MDS-ET",
			"status": "ACTIVE",
			"valid_from": "2026-10-28T05:26:13+00:00",
			"valid_to": None,
		},
	],
}


class TestMdsGeoSync(IntegrationTestCase):
	def test_level_order_and_leaf_levels(self):
		order = mds_geo_sync._level_order(_META_V5["levels"])
		self.assertEqual(order, {"country": 0, "region": 1, "zone": 2})
		self.assertEqual(mds_geo_sync._leaf_levels(_META_V5["levels"]), {"zone"})

	def test_sync_skips_when_version_matches(self):
		fake_settings = SimpleNamespace(mds_base_url="https://mds.example.org", mds_geo_version=5)
		with (
			patch.object(frappe, "get_doc", return_value=fake_settings),
			patch.object(mds_geo_sync, "_get_json", return_value=_META_V5),
		):
			result = mds_geo_sync.sync_from_mds(force=False)
		self.assertEqual(result, {"skipped": True, "version": 5})

	def test_sync_inserts_parents_before_children_and_updates(self):
		fake_settings = SimpleNamespace(mds_base_url="https://mds.example.org", mds_geo_version=4)

		def fake_get(url, params=None):
			if url.endswith("/public/geography"):
				return _META_V5
			return _UNITS_V5

		with (
			patch.object(frappe, "get_doc", wraps=frappe.get_doc) as real_get_doc,
			patch.object(mds_geo_sync, "_get_json", side_effect=fake_get),
		):
			# Return fake_settings only when Grievance Settings is requested.
			def get_doc_side_effect(*args, **kwargs):
				if args and args[0] == "Grievance Settings":
					return fake_settings
				return real_get_doc(*args, **kwargs)

			real_get_doc.side_effect = get_doc_side_effect
			result = mds_geo_sync.sync_from_mds()

		self.assertFalse(result["skipped"])
		self.assertEqual(result["version"], 5)
		self.assertEqual(result["created"], 3)
		zone_parent = frappe.db.get_value(
			"Grievance Administrative Area", {"code": "MDS-Z1"}, "parent_administrative_area"
		)
		region_name = frappe.db.get_value("Grievance Administrative Area", {"code": "MDS-R1"}, "name")
		self.assertEqual(zone_parent, region_name)

	def test_enqueue_sync_uses_long_background_queue(self):
		fake_settings = SimpleNamespace(mds_base_url="https://mds.example.org", mds_geo_sync_enabled=1)
		fake_job = SimpleNamespace(id="job-123")
		with (
			patch.object(frappe, "get_cached_doc", return_value=fake_settings),
			patch.object(frappe, "enqueue", return_value=fake_job) as mock_enqueue,
		):
			job_id = mds_geo_sync.enqueue_sync(force=True)
		self.assertEqual(job_id, "job-123")
		mock_enqueue.assert_called_once()
		self.assertEqual(mock_enqueue.call_args.kwargs["queue"], "long")
		self.assertTrue(mock_enqueue.call_args.kwargs["deduplicate"])
