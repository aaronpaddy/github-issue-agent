from src.agent.assessment import parse_assessment, parse_clarification, parse_no_change


def test_parses_a_complete_assessment():
    a = parse_assessment(
        {
            "summary": " Fixed it. ",
            "confidence": "MEDIUM",
            "interpretation": "Rounding was wrong",
            "assumptions": ["USD only", "  ", "no negatives"],
        }
    )
    assert a.summary == "Fixed it."
    assert a.confidence == "medium"
    assert a.assumptions == ["USD only", "no negatives"]


def test_an_unknown_confidence_is_treated_as_low():
    assert parse_assessment({"summary": "x", "confidence": "very sure"}).confidence == "low"
    assert parse_assessment({"summary": "x"}).confidence == "low"


def test_assumptions_are_optional():
    assert parse_assessment({"summary": "x", "confidence": "high"}).assumptions == []


def test_parses_a_clarification():
    c = parse_clarification({"question": " Which? ", "findings": "unclear"})
    assert c.question == "Which?"
    assert c.findings == "unclear"


def test_parses_a_no_change_finding():
    n = parse_no_change({"reason": " Already fixed. ", "evidence": "commit abc"})
    assert n.reason == "Already fixed."
    assert n.evidence == "commit abc"
