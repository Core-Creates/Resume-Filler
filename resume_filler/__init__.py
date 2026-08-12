"""Resume-Filler: parse a resume and pre-fill online job applications.

The design principle is human in the loop. The tool reads an application form,
works out which piece of your data belongs in each control, fills what it is
confident about, and reports everything it could not answer. Submission is opt
in and refuses to fire when required fields are still unanswered.
"""

from __future__ import annotations

__version__ = "2.0.0"

from .models import (
    ApplicationResult,
    ApplicationStatus,
    Education,
    FieldMatch,
    FillStatus,
    FormField,
    JobPosting,
    Position,
    ResumeData,
)

__all__ = [
    "ApplicationResult",
    "ApplicationStatus",
    "Education",
    "FieldMatch",
    "FillStatus",
    "FormField",
    "JobPosting",
    "Position",
    "ResumeData",
    "__version__",
]
