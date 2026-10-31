# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""WeasyPrint, loaded once per worker, on the worker's first export.

Importing WeasyPrint loads Pango, text shaping and fontTools, which takes about four seconds,
and the first PDF after that primes the font machinery. That cost lands on the first export a
worker serves, and every export after it on that worker is fast. A worker that never exports
never loads WeasyPrint, so a pod boots lean and the workers serving intake, login and status
checks carry none of it. There is deliberately no warm-up at worker start: it would load the
library in every worker, at pod start, whether or not it ever exports.

Nothing here runs at import time.

What is kept between requests is the library and one `FontConfiguration` per thread (font
discovery is the repeated setup). Pango's font map is not safe to share between threads, so each
thread builds its own. Nothing about a ticket or a user is kept: the HTML for each export is
built, rendered and dropped inside `render`.
"""

import threading

_thread_state = threading.local()


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
