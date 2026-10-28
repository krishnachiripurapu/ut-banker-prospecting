"""Find UT Austin bankers and fill their emails for free.

Step 0 (--discover): search engine "X-ray" queries against public LinkedIn
        profiles (site:linkedin.com/in "University of Texas at Austin" "<firm>")
        to add new people for firms that have fewer than --per-firm contacts.
        Uses Brave Search (BRAVE_API_KEY, ~1,000 free queries/month on the
        monthly credit) or a legacy Google Custom Search key
        (GOOGLE_API_KEY + GOOGLE_CSE_ID; that API shuts down Jan 1, 2027).
        Results are cached in .search_cache.json so reruns cost nothing.
Step 1: learn patterns from confirmed emails already in the CSV (Clay / Vibe).
Step 2: for firms with no confirmed email, pull example addresses at the firm's
        domain from SEC EDGAR full-text search and learn the pattern from the
        names printed next to them in the filing.
Step 3: write guesses back into the CSV with a confidence label.

Guessed emails and search-found people are NOT verified. Check titles before
reaching out, and send a small batch first; a bounce means the pattern is wrong.

Usage:
    python prospecting/email_finder.py prospecting/UT_Alumni_Bankers.csv
    python prospecting/email_finder.py prospecting/UT_Alumni_Bankers.csv --no-sec
    python prospecting/email_finder.py prospecting/UT_Alumni_Bankers.csv --discover \\
        --targets prospecting/IB_Target_List.csv --per-firm 5 --max-queries 100
"""
import argparse
import collections
import csv
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

# SEC requires "Name email" in the User-Agent or it returns 403. Set SEC_UA to your own.
SEC_UA = os.environ.get("SEC_UA", "UT-alumni-research contact@example.com")

# Firms whose staff we found but have no confirmed email yet. Domains to try,
# most likely first. Extend this as new firms are added to the sheet.
FIRM_DOMAINS = {
    "UBS": ["ubs.com"],
    "Greenhill (Mizuho)": ["greenhill.com", "mizuhogroup.com"],
    "MUFG": ["mufgsecurities.com", "us.sc.mufg.jp", "mufgamericas.com"],
    "BMO Capital Markets": ["bmo.com", "bmocm.com"],
    "FT Partners": ["ftpartners.com"],
    "Lincoln International": ["lincolninternational.com"],
    "SMBC Nikko Securities America, Inc.": ["smbcnikko-si.com", "smbc.co.jp"],
    "Capstone Partners": ["capstonepartners.com"],
    "Regions Securities": ["regions.com"],
    "Siebert Williams Shank": ["siebertwilliams.com"],
}

PATTERNS = {
    "first.last": lambda f, l: f"{f}.{l}",
    "firstlast": lambda f, l: f"{f}{l}",
    "flast": lambda f, l: f"{f[0]}{l}",
    "f.last": lambda f, l: f"{f[0]}.{l}",
    "first_last": lambda f, l: f"{f}_{l}",
    "last.first": lambda f, l: f"{l}.{f}",
    "lastf": lambda f, l: f"{l}{f[0]}",
    "last": lambda f, l: l,
    "first": lambda f, l: f,
}

GENERIC_WORDS = {"media", "relations", "investor", "investors", "press", "corporate", "communications",
                 "office", "team", "group", "desk", "contact", "general", "counsel", "legal", "info",
                 "public", "affairs", "news", "service", "services", "department", "compliance"}

CREDENTIALS = {"cfa", "cpa", "cfp", "cpwa", "mba", "jr", "sr", "ii", "iii", "phd"}


def ascii_lower(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def split_name(full):
    """Return (first, last) as lowercase ascii, or None if the surname is an initial."""
    s = re.sub(r"\(.*?\)", " ", full)  # drop "(Wei)"-style nicknames
    s = s.split(",")[0]  # drop ", CFA" etc.
    tokens = [t for t in re.sub(r"[^a-z\s'-]", " ", ascii_lower(s)).split() if t not in CREDENTIALS]
    tokens = [t.replace("'", "").replace("-", "") for t in tokens]
    if len(tokens) < 2 or len(tokens[-1]) < 2:
        return None
    return tokens[0], tokens[-1]


def match_pattern(local, first, last):
    hits = [name for name, fn in PATTERNS.items() if fn(first, last) == local]
    if hits:
        return hits[0]
    # first.<middle-initial>.last (Goldman style) -> treat as first.last variant
    if re.fullmatch(rf"{re.escape(first)}\.[a-z]\.{re.escape(last)}", local):
        return "first.x.last"
    return None


# ---------------------------------------------------------------- step 1: CSV

def learn_from_csv(rows):
    pats = collections.defaultdict(collections.Counter)
    domains = collections.defaultdict(collections.Counter)
    for r in rows:
        email = r["Email"].strip().lower()
        if "@" not in email or r.get("Source", "").startswith("Pattern"):
            continue
        name = split_name(r["Name"])
        if not name:
            continue
        local, domain = email.split("@", 1)
        domains[r["Firm"]][domain] += 1
        pats[r["Firm"]][match_pattern(local, *name) or "other"] += 1
    return pats, domains


# ------------------------------------------------------------- step 2: EDGAR

def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "ignore")


def sec_examples(domain, max_docs=10):
    """Yield (local_part, surrounding_text, filing_year) for addresses at `domain`, newest filings first."""
    q = urllib.parse.quote(f'"@{domain}"')
    try:
        hits = json.loads(http_get(f"https://efts.sec.gov/LATEST/search-index?q={q}"))["hits"]["hits"]
    except Exception as e:  # network or schema change: skip this domain
        print(f"    SEC search failed for {domain}: {e}")
        return
    hits.sort(key=lambda h: h["_source"].get("file_date", ""), reverse=True)
    seen = set()
    for h in hits[:max_docs]:
        year = int(h["_source"].get("file_date", "1900")[:4])
        acc, fname = h["_id"].split(":", 1)
        cik = h["_source"]["ciks"][0].lstrip("0")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{fname}"
        try:
            text = re.sub(r"<[^>]+>", " ", http_get(url))
        except Exception:
            continue
        time.sleep(0.15)  # SEC fair-access limit is 10 req/s
        for m in re.finditer(rf"([A-Za-z0-9._%+-]+)@{re.escape(domain)}", text, re.I):
            local = m.group(1).lower()
            if local in seen:
                continue
            seen.add(local)
            yield local, text[max(0, m.start() - 250): m.end() + 50], year


def learn_from_sec(domain):
    """Infer the pattern by pairing each found address with a nearby printed name.
    Recent filings count more, since firms change formats (Greenhill: flast -> first.last).
    Big banks' newest filings often list only media mailboxes, so dig deeper if nothing turns up."""
    for depth in (10, 40):
        votes, examples = _learn_from_sec(domain, depth)
        if votes:
            break
    return votes, examples


def _learn_from_sec(domain, max_docs):
    votes = collections.Counter()
    examples = []
    for local, ctx, year in sec_examples(domain, max_docs):
        if local in {"info", "ir", "media", "press", "investor.relations", "investorrelations"}:
            continue
        for first, last in re.findall(r"\b([A-Z][a-z]+)\s+(?:[A-Z]\.\s+)?([A-Z][a-zA-Z'-]+)\b", ctx):
            if {first.lower(), last.lower()} & GENERIC_WORDS:  # "Media Relations" is a mailbox, not a person
                continue
            p = match_pattern(local, ascii_lower(first), ascii_lower(last).replace("'", "").replace("-", ""))
            if p:
                votes[p] += 3 if year >= 2020 else 2 if year >= 2015 else 1
                examples.append(f"{first} {last} -> {local}@{domain} ({year})")
                break
    return votes, examples


# ---------------------------------------------------------- step 0: discover

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".search_cache.json")
FIRM_STOPWORDS = {"partners", "securities", "co", "company", "inc", "llc", "group", "capital",
                  "markets", "financial", "corp", "the", "and", "international", "america", "americas"}
SKIP_TITLE = re.compile(r"\b(intern|summer|incoming|student|candidate|wealth|financial advisor|"
                        r"director|head of|former|retired|recruit\w*)\b")
UT_MARKERS = ("university of texas at austin", "ut austin", "mccombs", "texas at austin")


def firm_key(name):
    name = re.sub(r"\(.*?\)", " ", ascii_lower(name))
    return " ".join(t for t in re.sub(r"[^a-z0-9 ]", " ", name).split() if t not in FIRM_STOPWORDS)


def same_firm(a, b):
    """Word-level match so 'Citi' doesn't match 'Citizens'. Either side's words may contain the other's."""
    ta, tb = set(firm_key(a).split()), set(firm_key(b).split())
    return bool(ta and tb) and (ta <= tb or tb <= ta)


def mentions_firm(text, firm):
    words = set(firm_key(text).split())
    key = set(firm_key(firm).split())
    return bool(key) and key <= words


def search_name(firm):
    """Name as it would appear on a LinkedIn profile: drop '(Mizuho)', '(PWP)', 'Inc.' etc."""
    return re.sub(r"\s*\(.*?\)|,?\s+(Inc\.?|LLC)$", "", firm).strip()


def norm_linkedin(url):
    m = re.search(r"linkedin\.com/in/([^/?#]+)", url or "", re.I)
    return f"https://www.linkedin.com/in/{urllib.parse.unquote(m.group(1)).lower()}/" if m else None


def web_search(query, cache):
    """Return [(url, title, snippet)] from Brave or Google; cached by query."""
    if query in cache:
        return cache[query], False
    brave, gkey, gcx = os.environ.get("BRAVE_API_KEY"), os.environ.get("GOOGLE_API_KEY"), os.environ.get("GOOGLE_CSE_ID")
    if brave:
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": 20})
        req = urllib.request.Request(url, headers={"X-Subscription-Token": brave, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        results = [(r["url"], r.get("title", ""), re.sub(r"<[^>]+>", "", r.get("description", "")))
                   for r in data.get("web", {}).get("results", [])]
        time.sleep(1.1)  # free plan is 1 request/second
    elif gkey and gcx:
        url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(
            {"key": gkey, "cx": gcx, "q": query, "num": 10})
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
        results = [(r["link"], r.get("title", ""), r.get("snippet", "")) for r in data.get("items", [])]
    else:
        raise SystemExit("--discover needs BRAVE_API_KEY, or GOOGLE_API_KEY + GOOGLE_CSE_ID, in the environment.")
    cache[query] = results
    return results, True


def parse_profile(title, snippet, firm):
    """LinkedIn result titles look like 'Jane Doe - IB Analyst - Goldman Sachs | LinkedIn'.
    Return (name, headline) if it's a current UT Austin banker at `firm`, else None."""
    title = re.sub(r"\s*[|\-–]\s*LinkedIn.*$", "", title).strip()
    parts = [p.strip() for p in re.split(r"\s+[-–]\s+", title) if p.strip()]
    if len(parts) < 2:
        return None
    name, rest = parts[0], " - ".join(parts[1:])
    text = ascii_lower(f"{title} {snippet}")
    if not mentions_firm(text, firm):
        return None
    # the last title segment is the current employer when present; reject if it's another firm
    if len(parts) >= 3 and not mentions_firm(parts[-1], firm):
        return None
    if not any(m in text for m in UT_MARKERS):
        return None
    if SKIP_TITLE.search(ascii_lower(rest)):
        return None
    if not split_name(name):
        return None
    headline = parts[1] if len(parts) >= 3 else rest
    return name, headline


def discover(rows, targets_path, per_firm, max_queries):
    with open(targets_path, encoding="utf-8-sig") as f:
        targets = list(csv.DictReader(f))
    sheet_firms = sorted({r["Firm"] for r in rows})
    have_urls = {norm_linkedin(r["LinkedIn"]) for r in rows}
    counts = collections.Counter(r["Firm"] for r in rows)
    try:
        cache = json.load(open(CACHE_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}

    used = added = 0
    print(f"\nDiscovering people (top up to {per_firm} per firm, max {max_queries} queries):")
    for t in targets:
        # reuse the sheet's spelling of the firm so step 1 can apply its email pattern
        firm = next((s for s in sheet_firms if firm_key(s) == firm_key(t["Firm"])), None) \
            or next((s for s in sheet_firms if same_firm(s, t["Firm"])), t["Firm"])
        need = per_firm - counts[firm]
        if need <= 0:
            continue
        query = (f'site:linkedin.com/in "University of Texas at Austin" "{search_name(t["Firm"])}" '
                 f'("Investment Banking" OR "Analyst" OR "Associate" OR "Vice President")')
        if query not in cache:
            if used >= max_queries:
                print(f"  query budget reached; stopping before {t['Firm']}")
                break
            used += 1
        try:
            results, _ = web_search(query, cache)
        except urllib.error.HTTPError as e:
            print(f"  {t['Firm']}: search error {e.code}; stopping")
            break
        got = 0
        for url, title, snippet in results:
            li = norm_linkedin(url)
            if not li or li in have_urls:
                continue
            parsed = parse_profile(title, snippet, search_name(t["Firm"]))
            if not parsed:
                continue
            name, headline = parsed
            category = re.sub(r"\s*\(.*?\)", "", t["Category"]).strip()
            rows.append({"Category": "Regional (TX)" if category.startswith("Regional") else category,
                         "Firm": firm, "Name": name, "Title": headline, "Location": "", "Email": "",
                         "Source": "", "LinkedIn": li, "Note": "Found via web search - check title/school",
                         "Confidence": "", "Alt Guess": ""})
            have_urls.add(li)
            got += 1
            if got >= need:
                break
        added += got
        print(f"  {t['Firm']:40} +{got}")

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    print(f"Added {added} people using {used} new queries (cached queries are free).")


# --------------------------------------------------------------- step 3: fill

def best(counter):
    (pat, n), total = counter.most_common(1)[0], sum(counter.values())
    return pat, n, total


def guess(pattern, first, last):
    if pattern == "first.x.last":  # can't know the middle initial; plain first.last is the safer guess
        pattern = "first.last"
    return PATTERNS[pattern](first, last)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--no-sec", action="store_true", help="skip SEC lookups for unseeded firms")
    ap.add_argument("--discover", action="store_true", help="find new people via web search first")
    ap.add_argument("--targets", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "IB_Target_List.csv"))
    ap.add_argument("--per-firm", type=int, default=5, help="top each firm up to this many people")
    ap.add_argument("--max-queries", type=int, default=100, help="cap on new (uncached) search queries")
    args = ap.parse_args()

    with open(args.csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys())
    for col in ("Source", "Confidence", "Alt Guess"):
        if col not in fields:
            fields.append(col)

    if args.discover:
        discover(rows, args.targets, args.per_firm, args.max_queries)

    pats, domains = learn_from_csv(rows)
    print("Patterns learned from confirmed emails:")
    for firm in sorted(pats):
        p, n, t = best(pats[firm])
        print(f"  {firm:38} {p:12} {n}/{t}  @{domains[firm].most_common(1)[0][0]}")

    missing_firms = {r["Firm"] for r in rows if "@" not in r["Email"]}
    sec_firms = set()
    if not args.no_sec:
        print("\nSEC EDGAR lookups for firms with no confirmed email:")
        for firm in sorted(missing_firms):
            if firm in pats and best(pats[firm])[0] != "other":
                continue
            for domain in FIRM_DOMAINS.get(firm, []):
                votes, ex = learn_from_sec(domain)
                if votes:
                    sec_firms.add(firm)
                    pats[firm] = votes
                    domains[firm] = collections.Counter({domain: sum(votes.values())})
                    p, n, t = best(votes)
                    print(f"  {firm:38} {p:12} {n}/{t}  @{domain}   e.g. {ex[0]}")
                    break
                print(f"  {firm:38} no usable examples at @{domain}")

    filled = 0
    for r in rows:
        r.setdefault("Confidence", "")
        r.setdefault("Alt Guess", "")
        if "@" in r["Email"]:
            if not r.get("Confidence"):
                r["Confidence"] = "Confirmed"
            continue
        name = split_name(r["Name"])
        firm = r["Firm"]
        if not name or firm not in pats:
            continue
        p, n, t = best(pats[firm])
        if p == "other":
            continue
        domain = domains[firm].most_common(1)[0][0]
        r["Email"] = f"{guess(p, *name)}@{domain}"
        via = "SEC filings" if firm in sec_firms else "confirmed emails"
        r["Source"] = f"Pattern from {via} ({p}, {n}/{t})"
        r["Confidence"] = "High" if n >= 3 and n / t >= 0.75 else "Medium" if n / t >= 0.5 else "Low"
        alts = [f"{guess(q, *name)}@{domain}" for q, _ in pats[firm].most_common(3)[1:] if q != "other"]
        # acquired firms: also offer the parent's domain (Greenhill -> mizuhogroup.com)
        alts += [f"{guess(p, *name)}@{d}" for d in FIRM_DOMAINS.get(firm, []) if d != domain]
        nick = re.search(r"\((\w+)\)", r["Name"])  # e.g. Jane (Wei) Doe
        if nick:
            alts.insert(0, f"{guess(p, ascii_lower(nick.group(1)), name[1])}@{domain}")
        r["Alt Guess"] = "; ".join(dict.fromkeys(a for a in alts if a != r["Email"]))
        r["Note"] = "Guessed from firm pattern - unverified"
        filled += 1

    shutil.copy(args.csv_path, args.csv_path + ".bak")
    with open(args.csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    still = [f"{r['Firm']}: {r['Name']}" for r in rows if "@" not in r["Email"]]
    print(f"\nFilled {filled} emails. {len(still)} still missing:")
    for s in still:
        print("  " + s)


if __name__ == "__main__":
    sys.exit(main())
