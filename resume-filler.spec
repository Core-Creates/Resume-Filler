# PyInstaller build. Run with:  pyinstaller resume-filler.spec
#
# Two things have to be collected explicitly or the executable builds cleanly
# and then fails at the moment it matters:
#
#   selenium   ships selenium-manager.exe, the helper that fetches the right
#              chromedriver. Without it the browser never starts, and the error
#              gives no hint why.
#   pdfminer   carries character map data used for text extraction. Without it
#              some PDFs come out empty or mojibake, which looks like a parser
#              bug rather than a packaging one.

from PyInstaller.utils.hooks import collect_all, collect_data_files

selenium_datas, selenium_binaries, selenium_hidden = collect_all("selenium")

datas = selenium_datas + collect_data_files("pdfminer")
binaries = selenium_binaries
hiddenimports = selenium_hidden + [
    "pdfplumber",
    "pdfminer.cmapdb",
    "bs4",
    "soupsieve",
    "dotenv",
]

a = Analysis(
    ["packaging_entry.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Nothing here is needed at runtime, and each drags in tens of megabytes.
    excludes=["mypy", "pytest", "ruff", "PIL.ImageQt", "tkinter", "matplotlib"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="resume-filler",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX compression is a reliable way to attract antivirus alerts.
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
