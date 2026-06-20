"""Schemas for company name normalization."""

from typing import Optional

from pydantic import BaseModel


class NameNormalizerInput(BaseModel):
    company_name: str
    country: Optional[str] = None
    domain: Optional[str] = None


class NameVariant(BaseModel):
    value: str
    reason: str


class NameNormalizerResult(BaseModel):
    original_name: str
    normalized_name: str
    search_names: list[str]
    domain_candidates: list[str]
    variants: list[NameVariant]
    warnings: list[str] = []
