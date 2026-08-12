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


@pytest.fixture
def react_select_html() -> str:
    return (FIXTURES / "react_select_form.html").read_text(encoding="utf-8")


@pytest.fixture
def lever_html() -> str:
    return (FIXTURES / "lever_form.html").read_text(encoding="utf-8")


class TestShadowDom:
    """Controls inside web components are invisible to an ordinary query.

    Verified in a real headless browser against a page with an open root, a root
    nested two levels deep, and a closed root: the first two were found with
    their labels, the closed one correctly was not. These tests pin the
    behaviour that browser run confirmed.
    """

    def test_scan_descends_into_shadow_roots(self) -> None:
        from resume_filler.extractors import _SHADOW_SCAN_SCRIPT

        assert "shadowRoot" in _SHADOW_SCAN_SCRIPT
        assert "walk(" in _SHADOW_SCAN_SCRIPT, "the recursive descent is missing"

    def test_scan_is_depth_bounded(self) -> None:
        """An unbounded walk on a deeply nested component tree can hang."""
        from resume_filler.extractors import _SHADOW_SCAN_SCRIPT, SHADOW_MAX_DEPTH

        assert "maxDepth" in _SHADOW_SCAN_SCRIPT
        assert SHADOW_MAX_DEPTH >= 1

    def test_labels_resolve_against_the_elements_own_root(self) -> None:
        """label[for=] does not cross the shadow boundary, so querying document
        finds nothing for a control inside a shadow tree."""
        from resume_filler.extractors import _LABEL_SCRIPT

        assert "getRootNode" in _LABEL_SCRIPT
        assert "document.querySelector(`label" not in _LABEL_SCRIPT

    def test_falls_back_to_a_flat_query_when_script_execution_fails(self) -> None:
        """A page that blocks scripts must still get an ordinary scan."""
        from selenium.common.exceptions import WebDriverException

        from resume_filler.extractors import _elements_in_context

        sentinel = [object(), object()]

        class BlockedDriver:
            def execute_script(self, *args):
                raise WebDriverException("blocked")

            def find_elements(self, by, selector):
                return sentinel

        assert _elements_in_context(BlockedDriver(), "input") == sentinel

    def test_uses_the_script_result_when_it_succeeds(self) -> None:
        from resume_filler.extractors import _elements_in_context

        sentinel = [object()]

        class Driver:
            def execute_script(self, *args):
                return [sentinel, 0]

            def find_elements(self, by, selector):
                raise AssertionError("should not fall back")

        assert _elements_in_context(Driver(), "input") == sentinel

    def test_malformed_script_result_falls_back_rather_than_crashing(self) -> None:
        from resume_filler.extractors import _elements_in_context

        class OddDriver:
            def execute_script(self, *args):
                return None  # older drivers have returned odd shapes here

            def find_elements(self, by, selector):
                return ["fallback"]

        assert _elements_in_context(OddDriver(), "input") == ["fallback"]


class TestAdapterParity:
    """The static and live adapters must resolve labels the same way.

    They drifted once already. The container search that reads label-like divs
    was added to the BeautifulSoup adapter only, and because every unit test
    goes through that adapter, nothing caught it. A live run against Lever
    returned 14 unlabelled required controls that the static scan labelled
    correctly.

    These are canaries, not proofs. Only a browser can truly verify the script,
    but a canary fails loudly when someone edits one adapter and not the other.
    """

    def test_label_script_implements_the_container_search(self) -> None:
        from resume_filler.extractors import _LABEL_SCRIPT

        assert '[class*="label"]' in _LABEL_SCRIPT, (
            "the live adapter must recognise label-like divs, as the static one does"
        )
        assert "parentElement" in _LABEL_SCRIPT, "the container walk is missing"

    def test_label_script_covers_every_static_strategy(self) -> None:
        from resume_filler.extractors import _LABEL_SCRIPT

        for strategy in ("label[for=", "aria-labelledby", "closest('label')", "legend"):
            assert strategy in _LABEL_SCRIPT, f"live adapter is missing {strategy}"

    def test_both_adapters_share_one_control_selector(self) -> None:
        """A shared selector is what stops them disagreeing on what a control is."""
        import inspect

        from resume_filler import extractors

        static_src = inspect.getsource(extractors.fields_from_html)
        live_src = inspect.getsource(extractors._scan_context)
        assert "CONTROL_SELECTOR" in static_src
        assert "CONTROL_SELECTOR" in live_src

    def test_both_adapters_skip_decorative_controls(self) -> None:
        import inspect

        from resume_filler import extractors

        for func in (extractors.fields_from_html, extractors._scan_context):
            assert "_is_decorative" in inspect.getsource(func)


class TestRealLeverMarkup:
    """Regressions found by running the engine against a live Lever page."""

    def test_div_labels_are_read(self, lever_html: str) -> None:
        """Lever labels custom questions with <div class='application-label'>,
        not <label>. Only looking for <label> misses every one."""
        labels = {f.label for f in fields_from_html(lever_html)}
        assert "Current Street Address" in labels
        assert "Current Address (Postal Code)" in labels

    def test_a_label_never_leaks_across_sections(self, lever_html: str) -> None:
        """The live page assigned 'Portfolio URL' to ten unrelated custom
        questions, because the fallback searched the whole document backwards.
        A wrong label is worse than none: it can route a value into the wrong
        field."""
        labels = [f.label for f in fields_from_html(lever_html)]
        assert labels.count("Portfolio URL") == 1

    def test_each_address_part_maps_to_its_own_field(self, lever_html: str) -> None:
        result = {m.form_field.label: m.canonical for m in match_form(fields_from_html(lever_html))}
        assert result["Current Street Address"] == "address_line1"
        assert result["Current Address (City)"] == "city"
        assert result["Current Address (State)"] == "state"
        assert result["Current Address (Postal Code)"] == "postal_code"
        assert result["Current Address (Country)"] == "country"

    def test_standard_wrapping_labels_still_work(self, lever_html: str) -> None:
        """Both label styles appear on one page; neither fix may break the other."""
        result = {m.form_field.label: m.canonical for m in match_form(fields_from_html(lever_html))}
        assert result["Full name"] == "full_name"
        assert result["Email"] == "email"
        assert result["LinkedIn URL"] == "linkedin_url"
        assert result["Portfolio URL"] == "portfolio_url"

    def test_opaque_uuid_field_names_are_no_obstacle(self, lever_html: str) -> None:
        """cards[uuid][field2] carries no signal; the label is everything."""
        fields = fields_from_html(lever_html)
        card_fields = [f for f in fields if f.name.startswith("cards[")]
        assert len(card_fields) == 5
        assert all(f.label for f in card_fields)


class TestRealGreenhouseMarkup:
    """Regressions found by running the engine against a live Greenhouse page.

    Both bugs here passed every hand-written fixture and would have broken every
    real application.
    """

    def test_hidden_required_proxies_are_ignored(self, react_select_html: str) -> None:
        """react-select ships a hidden required input per dropdown purely to
        trigger native validation. Collecting them produces unmappable required
        fields that block submission on every Greenhouse page."""
        fields = fields_from_html(react_select_html)
        phantoms = [f for f in fields if f.required and not f.label and not f.element_id]
        assert phantoms == []

    def test_control_count_matches_what_a_human_sees(self, react_select_html: str) -> None:
        fields = fields_from_html(react_select_html)
        # 3 text, 2 dropdowns, 2 file, 2 custom questions. The 2 proxies are not
        # controls a person could fill.
        assert len(fields) == 9

    def test_react_select_is_recognised_as_a_dropdown(self, react_select_html: str) -> None:
        """It advertises aria-autocomplete='list', not role='combobox'. Missing
        that means typing into it, which never commits and submits empty."""
        fields = {f.element_id: f for f in fields_from_html(react_select_html)}
        assert fields["country"].is_combobox
        assert fields["candidate-location"].is_combobox

    def test_plain_text_inputs_are_not_mistaken_for_dropdowns(self, react_select_html: str) -> None:
        fields = {f.element_id: f for f in fields_from_html(react_select_html)}
        assert not fields["first_name"].is_combobox
        assert not fields["email"].is_combobox

    def test_aria_label_only_fields_still_map(self, react_select_html: str) -> None:
        """Real Greenhouse pages carry no <label for>; aria-label is all there is."""
        result = {
            m.form_field.element_id: m.canonical
            for m in match_form(fields_from_html(react_select_html))
        }
        assert result["first_name"] == "first_name"
        assert result["last_name"] == "last_name"
        assert result["email"] == "email"
        assert result["question_37020453002"] == "linkedin_url"
        assert result["question_37020454002"] == "portfolio_url"

    def test_hidden_file_inputs_are_still_collected(self, react_select_html: str) -> None:
        """A file input hidden behind a styled button is the one hidden control
        that must survive, because that is how uploads are always built."""
        result = {
            m.form_field.element_id: m.canonical
            for m in match_form(fields_from_html(react_select_html))
        }
        assert result["resume"] == "resume_file"
        assert result["cover_letter"] == "cover_letter"

    def test_no_required_field_is_left_unmappable(self, react_select_html: str) -> None:
        """Every required control on this page must be something the engine can
        either fill or hand to the human by name."""
        for match in match_form(fields_from_html(react_select_html)):
            if match.form_field.required:
                assert match.canonical, f"unmapped required field: {match.form_field.describe()}"


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
