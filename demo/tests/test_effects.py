"""Bounded effects are a separate transport choice, never speculative mixer input."""
from copy import deepcopy
import unittest
from test_environment import Native, both, decision
from test_policy import snapshot, response, library
from djjev import policy
from djjev.environment import Rekordbox, LocalPreDispatch

class EffectTests(unittest.IsolatedAsyncioTestCase):
    def frame(self):
        f=both();f['effects']={'subtleEcho':True};f['mixer']['crossfader_position']=.5
        return f

    async def test_paused_effects_cannot_be_chosen_or_dispatched(self):
        f=self.frame();native=Native(f);env=Rekordbox(native,library());s=env.snapshot(f)
        self.assertFalse(s['effects']['subtle_echo_supported'])
        self.assertFalse(any(k.startswith('echo_') for k in policy.prepare(s)['questions']['transport']['criteria']))
        with self.assertRaises(LocalPreDispatch):await env.execute(decision(transport='echo_A'),s)
        self.assertEqual(native.physical,[])

    async def test_effect_is_available_only_with_time_audible_route_and_cooldown_finished(self):
        f=self.frame();env=Rekordbox(Native(f),library(),effects_enabled=True);s=env.snapshot(f)
        self.assertIn('echo_A',policy.prepare(s)['questions']['transport']['criteria'])
        for defect in ('clock','muted','cooldown','unsupported'):
            changed=deepcopy(s)
            if defect=='clock':changed['decks']['A']['remaining']=20.
            elif defect=='muted':changed['mixer']['cross']=1.
            elif defect=='cooldown':changed['effects']['cooldown_seconds']=12.
            else:changed['effects']['subtle_echo_supported']=False
            self.assertNotIn('echo_A',policy.prepare(changed)['questions']['transport']['criteria'])
        req=policy.prepare(s)
        chosen=policy.resolve(req,response(req,transport='echo_A',bass='B',mid='A_cut'))
        self.assertEqual((chosen['bass'],chosen['mid']),('hold','hold'))
        self.assertTrue(policy.applicable(chosen,s))

    async def test_execution_requires_verified_off_and_prevents_followup_accent(self):
        for off in (True,False):
            f=self.frame();native=Native(f);original=native.call
            async def call(role,name,**params):
                if name=='echoAccent':
                    native.calls.append((role,name,params))
                    return {'verified':True,'dispatched':True,'effectOffVerified':off,'after':deepcopy(native.raw)}
                return await original(role,name,**params)
            native.call=call;env=Rekordbox(native,library(),effects_enabled=True);s=env.snapshot(f)
            if off:
                result=await env.execute(decision(transport='echo_A'),s)
                self.assertTrue(result['verified'])
                self.assertNotIn('echo_A',policy.prepare(env.snapshot(f))['questions']['transport']['criteria'])
                with self.assertRaises(LocalPreDispatch):await env.execute(decision(transport='echo_A'),env.snapshot(f))
            else:
                with self.assertRaisesRegex(RuntimeError,'uitschakelen'):await env.execute(decision(transport='echo_A'),s)
            self.assertEqual([name for _,name,_ in native.physical],['echoAccent'])
