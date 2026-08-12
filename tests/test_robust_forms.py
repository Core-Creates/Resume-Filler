"""Tests for iframe traversal, multi-step wizards, and scripted dropdowns.

These three were the known gaps after the first pass: the engine found zero
fields on an embedded board, stopped after page one of a wizard, and typed into
scripted dropdowns without committing the value.

Selenium is exercised through stub objects that model the parts of the WebDriver
contract these code paths depend on, so no browser is launched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_filler import form_filler
from resume_filler.extractors import fields_from_html
from resume_filler.field_map import match_form, plan_fill
from resume_filler.models import FillStatus, FormField, JobPosting

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def workday_html() -> str:
    return (FIXTURES / "workday_step1.html").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Scripted dropdowns
# --------------------------------------------------------------------------


class TestComboboxDetection:
    def test_scripted_dropdown_is_found_at_all(self, workday_html: str) -> None:
        """A div-based combobox is a control. Scanning only input/select misses it."""
        fields = fields_from_html(workday_html)
        comboboxes = [f for f in fields if f.is_combobox]
        assert len(comboboxes) == 1
        assert comboboxes[0].label == "Country"

    def test_scripted_dropdown_is_mapped_like_any_field(self, workday_html: str) -> None:
        matches = {m.form_field.label: m for m in match_form(fields_from_html(workday_html))}
        assert matches["Country"].canonical == "country"

    def test_option_container_is_not_treated_as_a_control(self, workday_html: str) -> None:
        """The <ul role='listbox'> holds the options; it is not itself fillable."""
        fields = fields_from_html(workday_html)
        assert all(f.tag not in {"ul", "ol"} for f in fields)

    def test_options_are_captured_from_the_listbox(self, workday_html: str) -> None:
        combobox = next(f for f in fields_from_html(workday_html) if f.is_combobox)
        assert "United States" in combobox.options

    def test_native_select_is_not_flagged_as_a_combobox(self, greenhouse_html: str) -> None:
        for field in fields_from_html(greenhouse_html):
            if field.tag == "select":
                assert not field.is_combobox


class StubOption:
    def __init__(self, text: str) -> None:
        self.text = text
        self.clicked = False

    def click(self) -> None:
        self.clicked = True


class ComboboxElement:
    """Models a scripted dropdown: typing does nothing, only clicking commits."""

    def __init__(self, committed: str = "") -> None:
        self.committed = committed
        self.typed: list[str] = []
        self.opened = False

    def click(self) -> None:
        self.opened = True

    def send_keys(self, value: str) -> None:
        self.typed.append(value)

    def get_attribute(self, name: str) -> str:
        return self.committed if name == "value" else ""

    @property
    def text(self) -> str:
        return self.committed


class ComboboxDriver:
    def __init__(self, options: list[StubOption]) -> None:
        self._options = options
        self.current_url = "https://example.com/apply"

    def execute_script(self, script: str, *args: object) -> str:
        return ""

    def find_elements(self, by: object, selector: str) -> list[StubOption]:
        return self._options


class TestComboboxFilling:
    def test_selects_by_clicking_the_matching_option(self) -> None:
        options = [StubOption("Canada"), StubOption("United States")]
        driver = ComboboxDriver(options)
        element = ComboboxElement()
        match = plan_fill(
            [FormField(tag="div", label="Country", widget="combobox", handle=element)],
            _resume_with_country(),
        )[0]

        form_filler._fill_combobox(driver, match, timeout=5)

        assert element.opened, "the dropdown must be opened before selecting"
        assert options[1].clicked, "the matching option must be clicked"
        assert not element.typed, "typing into a scripted dropdown does not commit"

    def test_prefers_an_exact_option_over_a_substring(self) -> None:
        options = [StubOption("United States Minor Outlying Islands"), StubOption("United States")]
        driver = ComboboxDriver(options)
        element = ComboboxElement()
        match = plan_fill(
            [FormField(tag="div", label="Country", widget="combobox", handle=element)],
            _resume_with_country(),
        )[0]

        form_filler._fill_combobox(driver, match, timeout=5)

        assert options[1].clicked
        assert not options[0].clicked

    def test_uncommitted_selection_raises_rather_than_silently_passing(self) -> None:
        """The failure mode this exists to prevent: looks filled, submits empty."""
        driver = ComboboxDriver([])
        element = ComboboxElement(committed="")
        match = plan_fill(
            [FormField(tag="div", label="Country", widget="combobox", handle=element)],
            _resume_with_country(),
        )[0]

        with pytest.raises(ValueError, match="did not commit"):
            form_filler._fill_combobox(driver, match, timeout=5)


def _resume_with_country():
    from resume_filler.models import ResumeData

    return ResumeData(first_name="Jane", last_name="Rivera", country="United States")


# --------------------------------------------------------------------------
# iframes
# --------------------------------------------------------------------------


class FrameStubElement:
    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[str] = []

    def clear(self) -> None:
        pass

    def send_keys(self, value: str) -> None:
        self.sent.append(value)


class SwitchRecorder:
    def __init__(self, driver: FrameDriver) -> None:
        self._driver = driver

    def default_content(self) -> None:
        self._driver.context = ()
        self._driver.switches.append("top")

    def frame(self, index: int) -> None:
        self._driver.context = (*self._driver.context, index)
        self._driver.switches.append(f"frame:{index}")


class FrameDriver:
    """Tracks which browsing context the driver is in."""

    def __init__(self) -> None:
        self.context: tuple[int, ...] = ()
        self.switches: list[str] = []
        self.current_url = "https://example.com/apply"
        self.switch_to = SwitchRecorder(self)
        self.fill_contexts: list[tuple[int, ...]] = []

    def execute_script(self, script: str, *args: object) -> str:
        return ""


class TestFrameSwitching:
    def test_switch_walks_from_the_top_every_time(self) -> None:
        from resume_filler.extractors import switch_to_frame_path

        driver = FrameDriver()
        switch_to_frame_path(driver, (0, 2))
        assert driver.switches == ["top", "frame:0", "frame:2"]
        assert driver.context == (0, 2)

    def test_top_document_path_is_just_a_reset(self) -> None:
        from resume_filler.extractors import switch_to_frame_path

        driver = FrameDriver()
        switch_to_frame_path(driver, ())
        assert driver.switches == ["top"]

    def test_filler_enters_each_field_frame_before_writing(self, monkeypatch) -> None:
        """An embedded Greenhouse board lives in an iframe; writing from the top
        document would raise NoSuchElement or silently target the wrong page."""
        driver = FrameDriver()
        top = FormField(
            tag="input", label="First Name", frame_path=(), handle=FrameStubElement("top")
        )
        nested = FormField(
            tag="input", label="Email", frame_path=(1,), handle=FrameStubElement("nested")
        )
        monkeypatch.setattr(form_filler, "fields_from_driver", lambda d: [nested, top])

        recorded: list[tuple[int, ...]] = []
        original = form_filler._apply_one

        def spy(drv, match, timeout):
            recorded.append(drv.context)
            original(drv, match, timeout)

        monkeypatch.setattr(form_filler, "_apply_one", spy)

        from resume_filler.models import ResumeData

        form_filler.fill_form(
            driver,
            ResumeData(first_name="Jane", email="jane@example.com"),
            dry_run=False,
        )

        # Top document field filled from the top, nested field from inside frame 1.
        assert recorded == [(), (1,)]

    def test_driver_returns_to_the_top_document_afterwards(self, monkeypatch) -> None:
        driver = FrameDriver()
        nested = FormField(
            tag="input", label="Email", frame_path=(2,), handle=FrameStubElement("nested")
        )
        monkeypatch.setattr(form_filler, "fields_from_driver", lambda d: [nested])

        from resume_filler.models import ResumeData

        form_filler.fill_form(driver, ResumeData(email="jane@example.com"), dry_run=False)
        assert driver.context == (), "a stranded frame context breaks the next page"


# --------------------------------------------------------------------------
# Multi-step wizards
# --------------------------------------------------------------------------


class WizardDriver:
    """Serves a different set of fields per step, as an SPA wizard does."""

    def __init__(self, steps: list[list[FormField]]) -> None:
        self.steps = steps
        self.index = 0
        self.current_url = "https://example.com/apply/step1"
        self.switch_to = SwitchRecorder(self)
        self.context: tuple[int, ...] = ()
        self.switches: list[str] = []

    def execute_script(self, script: str, *args: object) -> str:
        return ""

    def advance(self) -> bool:
        if self.index + 1 < len(self.steps):
            self.index += 1
            self.current_url = f"https://example.com/apply/step{self.index + 1}"
            return True
        return False

    def current_fields(self) -> list[FormField]:
        return self.steps[self.index]


def _wizard_steps() -> list[list[FormField]]:
    return [
        [FormField(tag="input", label="First Name", handle=FrameStubElement("a"))],
        [FormField(tag="input", label="Email", handle=FrameStubElement("b"))],
        [FormField(tag="input", label="City", handle=FrameStubElement("c"))],
    ]


@pytest.fixture
def wizard(monkeypatch):
    driver = WizardDriver(_wizard_steps())
    monkeypatch.setattr(form_filler, "fields_from_driver", lambda d: d.current_fields())
    monkeypatch.setattr(
        form_filler,
        "_click_first",
        lambda d, xpaths, timeout: (
            d.advance() if xpaths is form_filler.NEXT_BUTTON_XPATHS else True
        ),
    )
    monkeypatch.setattr(form_filler, "_has_next_button", lambda d: True)
    return driver


class TestWizard:
    def test_walks_every_step_and_returns_the_combined_plan(self, wizard, resume) -> None:
        matches = form_filler.fill_wizard(wizard, resume, dry_run=False)
        labels = [m.form_field.label for m in matches]
        assert labels == ["First Name", "Email", "City"]

    def test_dry_run_stops_at_the_first_step(self, wizard, resume) -> None:
        """Advancing clicks a real button on the employer's form, so preview
        never does it."""
        matches = form_filler.fill_wizard(wizard, resume, dry_run=True)
        assert [m.form_field.label for m in matches] == ["First Name"]
        assert wizard.index == 0

    def test_single_page_form_exits_after_one_pass(self, monkeypatch, resume) -> None:
        driver = WizardDriver(
            [[FormField(tag="input", label="First Name", handle=FrameStubElement("a"))]]
        )
        monkeypatch.setattr(form_filler, "fields_from_driver", lambda d: d.current_fields())
        monkeypatch.setattr(form_filler, "_click_first", lambda d, x, t: False)
        matches = form_filler.fill_wizard(driver, resume, dry_run=False)
        assert len(matches) == 1

    def test_stops_when_a_step_repeats_itself(self, monkeypatch, resume) -> None:
        """A wizard that will not advance must not loop until the step cap."""
        stuck = [FormField(tag="input", label="First Name", handle=FrameStubElement("a"))]
        driver = WizardDriver([stuck])
        monkeypatch.setattr(form_filler, "fields_from_driver", lambda d: stuck)
        monkeypatch.setattr(form_filler, "_click_first", lambda d, x, t: True)
        matches = form_filler.fill_wizard(driver, resume, dry_run=False, max_steps=8)
        assert len(matches) == 2, "one real pass plus one that detected the repeat"

    def test_step_cap_is_honoured(self, monkeypatch, resume) -> None:
        counter = {"n": 0}

        def unique_fields(driver):
            counter["n"] += 1
            return [
                FormField(tag="input", label=f"Field {counter['n']}", handle=FrameStubElement("x"))
            ]

        driver = WizardDriver([[]])
        monkeypatch.setattr(form_filler, "fields_from_driver", unique_fields)
        monkeypatch.setattr(form_filler, "_click_first", lambda d, x, t: True)
        matches = form_filler.fill_wizard(driver, resume, dry_run=False, max_steps=3)
        assert len(matches) == 3

    def test_advancing_never_uses_the_submit_xpaths(self) -> None:
        """A Continue click must not be able to send the application."""
        joined = " ".join(form_filler.NEXT_BUTTON_XPATHS).lower()
        assert "submit" not in joined


class TestWizardIntegration:
    def test_apply_to_job_walks_the_wizard(self, monkeypatch, resume, tmp_path) -> None:
        driver = WizardDriver(_wizard_steps())
        driver.get = lambda url: None  # type: ignore[method-assign]
        monkeypatch.setattr(form_filler, "fields_from_driver", lambda d: d.current_fields())

        def click(d, xpaths, timeout):
            if xpaths is form_filler.NEXT_BUTTON_XPATHS:
                return d.advance()
            return True

        monkeypatch.setattr(form_filler, "_click_first", click)
        result = form_filler.apply_to_job(
            driver, JobPosting(url="https://example.com/j"), resume, submit=True
        )
        labels = [m.form_field.label for m in result.matches]
        assert labels == ["First Name", "Email", "City"]
        assert result.filled_count == 3

    def test_required_gap_on_a_later_step_still_blocks_submit(self, monkeypatch, resume) -> None:
        steps = [
            [FormField(tag="input", label="First Name", handle=FrameStubElement("a"))],
            [
                FormField(
                    tag="input",
                    label="Desired Salary",
                    required=True,
                    handle=FrameStubElement("b"),
                )
            ],
        ]
        driver = WizardDriver(steps)
        driver.get = lambda url: None  # type: ignore[method-assign]
        monkeypatch.setattr(form_filler, "fields_from_driver", lambda d: d.current_fields())
        monkeypatch.setattr(
            form_filler,
            "_click_first",
            lambda d, x, t: d.advance() if x is form_filler.NEXT_BUTTON_XPATHS else True,
        )
        result = form_filler.apply_to_job(
            driver, JobPosting(url="https://example.com/j"), resume, submit=True
        )
        assert result.status.value == "needs_review"
        assert any(g.form_field.label == "Desired Salary" for g in result.required_gaps)


class TestWorkdayFixture:
    def test_maps_workday_generated_ids_by_label_alone(self, workday_html: str) -> None:
        result = {
            m.form_field.label: m.canonical for m in match_form(fields_from_html(workday_html))
        }
        assert result["Legal Name: First Name"] == "first_name"
        assert result["Legal Name: Last Name"] == "last_name"
        assert result["Email Address"] == "email"
        assert result["Phone Number"] == "phone"
        assert result["Address Line 1"] == "address_line1"
        assert result["City"] == "city"

    def test_plan_fills_the_combobox_from_the_resume(self, workday_html: str) -> None:
        matches = {
            m.canonical: m
            for m in plan_fill(fields_from_html(workday_html), _resume_with_country())
        }
        assert matches["country"].status is FillStatus.FILLED
        assert matches["country"].value == "United States"
        assert matches["country"].form_field.is_combobox
