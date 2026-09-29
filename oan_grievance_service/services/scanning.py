# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Attachment scanning and validation.

WHY THIS EXISTS
---------------
A grievance portal accepts arbitrary files from the public internet and then
asks government staff to open them. Without a scanner in front of that, the
system is a malware delivery channel with an official letterhead.

THREE CHECKS, IN ORDER
----------------------
1. **Type, from the content.** Frappe's own File doctype derives `content_type`
   from `mimetypes.guess_type(file_name)` (file.py:735), which reads only the
   extension. A payload renamed from `.exe` to `.jpg` is reported by Frappe as
   `image/jpeg`. Demonstrated:

       disguised.jpg   sniffed=application/x-msdownload   by-extension=image/jpeg

   So the type is read from the leading bytes instead, and the extension is
   checked only for agreement with what the bytes say.

2. **Location metadata, stripped.** A phone photo carries GPS coordinates in its
   EXIF block. FSD 9.2 lets a submitter ask for anonymity -- and a geotagged
   photograph of their own plot defeats that completely, whatever the database
   says about their name. Coordinates are removed before the file is stored.

3. **Malware, by an external scanner.** ClamAV over its daemon socket.

WHY CLAMAV RATHER THAN A CLOUD SCANNER
--------------------------------------
The alternative is a hosted API -- VirusTotal or a cloud provider's scanner --
which means uploading the file itself to a third party outside Ethiopia.
Grievance attachments are evidence submitted by farmers: receipts with names on
them, photographs of their land, voice recordings of their complaint. Sending
that abroad to decide whether it is a virus trades one risk for a worse one,
and it is not a trade a public grievance system gets to make quietly.

ClamAV is self-hosted, its signature database updates over the network without
the files leaving, and it runs as a sidecar container alongside MariaDB and
Redis. Configure it with `grievance_clamav_host` and `grievance_clamav_port` in
site config.

FAIL CLOSED
-----------
With no scanner reachable, a file stays `Pending`, never `Clean`, and is asked
about again on the next sweep. Nothing is served to an officer until something
has actually looked at it. An outage makes attachments unavailable; it does not
make them trusted.
"""

import hashlib
import socket
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import now_datetime

from oan_grievance_service.grievance_management.doctype.grievance_attachment.grievance_attachment import (
	ALLOWED_MIME_TYPES,
	EXTENSION_FOR_MIME,
	MAX_SIZE_BYTES,
)

CLAMAV_DEFAULT_PORT = 3310
CLAMAV_TIMEOUT_SECONDS = 30
CLAMAV_CHUNK = 8192

SCAN_PENDING = "Pending"
SCAN_CLEAN = "Clean"
SCAN_INFECTED = "Infected"
SCAN_FAILED = "Failed"

# scan_bytes' answer when the scanner could not be asked at all: unreachable, timed
# out, or not configured. Never stored as a status. The row stays Pending, which
# withholds the object just as Failed did, and the next sweep tries again.
SCAN_RETRY = "retry"

# One hourly sweep per attempt: a day of outage before a row is given up on.
MAX_SCAN_ATTEMPTS = 24

# EXIF tag 34853 is the GPS block. Pillow exposes it by number rather than name.
EXIF_GPS_TAG = 34853


# ---------------------------------------------------------------------------
# 1. Type, read from the content
# ---------------------------------------------------------------------------


def sniff_mime(content: bytes) -> str | None:
	"""The MIME type the leading bytes actually describe, or None if unrecognised.

	`filetype` ships with Frappe and reads magic numbers in pure Python, so this
	needs no subprocess and no libmagic build.
	"""
	import filetype

	kind = filetype.guess(content)
	return kind.mime if kind else None


def sha256_of(content: bytes) -> str:
	"""A tamper-evident digest of the stored bytes.

	Core's File.content_hash is MD5 and is marked usedforsecurity=False -- it exists
	to spot a duplicate upload, not to prove a file is the one that was submitted.
	Grievance evidence may later be what a decision rested on, so it gets a real
	digest, taken after the EXIF strip so it matches what is actually on disk.
	"""
	return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
	"""Strongly-typed, immutable record of an upload validated against policy."""

	file_name: str
	mime_type: str
	size_bytes: int


def validate_upload(file_name: str, content: bytes) -> ValidatedUpload:
	"""Check one upload against the evidence policy. Returns the validated metadata.

	Raises rather than returning a verdict, because every caller here wants the
	upload refused rather than recorded as suspect.
	"""
	size = len(content)
	if size > MAX_SIZE_BYTES:
		frappe.throw(
			_("{0} is {1} MB. The limit is {2} MB.").format(
				frappe.bold(file_name),
				round(size / (1024 * 1024), 1),
				MAX_SIZE_BYTES // (1024 * 1024),
			),
			title=_("File Too Large"),
		)

	mime = sniff_mime(content)
	if mime is None:
		frappe.throw(
			_("{0} is not a file type we recognise. Allowed: JPG, PNG, PDF and MP3.").format(
				frappe.bold(file_name)
			),
			title=_("Unrecognised File"),
		)

	if mime not in ALLOWED_MIME_TYPES:
		frappe.throw(
			_("{0} files are not accepted. Allowed: JPG, PNG, PDF and MP3.").format(mime),
			title=_("Unsupported File Type"),
		)

	lowered = (file_name or "").lower()
	if lowered and not lowered.endswith(EXTENSION_FOR_MIME[mime]):
		frappe.throw(
			_("{0} does not match its contents, which are {1}.").format(frappe.bold(file_name), mime),
			title=_("Extension Does Not Match Content"),
		)

	if mime == "application/pdf":
		_assert_pdf_parses(file_name, content)

	return ValidatedUpload(
		file_name=file_name,
		mime_type=mime,
		size_bytes=size,
	)


def _assert_pdf_parses(file_name: str, content: bytes) -> None:
	"""Refuse a PDF that pypdf cannot open, before core gets to it.

	Core's File.check_content runs pypdf over every PDF to look for embedded
	JavaScript and lets a parse failure escape as an uncaught exception. Found
	with a %PDF header and no cross-reference table: the upload came back as an
	INTERNAL_ERROR instead of a rejection. A file the reader cannot open is not
	evidence anyone can view, so it is refused here with a reason.
	"""
	from io import BytesIO

	from pypdf import PdfReader
	from pypdf.errors import PyPdfError

	try:
		PdfReader(BytesIO(content))
	except (PyPdfError, ValueError, OSError):
		frappe.throw(
			_("{0} is not a readable PDF.").format(frappe.bold(file_name)),
			title=_("Corrupt PDF"),
		)


# ---------------------------------------------------------------------------
# 2. Location metadata, stripped
# ---------------------------------------------------------------------------


def strip_location_metadata(content: bytes, mime: str) -> bytes:
	"""Remove EXIF from an image, GPS coordinates included.

	Only images carry this. A submitter who asked for anonymity and then attached
	a photograph of their own field has told anyone with the file exactly where
	they are, and no amount of masking in the database undoes that.

	Re-encoding drops every EXIF block rather than only the GPS tag, which is the
	safer default: camera serial numbers and owner names live there too.
	"""
	if mime not in ("image/jpeg", "image/png"):
		return content

	import io

	from PIL import Image

	try:
		source = Image.open(io.BytesIO(content))
		clean = Image.new(source.mode, source.size)
		# paste() copies in C. putdata(list(getdata())) built a Python list of every
		# pixel first, so a 12-megapixel photo -- well inside the 10 MB ceiling --
		# cost hundreds of megabytes before a single byte was written.
		clean.paste(source)

		out = io.BytesIO()
		clean.save(out, format=source.format)
		return out.getvalue()
	except Exception:
		# A file Pillow cannot parse is not one we should be re-encoding. Leave it
		# for the malware scan to judge rather than silently passing it through
		# half-processed.
		frappe.log_error(title="Attachment metadata strip failed")
		return content


def has_location_metadata(content: bytes) -> bool:
	"""Whether an image still carries GPS coordinates. Used by the tests."""
	import io

	from PIL import Image

	try:
		exif = Image.open(io.BytesIO(content)).getexif()
	except Exception:
		return False

	return EXIF_GPS_TAG in exif


# ---------------------------------------------------------------------------
# 3. Malware, by ClamAV
# ---------------------------------------------------------------------------


def clamav_target() -> tuple[str, int] | None:
	host = frappe.conf.get("grievance_clamav_host")
	if not host:
		return None
	return host, int(frappe.conf.get("grievance_clamav_port") or CLAMAV_DEFAULT_PORT)


def scan_bytes(content: bytes) -> tuple[str, str]:
	"""Hand the content to clamd and return (status, detail).

	Uses INSTREAM so nothing is written to a path the scanner has to share. The
	wire format is clamd's own: a length-prefixed chunk sequence terminated by a
	zero length.
	"""
	target = clamav_target()
	if not target:
		return SCAN_RETRY, "No scanner configured (grievance_clamav_host is unset)."

	if isinstance(content, str):
		# Never let a text-decoded object crash the send; scan its bytes.
		content = content.encode("utf-8")

	host, port = target
	try:
		with socket.create_connection((host, port), timeout=CLAMAV_TIMEOUT_SECONDS) as sock:
			sock.sendall(b"zINSTREAM\0")
			for start in range(0, len(content), CLAMAV_CHUNK):
				chunk = content[start : start + CLAMAV_CHUNK]
				sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
			sock.sendall((0).to_bytes(4, "big"))

			# The z-prefixed command asks clamd to NUL-terminate its reply, and
			# str.strip() leaves NUL alone: a reply of stream: OK plus NUL must still read as OK.
			reply = sock.recv(4096).decode("utf-8", "replace").rstrip(chr(0)).strip()
	except OSError as exc:
		return SCAN_RETRY, f"Scanner unreachable: {exc}"

	if reply.endswith("OK"):
		return SCAN_CLEAN, reply
	if "FOUND" in reply:
		return SCAN_INFECTED, reply
	return SCAN_FAILED, reply or "Scanner returned nothing."


def scanner_available() -> bool:
	"""Whether clamd answers a PING right now.

	The sweep asks once before a batch. An outage should not cost every Pending
	row one of its retries; the rows simply wait for the next sweep.
	"""
	target = clamav_target()
	if not target:
		return False
	try:
		with socket.create_connection(target, timeout=CLAMAV_TIMEOUT_SECONDS) as sock:
			sock.sendall(b"zPING\0")
			reply = sock.recv(64).decode("utf-8", "replace").rstrip(chr(0)).strip()
	except OSError:
		return False
	return reply == "PONG"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def enqueue_scan_attachment(name: str) -> None:
	"""Asynchronously enqueue malware scan for an attachment."""
	try:
		frappe.enqueue(
			"oan_grievance_service.services.scanning.scan_attachment",
			queue="short",
			name=name,
			enqueue_after_commit=True,
			is_async=True,
			# The upload enqueues a scan and the sweep may enqueue the same row
			# before it runs; one job per row is enough.
			job_id=f"scan-attachment-{name}",
			deduplicate=True,
		)
	except Exception:
		frappe.log_error(title=f"Failed to enqueue scan for attachment: {name}")


def enqueue_scan_attachments(names: list[str]) -> None:
	"""Asynchronously enqueue malware scans for multiple attachments."""
	for name in names:
		enqueue_scan_attachment(name)


def scan_attachment(name: str) -> str:
	"""Scan one Grievance Attachment and record the verdict.

	An infected file loses its object and keeps its row: the case still needs to
	show that something was submitted and what happened to it.

	No row lock is held while clamd works. The upload enqueues a scan and the
	hourly sweep picks up whatever is still Pending, so two scanners can hold one
	row; a lock across a call that can wait CLAMAV_TIMEOUT_SECONDS made a delete
	during a scan hang. Instead the verdict is written with a compare-and-set on
	`scan_status = Pending`: whichever writer settles the row first wins, and the
	other drops its verdict rather than overwrite it.
	"""
	row = frappe.db.get_value(
		"Grievance Attachment",
		name,
		["scan_status", "file", "file_url", "scan_attempts", "checksum_sha256"],
		as_dict=True,
	)
	if not row:
		return SCAN_FAILED
	if row.scan_status != SCAN_PENDING:
		return row.scan_status

	file_name = row.file or _file_name_for(row.file_url)

	# The same bytes already found infected on another row need no second scan,
	# and must not be served meanwhile.
	twin = _infected_twin(row.checksum_sha256, exclude=name)
	if twin:
		status, detail = SCAN_INFECTED, f"Identical to {twin}, which was found infected."
	else:
		content = read_object(file_name)
		if content is None:
			return _settle(name, SCAN_FAILED, "File object could not be read.")
		status, detail = scan_bytes(content)

	if status == SCAN_RETRY:
		return _defer(name, row.scan_attempts or 0, detail)

	# A twin condemned while this scan was in flight condemns this row too; a
	# Clean written now would leave the same bytes servable here.
	if status == SCAN_CLEAN:
		twin = _infected_twin(row.checksum_sha256, exclude=name)
		if twin:
			status, detail = SCAN_INFECTED, f"Identical to {twin}, which was found infected."

	settled = _settle(name, status, detail)
	if status == SCAN_INFECTED and settled == SCAN_INFECTED:
		_discard_infected(name, file_name, row.checksum_sha256)
	return settled


def scan_pending(limit: int = 50) -> int:
	"""Drain the scan queue. Wired to the scheduler.

	Skipped whole while the scanner is down, so an outage costs no row a retry.
	"""
	if not scanner_available():
		# One line in the Error Log per skipped sweep, so an outage is visible
		# without charging every waiting row a retry.
		waiting = frappe.db.count("Grievance Attachment", {"scan_status": SCAN_PENDING})
		if waiting:
			frappe.log_error(
				title="Attachment scan sweep skipped: scanner unreachable",
				message=f"{waiting} attachment(s) are waiting for a scan. No retry was charged.",
			)
		return 0

	pending = frappe.get_all(
		"Grievance Attachment",
		filters={"scan_status": SCAN_PENDING},
		pluck="name",
		limit_page_length=limit,
		order_by="creation",
	)
	for name in pending:
		try:
			scan_attachment(name)
		except Exception:
			frappe.log_error(title=f"Attachment scan failed: {name}")
	return len(pending)


def _settle(name: str, status: str, detail: str) -> str:
	"""Record a verdict, and say what the row holds now.

	Clean and Failed only fill a row that is still Pending: if another scanner
	settled it first, or a delete removed it, the verdict is dropped and the
	row's actual status (or Failed for a vanished row) is returned. Infected
	outranks Clean. The sweep calls scan_attachment directly, so two scanners can
	be inside clamd for one row at once; if the Clean lands first, the malware
	this call found must still condemn the row and discard the object.
	"""
	overwrites = [SCAN_PENDING, SCAN_CLEAN] if status == SCAN_INFECTED else [SCAN_PENDING]
	frappe.db.set_value(
		"Grievance Attachment",
		{"name": name, "scan_status": ["in", overwrites]},
		{"scan_status": status, "scan_detail": detail[:500], "scanned_at": now_datetime()},
		update_modified=False,
	)
	return frappe.db.get_value("Grievance Attachment", name, "scan_status") or SCAN_FAILED


def _defer(name: str, attempts: int, detail: str) -> str:
	"""Keep a row Pending after the scanner could not be asked, counting the attempt.

	A transient failure (clamd down, timed out, not configured) used to be
	recorded as Failed, which nothing ever revisited: one scanner outage left
	every upload of that window unviewable for good. Pending withholds the object
	just the same, and the sweep comes back to it. After MAX_SCAN_ATTEMPTS the row
	is given up on as Failed, loudly, so someone looks at the scanner.
	"""
	attempts += 1
	if attempts >= MAX_SCAN_ATTEMPTS:
		frappe.log_error(
			title=f"Attachment scan gave up: {name}",
			message=f"{attempts} attempts, last: {detail}",
		)
		return _settle(name, SCAN_FAILED, f"Scanner unavailable for {attempts} attempts. Last: {detail}")

	frappe.db.set_value(
		"Grievance Attachment",
		{"name": name, "scan_status": SCAN_PENDING},
		{"scan_attempts": attempts, "scan_detail": f"Deferred ({attempts}): {detail}"[:500]},
		update_modified=False,
	)
	# The row may have settled or vanished meanwhile; say what it holds, as _settle does.
	return frappe.db.get_value("Grievance Attachment", name, "scan_status") or SCAN_FAILED


def _infected_twin(checksum: str | None, exclude: str) -> str | None:
	if not checksum:
		return None
	return frappe.db.get_value(
		"Grievance Attachment",
		{"checksum_sha256": checksum, "scan_status": SCAN_INFECTED, "name": ["!=", exclude]},
		"name",
	)


def _discard_infected(name: str, file_name: str | None, checksum: str | None) -> None:
	"""Remove the infected object, and every other attachment's copy of the same bytes.

	Core's File keeps one object per content hash and only deletes it from disk
	once no File row shares the hash. Deleting this row's File alone would leave
	the bytes on disk behind any identical upload, served under that row's Clean
	verdict. So every attachment with the same SHA-256 is marked Infected and
	loses its File too. A sharing File that belongs to some other doctype cannot
	be removed from here; it is reported instead.
	"""
	content_hash = frappe.db.get_value("File", file_name, "content_hash") if file_name else None

	_drop_object(name, file_name)
	if not checksum:
		return

	twins = frappe.get_all(
		"Grievance Attachment",
		filters={"checksum_sha256": checksum, "name": ["!=", name], "scan_status": ["!=", SCAN_INFECTED]},
		fields=["name", "file", "file_url"],
	)
	for twin in twins:
		twin_file = twin.file or _file_name_for(twin.file_url)
		frappe.db.set_value(
			"Grievance Attachment",
			twin.name,
			{
				"scan_status": SCAN_INFECTED,
				"scan_detail": f"Identical to {name}, which was found infected.",
				"scanned_at": now_datetime(),
			},
			update_modified=False,
		)
		_drop_object(twin.name, twin_file)

	if content_hash:
		sharers = frappe.get_all(
			"File",
			filters={"content_hash": content_hash},
			fields=["name", "attached_to_doctype", "attached_to_name"],
		)
		if sharers:
			frappe.log_error(
				title=f"Infected bytes still on disk: {name}",
				message="These File records share the infected object's content hash and were not "
				"removed because they belong to another record:\n"
				+ "\n".join(f"{f.name} -> {f.attached_to_doctype} {f.attached_to_name}" for f in sharers),
			)


def _drop_object(attachment_name: str, file_name: str | None) -> None:
	"""Delete an attachment's File and unlink it, keeping the row and its trail."""
	if file_name and frappe.db.exists("File", file_name):
		frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)
	frappe.db.set_value("Grievance Attachment", attachment_name, "file", None, update_modified=False)


def _record(attachment, status: str, detail: str) -> None:
	attachment.db_set(
		{"scan_status": status, "scan_detail": detail[:500], "scanned_at": now_datetime()},
		update_modified=False,
	)


def read_object(file_name: str | None) -> bytes | None:
	"""The stored bytes behind a File, or None if there is no object to read.

	Takes the File name that `Grievance Attachment.file` links to. A file URL is
	still accepted for rows that predate the link, and resolved the old way.

	Used by the scanner and by the view endpoint, so both serve the same bytes
	the checksum was taken over.
	"""
	name = _file_name_for(file_name)
	if not name:
		return None
	try:
		file_doc = frappe.get_doc("File", name)
	except Exception:
		return None

	# Core's File.get_content() tries a list of text encodings and hands back str
	# for anything that decodes, so an all-ASCII upload came back as text and the
	# INSTREAM send failed on it -- which left the file Pending forever, never
	# scanned. An empty encodings list skips that step and returns the stored
	# bytes, while still going through core's own file-path validation.
	try:
		content = file_doc.get_content(encodings=[])
	except Exception:
		return None
	return content.encode("utf-8") if isinstance(content, str) else content


def _file_name_for(value: str | None) -> str | None:
	"""A File name from either a File name or, for rows without the link, a file URL."""
	if not value:
		return None
	if value.startswith("/"):
		return frappe.db.get_value("File", {"file_url": value}, "name")
	return value if frappe.db.exists("File", value) else None
