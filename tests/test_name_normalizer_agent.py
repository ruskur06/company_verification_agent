from app.agents.name_normalizer_agent import NameNormalizer
from app.schemas.name_normalizer import NameNormalizerInput


def test_usa_company_normalization():
    agent = NameNormalizer()
    result = agent.run(
        NameNormalizerInput(
            company_name="Servochron",
            country="USA",
            domain="https://www.servochron.com/about",
        )
    )

    assert result.original_name == "Servochron"
    assert result.normalized_name == "Servochron"
    assert "Servochron" in result.search_names
    assert "Servochron Inc" in result.search_names
    assert "Servochron LLC" in result.search_names
    assert "servochron.com" in result.domain_candidates


def test_removing_legal_suffix():
    agent = NameNormalizer()
    result = agent.run(
        NameNormalizerInput(
            company_name="Acme LLC",
            country="USA",
        )
    )

    assert result.original_name == "Acme LLC"
    assert result.normalized_name == "Acme"
    assert "Acme" in result.search_names
    assert "Acme LLC" in result.search_names


def test_no_duplicate_variants():
    agent = NameNormalizer()
    result = agent.run(
        NameNormalizerInput(
            company_name="Acme",
            country="USA",
            domain="acme.com",
        )
    )

    lowered = [name.casefold() for name in result.search_names]
    assert len(lowered) == len(set(lowered))


def test_israel_company_variants():
    agent = NameNormalizer()
    result = agent.run(
        NameNormalizerInput(
            company_name="Example",
            country="Israel",
        )
    )

    assert "Example Ltd" in result.search_names
    assert "Example Limited" in result.search_names
    assert 'Example בע"מ' in result.search_names
