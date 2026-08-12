"""Tests for the profile supplement and for duplicated controls.

Both came from analysing what was still unfilled across four real application
forms. Street address was the one field every form demanded and no resume
carried, and iCIMS asks for name and email twice, once to register an account
and once for the profile.
"""

from __future__ import annotations

import json

import pytest

from resume_filler.extractors import fields_from_html
from resume_filler.field_map import match_form, plan_fill
from resume_filler.models import FillStatus, FormField
from resume_filler.profile import Profile, load_profile

DUPLICATED_FORM = """<html><body><form>
  <fieldset><legend>Create an account</legend>
    <label for="reg_first">First Name</label><input id="reg_first" required>
    <label for="reg_last">Last Name</label><input id="reg_last" required>
    <label for="login">Login</label><input id="login" required>
    <label for="pw">Password</label><input id="pw" type="password" required>
  </fieldset>
  <fieldset><legend>Your profile</legend>
    <label for="p_first">First Name</label><input id="p_first" required>
    <label for="p_last">Last Name</label><input id="p_last" required>
    <label for="p_addr">Address</label><input id="p_addr" required>
  </fieldset>
</form></body></html>"""


class TestDuplicatedControls:
    @pytest.fixture
    def matches(self, resume):
        return {
            m.form_field.element_id: m for m in plan_fill(fields_from_html(DUPLICATED_FORM), resume)
        }

    def test_both_copies_of_a_repeated_question_are_filled(self, matches) -> None:
        """One to one assignment left the second copy blank, and both were
        marked required, so the form could not be submitted."""
        assert matches["reg_first"].value == "Jane"
        assert matches["p_first"].value == "Jane"
        assert matches["reg_last"].value == "Rivera"
        assert matches["p_last"].value == "Rivera"

    def test_the_duplicate_gets_the_same_canonical(self, matches) -> None:
        assert matches["p_first"].canonical == "first_name"

    def test_credentials_are_still_never_filled(self, matches) -> None:
        """A password field must not inherit anything, duplicate rule or not."""
        for element_id in ("login", "pw"):
            assert matches[element_id].status is not FillStatus.FILLED
            assert not matches[element_id].value

    def test_different_questions_are_not_treated_as_duplicates(self, resume) -> None:
        fields = fields_from_html(
            "<form>"
            "<label for='a'>First Name</label><input id='a'>"
            "<label for='b'>Last Name</label><input id='b'>"
            "</form>"
        )
        result = {m.form_field.element_id: m.canonical for m in plan_fill(fields, resume)}
        assert result["a"] == "first_name"
        assert result["b"] == "last_name"

    def test_file_inputs_are_never_duplicated(self, resume) -> None:
        """Two document uploads are usually resume and cover letter, not the
        same field twice, so the same file must not go to both."""
        fields = fields_from_html(
            "<form>"
            "<input type='file' id='f1' accept='.pdf'>"
            "<input type='file' id='f2' accept='.pdf'>"
            "</form>"
        )
        matches = plan_fill(fields, resume, resume_path="/tmp/cv.pdf")
        filled = [m for m in matches if m.status is FillStatus.FILLED]
        assert len(filled) == 1

    def test_a_weak_match_is_not_propagated(self) -> None:
        """Only confident assignments are worth copying onto a twin."""
        weak = FormField(tag="input", label="Attach")
        other = FormField(tag="input", label="Attach")
        canonicals = [m.canonical for m in match_form([weak, other])]
        assert canonicals.count("resume_file") <= 1


class TestProfileLoading:
    def test_a_missing_file_is_not_an_error(self, tmp_path) -> None:
        """Having no profile is the normal starting state."""
        profile = load_profile(tmp_path / "absent.json")
        assert not profile
        assert profile.get("address_line1") == ""

    def test_values_are_read(self, tmp_path) -> None:
        path = tmp_path / "profile.json"
        path.write_text(json.dumps({"address_line1": "1 Example St"}), encoding="utf-8")
        assert load_profile(path).get("address_line1") == "1 Example St"

    def test_blank_values_are_dropped(self, tmp_path) -> None:
        """An empty string means "I have not answered", not "answer with blank"."""
        path = tmp_path / "profile.json"
        path.write_text(json.dumps({"desired_salary": "", "city": "  "}), encoding="utf-8")
        profile = load_profile(path)
        assert not profile

    def test_malformed_json_raises_rather_than_silently_ignoring(self, tmp_path) -> None:
        path = tmp_path / "profile.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_profile(path)

    def test_a_non_object_raises(self, tmp_path) -> None:
        path = tmp_path / "profile.json"
        path.write_text('["a", "b"]', encoding="utf-8")
        with pytest.raises(ValueError, match="JSON object"):
            load_profile(path)

    def test_unrecognised_keys_are_reported(self, tmp_path) -> None:
        """A typo would otherwise look like the profile had no effect."""
        path = tmp_path / "profile.json"
        path.write_text(json.dumps({"adress_line1": "typo"}), encoding="utf-8")
        assert load_profile(path).unknown_keys() == ["adress_line1"]


class TestProfileFilling:
    def test_it_supplies_what_the_resume_cannot(self, resume) -> None:
        """Street address is required nearly everywhere and appears on no resume."""
        fields = fields_from_html("<form><label for='a'>Address</label><input id='a'></form>")
        profile = Profile({"address_line1": "1 Example St"})
        match = plan_fill(fields, resume, profile=profile)[0]
        assert match.value == "1 Example St"
        assert match.status is FillStatus.FILLED

    def test_the_source_is_visible_in_the_plan(self, resume) -> None:
        fields = fields_from_html("<form><label for='a'>Address</label><input id='a'></form>")
        match = plan_fill(fields, resume, profile=Profile({"address_line1": "1 St"}))[0]
        assert match.reason == "From your profile."

    def test_it_may_answer_a_question_the_engine_refuses_to_guess(self, resume) -> None:
        """The policy stops the tool inventing a legal declaration. It does not
        stop the applicant making one."""
        fields = fields_from_html(
            "<form><label for='a'>Are you legally authorized to work in the US?</label>"
            "<input id='a'></form>"
        )
        without = plan_fill(fields, resume)[0]
        assert without.status is FillStatus.SKIPPED_BY_POLICY

        with_profile = plan_fill(fields, resume, profile=Profile({"work_authorization": "Yes"}))[0]
        assert with_profile.status is FillStatus.FILLED
        assert with_profile.value == "Yes"

    def test_an_unanswered_policy_field_is_still_withheld(self, resume) -> None:
        fields = fields_from_html(
            "<form><label for='a'>Desired Salary</label><input id='a'></form>"
        )
        match = plan_fill(fields, resume, profile=Profile({"address_line1": "1 St"}))[0]
        assert match.status is FillStatus.SKIPPED_BY_POLICY

    def test_no_profile_behaves_exactly_as_before(self, greenhouse_html, resume) -> None:
        fields = fields_from_html(greenhouse_html)
        assert [m.status for m in plan_fill(fields, resume)] == [
            m.status for m in plan_fill(fields, resume, profile=Profile())
        ]

    def test_a_gap_names_the_key_to_add(self) -> None:
        """The plan should tell the applicant how to fix the gap."""
        from resume_filler.models import ResumeData

        no_address = ResumeData(first_name="Jane", last_name="Rivera")
        fields = fields_from_html("<form><label for='a'>Address</label><input id='a'></form>")
        match = plan_fill(fields, no_address)[0]
        assert match.status is FillStatus.SKIPPED_NO_VALUE
        assert "address_line1" in match.reason
        assert "profile" in match.reason.lower()
