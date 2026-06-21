"""Agent that wraps the company registry search tool."""

from __future__ import annotations

from app.schemas.registry import RegistryCheckResult
from app.tools.registry_search import search_company_registry


def _dedupe_case_insensitive(names: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for name in names:
        cleaned = name.strip()
        if not cleaned:
            continue

        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            result.append(cleaned)

    return result


class RegistryAgent:
    """Searches official company registries for registration evidence."""

    def run(self, country: str, search_names: list[str]) -> RegistryCheckResult:
        """Try registry search across normalized name variants."""
        candidates = _dedupe_case_insensitive(search_names)

        if not candidates:
            result = search_company_registry(company_name="", country=country)
            return result.model_copy(update={"searched_names": []})

        last_result: RegistryCheckResult | None = None

        for name in candidates:
            result = search_company_registry(company_name=name, country=country)
            last_result = result

            if result.registry_found:
                return result.model_copy(
                    update={
                        "matched_name": name,
                        "searched_names": candidates,
                    }
                )

        assert last_result is not None
        return last_result.model_copy(
            update={
                "searched_names": candidates,
                "notes": [
                    *last_result.notes,
                    f"No registry match among {len(candidates)} searched name variant(s).",
                ],
            }
        )
