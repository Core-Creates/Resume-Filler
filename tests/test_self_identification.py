"""Tests for recognising self-identification options and essay prompts.

Found on a real Ashby page. Its demographic questions are a bare list of radios
with no group label at all, so the only text available is the option itself:
"Man", "Woman", "Under 30". Matching on the question wording left every one of
them merely unrecognised, which loses the reason they must not be answered
automatically.
"""

from __future__ import annotations

import pytest

from resume_filler.field_map import match_form, plan_fill
from resume_filler.models import FillStatus, FormField


def choice(label: str) -> FormField:
    return FormField(tag="input", field_type="radio", label=label)


class TestSelfIdentificationOptions:
    @pytest.mark.parametrize(
        "label",
        [
            "Man",
            "Woman",
            "Non-Binary",
            "Another Gender Identity",
            "I prefer not to answer",
            "Decline to self identify",
        ],
    )
    def test_gender_options_are_flagged(self, label: str) -> None:
        match = match_form([choice(label)])[0]
        assert match.canonical in {"demographic", "demographic_option"}, label
        assert match.status is FillStatus.SKIPPED_BY_POLICY

    @pytest.mark.parametrize("label", ["Under 30", "30-39", "40-49", "60 or older"])
    def test_age_bands_are_flagged(self, label: str) -> None:
        """Age is a protected characteristic and the bands name it nowhere."""
        match = match_form([choice(label)])[0]
        assert match.canonical == "demographic_option", label
        assert match.status is FillStatus.SKIPPED_BY_POLICY

    def test_hyphenated_ranges_survive_normalisation(self) -> None:
        """Normalisation strips punctuation, so "30-39" arrives as "30 39"."""
        assert match_form([choice("30-39")])[0].canonical == "demographic_option"

    @pytest.mark.parametrize(
        "label",
        [
            "Hispanic or Latino",
            "Black or African American",
            "Two or More Races",
            "American Indian or Alaska Native",
            "I identify as a protected veteran",
        ],
    )
    def test_race_and_veteran_options_are_flagged(self, label: str) -> None:
        match = match_form([choice(label)])[0]
        assert match.canonical in {"demographic", "demographic_option"}, label

    def test_they_are_never_filled(self, resume) -> None:
        fields = [choice(o) for o in ("Man", "Woman", "Under 30", "I prefer not to answer")]
        for match in plan_fill(fields, resume):
            assert match.status is FillStatus.SKIPPED_BY_POLICY
            assert not match.value

    def test_many_options_may_be_flagged_at_once(self) -> None:
        """A form asks several of these; one-to-one assignment must not stop at
        the first."""
        fields = [choice(o) for o in ("Man", "Woman", "Non-Binary", "Under 30", "30-39")]
        flagged = [m for m in match_form(fields) if m.canonical == "demographic_option"]
        assert len(flagged) == len(fields)


class TestOptionPatternsAreScopedToChoices:
    """These phrasings are only unambiguous on a bare radio or checkbox."""

    def test_a_text_field_is_not_matched(self) -> None:
        field = FormField(tag="input", field_type="text", label="Man")
        assert match_form([field])[0].canonical != "demographic_option"

    def test_a_normal_text_field_is_unaffected(self) -> None:
        field = FormField(tag="input", field_type="text", label="First Name")
        assert match_form([field])[0].canonical == "first_name"

    def test_a_select_may_still_match(self) -> None:
        field = FormField(tag="select", label="I prefer not to answer")
        assert match_form([field])[0].canonical == "demographic_option"


class TestEssayPrompts:
    @pytest.mark.parametrize(
        "label",
        [
            "Describe a role you were in that brought out your absolute best work",
            "Describe a choice you made when shipping a product",
            "What's something, in a professional context, you've gone deep on",
            "Walk us through a technical decision you regret",
            "Give us an example of a time you disagreed with a manager",
        ],
    )
    def test_prompts_are_left_to_the_applicant(self, label: str) -> None:
        field = FormField(tag="textarea", label=label)
        match = match_form([field])[0]
        assert match.canonical == "free_text_question", label
        assert match.status is FillStatus.SKIPPED_BY_POLICY

    def test_an_unusual_prompt_is_caught_by_shape(self) -> None:
        """Employers word these however they like, so a pattern list will always
        trail behind. A long textarea label is the reliable signal."""
        field = FormField(
            tag="textarea",
            label="If you could redesign any everyday object, which and why would you?",
        )
        assert match_form([field])[0].canonical == "free_text_question"

    def test_a_question_mark_alone_is_enough(self) -> None:
        field = FormField(tag="textarea", label="Your proudest work?")
        assert match_form([field])[0].canonical == "free_text_question"

    def test_a_short_labelled_textarea_is_not_assumed_to_be_an_essay(self) -> None:
        field = FormField(tag="textarea", label="Notes")
        assert match_form([field])[0].canonical == ""

    def test_the_shape_rule_does_not_apply_to_text_inputs(self) -> None:
        """A long label on a single line input is a normal question, not an essay."""
        field = FormField(
            tag="input", field_type="text", label="Please provide your full legal name as written"
        )
        assert match_form([field])[0].canonical != "free_text_question"
