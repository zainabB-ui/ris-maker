import streamlit as st
import re
import json
from curl_cffi import requests as cffi_requests
from curl_cffi.requests.exceptions import RequestException as CffiRequestException

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
    "article": "JOUR", "book": "BOOK", "inbook": "CHAP", "incollection": "CHAP",
    "inproceedings": "CPAPER", "conference": "CONF", "techreport": "RPRT",
    "report": "RPRT", "phdthesis": "THES", "mastersthesis": "THES", "misc": "GEN",
    "unpublished": "UNPD", "manual": "RPRT", "online": "ELEC", "electronic": "ELEC",
}

FIELD_ORDER = ["AU", "A2", "A3", "TI", "T2", "T3", "PY", "DA", "VL", "IS",
               "SP", "EP", "PB", "CY", "SN", "UR", "LA", "N1", "N2", "KW",
               "JU", "VO", "SE"]

FRIENDLY_LABELS = {
    'TY': 'Type', 'AU': 'Author(s)', 'A2': 'Jurisdiction / secondary author',
    'TI': 'Title', 'T2': 'Journal / publication', 'VL': 'Volume / number',
    'IS': 'Issue', 'SP': 'Start page', 'EP': 'End page', 'PY': 'Year',
    'DA': 'Full date', 'PB': 'Publisher', 'CY': 'City', 'UR': 'URL', 'N1': 'Notes',
}


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


def render_review_form(fields: dict, key_prefix: str) -> dict:
    """Shared editable review UI used by every tab."""
    ty_names = {v: k for k, v in RIS_TYPE_MAP.items()}
    kind_options = sorted(set(RIS_TYPE_MAP.keys()))
    default_kind = ty_names.get(fields.get('TY'), 'report')
    chosen_kind = st.selectbox("Reference type", kind_options,
                                index=kind_options.index(default_kind) if default_kind in kind_options else 0,
                                key=f"{key_prefix}_type")
    fields = dict(fields)
    fields['TY'] = RIS_TYPE_MAP[chosen_kind]

    editable_fields = {"TY": fields['TY']}
    for tag in ["AU", "A2", "TI", "T2", "VL", "IS", "SP", "EP", "PY", "DA", "PB", "CY", "N1"]:
        current = fields.get(tag)
        if current is None:
            continue
        label = FRIENDLY_LABELS.get(tag, tag)
        if isinstance(current, list):
            current = "; ".join(str(c) for c in current if c)
        new_val = st.text_input(label, value=current or "", key=f"{key_prefix}_{tag}")
        if new_val:
            editable_fields[tag] = new_val

    url_val = st.text_input("URL", value=fields.get("UR", ""), key=f"{key_prefix}_UR")
    if url_val:
        editable_fields['UR'] = url_val

    return {k: v for k, v in editable_fields.items() if v}


# ---------------------------------------------------------------------------
# Free-text citation parsing
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
    return {'TY': 'RPRT', 'AU': author, 'TI': title, 'CY': city, 'PB': publisher, 'PY': year}


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
    return {'TY': 'JOUR', 'AU': author, 'TI': title, 'T2': journal,
            'VL': volume, 'IS': issue, 'SP': sp, 'EP': ep, 'PY': year}


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
    num_m = re.search(r'(No\.\s*\d+\s*of\s*\d{4}|Pub\.?\s?L\.?\s?No\.?\s?[\d\-]+|Public Law\s+[\d\-]+)', text, re.I)
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
    'journal': parse_journal, 'statute': parse_statute, 'bill': parse_bill,
    'case': parse_case, 'book_or_report': parse_chicago_style,
}


# ---------------------------------------------------------------------------
# BibTeX -> RIS
# ---------------------------------------------------------------------------

def clean_braces(val: str) -> str:
    return val.replace('{', '').replace('}', '').strip()


def split_authors(author_field: str):
    if not author_field:
        return []
    return [clean_braces(a).strip() for a in author_field.split(' and ') if a.strip()]


def parse_bibtex(text: str):
    entries = []
    pattern = re.compile(r'@(\w+)\s*\{\s*([^,]+),(.*?)\n\}', re.DOTALL)
    for m in pattern.finditer(text):
        entry_type = m.group(1).lower()
        body = m.group(3)
        fields = {}
        field_pattern = re.compile(r'(\w+)\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}|\s*(\w+)\s*=\s*"([^"]*)"')
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
    if fields.get('note'):
        ris_fields['N1'] = clean_braces(fields['note'])
    return ris_fields


# ---------------------------------------------------------------------------
# News / web article extraction (Open Graph + schema.org JSON-LD)
# ---------------------------------------------------------------------------

def extract_meta_tags(html: str) -> dict:
    tags = {}
    for m in re.finditer(r'<meta\s+([^>]+?)/?>', html, re.I):
        attrs_str = m.group(1)
        prop_m = re.search(r'(?:property|name)\s*=\s*["\']([^"\']+)["\']', attrs_str, re.I)
        content_m = re.search(r'content\s*=\s*["\']([^"\']*)["\']', attrs_str, re.I)
        if prop_m and content_m:
            tags[prop_m.group(1).lower()] = content_m.group(1)
    return tags


def extract_json_ld(html: str) -> dict:
    scripts = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S)
    wanted_types = ("NewsArticle", "Article", "Report", "BlogPosting")
    for s in scripts:
        try:
            data = json.loads(s.strip())
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") in wanted_types:
                        return item
            elif isinstance(data, dict):
                if data.get("@type") in wanted_types:
                    return data
                graph = data.get("@graph")
                if isinstance(graph, list):
                    for item in graph:
                        if isinstance(item, dict) and item.get("@type") in wanted_types:
                            return item
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
    return {}


def news_fields_from_html(html: str, source_url: str = None) -> dict:
    meta = extract_meta_tags(html)
    jsonld = extract_json_ld(html)

    title = jsonld.get("headline") or meta.get("og:title") or meta.get("twitter:title")

    authors = []
    jsonld_author = jsonld.get("author")
    if jsonld_author:
        if isinstance(jsonld_author, list):
            authors = [a.get("name") for a in jsonld_author if isinstance(a, dict) and a.get("name")]
        elif isinstance(jsonld_author, dict) and jsonld_author.get("name"):
            authors = [jsonld_author["name"]]
    if not authors and meta.get("author"):
        authors = [meta["author"]]

    date_raw = jsonld.get("datePublished") or meta.get("article:published_time")
    year, full_date = None, None
    if date_raw:
        date_m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_raw)
        if date_m:
            year = date_m.group(1)
            full_date = f"{date_m.group(1)}/{date_m.group(2)}/{date_m.group(3)}"

    publisher = None
    jsonld_pub = jsonld.get("publisher")
    if isinstance(jsonld_pub, dict):
        publisher = jsonld_pub.get("name")
    if not publisher:
        publisher = meta.get("og:site_name")

    url = meta.get("og:url") or source_url

    fields = {
        "TY": "NEWS",
        "AU": authors if authors else None,
        "TI": title,
        "T2": publisher,
        "PY": year,
        "DA": full_date,
        "UR": url,
    }
    return {k: v for k, v in fields.items() if v}


def fetch_article_html(url: str) -> str:
    """
    Fetches the article's HTML using curl_cffi, which replicates a real Chrome
    browser's TLS/HTTP2 fingerprint (not just the User-Agent header). This gets
    past ordinary bot-blocking on most news sites that reject plain `requests`
    calls. It will NOT get past sites that require solving a JavaScript
    challenge (e.g. Cloudflare Turnstile) or that are paywalled -- those need a
    real browser and raise a clear error instead of silently failing, so the
    'Paste a citation' tab remains the fallback.
    """
    resp = cffi_requests.get(url, impersonate="chrome", timeout=15)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="RIS Maker", page_icon="📚")
st.title("📚 RIS Maker")
st.caption("Paste a reference, a news article URL, or upload a BibTeX file — get a ready-to-import .ris file for EndNote, Zotero, or Mendeley.")

tab1, tab2, tab3 = st.tabs(["Paste a citation", "News article URL", "Upload BibTeX (.bib)"])

with tab1:
    st.write("Works best with Chicago/Turabian-style citations, plus common U.S. bill, statute, and case formats.")
    example = "Pakistan Bureau of Statistics. 1998 Census Report of Pakistan. Islamabad: Government of Pakistan, 2001."
    text = st.text_area("Paste your reference here:", placeholder=example, height=100, key="paste_text")

    if text.strip():
        kind = detect_type(text)
        fields = PARSERS[kind](text)
        st.subheader("Review parsed fields")
        final_fields = render_review_form(fields, key_prefix="paste")
        ris_text = build_ris_record(final_fields)
        st.subheader("Result")
        st.code(ris_text, language=None)
        st.download_button("⬇️ Download .ris file", data=ris_text, file_name="reference.ris",
                            mime="application/x-research-info-systems", key="paste_download")

with tab2:
    st.write("Paste the URL of a news article (works in any browser — Chrome, Safari, Firefox). "
             "This reads the same Open Graph and schema.org metadata that BibItNow! reads, using a fetcher "
             "that mimics a real Chrome browser's network fingerprint so more sites let the request through.")
    url = st.text_input("Article URL:", placeholder="https://www.nytimes.com/2022/08/30/world/asia/pakistan-floods.html")

    if url.strip():
        try:
            with st.spinner("Fetching article..."):
                html = fetch_article_html(url.strip())
            fields = news_fields_from_html(html, source_url=url.strip())
            if not fields.get("TI"):
                st.warning("Couldn't find a title on this page. The site may not publish standard metadata. "
                           "Try the 'Paste a citation' tab instead and type the citation in "
                           "Author. \"Title.\" Publication, Date. format.")
            else:
                st.subheader("Review parsed fields")
                final_fields = render_review_form(fields, key_prefix="news")
                ris_text = build_ris_record(final_fields)
                st.subheader("Result")
                st.code(ris_text, language=None)
                st.download_button("⬇️ Download .ris file", data=ris_text, file_name="news_article.ris",
                                    mime="application/x-research-info-systems", key="news_download")
        except CffiRequestException as e:
            st.error(f"Couldn't fetch that page ({e}). This site may require solving a JavaScript challenge, "
                     "may be paywalled, or may block automated access outright. Use the 'Paste a citation' tab "
                     "instead \u2014 type it as: Author. \"Title.\" Publication Name, Month Day, Year.")
        except Exception as e:
            st.error(f"Unexpected error fetching that page ({e}). Try the 'Paste a citation' tab instead.")

with tab3:
    st.write("Paste in a whole BibTeX file (e.g. downloaded from AnyStyle.io, Google Scholar's 'Cite' button, or Overleaf).")
    bib_text = st.text_area("Paste BibTeX content here:", height=200,
                             placeholder="@book{anderson1983imagined,\n  author = {Anderson, Benedict},\n  title = {Imagined Communities},\n  publisher = {Verso},\n  address = {London},\n  year = {1983}\n}",
                             key="bib_text")
    uploaded = st.file_uploader("...or upload a .bib file", type=["bib", "txt"])

    if uploaded is not None:
        bib_text = uploaded.read().decode("utf-8")

    if bib_text and bib_text.strip():
        entries = parse_bibtex(bib_text)
        if not entries:
            st.error("No BibTeX entries found. Make sure it looks like '@article{key, ... }' blocks.")
        else:
            records = []
            for entry_type, fields in entries:
                ris_fields = bibtex_entry_to_ris(entry_type, fields)
                records.append(build_ris_record(ris_fields))
            st.success(f"Parsed {len(records)} reference(s).")
            full_text = "\n\n".join(records) + "\n\n"
            st.code(full_text, language=None)
            st.download_button("⬇️ Download .ris file", data=full_text, file_name="references.ris",
                                mime="application/x-research-info-systems", key="bib_download")

st.divider()
st.caption("Built for turning legislation, government reports, news articles, and PDFs into RIS files when "
           "BibItNow! and Google Scholar's export don't work well or aren't available in your browser.")
