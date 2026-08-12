"""Entry point for the standalone executable.

``resume_filler/__main__.py`` imports with ``from .cli import main``, which is
correct for ``python -m resume_filler`` but fails as a PyInstaller entry script:
the script is run as a top level module with no package, so the relative import
has nothing to resolve against. An absolute import from a plain script is what
the bundler needs.
"""

from __future__ import annotations

import multiprocessing

from resume_filler.cli import main

if __name__ == "__main__":
    # Harmless in a normal run, and required if a frozen build ever spawns a
    # child process, which would otherwise re-run the whole program.
    multiprocessing.freeze_support()
    raise SystemExit(main())
