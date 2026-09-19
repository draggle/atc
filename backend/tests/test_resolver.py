from schemas import AircraftState, OpenClearance, Transmission
from tower.check import check
from tower.llm import MockLLM, get_llm
from tower.normalize import normalize
from tower.parse import parse
from tower.pipeline import TowerCore
from tower.resolver.agent import Resolver
from tower.resolver.tools import TERMINAL_TOOLS, ResolverTools

ACTIVE = ["ACA123", "ACA133", "DAL456"]


def make_tools(n_best, active_list=ACTIVE, history=None):
    return ResolverTools(
        relisten=lambda tid: list(n_best),
        active_aircraft=lambda: [{"callsign": c, "open_clearances": []} for c in active_list],
        frequency_history=lambda cs, n: list(history or []),
        aircraft_state=lambda cs: AircraftState(callsign=cs, x_nm=0, y_nm=0, alt_ft=30000, target_alt_ft=30000,
                                                hdg_deg=90, gs_kt=420),
    )


def ambiguous_case(ctrl, pilot, n_best, conf=0.7):
    c_ext = parse(normalize(ctrl), ACTIVE, "controller")
    c = OpenClearance(id="c1", callsign=c_ext.callsign, items=c_ext.items, issued_at=0.0)
    norm = normalize(pilot)
    tx = Transmission(id="t2", t_start=5, t_end=7, audio_ref="", text_raw=pilot, text_norm=norm,
                      asr_confidence=conf, speaker="pilot", n_best=[normalize(h) for h in n_best])
    ext = parse(norm, ACTIVE, "pilot", "t2")
    v = check(c, ext, tx, active=ACTIVE)
    assert v.result == "ambiguous"
    return c, v, tx


def assert_well_formed(res):
    assert 1 <= len(res.steps) <= 4
    assert res.steps[-1].tool in TERMINAL_TOOLS
    assert all(s.tool not in TERMINAL_TOOLS for s in res.steps[:-1])
    assert [s.step for s in res.steps] == list(range(1, len(res.steps) + 1))
    assert res.verdict.decided_by == "resolver"
    assert res.verdict.reason


def test_garbled_altitude_relistens_then_watches():
    n_best = ["descend flight level 210 ACA123", "descend flight level 240 ACA123"]
    c, v, tx = ambiguous_case("air canada one two three descend flight level two four zero",
                              "descend flight level two one zero air canada one two three", n_best)
    res = Resolver(MockLLM(), make_tools(n_best)).resolve(c, v, tx)
    assert_well_formed(res)
    assert res.steps[0].tool == "relisten"
    assert res.steps[-1].tool == "watch"
    assert res.watch_request is not None and res.watch_request.callsign == "ACA123"
    assert res.verdict.result == "ambiguous"


def test_relisten_supporting_expected_dismisses():
    n_best = ["descend flight level 240 ACA123", "descend flight level 240 ACA123"]
    c, v, tx = ambiguous_case("air canada one two three descend flight level two four zero",
                              "descend flight level two one zero air canada one two three", n_best)
    res = Resolver(MockLLM(), make_tools(n_best)).resolve(c, v, tx)
    assert_well_formed(res)
    assert res.steps[-1].tool == "dismiss" and res.verdict.result == "match"


def test_wrong_aircraft_checks_active_then_alerts():
    n_best = ["descend flight level 240 ACA123"]
    c, v, tx = ambiguous_case("air canada one two three descend flight level two four zero",
                              "descend flight level two four zero air canada one three three", n_best)
    assert v.error_type == "wrong_aircraft"
    res = Resolver(MockLLM(), make_tools(n_best)).resolve(c, v, tx, extra_context={"readback_callsign": "ACA133"})
    assert_well_formed(res)
    assert "active_aircraft" in [s.tool for s in res.steps]
    assert res.steps[-1].tool == "raise_alert"
    assert res.verdict.result == "mismatch" and res.verdict.error_type == "wrong_aircraft"
    assert res.verdict.correction_phrase


def test_budget_forces_uncertain_when_model_never_terminates():
    class Chatty:
        def chat(self, messages, tools=None, **kw):
            from tower.llm import ChatResponse, ToolCall
            return ChatResponse(content=None, tool_calls=[ToolCall("x", "active_aircraft", {})])

    n_best = ["descend flight level 210 ACA123"]
    c, v, tx = ambiguous_case("air canada one two three descend flight level two four zero",
                              "descend flight level two one zero air canada one two three", n_best, conf=0.3)
    res = Resolver(Chatty(), make_tools(n_best)).resolve(c, v, tx)
    assert len(res.steps) == 4 and res.steps[-1].tool == "mark_uncertain"
    assert res.verdict.result == "ambiguous"


def test_wall_clock_budget_forces_uncertain():
    ticks = iter([0.0, 0.0, 10.0, 10.0, 10.0])
    n_best = ["descend flight level 210 ACA123", "descend flight level 240 ACA123"]
    c, v, tx = ambiguous_case("air canada one two three descend flight level two four zero",
                              "descend flight level two one zero air canada one two three", n_best)
    res = Resolver(MockLLM(), make_tools(n_best), clock=lambda: next(ticks)).resolve(c, v, tx)
    assert res.steps[-1].tool == "mark_uncertain" and len(res.steps) <= 4


def test_get_llm_is_mock_without_key(monkeypatch):
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    assert isinstance(get_llm(), MockLLM)


# --- pipeline end to end --------------------------------------------------------------------------

def tx(tid, text, speaker, t, n_best=(), conf=1.0):
    return Transmission(id=tid, t_start=t, t_end=t + 2, audio_ref=f"{tid}.wav", text_raw=text, text_norm="",
                        asr_confidence=conf, speaker=speaker, n_best=list(n_best))


def test_pipeline_wrong_readback_alerts_with_correction_phrase():
    core = TowerCore(llm=MockLLM())
    ev = core.on_transmission(tx("t1", "air canada one two three descend flight level two four zero", "controller", 0), ACTIVE)
    assert [e["type"] for e in ev] == ["clearance_opened"]
    assert ev[0]["payload"]["callsign"] == "ACA123" and ev[0]["payload"]["status"] == "open"

    ev = core.on_transmission(tx("t2", "descend flight level two one zero air canada one two three", "pilot", 5), ACTIVE)
    types = [e["type"] for e in ev]
    assert types == ["alert", "clearance_updated"]
    alert = ev[0]["payload"]
    assert alert["result"] == "mismatch" and alert["error_type"] == "wrong_value"
    assert alert["correction_phrase"] == "Air Canada one two three, negative, descend flight level two four zero"
    assert alert["audio_ref"] == "t2.wav"
    assert ev[1]["payload"]["status"] == "mismatched"
    cmd = core.sim_command_for_readback(core.last_extraction)
    assert (cmd.kind, cmd.value) == ("altitude", 21000.0)  # the plane obeys what the pilot said


def test_pipeline_correct_readback_closes_and_watches_radar():
    core = TowerCore(llm=MockLLM())
    core.on_transmission(tx("t1", "air canada one two three descend flight level two four zero", "controller", 0), ACTIVE)
    ev = core.on_transmission(tx("t2", "down to flight level two four zero canada one two three", "pilot", 5), ACTIVE)
    assert [e["type"] for e in ev] == ["clearance_updated"]
    assert ev[0]["payload"]["status"] == "matched"
    assert core.conformance.watching("ACA123")


def test_pipeline_ambiguous_runs_resolver_and_emits_steps():
    core = TowerCore(llm=MockLLM())
    core.on_transmission(tx("t1", "air canada one two three descend flight level two four zero", "controller", 0), ACTIVE)
    ev = core.on_transmission(tx("t2", "descend flight level two one zero air canada one two three", "pilot", 5,
                                 n_best=["descend flight level two four zero air canada one two three"], conf=0.7), ACTIVE)
    types = [e["type"] for e in ev]
    assert types[0] == "resolver_step" and types[-1] == "clearance_updated"
    assert "alert" not in types  # n-best split -> watch, not an alert
    assert core.conformance.watching("ACA123")
    assert ev[-1]["payload"]["status"] == "uncertain"


def test_pipeline_missing_readback_on_tick():
    core = TowerCore(llm=MockLLM(), timeout_s=25.0)
    core.on_transmission(tx("t1", "air canada one two three descend flight level two four zero", "controller", 0), ACTIVE)
    assert core.tick(20.0) == []
    ev = core.tick(30.0)
    assert [e["type"] for e in ev] == ["alert", "clearance_updated"]
    assert ev[0]["payload"]["error_type"] == "missing_readback"
    assert ev[1]["payload"]["status"] == "missing"


def test_pipeline_wrong_aircraft_end_to_end():
    core = TowerCore(llm=MockLLM())
    core.on_transmission(tx("t1", "air canada one two three descend flight level two four zero", "controller", 0), ACTIVE)
    ev = core.on_transmission(tx("t2", "descend flight level two four zero air canada one three three", "pilot", 5), ACTIVE)
    alerts = [e for e in ev if e["type"] == "alert"]
    assert alerts and alerts[0]["payload"]["error_type"] == "wrong_aircraft"
