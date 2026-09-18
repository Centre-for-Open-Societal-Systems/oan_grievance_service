"""Version 1 of the public API.

This package is frozen once released: additive changes only. A breaking change opens
`api/v2/` rather than editing anything here. See `oan_grievance_service.api` for the
policy and the reasoning.

Endpoints
---------
    v1.grievance.submit     FSD FR-02 / 4.1   lodge a grievance on any channel
    v1.grievance.track      FSD FR-04         status lookup by ticket number
    v1.grievance.reply      FSD Appendix C    answer a More Info Needed request
    v1.grievance.confirm    FSD FR-06 / UC-03 confirm the resolution
    v1.grievance.reopen     FSD FR-06         reopen with a mandatory reason
    v1.grievance.escalate   FSD FR-07         escalate once the SLA has elapsed

    v1.submission.form_meta        FSD 3.11.5   wizard enums in one call
    v1.submission.categories       FSD 3.2.2    service categories
    v1.submission.grievance_types  FSD 3.2.2    types for one category
    v1.submission.ticket_preview   FSD 3.2.3    the ticket prefix, before submitting

    v1.administrative_area.get_areas   FSD 3.2.2   the location cascade and search

    v1.draft.save           FSD 3.2.1 / 7      persist a partial submission (POST /api/v1/drafts)
    v1.draft.load           FSD 3.2.1 / 7      resume where the wizard stopped
    v1.draft.discard        FSD 3.2.1 / 7      abandon a draft
"""

VERSION = "v1"
