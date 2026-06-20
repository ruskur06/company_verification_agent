"""Deterministic company name normalization agent."""

from __future__ import annotations

import re

from app.schemas.name_normalizer import (
    NameNormalizerInput,
    NameNormalizerResult,
    NameVariant,
)

LEGAL_SUFFIXES = [
    "Incorporated",
    "Corporation",
    "L.L.C.",
    "Company",
    "Limited",
    "GmbH",
    "SARL",
    "Corp.",
    "Corp",
    "Inc.",
    "Inc",
    "LLC",
    "Ltd.",
    "Ltd",
    "Co.",
    "Co",
    "AG",
    "BV",
    "PLC",
]

USA_COUNTRY_NAMES = {"usa", "us", "united states", "united states of america"}
ISRAEL_COUNTRY_NAMES = {"israel", "il"}


class NameNormalizer:
    """Normalizes company names and generates deterministic search variants."""

    def run(self, data: NameNormalizerInput) -> NameNormalizerResult:
        cleaned_name = self._clean_company_name(data.company_name)
        normalized_name, had_suffix = self._remove_legal_suffix(cleaned_name)
        country_key = self._normalize_country(data.country)

        search_entries = self._build_search_entries(
            cleaned_name=cleaned_name,
            normalized_name=normalized_name,
            had_suffix=had_suffix,
            country_key=country_key,
        )
        search_names = self._dedupe_case_insensitive([entry.value for entry in search_entries])
        variants = self._dedupe_variants(search_entries)
        domain_candidates = self._normalize_domain(data.domain)

        warnings: list[str] = []
        if len(normalized_name) <= 2:
            warnings.append("Normalized company name is very short and may produce noisy search results.")

        return NameNormalizerResult(
            original_name=cleaned_name,
            normalized_name=normalized_name,
            search_names=search_names,
            domain_candidates=domain_candidates,
            variants=variants,
            warnings=warnings,
        )

    def _clean_company_name(self, company_name: str) -> str:
        name = company_name.strip()
        name = re.sub(r"\s+", " ", name)

        if len(name) >= 2 and name[0] == name[-1] and name[0] in {'"', "'"}:
            name = name[1:-1].strip()

        return re.sub(r"\s+", " ", name)

    def _remove_legal_suffix(self, name: str) -> tuple[str, bool]:
        for suffix in sorted(LEGAL_SUFFIXES, key=len, reverse=True):
            pattern = rf"(?P<base>.+?)(?:,?\s+{re.escape(suffix)})$"
            match = re.match(pattern, name, flags=re.IGNORECASE)
            if match:
                return match.group("base").strip(), True

        return name, False

    def _normalize_country(self, country: str | None) -> str:
        if not country:
            return "unknown"
        return country.strip().lower()

    def _build_search_entries(
        self,
        cleaned_name: str,
        normalized_name: str,
        had_suffix: bool,
        country_key: str,
    ) -> list[NameVariant]:
        entries: list[NameVariant] = []

        def add(value: str, reason: str) -> None:
            entries.append(NameVariant(value=value, reason=reason))

        if country_key in USA_COUNTRY_NAMES:
            country_suffixes = ["Inc", "LLC", "Corp", "Corporation", "Co"]
            country_label = "USA"
        elif country_key in ISRAEL_COUNTRY_NAMES:
            country_suffixes = ["Ltd", "Limited", 'בע"מ']
            country_label = "Israel"
        else:
            country_suffixes = ["Inc", "LLC", "Ltd"]
            country_label = "generic"

        add(normalized_name, "Base normalized company name")

        for suffix in country_suffixes:
            add(
                f"{normalized_name} {suffix}",
                f"{country_label} legal suffix variant",
            )

        if had_suffix and cleaned_name.casefold() != normalized_name.casefold():
            add(cleaned_name, "Original cleaned name with legal suffix preserved")

        return entries

    def _normalize_domain(self, domain: str | None) -> list[str]:
        if not domain:
            return []

        value = domain.strip()
        if not value:
            return []

        value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
        value = value.split("/")[0]
        value = value.split(":")[0]
        value = value.lower()

        if value.startswith("www."):
            value = value[4:]

        if "." not in value:
            return []

        return [value]

    def _dedupe_case_insensitive(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []

        for item in items:
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                result.append(item)

        return result

    def _dedupe_variants(self, variants: list[NameVariant]) -> list[NameVariant]:
        seen: set[str] = set()
        result: list[NameVariant] = []

        for variant in variants:
            key = variant.value.casefold()
            if key not in seen:
                seen.add(key)
                result.append(variant)

        return result
