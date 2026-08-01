import asyncio
from nanobot.avatar.base import AvatarIntent
from nanobot.avatar.vts_runtime import VTubeStudioRuntime, _maybe_await
import inspect

class FakeReq:
    def requestTriggerHotKey(self, hid, item_instance_id=None):
        return {'data': {'hotkeyID': hid}}
    def requestSetParameterValue(self, parameter, value, **kw):
        return {'data': {'parameterValues': [{'id': parameter, 'weight': 1, 'value': value}]}}

class FakeClient:
    def __init__(self):
        self.vts_request = FakeReq()
        self.called = []
    async def connect(self): pass
    async def request_authenticate_token(self): pass
    async def request_authenticate(self): pass
    async def request(self, msg):
        self.called.append(msg)
        return {'data': {}}

holder = {}
def factory(**kw):
    c = FakeClient()
    holder['c'] = c
    return c

async def main():
    r = VTubeStudioRuntime(client_factory=factory)
    await r.connect()
    c = holder['c']
    
    # Debug: check what apply_intent sees
    print('vts_req:', c.vts_request)
    print('has vts_request attr:', hasattr(c, 'vts_request'))
    print('request callable:', callable(getattr(c, 'request', None)))
    vts_req = getattr(c, 'vts_request', None)
    trigger_fn = getattr(vts_req, 'requestTriggerHotKey', None)
    print('trigger_fn:', trigger_fn)
    print('trigger callable:', callable(trigger_fn))
    
    # Test _maybe_await
    print('_maybe_await on True:', await _maybe_await(True))
    
    await r.apply_intent(AvatarIntent(expression='smile', speaking=True))
    print('call count:', len(c.called))
    for m in c.called:
        print('  msg:', m)

asyncio.run(main())
