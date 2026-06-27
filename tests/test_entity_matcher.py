from datetime import datetime, timezone

from app.schemas.source import RelevanceLevel, SourceResult, SourceType
from app.tools.entity_matcher import annotate_relevance, score_relevance


def _source(
    *,
    title: str,
    url: str = "",
    snippet: str = "",
    is_mock: bool = False,
) -> SourceResult:
    now = datetime.now(timezone.utc)
    return SourceResult(
        title=title,
        url=url or f"https://example.com/{title.lower().replace(' ', '-')}",
        snippet=snippet,
        source_type=SourceType.search_result,
        retrieved_at=now,
        is_mock=is_mock,
    )


def test_mock_source_is_relevant_but_remains_mock():
    source = _source(title="Any title", is_mock=True)

    result = score_relevance("Servochron", "Austria", source)

    assert result.level == RelevanceLevel.relevant
    assert result.score == 1.0
    assert source.is_mock is True


def test_exact_company_name_in_title_is_relevant():
    source = _source(title="SERVOCHRON GmbH official profile")

    result = score_relevance("Servochron GmbH", "Austria", source)

    assert result.level == RelevanceLevel.relevant
    assert result.score >= 0.5


def test_legal_suffix_differences_still_match():
    source = _source(title="Servochron business profile")

    result = score_relevance("Servochron GmbH", "Austria", source)

    assert result.level == RelevanceLevel.relevant


def test_case_insensitive_match():
    source = _source(title="servochron COMPANY overview")

    result = score_relevance("SERVOCHRON", "Austria", source)

    assert result.level == RelevanceLevel.relevant


def test_unrelated_company_is_irrelevant():
    source = _source(
        title="Avron GmbH profile",
        url="https://example.com/avron-gmbh",
        snippet="Avron GmbH company information.",
    )

    result = score_relevance("Servochron", "Austria", source)

    assert result.level == RelevanceLevel.irrelevant
    assert result.score < 0.15


def test_country_only_mention_is_not_enough():
    source = _source(
        title="Austria business news",
        url="https://example.com/austria-news",
        snippet="General Austria market update.",
    )

    result = score_relevance("Servochron", "Austria", source)

    assert result.level == RelevanceLevel.irrelevant


def test_name_only_in_snippet_is_not_irrelevant():
    source = _source(
        title="Business directory listing",
        url="https://example.com/listing",
        snippet="Profile for Servochron with office details.",
    )

    result = score_relevance("Servochron", "Austria", source)

    assert result.level in {RelevanceLevel.uncertain, RelevanceLevel.relevant}
    assert result.level != RelevanceLevel.irrelevant


def test_annotate_relevance_preserves_source_count_and_order():
    sources = [
        _source(title="SERVOCHRON GmbH profile"),
        _source(title="Avron GmbH profile"),
        _source(title="Mock search result", is_mock=True),
    ]

    annotated = annotate_relevance("Servochron", "Austria", sources)

    assert len(annotated) == 3
    assert [source.title for source in annotated] == [source.title for source in sources]


def test_annotate_relevance_does_not_mutate_original_source_result():
    original = _source(title="SERVOCHRON GmbH profile")

    annotated = annotate_relevance("Servochron", "Austria", [original])

    assert original.relevance == RelevanceLevel.uncertain
    assert original.relevance_score == 0.0
    assert not original.relevance_reasons
    assert annotated[0].relevance == RelevanceLevel.relevant
    assert annotated[0].relevance_score >= 0.5


def test_us_water_rockets_servochron_manual_is_not_relevant_for_company():
    source = _source(
        title="ServoChron Parachute deployment system - Construction and Programming User Manual",
        url="http://www.uswaterrockets.com/documents/ServoChron/manual.htm",
        snippet="Tutorial documentation for the ServoChron deployment system.",
    )

    result = score_relevance("Servochron", "Austria", source)

    assert result.level != RelevanceLevel.relevant
    assert result.level in {RelevanceLevel.uncertain, RelevanceLevel.irrelevant}
    assert "product_manual_context_without_company_identity" in result.reasons


def test_servochron_com_official_site_remains_relevant():
    source = _source(
        title="SERVOCHRON GmbH official website",
        url="https://servochron.com",
        snippet="Official company homepage for Servochron in Austria.",
    )

    result = score_relevance("Servochron", "Austria", source)

    assert result.level == RelevanceLevel.relevant
    assert result.score >= 0.5
