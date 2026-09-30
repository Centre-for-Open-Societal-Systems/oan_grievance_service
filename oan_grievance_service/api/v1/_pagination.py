"""Shared pagination schemas and metadata builders for API v1 controllers."""

import math
from typing import Any

from pydantic import BaseModel, Field


class PageParams(BaseModel):
	model_config = {"extra": "allow"}

	page: int = Field(default=1, ge=1, description="Page number, 1-indexed")
	page_size: int = Field(default=20, ge=1, le=100, description="Items per page")


def page_meta(total_count: int, page: int, page_size: int) -> dict[str, Any]:
	"""Build standardized pagination metadata dictionary."""
	total_pages = math.ceil(total_count / page_size) if total_count > 0 and page_size > 0 else 1
	return {
		"page": page,
		"page_size": page_size,
		"total_count": total_count,
		"total_pages": total_pages,
		"has_next": page < total_pages,
		"has_prev": page > 1,
	}
