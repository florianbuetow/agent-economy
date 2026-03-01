import pytest

from base_agent.agent import BaseAgent
from base_agent.config import AgentConfig


@pytest.mark.e2e
class TestIdentityE2E:
    async def test_register_and_verify(self, agent_config: AgentConfig) -> None:
        agent = BaseAgent(config=agent_config)
        try:
            result = await agent.register()
            assert result["agent_id"].startswith("a-")
            assert agent.agent_id is not None
            assert agent.agent_id.startswith("a-")
            first_agent_id = agent.agent_id

            result2 = await agent.register()
            assert agent.agent_id == first_agent_id
            assert result2["agent_id"] == first_agent_id

            info = await agent.get_agent_info(agent.agent_id)
            assert info["name"] == "E2E Test Agent"
            assert info["public_key"] == f"ed25519:{agent.get_public_key_b64()}"

            agents = await agent.list_agents()
            agent_ids = [a["agent_id"] for a in agents]
            assert agent.agent_id in agent_ids

            token = agent._sign_jws({"action": "test", "agent_id": agent.agent_id})
            verify_result = await agent.verify_jws(token)
            assert verify_result["valid"] is True
        finally:
            await agent.close()
