import asyncio
import json
import os
from pathlib import Path
import sys
from service_auth import AgentFactory

async def run():
    factory=AgentFactory(Path(os.environ['AGENT_CONFIG_PATH']))
    agent=factory.user_agent('operator')
    try:
        await agent.register()
        created=await agent.post_task(title='Disposable isolation proof', spec='Return the integer 2.', reward=10, bidding_deadline_seconds=3600, execution_deadline_seconds=3600, review_deadline_seconds=3600)
        own=await agent.get_task(created['task_id'])
        result={'agent_id':agent.agent_id,'task_id':created['task_id'],'status':own['status'],'balance':await agent.get_balance()}
        if len(sys.argv)>2:
            response=await agent._request_raw('GET', f"{agent.config.task_board_url}/tasks/{sys.argv[2]}")
            assert response.status_code==404, response.text
            result['foreign_task_status']=response.status_code
        Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+'\n')
    finally:
        await agent.close()
asyncio.run(run())
