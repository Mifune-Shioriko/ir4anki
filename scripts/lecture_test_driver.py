"""Restricted-runner timer wakeups, with all real suite/owner effects intact.

Use only for tests. Keeps a public asyncio timer alive during executor shutdown
when the sandbox suppresses self-pipe wakeups. No API/model/collection mocking.
"""
import asyncio,runpy,sys
from pathlib import Path
class TimerPolicy(asyncio.DefaultEventLoopPolicy):
 def new_event_loop(self):
  loop=super().new_event_loop()
  def tick():
   if not loop.is_closed():loop.call_later(.02,tick)
  loop.call_later(.02,tick)
  return loop
asyncio.set_event_loop_policy(TimerPolicy())
if __name__=='__main__':
 target=sys.argv[1];sys.argv=sys.argv[1:]
 if target=='pytest':
  import pytest
  raise SystemExit(pytest.main(sys.argv[1:]))
 sys.path.insert(0,str(Path(target).resolve().parent));runpy.run_path(target,run_name='__main__')
