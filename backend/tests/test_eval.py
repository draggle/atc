from eval.montecarlo import run
from sim.scenarios import load


def test_three_run_monte_carlo():
    res = run(load("demo"), n_runs=3, seed=1, error_rate=0.05)
    arms = res["arms"]
    assert set(arms) == {"fixed", "tower_off", "tower_on"}
    for a in arms.values():
        assert a["flight_hours"] > 0 and a["closest_min_nm"] is not None
        assert "scoreboard" in a and a["scoreboard"]["losses_of_separation"] == a["los_total"]
    assert arms["tower_on"]["los_total"] <= arms["tower_off"]["los_total"]
    assert arms["tower_on"]["errors_caught"] == arms["tower_on"]["errors_injected"]
    assert arms["tower_off"]["errors_caught"] == 0
    assert arms["tower_on"]["miles_mean"] < arms["fixed"]["miles_mean"]
