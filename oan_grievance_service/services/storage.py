# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Attachment object storage abstraction supporting Frappe File and S3-compatible external storage."""

from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import cint
from oan_auth_service.api import operational_log as oplog

S3_URI_PREFIX = "s3://"


@dataclass(frozen=True, slots=True)
class StoredObject:
	"""Reference to a persisted attachment blob."""

	file: str | None
	file_url: str
	file_name: str


def is_s3_uri(value: str | None) -> bool:
	"""Return True when `value` points to an S3 object URI."""
	return bool(value and value.startswith(S3_URI_PREFIX))


def parse_s3_uri(uri: str) -> tuple[str, str]:
	"""Split an `s3://<bucket>/<key>` URI into `(bucket, key)`."""
	if not is_s3_uri(uri):
		raise ValueError(f"Not an S3 URI: {uri}")
	rest = uri[len(S3_URI_PREFIX) :]
	bucket, _, key = rest.partition("/")
	if not bucket or not key:
		raise ValueError(f"Malformed S3 URI: {uri}")
	return bucket, key


def store_attachment_bytes(case_name: str, file_name: str, content: bytes, mime_type: str) -> StoredObject:
	"""Persist attachment bytes to the configured backend (external S3 or Frappe File)."""
	settings = _get_settings()
	if settings and cint(settings.use_external_storage):
		return _store_in_s3(settings, case_name, file_name, content, mime_type)

	stored = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": content,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	return StoredObject(file=stored.name, file_url=stored.file_url, file_name=stored.file_name)


def link_stored_file(file_name: str | None, attachment_name: str) -> None:
	"""Attach a Frappe File record to its Grievance Attachment; no-op for S3."""
	if not file_name:
		return
	frappe.db.set_value(
		"File",
		file_name,
		{"attached_to_doctype": "Grievance Attachment", "attached_to_name": attachment_name},
		update_modified=False,
	)


def read_attachment_bytes(file_or_url: str | None) -> bytes | None:
	"""Read raw attachment bytes from S3 (s3://) or Frappe File."""
	if not file_or_url:
		return None
	if is_s3_uri(file_or_url):
		return _read_from_s3(file_or_url)
	return _read_from_frappe_file(file_or_url)


def delete_stored_object(file_name: str | None = None, file_url: str | None = None) -> None:
	"""Delete an attachment's underlying object from Frappe File or S3."""
	if file_name and frappe.db.exists("File", file_name):
		frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)
	if is_s3_uri(file_url):
		_delete_from_s3(file_url)


def _store_in_s3(settings, case_name: str, file_name: str, content: bytes, mime_type: str) -> StoredObject:
	bucket = settings.s3_bucket.strip()
	key = _build_object_key(settings.s3_key_prefix, case_name, file_name)
	client = _s3_client(settings)
	client.put_object(
		Bucket=bucket,
		Key=key,
		Body=content,
		ContentType=mime_type or "application/octet-stream",
	)
	return StoredObject(file=None, file_url=f"{S3_URI_PREFIX}{bucket}/{key}", file_name=file_name)


def _read_from_s3(uri: str) -> bytes | None:
	settings = _get_settings()
	if not settings:
		return None
	try:
		bucket, key = parse_s3_uri(uri)
		response = _s3_client(settings).get_object(Bucket=bucket, Key=key)
		return response["Body"].read()
	except Exception:
		oplog.log_error(title="oan_grievance_service: S3 attachment read failed")
		return None


def _delete_from_s3(uri: str) -> None:
	settings = _get_settings()
	if not settings:
		return
	try:
		bucket, key = parse_s3_uri(uri)
		_s3_client(settings).delete_object(Bucket=bucket, Key=key)
	except Exception:
		# A storage outage must not block deleting the case or discarding an infected file.
		oplog.log_error(title="oan_grievance_service: S3 attachment delete failed")


def _read_from_frappe_file(file_or_url: str) -> bytes | None:
	name = frappe_file_name_for(file_or_url)
	if not name:
		return None
	try:
		file_doc = frappe.get_doc("File", name)
	except Exception:
		return None

	# Empty encodings skips text decoding so binary/ASCII uploads return raw bytes.
	try:
		content = file_doc.get_content(encodings=[])
	except Exception:
		return None
	return content.encode("utf-8") if isinstance(content, str) else content


def frappe_file_name_for(value: str | None) -> str | None:
	if not value or is_s3_uri(value):
		return None
	if value.startswith("/"):
		return frappe.db.get_value("File", {"file_url": value}, "name")
	return value if frappe.db.exists("File", value) else None


def _build_object_key(prefix: str | None, case_name: str, file_name: str) -> str:
	safe_name = PurePosixPath(file_name.replace("\\", "/")).name or "attachment"
	parts = [
		p.strip("/") for p in (prefix or "", case_name, f"{uuid4().hex}-{safe_name}") if p and p.strip("/")
	]
	return "/".join(parts)


def _s3_client(settings):
	try:
		import boto3
		from botocore.config import Config
	except ImportError as exc:
		frappe.throw(
			_("boto3 is required for external S3 storage. Install it in the bench environment."),
			title=_("Storage Dependency Missing"),
		)
		raise exc

	secret = settings.get_password("s3_secret_key", raise_exception=False)
	return boto3.client(
		"s3",
		endpoint_url=settings.s3_endpoint.strip(),
		region_name=(settings.s3_region or "us-east-1").strip(),
		aws_access_key_id=settings.s3_access_key.strip(),
		aws_secret_access_key=secret,
		config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
	)


def _get_settings():
	try:
		return frappe.get_cached_doc("Grievance Attachment Settings")
	except frappe.DoesNotExistError:
		return None
