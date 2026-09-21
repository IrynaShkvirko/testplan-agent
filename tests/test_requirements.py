from testplan_agent.requirements import find_vague_terms, parse_story

STORY = """# Cap discounts

Some background prose that is not a requirement.

## Acceptance criteria

- AC-1: A cart with 3 or more items gets at most 50% off.
- AC-2: The customer is shown a reasonable message.
- [ ] Rounded to the nearest cent

## Out of scope

- Loyalty points
"""


def test_labelled_and_unlabelled_criteria_get_stable_ids():
    story = parse_story(STORY)
    assert story.title == "Cap discounts"
    assert [r.id for r in story.criteria] == ["AC-1", "AC-2", "AC-3"]
    assert story.criteria[0].text.startswith("A cart with 3 or more items")
    assert [r.text for r in story.non_goals] == ["Loyalty points"]


def test_background_prose_is_not_a_requirement():
    story = parse_story(STORY)
    assert all("background" not in r.text for r in story.requirements)


def test_vague_wording_is_flagged_against_its_requirement():
    story = parse_story(STORY)
    terms = {(a.requirement_id, a.term) for a in story.ambiguities}
    assert ("AC-2", "reasonable") in terms
    assert not any(a.requirement_id == "AC-1" for a in story.ambiguities)


def test_given_when_then_scenarios_become_one_criterion():
    story = parse_story(
        "## Acceptance criteria\n\n- Given a cart with 3 items\n- When a 90% coupon is applied\n"
        "- Then the discount is 50%\n"
    )
    assert len(story.criteria) == 1
    text = story.criteria[0].text.lower()
    assert "given" in text and "then" in text


def test_find_vague_terms_matches_whole_words_only():
    assert find_vague_terms("It should be fast and, where possible, easy") == [
        "fast",
        "where possible",
        "easy",
    ]
    assert find_vague_terms("The breakfast menu is fastidious") == []


def test_empty_story_has_no_requirements():
    story = parse_story("")
    assert story.requirements == [] and story.ambiguities == []
