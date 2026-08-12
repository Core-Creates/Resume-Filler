"""Tests for the field mapping engine.

These run against saved HTML fixtures, so they exercise the real matching path
end to end without a browser, a network connection, or a driver binary.
"""

from __future__ import annotations

import pytest

from resume_filler.extractors import fields_from_html
from resume_filler.field_map import (
    CANONICAL_FIELDS,
    FillPolicy,
    match_form,
    normalize,
    plan_fill,
    resolve_value,
    score_field,
)
from resume_filler.models import FillStatus, FormField


def mapping(matches) -> dict[str, str]:
    """Map each control's identifying name to the canonical field it received."""
    result = {}
    for match in matches:
        key = match.form_field.name or match.form_field.element_id
        result[key] = match.canonical
    return result


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("firstName", "first name"),
            ("first_name", "first name"),
            ("First Name *", "first name"),
            ("job_application[first_name]", "job application first name"),
            ("E-Mail Address", "e mail address"),
            ("", ""),
        ],
    )
    def test_normalizes_identifier_styles(self, raw: str, expected: str) -> None:
        assert normalize(raw) == expected


class TestScoring:
    def test_autocomplete_token_wins_outright(self) -> None:
        field = FormField(tag="input", name="xyz_9f2", autocomplete="given-name")
        canonical = next(f for f in CANONICAL_FIELDS if f.name == "first_name")
        assert score_field(field, canonical) == 1.0

    def test_label_outranks_name_attribute(self) -> None:
        canonical = next(f for f in CANONICAL_FIELDS if f.name == "email")
        by_label = FormField(tag="input", label="Email")
        by_name = FormField(tag="input", name="email")
        assert score_field(by_label, canonical) > score_field(by_name, canonical)

    def test_negative_pattern_disqualifies(self) -> None:
        canonical = next(f for f in CANONICAL_FIELDS if f.name == "email")
        confirm = FormField(tag="input", label="Confirm Email Address")
        assert score_field(confirm, canonical) == 0.0

    def test_unrecognised_field_scores_zero_everywhere(self) -> None:
        field = FormField(tag="input", label="Favourite dinosaur")
        assert all(score_field(field, canonical) == 0.0 for canonical in CANONICAL_FIELDS)


class TestGreenhouseForm:
    def test_extracts_only_fillable_controls(self, greenhouse_html: str) -> None:
        fields = fields_from_html(greenhouse_html)
        # The hidden authenticity token and the submit button must not appear.
        assert all(f.field_type not in {"hidden", "submit"} for f in fields)
        assert any(f.element_id == "first_name" for f in fields)

    def test_maps_standard_contact_fields(self, greenhouse_html: str) -> None:
        result = mapping(match_form(fields_from_html(greenhouse_html)))
        assert result["job_application[first_name]"] == "first_name"
        assert result["job_application[last_name]"] == "last_name"
        assert result["job_application[email]"] == "email"
        assert result["job_application[phone]"] == "phone"

    def test_distinguishes_resume_from_cover_letter_upload(self, greenhouse_html: str) -> None:
        result = mapping(match_form(fields_from_html(greenhouse_html)))
        assert result["job_application[resume]"] == "resume_file"
        assert result["job_application[cover_letter]"] == "cover_letter"

    def test_maps_opaque_custom_question_ids_by_label(self, greenhouse_html: str) -> None:
        """Greenhouse names custom questions answers_attributes[N]. Only the label helps."""
        matches = {
            m.form_field.element_id: m.canonical
            for m in match_form(fields_from_html(greenhouse_html))
        }
        assert matches["question_12345"] == "linkedin_url"
        assert matches["question_12346"] == "portfolio_url"
        assert matches["question_12347"] == "current_company"

    def test_reads_labels_from_fieldset_legend(self, greenhouse_html: str) -> None:
        fields = fields_from_html(greenhouse_html)
        work_auth = [f for f in fields if f.name == "work_auth"]
        assert work_auth, "radio group not found"
        assert "legally authorized" in work_auth[0].label.lower()

    def test_required_flag_is_captured(self, greenhouse_html: str) -> None:
        fields = {f.element_id: f for f in fields_from_html(greenhouse_html)}
        assert fields["first_name"].required is True
        assert fields["phone"].required is False


class TestTrickyForm:
    def test_confirm_email_does_not_steal_the_email_slot(self, tricky_html: str) -> None:
        result = mapping(match_form(fields_from_html(tricky_html)))
        assert result["e1"] == "email"
        assert result["e2"] == "confirm_email"

    def test_company_name_is_not_the_applicant_name(self, tricky_html: str) -> None:
        result = mapping(match_form(fields_from_html(tricky_html)))
        assert result["co"] == "current_company"
        assert result["applicant"] == "full_name"

    def test_salutation_title_is_not_a_job_title(self, tricky_html: str) -> None:
        result = mapping(match_form(fields_from_html(tricky_html)))
        assert result["salutation"] != "current_title"
        assert result["jt"] == "current_title"

    def test_country_code_is_not_the_phone_number(self, tricky_html: str) -> None:
        result = mapping(match_form(fields_from_html(tricky_html)))
        assert result["ccode"] != "phone"
        assert result["mobile"] == "phone"

    def test_address_line_2_does_not_claim_the_street(self, tricky_html: str) -> None:
        result = mapping(match_form(fields_from_html(tricky_html)))
        assert result["addr1"] == "address_line1"
        assert result["addr2"] != "address_line1"

    def test_aria_labelledby_resolves(self, tricky_html: str) -> None:
        fields = {f.name: f for f in fields_from_html(tricky_html)}
        assert fields["applicant"].label == "Full Name"

    def test_wrapping_label_resolves(self, tricky_html: str) -> None:
        fields = {f.name: f for f in fields_from_html(tricky_html)}
        assert "Email Address" in fields["e1"].label

    def test_unrecognised_field_is_reported_not_guessed(self, tricky_html: str) -> None:
        matches = {m.form_field.name: m for m in match_form(fields_from_html(tricky_html))}
        assert matches["ref"].status is FillStatus.SKIPPED_NO_MATCH
        assert matches["ref"].canonical == ""


class TestPolicy:
    def test_demographics_are_never_auto_filled(self, greenhouse_html: str) -> None:
        matches = {
            m.form_field.element_id: m for m in match_form(fields_from_html(greenhouse_html))
        }
        for element_id in ("gender", "veteran_status"):
            assert matches[element_id].canonical == "demographic"
            assert matches[element_id].status is FillStatus.SKIPPED_BY_POLICY

    def test_salary_and_essays_are_left_to_the_human(self, tricky_html: str, resume) -> None:
        matches = {m.form_field.name: m for m in plan_fill(fields_from_html(tricky_html), resume)}
        assert matches["sal"].status is FillStatus.SKIPPED_BY_POLICY
        assert matches["why"].status is FillStatus.SKIPPED_BY_POLICY

    def test_review_only_fields_may_repeat(self, greenhouse_html: str) -> None:
        """A form can ask several demographic questions. Each must be recognised."""
        matches = match_form(fields_from_html(greenhouse_html))
        demographic = [m for m in matches if m.canonical == "demographic"]
        assert len(demographic) >= 2

    def test_auto_fields_are_claimed_only_once(self, greenhouse_html: str) -> None:
        matches = match_form(fields_from_html(greenhouse_html))
        auto_names = [
            m.canonical
            for m in matches
            if m.canonical
            and next(f for f in CANONICAL_FIELDS if f.name == m.canonical).policy is FillPolicy.AUTO
        ]
        assert len(auto_names) == len(set(auto_names))


class TestPlanFill:
    def test_values_come_from_the_resume(self, greenhouse_html: str, resume) -> None:
        matches = {m.canonical: m for m in plan_fill(fields_from_html(greenhouse_html), resume)}
        assert matches["first_name"].value == "Jane"
        assert matches["email"].value == "jane.rivera@example.com"
        assert matches["current_company"].value == "Northwind Systems"
        assert matches["first_name"].status is FillStatus.FILLED

    def test_resume_upload_receives_the_file_path(self, greenhouse_html: str, resume) -> None:
        matches = {
            m.canonical: m
            for m in plan_fill(fields_from_html(greenhouse_html), resume, resume_path="/tmp/cv.pdf")
        }
        assert matches["resume_file"].value == "/tmp/cv.pdf"

    def test_missing_resume_data_is_a_reported_gap_not_a_guess(self, tricky_html: str) -> None:
        from resume_filler.models import ResumeData

        empty = ResumeData(first_name="Jane", last_name="Rivera")
        matches = {m.canonical: m for m in plan_fill(fields_from_html(tricky_html), empty)}
        assert matches["email"].status is FillStatus.SKIPPED_NO_VALUE
        assert matches["email"].value == ""
        assert "did not supply" in matches["email"].reason

    def test_threshold_controls_aggressiveness(self, tricky_html: str, resume) -> None:
        fields = fields_from_html(tricky_html)
        strict = plan_fill(fields, resume, threshold=0.99)
        loose = plan_fill(fields, resume, threshold=0.5)
        strict_filled = sum(1 for m in strict if m.status is FillStatus.FILLED)
        loose_filled = sum(1 for m in loose if m.status is FillStatus.FILLED)
        assert loose_filled > strict_filled


class TestResolveValue:
    def test_confirm_email_reuses_the_email_value(self, resume) -> None:
        assert resolve_value("confirm_email", resume) == resume.email

    def test_unknown_canonical_returns_empty(self, resume) -> None:
        assert resolve_value("not_a_field", resume) == ""

    def test_education_fields_use_the_latest_entry(self, resume) -> None:
        assert resolve_value("school", resume) == "The University of Texas at Austin"
        assert resolve_value("graduation_year", resume) == "2015"
