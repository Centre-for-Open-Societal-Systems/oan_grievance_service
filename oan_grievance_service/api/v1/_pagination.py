# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Page window parameters and the pagination block shared by list endpoints."""

import math

from pydantic import BaseModel, Field


class PageParams(BaseModel):
	page: int = Field(1, ge=1)
	page_size: int = Field(20, ge=1, le=100)

	@property
	def start(self) -> int:
		return (self.page - 1) * self.page_size


def page_meta(params: PageParams, total: int) -> dict:
	pages = max(1, math.ceil(total / params.page_size))
	return {
		"page": params.page,
		"page_size": params.page_size,
		"total_count": total,
		"total_pages": pages,
		"has_next": params.page < pages,
		"has_prev": params.page > 1,
	}
