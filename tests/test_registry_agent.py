from app.agents.registry_agent import RegistryAgent
from app.schemas.registry import RegistryCheckStatus


def test_registry_agent_returns_registry_result():
    agent = RegistryAgent()
    result = agent.run("Servochron", "USA")

    assert result.company_name == "Servochron"
    assert result.country == "USA"
    assert result.status == RegistryCheckStatus.found
    assert result.registry_found is True
