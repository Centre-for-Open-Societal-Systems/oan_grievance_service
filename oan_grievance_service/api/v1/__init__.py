"""Version 1 of the public API.

This package is frozen once released: additive changes only. A breaking change opens
`api/v2/` rather than editing anything here. See `oan_grievance_service.api` for the
policy and the reasoning.

Endpoints
---------
    v1.grievance.submit     lodge a grievance on any channel
    v1.grievance.timeline   chronological unified conversation and timeline
    v1.grievance.reply      answer a More Info Needed request
    v1.grievance.confirm    confirm the resolution
    v1.grievance.reopen     reopen with a mandatory reason
    v1.grievance.escalate   escalate once the SLA has elapsed

    v1.submitter.options    dropdowns & reference data
    v1.grievance.options    officer & staff management dropdowns
    v1.grievance.summary    KPI cards for the all-grievances status queue

    v1.administrative_area.get_areas   the location cascade and search

    v1.category_assignment          category-to-department routing rules
    v1.officer                      L1 / L2 officer profiles (/api/v1/officers)
    v1.officer_statistics           live L1/L2 officer performance figures

    v1.attachment.submit_document   upload evidence (POST /api/v1/grievances/<g>/attachments)
    v1.attachment.get_attachments   list evidence with scan verdicts (GET /api/v1/grievances/<g>/attachments)
    v1.attachment.download          a clean file's URL (GET /api/v1/attachments/<id>/download)
    v1.attachment.delete            remove evidence from an open case (DELETE /api/v1/attachments/<id>)
"""

VERSION = "v1"
