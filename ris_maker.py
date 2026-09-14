#!/usr/bin/env python3
"""
ris_maker.py — Turn references (pasted text, BibTeX, PDF, legislation, or CSV
batch) into properly formatted .ris files for EndNote / Zotero / Mendeley.

QUICK START #1 — paste a citation directly
--------------------------------------------
    python ris_maker.py --mode paste

Paste a citation you copied from a book, report, article, or law
(Chicago/Turabian-style works best, e.g. "Author. Title. City: Publisher,
Year."). The script guesses the type, shows you the parsed fields, lets you
fix anything, and writes a ready-to-import .ris file.

QUICK START #2 — use AnyStyle.io first, then convert here
-------------------------------------------------------------
AnyStyle.io (https://anystyle.io) uses a trained ML model and is more
accurate than this script's parser on messy or unusual citations, but it
only exports CSL-JSON, BibTeX, or XML — not RIS, which is what EndNote wants
for a true "double-click and it's in my library" import. Close that gap with:

    1. Paste your reference(s) into anystyle.io, click Parse, fix anything
       wrong in their editor, then choose "BibTeX" as the export/download
       format (BibTeX round-trips more reliably than their XML).
    2. Save the downloaded file, then run:
           python ris_maker.py --mode bibtex --file downloaded.bib
    3. This produces output.ris — import that into EndNote via
       File > Import > File, with Import Option set to "Reference Manager (RIS)".

This gives you AnyStyle's ML-quality parsing AND a real .ris file at the end,
with one extra copy/paste step instead of a full manual re-entry.

OTHER MODES
-----------
--mode law      Structured interactive form built for legislation (use this
                for tricky bills/acts/cases the auto-parser gets wrong)
--mode pdf      Pull whatever metadata a PDF has, then confirm/correct it
--mode csv      Batch-convert a spreadsheet of references at once
--mode bibtex   Convert a .bib file (e.g. from AnyStyle, Google Scholar's
                "BibTeX" cite option, or Overleaf) into .ris
--mode generic  Structured interactive form for books/articles/reports

All modes APPEND to output.ris (or a filename you set with --out), so you
can build one growing library file, or use --out somefile.ris to start fresh.

DEPENDENCIES
------------
Optional, only needed for --mode pdf:
    pip install pypdf
Everything else (including --mode bibtex) is pure Python standard library.

NOTES ON ACCURACY
------------------
--mode paste is a rule-based parser (not machine learning), tuned for
Chicago/Turabian-style citations plus common U.S. bill/statute/case patterns.
It handles clean citations like:

    Pakistan Bureau of Statistics. 1998 Census Report of Pakistan.
    Islamabad: Government of Pakistan, 2001.

well, but unusual punctuation or citation styles can produce a misparse —
that's why it always shows parsed fields before saving. For messier or
non-English references, route through AnyStyle.io + --mode bibtex instead,
per QUICK START #2 above.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

RIS_TYPE_MAP = {
    "statute": "STAT", "act": "STAT", "law": "STAT",
    "bill": "BILL", "resolution": "BILL", "unenacted bill": "UNBILL",
    "case": "CASE", "court case": "CASE",
    "hearing": "HEAR",
    "journal": "JOUR", "article": "JOUR",
    "book": "BOOK", "chapter": "CHAP",
    "report": "RPRT", "book_or_report": "RPRT",
    "news": "NEWS", "newspaper": "NEWS",
    "webpage": "ELEC", "website": "ELEC",
    "thesis": "THES", "dissertation": "THES",
    "conference": "CONF",
    "generic": "GEN",
}

BIBTEX_TYPE_MAP = {
    "article": "JOUR",
    "book": "BOOK",
    "inbook": "CHAP",
    "incollection": "CHAP",
    "inproceedings": "CPAPER",
    "conference": "CONF",
    "techreport": "RPRT",
    "report": "RPRT",
    "phdthesis": "THES",
    "mastersthesis": "THES",
    "misc": "GEN",
    "unpublished": "UNPD",
    "manual": "RPRT",
    "online": "ELEC",
    "electronic": "ELEC",
}

FIELD_ORDER = ["AU", "A2", "A3", "TI", "T2", "T3", "PY", "DA", "VL", "IS",
               "SP", "EP", "PB", "CY", "SN", "UR", "LA", "N1", "N2", "KW",
               "JU", "VO", "SE"]


# ---------------------------------------------------------------------------
# Core RIS writer
# ---------------------------------------------------------------------------

def build_ris_record(fields: dict) -> str:
    lines = [f"TY  - {fields.get('TY', 'GEN')}"]
    used = {"TY"}
    for tag in FIELD_ORDER:
        val = fields.get(tag)
        if not val:
            continue
        vals = val if isinstance(val, list) else [val]
        for v in vals:
            if v:
                lines.append(f"{tag}  - {v}")
        used.add(tag)
    for tag, val in fields.items():
        if tag in used or tag == "TY" or not val:
            continue
        vals = val if isinstance(val, list) else [val]
        for v in vals:
            if v:
                lines.append(f"{tag}  - {v}")
    lines.append("ER  - ")
    return "\n".join(lines)


def append_records(path: str, records: list):
    text = "\n\n".join(records) + "\n\n"
    mode = "a" if Path(path).exists() else "w"
    with open(path, mode, encoding="utf-8") as f:
        f.write(text)
    print(f"\nSaved {len(records)} record(s) to {path}")
    print("Open EndNote/Zotero/Mendeley and use File > Import (choose 'Reference Manager (RIS)' "
          "format) and select this file, or just double-click it if EndNote is set as the "
          "default handler for .ris files.")


def prompt(label, required=False, default=""):
    suffix = f" [{default}]" if default else ""
    while True:
        val = input(f"{label}{suffix}: ").strip()
        if not val and default:
            return default
        if val or not required:
            return val
        print("  (required, please enter a value)")


# ---------------------------------------------------------------------------
# BibTeX -> RIS conversion (--mode bibtex) — bridges AnyStyle.io's output
# ---------------------------------------------------------------------------

def clean_braces(val: str) -> str:
    return val.replace('{', '').replace('}', '').strip()


def split_authors(author_field: str):
    if not author_field:
        return []
    raw_authors = author_field.split(' and ')
    return [clean_braces(a).strip() for a in raw_authors if a.strip()]


def parse_bibtex(text: str):
    entries = []
    pattern = re.compile(r'@(\w+)\s*\{\s*([^,]+),(.*?)\n\}', re.DOTALL)
    for m in pattern.finditer(text):
        entry_type = m.group(1).lower()
        body = m.group(3)
        fields = {}
        field_pattern = re.compile(
            r'(\w+)\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}|\s*(\w+)\s*=\s*"([^"]*)"'
        )
        for fm in field_pattern.finditer(body):
            if fm.group(1):
                fname, fval = fm.group(1).lower(), fm.group(2).strip()
            else:
                fname, fval = fm.group(3).lower(), fm.group(4).strip()
            fields[fname] = fval
        entries.append((entry_type, fields))
    return entries


def bibtex_entry_to_ris(entry_type: str, fields: dict) -> dict:
    ty = BIBTEX_TYPE_MAP.get(entry_type, "GEN")
    ris_fields = {"TY": ty}

    authors = split_authors(fields.get('author', ''))
    if authors:
        ris_fields['AU'] = authors

    if fields.get('title'):
        ris_fields['TI'] = clean_braces(fields['title'])
    if fields.get('year'):
        ris_fields['PY'] = clean_braces(fields['year'])

    if fields.get('journal'):
        ris_fields['T2'] = clean_braces(fields['journal'])
    elif fields.get('booktitle'):
        ris_fields['T2'] = clean_braces(fields['booktitle'])

    if fields.get('volume'):
        ris_fields['VL'] = clean_braces(fields['volume'])
    if fields.get('number'):
        ris_fields['IS'] = clean_braces(fields['number'])

    if fields.get('pages'):
        pages = clean_braces(fields['pages']).replace('--', '-')
        if '-' in pages:
            sp, ep = pages.split('-', 1)
            ris_fields['SP'] = sp.strip()
            ris_fields['EP'] = ep.strip()
        else:
            ris_fields['SP'] = pages

    publisher = fields.get('publisher') or fields.get('institution') or fields.get('school')
    if publisher:
        ris_fields['PB'] = clean_braces(publisher)

    if fields.get('address'):
        ris_fields['CY'] = clean_braces(fields['address'])

    if fields.get('doi'):
        ris_fields['UR'] = f"https://doi.org/{clean_braces(fields['doi'])}"
    elif fields.get('url'):
        ris_fields['UR'] = clean_braces(fields['url'])

    if fields.get('isbn'):
        ris_fields['SN'] = clean_braces(fields['isbn'])
    elif fields.get('issn'):
        ris_fields['SN'] = clean_braces(fields['issn'])

    if fields.get('note'):
        ris_fields['N1'] = clean_braces(fields['note'])

    return ris_fields


def from_bibtex_file(path: str):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    entries = parse_bibtex(text)
    if not entries:
        sys.exit("No BibTeX entries found. Check the file looks like '@article{key, ... }' blocks.")
    records = []
    for entry_type, fields in entries:
        ris_fields = bibtex_entry_to_ris(entry_type, fields)
        records.append(build_ris_record(ris_fields))
    return records


# ---------------------------------------------------------------------------
# Free-text citation parsing (--mode paste)
# ---------------------------------------------------------------------------

def detect_type(text: str) -> str:
    t = text.lower()
    if re.search(r'\b[A-Z][A-Za-z\.\s]*\sv\.?\s[A-Z]', text) and re.search(r'\d{4}', text):
        return 'case'
    if re.search(r'\b(H\.R\.|S\.)\s?\d+', text) or re.search(r'\bcong(ress)?\.', t):
        return 'bill'
    if re.search(r'\bact,?\s+(no\.|public law|pub\.?\s?l\.)', t):
        return 'statute'
    if '"' in text or '\u201c' in text:
        return 'journal'
    return 'book_or_report'


def parse_chicago_style(text: str) -> dict:
    text = text.strip().rstrip('.')
    year_match = re.search(r',\s*(\d{4})\s*$', text)
    year = year_match.group(1) if year_match else None
    if year_match:
        text = text[:year_match.start()]

    segments = [s.strip() for s in text.split('. ') if s.strip()]
    author = title = city = publisher = None

    if len(segments) >= 3:
        author = segments[0]
        last = segments[-1]
        if ':' in last:
            city, publisher = (p.strip() for p in last.split(':', 1))
        else:
            publisher = last
        title = '. '.join(segments[1:-1]) if len(segments) > 3 else segments[1]
    elif len(segments) == 2:
        author = segments[0]
        last = segments[1]
        if ':' in last:
            title, publisher = (p.strip() for p in last.split(':', 1))
        else:
            title = last
    else:
        title = text

    return {'TY': 'RPRT', 'AU': [author] if author else None, 'TI': title,
            'CY': city, 'PB': publisher, 'PY': year}


def parse_journal(text: str) -> dict:
    text = text.strip().rstrip('.')
    m = re.match(r'^([^"\u201c]+?)\.?\s*[""\u201c]([^"\u201d]+)[""\u201d]', text)
    author = title = None
    rest = text
    if m:
        author = m.group(1).strip().rstrip('.')
        title = m.group(2).strip().rstrip('.')
        rest = text[m.end():].strip().lstrip('.').strip()

    year_m = re.search(r'\((\d{4})\)', rest)
    year = year_m.group(1) if year_m else None
    vol_m = re.search(r'(\d+)\s*,?\s*no\.\s*(\d+)', rest)
    volume, issue = (vol_m.group(1), vol_m.group(2)) if vol_m else (None, None)
    pages_m = re.search(r':\s*(\d+)\s*-\s*(\d+)', rest)
    sp, ep = (pages_m.group(1), pages_m.group(2)) if pages_m else (None, None)
    journal_m = re.match(r'^([A-Z][^,\d]+?)\s+\d', rest)
    journal = journal_m.group(1).strip() if journal_m else None

    return {'TY': 'JOUR', 'AU': [author] if author else None, 'TI': title,
            'T2': journal, 'VL': volume, 'IS': issue, 'SP': sp, 'EP': ep, 'PY': year}


def parse_statute(text: str) -> dict:
    text = text.strip().rstrip('.')
    jurisdiction = None
    juris_m = re.search(r'\(([^)]+)\)\s*$', text)
    if juris_m:
        jurisdiction = juris_m.group(1)
        text = text[:juris_m.start()].strip()

    year_m = re.search(r'\b(\d{4})\b', text)
    year = year_m.group(1) if year_m else None

    number = None
    num_m = re.search(r'(No\.\s*\d+\s*of\s*\d{4}|Pub\.?\s?L\.?\s?No\.?\s?[\d\-]+|Public Law\s+[\d\-]+)',
                       text, re.I)
    if num_m:
        number = num_m.group(1)
        text = text[:num_m.start()].rstrip(', ').strip()

    return {'TY': 'STAT', 'TI': text, 'A2': jurisdiction, 'VL': number, 'PY': year}


def parse_bill(text: str) -> dict:
    text = text.strip().rstrip('.')
    year = session = number = None

    year_m = re.search(r'\((\d{4})\)', text)
    if year_m:
        year = year_m.group(1)
        text = text[:year_m.start()].rstrip(', ').strip()

    sess_m = re.search(r'(\d+(?:st|nd|rd|th)\s+Cong(?:ress|\.)?)', text, re.I)
    if sess_m:
        session = sess_m.group(1)
        text = text[:sess_m.start()].rstrip(', ').strip()

    num_m = re.search(r'\b((?:H\.R\.|S\.)\s?\d+)', text)
    if num_m:
        number = num_m.group(1)
        text = text[:num_m.start()].rstrip(', ').strip()

    n1 = f"Session: {session}" if session else None
    return {'TY': 'BILL', 'TI': text, 'A2': 'US Congress', 'VL': number, 'PY': year, 'N1': n1}


def parse_case(text: str) -> dict:
    text = text.strip().rstrip('.')
    year = None
    year_m = re.search(r'\((\d{4})\)\s*$', text)
    if year_m:
        year = year_m.group(1)
        text = text[:year_m.start()].rstrip(', ').strip()

    parts = text.split(',', 1)
    title = parts[0].strip()
    number = parts[1].strip() if len(parts) > 1 else None

    return {'TY': 'CASE', 'TI': title, 'VL': number, 'PY': year}


PARSERS = {
    'journal': parse_journal,
    'statute': parse_statute,
    'bill': parse_bill,
    'case': parse_case,
    'book_or_report': parse_chicago_style,
}

FRIENDLY_LABELS = {
    'TY': 'Type', 'AU': 'Author(s)', 'A2': 'Jurisdiction/Secondary author',
    'TI': 'Title', 'T2': 'Journal/Container', 'VL': 'Volume/Number',
    'IS': 'Issue', 'SP': 'Start page', 'EP': 'End page', 'PY': 'Year',
    'PB': 'Publisher', 'CY': 'City', 'UR': 'URL', 'N1': 'Notes',
}


def review_and_edit(fields: dict) -> dict:
    print("\n--- Parsed result (press Enter to accept, or type a correction) ---")
    ty_names = {v: k for k, v in RIS_TYPE_MAP.items()}
    print(f"Detected type: {fields.get('TY')} ({ty_names.get(fields.get('TY'), 'unknown')})")
    new_type = input("Change type? Enter a kind (act/bill/case/journal/book/report) or leave blank: ").strip().lower()
    if new_type and new_type in RIS_TYPE_MAP:
        fields['TY'] = RIS_TYPE_MAP[new_type]

    for tag in ["AU", "A2", "TI", "T2", "VL", "IS", "SP", "EP", "PY", "PB", "CY", "N1"]:
        current = fields.get(tag)
        if current is None:
            continue
        label = FRIENDLY_LABELS.get(tag, tag)
        if isinstance(current, list):
            current_display = "; ".join(str(c) for c in current if c)
            new_val = input(f"{label} [{current_display}]: ").strip()
            if new_val:
                fields[tag] = [v.strip() for v in new_val.split(";") if v.strip()]
        else:
            new_val = input(f"{label} [{current}]: ").strip()
            if new_val:
                fields[tag] = new_val

    add_url = input("Add a URL? (leave blank to skip): ").strip()
    if add_url:
        fields['UR'] = add_url

    fields = {k: v for k, v in fields.items() if v}
    return fields


def mode_paste():
    print("\nPaste one reference (Chicago/Turabian style works best) and press Enter:\n")
    text = input("> ").strip()
    if not text:
        sys.exit("No text entered.")
    kind = detect_type(text)
    parser = PARSERS[kind]
    fields = parser(text)
    fields = review_and_edit(fields)
    return build_ris_record(fields)


# ---------------------------------------------------------------------------
# Structured legislation entry (--mode law)
# ---------------------------------------------------------------------------

def interactive_legislation():
    print("\n--- New legislation / legal reference ---")
    print("Kind options: act, bill, case, hearing, resolution")
    kind = prompt("Kind", required=True).lower()
    ty = RIS_TYPE_MAP.get(kind, "STAT")
    title = prompt("Title (full name of act/bill/case)", required=True)
    jurisdiction = prompt("Jurisdiction / enacting body (e.g. 'India', 'US Congress', 'UK Parliament')")
    number = prompt("Number (public law #, bill #, case citation #, etc.)")
    year = prompt("Year")
    session = prompt("Session (e.g. '117th Congress', leave blank if n/a)")
    section = prompt("Specific section/article cited (leave blank if citing whole law)")
    url = prompt("URL (if available)")
    notes = prompt("Notes (optional)")

    fields = {"TY": ty, "TI": title}
    if jurisdiction:
        fields["A2"] = jurisdiction
    if year:
        fields["PY"] = year
    if number:
        fields["VL"] = number
    if session:
        fields["N1"] = f"Session: {session}"
    if section:
        fields["SP"] = section
    if url:
        fields["UR"] = url
    if notes:
        existing = fields.get("N1", "")
        fields["N1"] = (existing + "; " if existing else "") + notes
    return build_ris_record(fields)


def interactive_generic():
    print("\n--- New generic reference ---")
    print("Kind options:", ", ".join(sorted(set(RIS_TYPE_MAP.keys()))))
    kind = prompt("Kind", required=True).lower()
    ty = RIS_TYPE_MAP.get(kind, "GEN")
    title = prompt("Title", required=True)
    authors_raw = prompt("Author(s), semicolon-separated (Last, First; Last, First)")
    authors = [a.strip() for a in authors_raw.split(";") if a.strip()] if authors_raw else []
    year = prompt("Year")
    publisher = prompt("Publisher (if book/report)")
    city = prompt("City (if book/report)")
    journal = prompt("Journal/container title (if article/chapter)")
    volume = prompt("Volume")
    issue = prompt("Issue")
    pages = prompt("Pages (e.g. 12-34)")
    url = prompt("URL")

    fields = {"TY": ty, "TI": title}
    if authors:
        fields["AU"] = authors
    if year:
        fields["PY"] = year
    if publisher:
        fields["PB"] = publisher
    if city:
        fields["CY"] = city
    if journal:
        fields["T2"] = journal
    if volume:
        fields["VL"] = volume
    if issue:
        fields["IS"] = issue
    if pages:
        if "-" in pages:
            sp, ep = pages.split("-", 1)
            fields["SP"] = sp.strip()
            fields["EP"] = ep.strip()
        else:
            fields["SP"] = pages
    if url:
        fields["UR"] = url
    return build_ris_record(fields)


def from_pdf(path: str):
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("This mode needs pypdf. Install it with:  pip install pypdf")

    reader = PdfReader(path)
    meta = reader.metadata or {}
    title = meta.get("/Title") or Path(path).stem
    author = meta.get("/Author")
    creation = meta.get("/CreationDate")
    year = None
    if creation and creation.startswith("D:"):
        year = creation[2:6]

    print(f"\nFound in PDF metadata for '{path}':")
    print(f"  Title:  {title}")
    print(f"  Author: {author}")
    print(f"  Year:   {year}")
    print("Press Enter to accept a field, or type a correction.\n")

    title = input(f"Title [{title}]: ").strip() or title
    author = input(f"Author [{author}]: ").strip() or author
    year = input(f"Year [{year}]: ").strip() or year
    kind = input("Kind (act/bill/case/report/journal/book/generic) [report]: ").strip().lower() or "report"
    url = input("URL (optional): ").strip()

    ty = RIS_TYPE_MAP.get(kind, "RPRT")
    fields = {"TY": ty, "TI": title}
    if author:
        fields["AU"] = [author]
    if year:
        fields["PY"] = year
    if url:
        fields["UR"] = url
    fields["N1"] = f"Auto-extracted from PDF metadata: {Path(path).name}"
    return build_ris_record(fields)


def from_csv(path: str):
    records = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
        for row in reader:
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
            kind = row.get("kind", "generic").lower()
            ty = RIS_TYPE_MAP.get(kind, "GEN")
            fields = {"TY": ty, "TI": row.get("title", "")}

            jurisdiction = row.get("jurisdiction")
            author = row.get("author")
            if ty in ("STAT", "BILL", "UNBILL", "CASE", "HEAR") and jurisdiction:
                fields["A2"] = jurisdiction
            elif author:
                fields["AU"] = [a.strip() for a in author.split(";") if a.strip()]

            if row.get("year"):
                fields["PY"] = row["year"]
            if row.get("number"):
                fields["VL"] = row["number"]
            if row.get("session"):
                fields["N1"] = f"Session: {row['session']}"
            if row.get("section"):
                fields["SP"] = row["section"]
            if row.get("publisher"):
                fields["PB"] = row["publisher"]
            if row.get("city"):
                fields["CY"] = row["city"]
            if row.get("url"):
                fields["UR"] = row["url"]
            if row.get("notes"):
                existing = fields.get("N1", "")
                fields["N1"] = (existing + "; " if existing else "") + row["notes"]

            records.append(build_ris_record(fields))
    return records


def main():
    parser = argparse.ArgumentParser(description="Turn references into .ris files.")
    parser.add_argument("--mode", choices=["paste", "law", "generic", "pdf", "csv", "bibtex"], default="paste")
    parser.add_argument("--file", help="Path to PDF (mode=pdf), CSV (mode=csv), or .bib (mode=bibtex)")
    parser.add_argument("--out", default="output.ris", help="Output .ris file (default: output.ris)")
    args = parser.parse_args()

    if args.mode == "paste":
        rec = mode_paste()
        append_records(args.out, [rec])
        more = input("\nPaste another reference? (y/n): ").strip().lower()
        while more == "y":
            rec = mode_paste()
            append_records(args.out, [rec])
            more = input("\nPaste another? (y/n): ").strip().lower()

    elif args.mode == "law":
        rec = interactive_legislation()
        append_records(args.out, [rec])
        more = input("\nAdd another law/legislation entry? (y/n): ").strip().lower()
        while more == "y":
            rec = interactive_legislation()
            append_records(args.out, [rec])
            more = input("\nAdd another? (y/n): ").strip().lower()

    elif args.mode == "generic":
        rec = interactive_generic()
        append_records(args.out, [rec])

    elif args.mode == "pdf":
        if not args.file:
            sys.exit("Please provide --file path/to.pdf")
        rec = from_pdf(args.file)
        append_records(args.out, [rec])

    elif args.mode == "csv":
        if not args.file:
            sys.exit("Please provide --file path/to.csv")
        records = from_csv(args.file)
        append_records(args.out, records)

    elif args.mode == "bibtex":
        if not args.file:
            sys.exit("Please provide --file path/to.bib")
        records = from_bibtex_file(args.file)
        append_records(args.out, records)


if __name__ == "__main__":
    main()
