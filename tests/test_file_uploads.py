"""Tests for choosing the right file input.

Found on a real SmartRecruiters page. It labels its avatar control "Upload
profile image" and its resume control not at all, so the engine uploaded the
resume PDF into the profile photo field and left the real upload empty. The
label is the weaker signal here; the accept list is the decisive one.
"""

from __future__ import annotations

import pytest

from resume_filler.extractors import fields_from_html
from resume_filler.field_map import accept_kind, match_form, plan_fill
from resume_filler.models import FillStatus, FormField

SMARTRECRUITERS_FORM = """<!doctype html>
<html><body>
<form>
  <!-- The resume upload carries no label at all, only an accept list. -->
  <input tabindex="-1" type="file" id="file-input"
         accept=".doc, .dot, .rmr, .rsm, .resume, .pdf, .rtf, .pages, .docx, .odt">

  <!-- The avatar control is the one with a helpful-sounding label. -->
  <input type="file" id="avatar-input" aria-label="Upload profile image"
         accept=".bmp,.gif,.jfif,.jpeg,.jpg,.png,.tif,.tiff,.webp">

  <input id="first-name-input" name="first-name-input" type="text" required>
  <input id="last-name-input" name="last-name-input" type="text" required>
  <input id="email-input" name="email-input" type="text" required>

  <!-- A phone dialling-code picker, not a state field. -->
  <input id="country-code" aria-label="Search by country/region or code" type="text">
  <input id="phone" aria-label="Phone number" type="text" required>
</form>
</body></html>
"""


@pytest.fixture
def form_fields():
    return fields_from_html(SMARTRECRUITERS_FORM)


class TestAcceptKind:
    @pytest.mark.parametrize(
        "accept",
        [
            ".bmp,.gif,.jfif,.jpeg,.jpg,.png,.tif,.tiff,.webp",
            "image/*",
            ".png, .jpg",
        ],
    )
    def test_image_lists_are_classified(self, accept: str) -> None:
        assert accept_kind(accept) == "image"

    @pytest.mark.parametrize(
        "accept",
        [
            ".doc, .pdf, .docx",
            "application/pdf",
            ".pdf",
            ".doc, .dot, .rmr, .rsm, .resume, .pdf",
        ],
    )
    def test_document_lists_are_classified(self, accept: str) -> None:
        assert accept_kind(accept) == "document"

    @pytest.mark.parametrize("accept", ["", "   ", ".zip,.csv"])
    def test_unknown_lists_stay_unknown(self, accept: str) -> None:
        assert accept_kind(accept) == ""

    def test_a_mixed_list_counts_as_document(self) -> None:
        """A control taking both is still somewhere a resume can go."""
        assert accept_kind(".pdf,.png") == "document"


class TestFileUploadTargeting:
    def test_the_accept_list_is_captured(self, form_fields) -> None:
        by_id = {f.element_id: f for f in form_fields}
        assert ".pdf" in by_id["file-input"].accept
        assert ".png" in by_id["avatar-input"].accept

    def test_an_unlabelled_document_input_is_recognised(self, form_fields) -> None:
        """No label, no name, no placeholder. Only the accept list says resume."""
        result = {m.form_field.element_id: m.canonical for m in match_form(form_fields)}
        assert result["file-input"] == "resume_file"

    def test_the_profile_image_never_takes_the_resume(self, form_fields, resume) -> None:
        """The bug this exists to prevent: resume PDF uploaded as an avatar."""
        matches = {
            m.form_field.element_id: m
            for m in plan_fill(form_fields, resume, resume_path="/tmp/cv.pdf")
        }
        avatar = matches["avatar-input"]
        assert avatar.canonical != "resume_file"
        assert avatar.status is not FillStatus.FILLED
        assert not avatar.value

    def test_an_image_input_is_rejected_even_when_labelled_resume(self) -> None:
        """The accept list outranks a misleading label in both directions."""
        field = FormField(
            tag="input",
            field_type="file",
            label="Upload your resume",
            accept="image/*",
        )
        assert match_form([field])[0].canonical == ""

    def test_a_document_input_labelled_photo_is_still_not_the_avatar(self) -> None:
        """Belt and braces: the label negatives also block image words."""
        field = FormField(tag="input", field_type="file", label="Profile photo", accept=".png,.jpg")
        assert match_form([field])[0].canonical == ""

    def test_the_resume_reaches_the_real_upload(self, form_fields, resume) -> None:
        matches = {
            m.form_field.element_id: m
            for m in plan_fill(form_fields, resume, resume_path="/tmp/cv.pdf")
        }
        assert matches["file-input"].value == "/tmp/cv.pdf"
        assert matches["file-input"].status is FillStatus.FILLED


class TestPhoneCountryCodePicker:
    def test_a_dialling_code_picker_is_not_the_state_field(self, form_fields) -> None:
        """'Search by country/region or code' matched state on the word
        "region", typing the applicant's state into a phone widget."""
        result = {m.form_field.element_id: m.canonical for m in match_form(form_fields)}
        assert result["country-code"] != "state"

    def test_the_real_phone_field_still_maps(self, form_fields) -> None:
        result = {m.form_field.element_id: m.canonical for m in match_form(form_fields)}
        assert result["phone"] == "phone"

    def test_a_genuine_state_field_is_unaffected(self) -> None:
        for label in ("State", "State/Province", "Province"):
            field = FormField(tag="input", label=label)
            assert match_form([field])[0].canonical == "state", label
