from src.cli import build_pr_body


def test_pr_body_adds_a_summary_heading_when_the_model_did_not():
    body = build_pr_body(7, "Fixed the rounding.", 1)
    assert body.startswith("Fixes #7")
    assert body.count("## Summary") == 1


def test_pr_body_does_not_stack_a_second_heading():
    body = build_pr_body(7, "## Summary\n\nFixed the rounding.", 2)
    assert body.count("## Summary") == 1
    assert "after 2 attempt(s)" in body


def test_pr_body_drops_chatter_before_the_models_own_heading():
    body = build_pr_body(4, "All checks pass now.\n\n## Summary\n\n- Added the helper.", 1)
    assert body.count("## Summary") == 1
    assert "All checks pass now" not in body
    assert "Added the helper" in body
