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
resume-filler init
```

That finds your resume, checks it parses, writes `.env` and `profile.json`, and
tells you what to run next. Press Enter through the prompts to take the
defaults, or `--yes` to skip them entirely.

**The tool never asks for a password.** Portals that need a sign in are handled
by the `login` command, which opens a browser so you sign in yourself and keeps
the session. Both `.env` and `profile.json` are gitignored.

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

**See how well you match before applying:**

```bash
python -m resume_filler tailor --greenhouse stripe --keywords python --cover-letters
```

Postings come back ranked by keyword coverage, best fit first:

```
  Coverage: 53% (9 of 17 emphasised terms)

  Present in your resume:
    kubernetes, aws, backend, engineer, kafka, python, terraform

  Emphasised by the posting, absent from your resume:
    elixir, graphql, snowflake

  Add the ones you genuinely have. Do not add the ones you do not.
```

**Export your application history:**

```bash
python -m resume_filler export applications.csv
```

## Portals that need a login

A browser started by the tool has no cookies, so on any portal that puts the
application behind a sign in it lands on a login page and finds nothing. That is
most of the enterprise ones.

| Vendor | Login needed |
| --- | --- |
| Greenhouse, Lever, Ashby, SmartRecruiters | No. Works live as is. |
| iCIMS, Workday, Infor CloudSuite | Yes. Use a saved session. |

Sign in once by hand and keep the session:

```bash
python -m resume_filler --session ~/.resume-filler-session \
    login https://careers-example.icims.com
```

A browser opens, you sign in, you press Enter. Every later run that passes the
same `--session` is already signed in:

```bash
python -m resume_filler --session ~/.resume-filler-session \
    inspect --url https://careers-example.icims.com/jobs/1594/candidate
```

Set `SESSION_DIR` in `.env` to avoid repeating the flag.

Two things worth knowing:

- **Treat that directory as a password.** It holds live login cookies and is
  worth exactly as much as your account. Keep it outside the repository.
- **A session-only cookie does not survive the browser closing.** If the site
  did not offer "remember me", the login ends when the run ends. Do the login
  and the work in one sitting, or tick "remember me" when signing in.

The tool never automates the sign in itself, and there is no reason it should:
you do it once by hand, and it does not have to hold your password.

## Your profile: the answers a resume cannot give

Every form asks for things no resume contains. A street address is required
almost everywhere and appears on no CV. So are work authorization, salary
expectations, and where you heard about the role.

```bash
cp profile.example.json profile.json
```

Keys are the canonical field names the plan prints in its **MAPPED TO** column,
so when a field reports a gap the plan tells you exactly which key to add:

```
[GAP ] *Address    address_line1   1.00   Not in your resume.
                                          Add "address_line1" to your profile file.
```

```json
{
  "address_line1": "123 Example Street",
  "work_authorization": "Yes",
  "how_did_you_hear": "LinkedIn"
}
```

`profile.json` is gitignored. Never commit it.

**A value here can answer a question the engine otherwise refuses to touch**,
such as work authorization. That is deliberate and is not a hole in the policy.
The policy exists so the tool never invents an answer to a legal declaration or
a negotiation. An answer you wrote yourself is not invented, and the plan labels
it `From your profile.` so it stays visible.

Anything you leave blank is still reported rather than guessed.

## Handling real ATS pages

Three things that break naive form automation are handled explicitly:

- **iframes.** Greenhouse and Lever boards embedded on a company careers site
  live inside an iframe. The scanner descends up to three levels of nesting and
  records which frame each control lives in, then re-enters that frame before
  writing to it. Scanning only the top document finds zero fields on these pages.
- **Multi-step wizards.** Workday and similar split an application across
  several pages. The filler works through each step, stopping when there is no
  Continue button, when a step repeats itself, or at a step cap. Advancing can
  never submit: the Continue selectors deliberately exclude Submit.
- **Scripted dropdowns.** React, Ant Design and Select2 render a combobox as a
  div, not a `<select>`. Typing into one leaves the widget's internal state
  unset, so the value looks right on screen and submits as empty. These are
  detected, opened, and selected by clicking the matching option, and a
  selection that does not commit raises rather than passing silently.

Dry run stops at step one of a wizard, because advancing means clicking a real
button on the employer's form. Use `--submit` to walk the whole thing.

## Cover letters

`--cover-letters` (or `--tailor` during an apply run) writes a draft per posting
into the output directory. The generator only recombines facts already in your
resume. It will not claim a skill you do not have, and when it finds no real
overlap with the posting it says so instead of inventing enthusiasm.

Drafts carry `[DRAFT NOTE: ...]` markers that you are expected to delete. That is
deliberate: a letter that reads as finished is one people send without reading.

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
