"""Diagnostic scheduling probe; never used as a passing acceptance test."""
import asyncio
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, '/Users/flo/Developer/github/agent-economy/services/task-board')
from tests.fakes.native_task_fixture import TaskTerms, native_task_context
from db_gateway_service.services.task_operation_writer import TaskOperationWriter

async def main():
    task_id = 't-00000000-0000-4000-8000-000000000401'
    event_inserted = threading.Event()
    replay_done = threading.Event()
    trace = []
    original_insert = TaskOperationWriter._insert_event
    with tempfile.TemporaryDirectory(prefix='ate-ui-race-') as directory, pytest.MonkeyPatch.context() as patch:
        with native_task_context(Path(directory), patch) as scenario:
            scenario.open_task(task_id, TaskTerms(100, 1, 1, 1, 1))
            scenario.clock.at_seconds(5)
            evaluator = scenario.evaluator(None)
            task = scenario.task(task_id)
            both_entered = asyncio.Event()
            deliveries = 0
            releases = 0

            def pause_event(cursor, request, now_us):
                event_id = original_insert(cursor, request, now_us)
                trace.append({'stage': 'finalize_event_inserted', 'thread': threading.get_ident(), 'event_id': event_id})
                event_inserted.set()
                assert replay_done.wait(5), 'Bank replay did not reach controlled boundary'
                present = cursor.execute('SELECT COUNT(*) FROM events WHERE event_id=?', (event_id,)).fetchone()[0]
                trace.append({'stage': 'finalize_resumed', 'event_still_present': present})
                return event_id

            patch.setattr(TaskOperationWriter, '_insert_event', staticmethod(pause_event))

            async def overlapping(escrow_id, recipient_account_id, operation_id, source_reference):
                nonlocal deliveries, releases
                deliveries += 1
                if deliveries == 2:
                    both_entered.set()
                await both_entered.wait()
                releases += 1
                ordinal = releases
                if ordinal == 2:
                    assert event_inserted.wait(5), 'Finalize did not insert its event'
                try:
                    return await scenario.persist_release(escrow_id, recipient_account_id, operation_id, source_reference)
                except Exception as error:
                    trace.append({'stage': 'bank_replay_error', 'thread': threading.get_ident(), 'type': type(error).__name__, 'message': str(error)})
                    raise
                finally:
                    if ordinal == 2:
                        replay_done.set()

            scenario.bank.escrow_release.side_effect = overlapping
            try:
                results = await asyncio.wait_for(asyncio.gather(evaluator.evaluate_deadline(dict(task)), evaluator.evaluate_deadline(dict(task)), return_exceptions=True), timeout=10)
                trace.append({'results': [{'type': type(value).__name__, 'message': str(value)} for value in results]})
            finally:
                evaluator.close()
    print(json.dumps(trace, indent=2))
    assert any(item.get('event_still_present') == 0 for item in trace)
    assert any(any(value['type'] == 'IntegrityError' and 'FOREIGN KEY' in value['message'] for value in item.get('results', [])) for item in trace)

asyncio.run(main())
