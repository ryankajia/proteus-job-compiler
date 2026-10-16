"""A reply that is scored criterion by criterion is a different document from a
continuous pitch, and each needs different advice.

    itemised    a heading per criterion, a short answer under each; every
                criterion needs its own answer.
    narrative   one supporting statement / pitch, often page-limited; assessors
                read it whole and then check every criterion was covered, so one
                strong example should answer several criteria.

Guidance for one is bad advice for the other: nine separate answers squeezed
into two pages read as a list, and a flowing pitch handed to a panel that scores
line by line leaves criteria unanswered.

The style is read from the PD's own instructions, not from the employer's name.
All text here is invented.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import calibration, db, identity, jobspec, reviewer, selection, webapp  # noqa: E402

CRITERIA = """SELECTION CRITERIA
1. Demonstrated ability to deliver a good quality service to stakeholders.
2. Demonstrated sound organisation and time management skills, including the ability to plan own work, set priorities and work to tight timelines.
3. Ability to communicate effectively and prepare general reports.
4. Demonstrated competency in Microsoft applications, including Word, Excel, PowerPoint and Outlook.
"""

NARRATIVE_PD = ("Grants Support Officer\nTo apply, write a two-page supporting statement/pitch that "
                "demonstrates how you can undertake the position by addressing the criteria.\n"
                + CRITERIA)
ITEMISED_PD = ("Grants Support Officer\nApplicants must submit a statement addressing the selection "
               "criteria.\n" + CRITERIA)


@pytest.fixture(autouse=True)
def _no_real_answers(tmp_path, monkeypatch):
    """The reuse bank reads finished packages from disk. On a real install that
    is somebody's actual past answers, which would make these tests differ from
    machine to machine. Point it at an empty folder."""
    monkeypatch.setattr(selection.reuse, "PKG", tmp_path / "no_packages")


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    identity.load_seed(c)
    calibration.calibrate(c)
    yield c
    c.close()


def _spec(raw, source="job_x"):
    s = jobspec.parse(raw, source=source)
    return s


def _build(conn, raw, source="job_x"):
    return selection.compile_selection_criteria(conn, _spec(raw, source))


# ------------------------------------------------------------ detection ----

def test_an_explicit_pitch_with_a_page_limit_is_narrative():
    r = selection.response_style(NARRATIVE_PD)
    assert r["style"] == "narrative" and r["pages"] == 2
    assert "supporting statement" in r["signal"]


def test_a_pd_with_no_instructions_at_all_is_itemised():
    """The usual case: the wording lives on the advert page, not in the PD."""
    r = selection.response_style(CRITERIA)
    assert r["style"] == "itemised" and r["pages"] is None and r["signal"] == ""


def test_a_statement_addressing_the_criteria_is_itemised():
    assert selection.response_style(ITEMISED_PD)["style"] == "itemised"


def test_a_page_limit_alone_does_not_make_it_narrative():
    """'A two-page statement addressing EACH criterion' is still scored one
    criterion at a time — but the limit is still worth knowing."""
    r = selection.response_style("Submit a two-page statement addressing each of the selection criteria.")
    assert r["style"] == "itemised" and r["pages"] == 2


@pytest.mark.parametrize("text,words", [
    ("Write a supporting statement of no more than 600 words.", 600),
    ("A pitch of maximum of 500 words is required.", 500),
])
def test_word_limits_are_read(text, words):
    r = selection.response_style(text)
    assert r["style"] == "narrative" and r["words"] == words
    assert selection.word_budget(r) == words


@pytest.mark.parametrize("word,n", [("one", 1), ("two", 2), ("three", 3), ("4", 4)])
def test_page_limits_in_words_or_digits(word, n):
    assert selection.response_style(f"Provide a {word}-page pitch.")["pages"] == n


def test_a_pitch_with_no_limit_has_no_budget():
    r = selection.response_style("Please attach a short pitch.")
    assert r["style"] == "narrative" and selection.word_budget(r) is None


def test_the_budget_is_pages_times_words_per_page():
    assert selection.word_budget({"pages": 2, "words": None}) == 2 * selection.WORDS_PER_PAGE


def test_a_hand_written_line_overrides_the_guess():
    """It is a heuristic, and the person reading the advert knows better."""
    forced = selection.response_style(NARRATIVE_PD + "\nResponse style: itemised")
    assert forced["style"] == "itemised" and "set by hand" in forced["signal"]
    other = selection.response_style(ITEMISED_PD + "\nResponse style: narrative")
    assert other["style"] == "narrative"


@pytest.mark.parametrize("text", ["", None, "   \n  "])
def test_empty_input_is_itemised_and_does_not_raise(text):
    assert selection.response_style(text)["style"] == "itemised"


def test_the_document_is_named_in_the_pds_own_terms():
    assert "2-page supporting statement" in selection.document_name(NARRATIVE_PD)
    assert selection.document_name(ITEMISED_PD) == "Statement addressing the selection criteria"


# ------------------------------------------------- the narrative worksheet ----

def test_a_narrative_pd_gets_a_pitch_worksheet_not_a_criterion_by_criterion_one(conn):
    md = _build(conn, NARRATIVE_PD)
    assert "supporting statement / pitch" in md
    assert "## Criterion 1" not in md, "a pitch is not nine separate answers"
    assert "## Coverage map" in md and "### Example 1" in md


def test_an_itemised_pd_still_gets_the_criterion_by_criterion_worksheet(conn):
    md = _build(conn, ITEMISED_PD)
    assert "## Criterion 1:" in md and "## Criterion 4:" in md
    assert "Coverage map" not in md


def test_every_criterion_appears_once_word_for_word_as_a_checkbox(conn):
    md = _build(conn, NARRATIVE_PD)
    for i, crit in enumerate(selection.extract_criteria(NARRATIVE_PD), 1):
        assert f"- [ ] **C{i}** {crit}" in md


def test_the_word_budget_is_stated(conn):
    assert f"about {2 * selection.WORDS_PER_PAGE} words" in _build(conn, NARRATIVE_PD)


def test_an_unstated_limit_is_flagged_as_an_assumption(conn):
    md = _build(conn, "A pitch is required.\n" + CRITERIA)
    assert "no limit stated" in md


def test_the_detection_signal_is_shown_so_a_wrong_call_is_visible(conn):
    md = _build(conn, NARRATIVE_PD)
    assert "Detected from the PD" in md and "Response style: itemised" in md


def test_the_pitch_worksheet_ends_with_a_read_back_checklist(conn):
    md = _build(conn, NARRATIVE_PD)
    assert "read it once as a panel would" in md


def test_criteria_with_no_evidence_are_named_not_padded(conn):
    """The floor stays: nothing is attached to a criterion it does not answer."""
    raw = ("A pitch is required.\nSELECTION CRITERIA\n"
           "1. Demonstrated ability to deliver a good quality service to stakeholders.\n"
           "2. Registration as a medical radiation practitioner with the national board.\n")
    md = _build(conn, raw)
    assert "Not covered by any evidence: C2" in md
    assert "### Example 1" in md and "answers C1" in md


def test_no_criteria_found_still_gives_the_paste_in_template(conn):
    md = _build(conn, "A two-page pitch is required. See the selection criteria in the PD.")
    assert "paste the criterion from the PD here" in md


# ------------------------------------------------------ choosing examples ----

def test_examples_are_chosen_to_cover_the_most_criteria():
    covers = {"a": {1, 2, 3, 4}, "b": {4, 5}, "c": {6}}
    examples, unplaced, none = selection._plan_examples(covers, {}, 7)
    assert [e[0] for e in examples] == ["a", "b", "c"]
    assert examples[0][1] == [1, 2, 3, 4]
    assert examples[1][1] == [5], "criterion 4 is already covered; only 5 is new"
    assert examples[1][2] == [4], "but it may also speak to 4"
    assert none == [7] and unplaced == []


def test_a_criterion_nothing_answers_is_reported_not_forced():
    _, _, none = selection._plan_examples({"a": {1}}, {}, 3)
    assert none == [2, 3]


def test_a_pitch_never_tells_more_than_the_example_budget_allows():
    covers = {f"cap{i}": {i} for i in range(1, 9)}
    examples, unplaced, _ = selection._plan_examples(covers, {}, 8)
    assert len(examples) == selection._MAX_EXAMPLES
    assert len(unplaced) == 8 - selection._MAX_EXAMPLES


def test_ties_prefer_the_stronger_capability_deterministically():
    covers = {"weak": {1, 2}, "strong": {1, 2}}
    levels = {"weak": {"level": 2.0}, "strong": {"level": 4.5}}
    assert selection._plan_examples(covers, levels, 2)[0][0][0] == "strong"


def test_no_capabilities_at_all_does_not_crash():
    assert selection._plan_examples({}, {}, 3) == ([], [], [1, 2, 3])


# --------------------------------------- earlier answers are not ignored ----

def test_a_criterion_with_earlier_wording_is_not_called_evidence_free(conn, monkeypatch):
    """A capability miss is not the same as having nothing to say: an earlier
    answer to a similar criterion is real material."""
    prior = {"package": "example_job", "answer": "I keep auditable records.", "score": 0.4,
             "criterion": "Attention to detail"}
    real = selection.reuse.best_match
    monkeypatch.setattr(selection.reuse, "best_match",
                        lambda crit, bank, *a, **k: prior if "AHPRA" in crit else real(crit, bank, *a, **k))
    raw = "A pitch is required.\nSELECTION CRITERIA\n1. Current AHPRA registration is held.\n"
    md = _build(conn, raw)
    assert "you have earlier wording for" in md
    assert "I keep auditable records." in md


def test_earlier_answers_are_offered_as_notes_not_as_draft_text(conn):
    md = _build(conn, NARRATIVE_PD)
    if "Raw material from your own earlier answers" in md:
        section = md.split("Raw material from your own earlier answers")[1]
        for ln in section.splitlines():
            assert ln.startswith(">") or not ln.strip() or ln.startswith("---"), ln


# ---------------------------------------------------- word-form synonyms ----

@pytest.mark.parametrize("crit", [
    "Demonstrated sound organisation and time management skills.",
    "Ability to set priorities and work to tight timelines.",
])
def test_the_bridge_recognises_the_other_word_forms(crit, conn):
    caps = identity.get_capabilities(conn)
    best = max(selection._crit_score(c, crit) for c in caps)
    assert best >= selection._MIN_CRIT_SCORE, "'organisation / priorities / timelines' found no evidence"


# --------------------------------------------- the other places that care ----

def _criteria_finding(conn, raw):
    spec = {"raw_text": raw, "required_skills": [], "title": "x", "deadline": ""}
    found = [f for f in reviewer.review(conn, spec, "resume text") if f["check"] == "selection-criteria"]
    assert len(found) == 1
    return found[0]


def test_the_reviewer_names_a_pitch_correctly(conn):
    msg = _criteria_finding(conn, NARRATIVE_PD)["msg"]
    assert "2-page supporting statement / pitch" in msg


def test_the_reviewer_still_names_an_itemised_statement_as_before(conn):
    msg = _criteria_finding(conn, ITEMISED_PD)["msg"]
    assert "separate statement addressing the selection criteria" in msg


def test_board_progress_counts_ticked_coverage_boxes(tmp_path, monkeypatch):
    pkg = tmp_path / "job_a"
    pkg.mkdir()
    (pkg / "selection_criteria.md").write_text(
        "- [x] **C1** first criterion text here\n"
        "- [ ] **C2** second criterion text here\n"
        "- [X] **C3** third criterion text here\n"
        "- [ ] **C4** fourth criterion text here\n", encoding="utf-8")
    monkeypatch.setattr(webapp, "PKG", tmp_path)
    assert webapp._criteria_progress("job_a") == (2, 4)


def test_board_progress_for_an_itemised_reply_is_unchanged(tmp_path, monkeypatch):
    pkg = tmp_path / "job_b"
    pkg.mkdir()
    body = " ".join(["word"] * 40)
    (pkg / "selection_criteria_FINAL.md").write_text(
        f"## 1. First criterion\n\n{body}\n\n## 2. Second criterion\n\n{body}\n", encoding="utf-8")
    monkeypatch.setattr(webapp, "PKG", tmp_path)
    assert webapp._criteria_progress("job_b") == (2, 2)


# ---------------------------- a criterion that opens with "Current" is a criterion ----

@pytest.mark.parametrize("item", [
    "Current registration with the relevant professional board.",
    "Current driver's licence and willingness to travel.",
    "Current Working with Vulnerable People card or the ability to obtain one.",
])
def test_a_criterion_that_starts_with_current_is_kept(item):
    """It used to be dropped as if it were 'a current CV must be uploaded'."""
    text = f"SELECTION CRITERIA\n1. {item}\n2. Ability to communicate clearly in writing.\n"
    assert selection.extract_criteria(text)[0] == item


@pytest.mark.parametrize("item", [
    "A current CV must be uploaded with your application.",
    "Current curriculum vitae including two referees.",
    "Cover letter of no more than one page.",
    "Statement addressing the selection criteria.",
])
def test_genuine_upload_instructions_are_still_filtered(item):
    text = f"SELECTION CRITERIA\n1. Demonstrated ability to run a program of work.\n2. {item}\n"
    found = selection.extract_criteria(text)
    assert len(found) == 1 and "run a program" in found[0]
