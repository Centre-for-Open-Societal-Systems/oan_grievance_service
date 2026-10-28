# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""WeasyPrint, loaded once per worker and ready before the first export asks for it.

Importing WeasyPrint loads Pango, Cairo-free text shaping and fontTools, which takes several
seconds, and the first PDF after that primes the font machinery. Left to the first export, that
whole cost lands on one user's click. `start_warm_up` is a `before_request` hook: the first
request a worker serves, whatever it is, starts one background thread that pays it, and the
export finds the engine ready.

Nothing here runs at import time, so a process that never exports (a migrate, a console, a
test run, a worker serving other routes) never loads WeasyPrint. A site can opt out with
`grievance_pdf_prewarm: 0` in site_config.json; the first export then pays as before.

What is kept between requests is the library and one `FontConfiguration` per thread (font
discovery is the repeated setup). Pango's font map is not safe to share between threads, so each
thread builds its own. Nothing about a ticket or a user is kept: the HTML for each export is
built, rendered and dropped inside `render`.
"""

import logging
import threading
import time

import frappe

_logger = logging.getLogger(__name__)

_state_lock = threading.Lock()
_warm_up_started = False
_thread_state = threading.local()

# Prewarm is on unless a site switches it off.
CONF_PREWARM = "grievance_pdf_prewarm"


def _load():
	"""WeasyPrint's HTML and FontConfiguration. The first call per process pays the import."""
	from weasyprint import HTML
	from weasyprint.text.fonts import FontConfiguration

	return HTML, FontConfiguration


def _font_config():
	"""This thread's FontConfiguration, built on first use."""
	config = getattr(_thread_state, "font_config", None)
	if config is None:
		_, font_configuration = _load()
		config = _thread_state.font_config = font_configuration()
	return config


def _refuse_fetch(url, *args, **kwargs):
	raise ValueError("External resources are not loaded in exports.")


def render(html: str) -> bytes:
	"""A PDF of `html`.

	Every value in the page is escaped and the page has no links or images, so there is nothing
	to fetch; refusing fetches outright keeps it that way.
	"""
	html_class, _ = _load()
	return html_class(string=html, url_fetcher=_refuse_fetch).write_pdf(font_config=_font_config())


def start_warm_up():
	"""`before_request` hook: start the warm-up once per process, without holding the request.

	It must never fail a request, so every problem is swallowed here.
	"""
	global _warm_up_started
	if _warm_up_started:
		return
	try:
		if not frappe.conf.get(CONF_PREWARM, 1):
			return
		with _state_lock:
			if _warm_up_started:
				return
			_warm_up_started = True
		threading.Thread(target=warm_up, name="grievance-pdf-warm-up", daemon=True).start()
	except Exception:
		_logger.warning("Could not start the PDF warm-up", exc_info=True)


def warm_up() -> bool:
	"""Load WeasyPrint and render a one-line page, so the first real export is a warm one.

	The page uses the export's own stylesheet, so the fonts it asks for (Latin, the ballot boxes
	and Ethiopic) are found and opened here. Returns whether the engine is ready. A server
	without Pango or without fonts logs why and returns False; an export on it then fails
	through its normal path (the `export_failed` audit row), exactly as it would have.
	"""
	from oan_grievance_service.services.ticket_render import BOX_EMPTY, BOX_TICKED, PDF_STYLE

	started = time.perf_counter()
	try:
		page = (
			f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{PDF_STYLE}</style></head>'
			f"<body><h1>Grievance</h1><p>{BOX_EMPTY} {BOX_TICKED} ሀስ</p></body></html>"
		)
		render(page)
	except Exception as exc:
		_logger.warning(
			"PDF engine is not ready (%s: %s); PDF exports will fail until WeasyPrint's system "
			"libraries and fonts are installed.",
			type(exc).__name__,
			exc,
		)
		return False
	_logger.info("PDF engine ready in %.0f ms", (time.perf_counter() - started) * 1000)
	return True
