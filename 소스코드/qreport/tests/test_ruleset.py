from qreport.ruleset import DEFAULT_RULES, RuleSet


def test_ruleset_roundtrip():
    r = DEFAULT_RULES
    assert r.ucl_sigma == 3 and r.run_length == 9 and r.surge_min == 8 and r.heatmap_basis == "max"
    assert RuleSet.from_json(r.to_json()) == r
    assert isinstance(RuleSet.from_json(r.to_json()).seg_months, tuple)


def test_ruleset_replace_frozen():
    r2 = DEFAULT_RULES.replace(ucl_sigma=2)
    assert r2.ucl_sigma == 2 and DEFAULT_RULES.ucl_sigma == 3
