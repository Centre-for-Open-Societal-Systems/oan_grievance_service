# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""A department response in two parts, stored as one text.

A response says what the department did and what that means for the submitter.
It is stored as a single text -- the action's `reason`, which is part of the
Grievance Status History hash chain, and a response template's `body` -- with
each part under a fixed heading. Clients send and receive the two parts as
separate fields; this module is the only place that joins and splits them, so
the headings cannot drift between writer and reader.

The headings are fixed English text, not translated: they are stored in the
record and must parse the same whatever language the reader uses. A client
shows its own translated labels from the split parts.
"""

import re

ACTION_TAKEN_HEADING = "Action Taken:"
RESOLUTION_SUMMARY_HEADING = "Resolution Summary:"

# Each heading must sit alone on its line, so the words typed inside a part
# ("...the resolution summary: see below") never split it. Case is ignored for
# text written before the headings were fixed here.
_HEADING_LINE = r"^[ \t]*{}[ \t]*\r?$"
_ACTION_TAKEN_RE = re.compile(_HEADING_LINE.format(re.escape(ACTION_TAKEN_HEADING)), re.I | re.M)
_RESOLUTION_SUMMARY_RE = re.compile(_HEADING_LINE.format(re.escape(RESOLUTION_SUMMARY_HEADING)), re.I | re.M)


def compose(action_taken: str, resolution_summary: str) -> str:
	"""Join the two parts into the stored text."""
	return (
		f"{ACTION_TAKEN_HEADING}\n{action_taken.strip()}\n\n"
		f"{RESOLUTION_SUMMARY_HEADING}\n{resolution_summary.strip()}"
	)


def split(text: str | None) -> dict | None:
	"""The two parts of a stored text, or None when it is not a two-part response.

	None covers every reason that was never written in two parts -- a rejection,
	a reopen, an older template -- so the client shows the text as it is.
	"""
	if not text:
		return None
	action_at = _ACTION_TAKEN_RE.search(text)
	if not action_at:
		return None
	summary_at = _RESOLUTION_SUMMARY_RE.search(text, action_at.end())
	if not summary_at:
		return None
	return {
		"action_taken": text[action_at.end() : summary_at.start()].strip(),
		"resolution_summary": text[summary_at.end() :].strip(),
	}
