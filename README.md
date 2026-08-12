# Resume-Filler

Parse your resume once, then let the tool pre-fill online job applications so you
only have to review and submit.

The design principle is human in the loop. Resume-Filler reads an application
form, works out which piece of your data belongs in each control, fills what it
is confident about, and reports everything it could not answer. It does not
submit anything unless you explicitly ask it to, and even then it refuses when a
required field is still unanswered.

## Why it works this way

Blind auto-submission produces bad applications and gets accounts restricted.
Pre-filling does the tedious 80 percent (name, contact details, work history,
resume upload, profile links) and leaves you the 20 percent that actually
differentiates you: salary expectations, work authorization declarations, and
the "why do you want to work here" essay. Those are deliberately never
auto-answered. Neither are voluntary demographic questions.

## Install

```bash
git clone https://github.com/Core-Creates/Resume-Filler.git
cd Resume-Filler
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .                 # optional, provides the resume-filler command
```

Requires Python 3.10 or newer, plus Chrome, Edge, or Firefox. Selenium 4.6+
downloads the matching driver binary on its own, so there is nothing else to
install.

## Configure

```bash
cp .env.example .env
```

Then edit `.env` and set at minimum `RESUME_PATH`. Credentials are only needed
for sites that require a login before showing the form; applying through public
ATS links needs no account. `.env` is gitignored. Never commit it.

## Use

**Check what the parser found.** Always start here. If your name or email comes
out wrong, every application will be wrong.

```bash
python -m resume_filler parse resume.pdf
```

**Preview a fill plan without touching a browser.** Save any application page as
HTML from your browser, then:

```bash
python -m resume_filler inspect --html saved_form.html
```

Output shows every control, what it mapped to, the confidence score, and the
value that would be entered:

```
  [FILL] *First Name                        first_name             1.00  Jane
  [FILL] *Email                             email                  1.00  jane.rivera@example.com
  [FILL] *Resume/CV                         resume_file            1.00  /home/jane/resume.pdf
  [YOU ]  Desired Salary                    desired_salary         1.00  Negotiation decision. Answer this yourself.
  [YOU ]  Gender                            demographic            1.00  Voluntary self identification. Left blank by design.
  [GAP ]  Website                           portfolio_url          0.85  Resume did not supply a value for 'portfolio_url'.
  [----]  Reference Name                    -                      0.00
```

**Preview against a live page:**

```bash
python -m resume_filler inspect --url https://boards.greenhouse.io/example/jobs/1234567
```

**Fill a batch of applications.** This is a dry run. Nothing is typed.

```bash
python -m resume_filler apply --urls my_shortlist.txt
```

**Actually fill and submit.** Requires the explicit flag, and still stops on any
unfilled required field:

```bash
python -m resume_filler apply --urls my_shortlist.txt --submit
```

**Pull postings from a public ATS board:**

```bash
python -m resume_filler apply --greenhouse stripe --keywords python security --limit 5
python -m resume_filler apply --lever netflix --location remote
```

**Export your application history:**

```bash
python -m resume_filler export applications.csv
```

## How the field mapping works

Given any form control, the engine decides what belongs in it using four rules,
in order:

1. **Trust `autocomplete`.** If the control carries a standard token such as
   `given-name` or `tel`, that is authoritative and scores 1.00.
2. **Score the descriptive attributes.** The visible `<label>`, `aria-label`,
   `name`, `id`, and `placeholder` are each matched against regex patterns per
   canonical field. A visible label outranks an internal identifier, because the
   label is what a human reads.
3. **Apply negative patterns.** These stop the classic mismatches: "Confirm
   Email" never claims the email slot, "Company Name" never claims the
   applicant's name, "Title" as a salutation never claims the job title, and
   "Country Code" never claims the phone number.
4. **Resolve the whole form at once.** A greedy one to one assignment means two
   controls can never claim the same piece of data, and the strongest match wins
   the control.

Anything scoring below the confidence threshold (0.55 by default, set via
`CONFIDENCE_THRESHOLD`) is reported rather than guessed. Raise the threshold to
fill less and review more.

Label discovery handles all five association styles found in the wild:
`<label for=>`, `aria-labelledby`, a wrapping `<label>`, a `<fieldset>` `<legend>`
for radio groups, and the nearest preceding label.

## What it never fills

| Category | Why |
| --- | --- |
| Gender, race, veteran and disability status | Voluntary self identification. Your choice alone. |
| Work authorization and sponsorship | Legal declarations. You must answer these. |
| Desired salary | A negotiation decision, not a data lookup. |
| "Why do you want to work here" essays | The part that actually gets you the interview. |

These are still detected and reported, so you know exactly what remains.

## Project layout

```
resume_filler/
  field_map.py      Canonical field definitions and the matching engine
  extractors.py     HTML and Selenium adapters producing FormField objects
  resume_parser.py  Section aware PDF resume parsing
  form_filler.py    Executes a plan, enforces the submission guardrails
  sources.py        Greenhouse and Lever APIs, URL lists, CSV
  tracker.py        SQLite history, deduplication, CSV export
  reporting.py      Plan tables and JSON run reports
  cli.py            parse, inspect, apply, export
tests/
  fixtures/         Saved ATS form HTML and a sample resume
```

The matching engine has no browser dependency, which is why the test suite
exercises it end to end against saved fixtures with no driver and no network.

## Development

```bash
pip install -r requirements-dev.txt
ruff check .
ruff format --check .
mypy resume_filler
pytest --cov=resume_filler
```

To add support for a form the engine handles badly, save the page as HTML into
`tests/fixtures/`, add a test asserting the correct mapping, then adjust the
patterns in `field_map.py` until it passes.

## A note on terms of service

Automating LinkedIn and Indeed conflicts with their user agreements, and their
anti-automation systems target exactly this pattern. The realistic risk is
restriction of the account you need for your job search. This tool therefore
prefers public ATS endpoints (Greenhouse, Lever) and direct application URLs,
which are the pages employers actually publish for applicants. Point it at other
sites at your own discretion.

## License

GPL-3.0. See [LICENSE](LICENSE).
