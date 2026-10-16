"""Selection criteria must be found, whole and word for word, in PDs laid out
differently from the ones the parser was first written against.

One real PD produced *no* criteria at all, and the system fell back to an empty
template. Three independent things caused it, and each is pinned here:

  1. five lines of instructions between the heading and the first criterion
     (the parser gave up after three);
  2. a running footer that differs only by its page number, so it was never
     recognised as a footer, landed after a blank line mid-list, and ended the
     list early — nine criteria came out as two;
  3. group labels ("Skills", "Behaviour", "Knowledge") sitting directly under
     the previous criterion, which were glued onto its text.

A fourth, older defect turned up while checking the result against the PDF: a
comma at the end of a wrapped line was dropped ("Excel, PowerPoint" became
"Excel PowerPoint"), quietly breaking the promise that criteria are verbatim.

The PD below is invented; it only reproduces the layout.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import jobdoc, selection  # noqa: E402

HEADER = "Example Branch Position Description | Example Agency  {n}"

PD = f"""POSITION DESCRIPTION
Position title: Grants Support Officer
Classification: Grade 4

{HEADER.format(n=1)}
DUTIES
1. Provide administrative support to the program team.
2. Maintain accurate records.

{HEADER.format(n=2)}
SELECTION CRITERIA (CAPABILITIES)
Your suitability for this position will be assessed based on your skills,
knowledge and behaviour in relation to the duties listed above.
To apply, write a two-page supporting statement that demonstrates how you
can undertake the position by addressing the criteria below.

Skills
1. Demonstrated ability to deliver quality service to stakeholders and
members of the public.
2. Demonstrated sound planning skills, including the ability to adapt to
changing situations and work to tight timelines.

{HEADER.format(n=3)}
3. Ability to communicate effectively and prepare general reports.
4. Demonstrated competency in Microsoft applications, including Word, Excel,
PowerPoint and Outlook.
Behaviour
1. Ability to help others and contribute to a positive team culture.
2. Attention to detail and a high degree of accuracy.
Knowledge
1. Demonstrated capability in administrative program support functions.
2. An interest in the sector would be well regarded but not essential.

COMPLIANCE REQUIREMENTS
Desirable:
• A current driver's licence may be required.

{HEADER.format(n=4)}
"""

EXPECTED = [
    "Demonstrated ability to deliver quality service to stakeholders and members of the public.",
    "Demonstrated sound planning skills, including the ability to adapt to changing situations and work to tight timelines.",
    "Ability to communicate effectively and prepare general reports.",
    "Demonstrated competency in Microsoft applications, including Word, Excel, PowerPoint and Outlook.",
    "Ability to help others and contribute to a positive team culture.",
    "Attention to detail and a high degree of accuracy.",
    "Demonstrated capability in administrative program support functions.",
    "An interest in the sector would be well regarded but not essential.",
]


def _through_the_pipeline(text: str) -> list[str]:
    """Extract as the upload path does: tidy first, then find the criteria."""
    return selection.extract_criteria(jobdoc.tidy(text))


# --------------------------------------------------- the whole document ----

def test_every_criterion_is_found_word_for_word():
    assert _through_the_pipeline(PD) == EXPECTED


def test_numbering_that_restarts_in_each_group_is_fine():
    found = _through_the_pipeline(PD)
    assert len(found) == 8, "three groups, each numbered from 1, are one list"


# ----------------------------------------------------- cause 1: lead-in ----

def test_five_lines_of_instructions_before_the_list_do_not_hide_it():
    text = ("SELECTION CRITERIA\n"
            "Line one of the instructions.\nLine two of the instructions.\n"
            "Line three of the instructions.\nLine four of the instructions.\n"
            "Line five of the instructions.\n\n"
            "1. Demonstrated ability to run a program of work.\n"
            "2. Ability to communicate clearly in writing.\n")
    assert len(selection.extract_criteria(text)) == 2


def test_the_lead_in_window_is_still_bounded():
    """Prose that merely mentions the phrase must not reach a bullet list far
    below and adopt it."""
    filler = "\n".join(f"Unrelated sentence number {i} about something else." for i in range(30))
    text = ("Applicants should address the selection criteria below.\n"
            "SELECTION CRITERIA\n" + filler + "\n"
            "- A bullet that has nothing to do with the heading above.\n")
    assert selection.extract_criteria(text) == []


# ------------------------------------------------ cause 2: page furniture ----

def test_a_footer_differing_only_by_page_number_is_removed():
    text = "\n".join([HEADER.format(n=n) + f"\nbody text that is unique to page {n}" for n in range(1, 6)])
    out = jobdoc.tidy(text)
    assert "Example Branch Position Description" not in out
    for n in range(1, 6):
        assert f"body text that is unique to page {n}" in out


def test_a_footer_in_the_middle_of_the_list_does_not_end_the_list():
    """It sat after a blank line, which the parser reads as 'the list is over'."""
    found = _through_the_pipeline(PD)
    assert EXPECTED[2] in found and EXPECTED[3] in found, "criteria after the footer were lost"


def test_body_text_that_ends_in_a_number_is_never_swept_away():
    """The first version of this fix deleted these: a long line repeated with
    only its trailing number changing looked exactly like a running footer, and
    the whole page vanished. An inline number is body text; only a number set
    off by a layout gap counts as a page number."""
    text = "\n".join(f"Requirement statement for the selected candidate number {n}" for n in range(1, 6))
    out = jobdoc.tidy(text)
    for n in range(1, 6):
        assert f"candidate number {n}" in out


def test_short_numbered_rows_are_content_not_furniture():
    """'Level 1 / Level 2 / Level 3' must never be counted as one repeated line."""
    text = "Level 1\nLevel 2\nLevel 3\nLevel 4\nAn actual sentence of content."
    out = jobdoc.tidy(text)
    for row in ("Level 1", "Level 2", "Level 3", "Level 4"):
        assert row in out


def test_a_genuine_repeated_long_line_is_still_treated_as_furniture():
    line = "Confidential position description for internal recruitment use only"
    out = jobdoc.tidy("\n".join([line, "body one", line, "body two", line, "body three"]))
    assert line not in out


# ------------------------------------------------ cause 3: group labels ----

def test_group_labels_are_not_glued_onto_the_criterion_above():
    for c in _through_the_pipeline(PD):
        assert not c.endswith(("Behaviour", "Knowledge", "Skills")), c


def test_a_wrapped_fragment_is_not_mistaken_for_a_group_label():
    """'PowerPoint and Outlook' is short, capitalised and unpunctuated — but it
    follows a line ending in a comma, so it continues the criterion."""
    text = ("SELECTION CRITERIA\n"
            "1. Demonstrated competency in Microsoft applications, including Word, Excel,\n"
            "PowerPoint and Outlook\n"
            "2. Ability to communicate clearly in writing and in person.\n")
    found = selection.extract_criteria(text)
    assert found[0].endswith("Excel, PowerPoint and Outlook")
    assert len(found) == 2


def test_a_short_line_is_only_dropped_as_a_label_when_an_item_follows():
    """The rule must never silently discard text. 'Notes' is short and
    capitalised, but prose follows it rather than a list item, so it is left
    alone (and keeps its existing treatment as a wrapped continuation)."""
    text = ("SELECTION CRITERIA\n"
            "1. Demonstrated ability to run a program of work.\n"
            "Notes\n"
            "some trailing prose that is not a criterion at all\n")
    found = selection.extract_criteria(text)
    assert len(found) == 1 and "Notes" in found[0]


# ------------------------------------------ the older bug: dropped commas ----

def test_a_comma_at_the_end_of_a_wrapped_line_is_kept():
    text = ("SELECTION CRITERIA\n"
            "1. Skills in Word, Excel,\n"
            "PowerPoint and Outlook.\n")
    assert selection.extract_criteria(text) == ["Skills in Word, Excel, PowerPoint and Outlook."]


@pytest.mark.parametrize("marker", [";", ","])
def test_a_list_separator_after_a_single_line_item_is_still_trimmed(marker):
    text = f"SELECTION CRITERIA\n- Demonstrated ability to run a program{marker}\n- Ability to write clearly and concisely{marker}\n"
    found = selection.extract_criteria(text)
    assert found == ["Demonstrated ability to run a program",
                     "Ability to write clearly and concisely"]


# ---------------------------------------------- behaviour that must not move ----

def test_a_list_still_ends_at_blank_line_then_prose():
    text = ("SELECTION CRITERIA\n"
            "1. Demonstrated ability to run a program of work.\n"
            "2. Ability to communicate clearly in writing.\n\n"
            "Other notes follow here and are not criteria.\n"
            "3. A stray line that should not be picked up as a criterion.\n")
    assert len(selection.extract_criteria(text)) == 2


def test_desirable_items_after_the_list_are_not_absorbed():
    found = _through_the_pipeline(PD)
    assert not any("driver" in c.lower() for c in found)


def test_upload_instructions_are_still_not_criteria():
    text = ("SELECTION CRITERIA\n"
            "1. Demonstrated ability to run a program of work.\n"
            "2. A current CV and a cover letter must be uploaded.\n")
    assert len(selection.extract_criteria(text)) == 1
