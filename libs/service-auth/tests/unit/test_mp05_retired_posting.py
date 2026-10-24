"""The SDK cannot allocate a fresh posting identity while sending it."""

import pytest

from service_auth.agent import BaseAgent

pytestmark = pytest.mark.unit


def test_one_shot_post_task_api_is_retired() -> None:
    assert not hasattr(BaseAgent, "post_task")
    assert callable(BaseAgent.prepare_task_posting)
    assert callable(BaseAgent.submit_task_posting)
    assert callable(BaseAgent.get_task_posting)
