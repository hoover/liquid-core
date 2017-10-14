import pytest

@pytest.fixture(autouse=True)
def mock_agent(monkeypatch):
    class Job: id = 'mock'
    from liquidcore.config import agent
    monkeypatch.setattr(agent, 'launch', lambda target_configuration: Job)
