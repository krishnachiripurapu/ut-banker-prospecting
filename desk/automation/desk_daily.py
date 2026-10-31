#!/usr/bin/env python3
"""Coverage Desk daily update helper.

A scheduled Claude session makes the tool calls (ArtifactData, Outlook or Gmail, Clay, Indeed).
This script does the bookkeeping, so every run applies the same rules as the page:

    python3 desk_daily.py plan  DB_DIR                     > WORK/plan.json
    python3 desk_daily.py apply DB_DIR WORK                > WORK/summary.json
    python3 desk_daily.py firms DB_DIR WORK domains.json   (adds hiring firms to the wish list)

DB_DIR   ArtifactData `list` dumps made with out_dir:
         DB_DIR/contacts/*.json, DB_DIR/targets/*.json, DB_DIR/config/*.json, DB_DIR/jobs/*.json
WORK/inputs.jsonl
         one line per connector result the session saved to a file:
         {"kind": "outlook_sent" | "outlook_inbox" | "gmail_sent" | "gmail_reply" | "gmail_bounce" | "clay" | "clay_tx" | "clay_any" | "clay_lookup" | "clay_verify"
                  | "indeed" | "indeed_open", "path": "...", "firm_id": "<indeed only>", "chunk": <clay_lookup/clay_verify only>}
apply writes WORK/writes/*.json (one document or patch each) and WORK/writes.json:
         [{"op": "set" | "update", "collection": ..., "doc_id": ..., "file_path": ..., "existing": bool}]
         Entries with existing=true need the document's current version as if_version.

Nothing here sends email. Clay credits are spent only by the session's add-contact-data-points calls,
which plan["verify"] caps at the profile's verifyPerDay.
"""
import collections
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    TZ = None

DAY = 86_400_000

# Mirrors LIB in coverage-desk.html: id, name, type, website domain (Clay), email domain, default pattern, name regexes.
LIB = [
    ("gs", "Goldman Sachs", "BB", "goldmansachs.com", "gs.com", "first.last", [r"goldman"]),
    ("ms", "Morgan Stanley", "BB", "morganstanley.com", "morganstanley.com", "first.last", [r"morgan stanley"]),
    ("jpm", "J.P. Morgan", "BB", "jpmorganchase.com", "jpmorgan.com", "first.last", [r"j\.?\s?p\.?\s?morgan", r"jpmorgan"]),
    ("bofa", "BofA Securities", "BB", "bankofamerica.com", "bofa.com", "first.last", [r"bank of america", r"\bbofa\b"]),
    ("citi", "Citi", "BB", "citi.com", "citi.com", "first.last", [r"\bciti(group|bank)?\b"]),
    ("barclays", "Barclays", "BB", "barclays.com", "barclays.com", "first.last", [r"barclays"]),
    ("wf", "Wells Fargo", "BB", "wellsfargo.com", "wellsfargo.com", "first.last", [r"wells fargo"]),
    ("db", "Deutsche Bank", "BB", "db.com", "db.com", "first.last", [r"deutsche bank"]),
    ("ubs", "UBS", "BB", "ubs.com", "ubs.com", "first.last", [r"\bubs\b"]),
    ("rbc", "RBC Capital Markets", "BB", "rbccm.com", "rbccm.com", "first.last", [r"\brbc\b"]),
    ("evr", "Evercore", "EB", "evercore.com", "evercore.com", "first.last", [r"evercore"]),
    ("laz", "Lazard", "EB", "lazard.com", "lazard.com", "first.last", [r"lazard"]),
    ("moelis", "Moelis & Company", "EB", "moelis.com", "moelis.com", "first.last", [r"moelis"]),
    ("pjt", "PJT Partners", "EB", "pjtpartners.com", "pjtpartners.com", "first.last", [r"\bpjt\b"]),
    ("cvp", "Centerview Partners", "EB", "centerview.com", "centerview.com", "flast", [r"centerview"]),
    ("pwp", "Perella Weinberg", "EB", "pwpartners.com", "pwpartners.com", "flast", [r"perella"]),
    ("gugg", "Guggenheim Securities", "EB", "guggenheimpartners.com", "guggenheimpartners.com", "first.last", [r"guggenheim"]),
    ("qatalyst", "Qatalyst Partners", "EB", "qatalyst.com", "qatalyst.com", "first.last", [r"qatalyst"]),
    ("roth", "Rothschild & Co", "EB", "rothschildandco.com", "rothschildandco.com", "first.last", [r"rothschild"]),
    ("hl", "Houlihan Lokey", "MM", "hl.com", "hl.com", "flast", [r"houlihan"]),
    ("jef", "Jefferies", "MM", "jefferies.com", "jefferies.com", "flast", [r"jefferies"]),
    ("wb", "William Blair", "MM", "williamblair.com", "williamblair.com", "flast", [r"william blair"]),
    ("hw", "Harris Williams", "MM", "harriswilliams.com", "harriswilliams.com", "flast", [r"harris williams"]),
    ("baird", "Baird", "MM", "rwbaird.com", "rwbaird.com", "flast", [r"\bbaird\b"]),
    ("lincoln", "Lincoln International", "MM", "lincolninternational.com", "lincolninternational.com", "flast", [r"lincoln international"]),
    ("piper", "Piper Sandler", "MM", "pipersandler.com", "psc.com", "first.last", [r"piper sandler"]),
    ("rj", "Raymond James", "MM", "raymondjames.com", "raymondjames.com", "first.last", [r"raymond james"]),
    ("stephens", "Stephens", "MM", "stephens.com", "stephens.com", "first.last", [r"^stephens\b", r"stephens inc"]),
    ("bmo", "BMO Capital Markets", "MM", "bmo.com", "bmo.com", "first.last", [r"\bbmo\b"]),
    ("mizuho", "Mizuho Americas", "MM", "mizuhoamericas.com", "mizuhogroup.com", "first.last", [r"mizuho"]),
    ("truist", "Truist Securities", "MM", "truist.com", "truist.com", "first.last", [r"truist"]),
    ("bx", "Blackstone Credit", "PC", "blackstone.com", "blackstone.com", "first.last", [r"blackstone"]),
    ("apollo", "Apollo", "PC", "apollo.com", "apollo.com", "flast", [r"apollo global", r"^apollo\b"]),
    ("ares", "Ares Management", "PC", "aresmgmt.com", "aresmgmt.com", "flast", [r"\bares\b"]),
    ("owl", "Blue Owl Capital", "PC", "blueowl.com", "blueowl.com", "first.last", [r"blue owl"]),
    ("hps", "HPS Investment Partners", "PC", "hpspartners.com", "hpspartners.com", "first.last", [r"\bhps\b"]),
    ("golub", "Golub Capital", "PC", "golubcapital.com", "golubcapital.com", "flast", [r"golub"]),
    ("sixth", "Sixth Street", "PC", "sixthstreet.com", "sixthstreet.com", "flast", [r"sixth street"]),
    ("kkr", "KKR Credit", "PC", "kkr.com", "kkr.com", "first.last", [r"\bkkr\b"]),
    ("oaktree", "Oaktree Capital", "PC", "oaktreecapital.com", "oaktreecapital.com", "flast", [r"oaktree"]),
    ("carlyle", "Carlyle Global Credit", "PC", "carlyle.com", "carlyle.com", "first.last", [r"carlyle"]),
    ("antares", "Antares Capital", "PC", "antares.com", "antares.com", "first.last", [r"antares"]),
    ("monroe", "Monroe Capital", "PC", "monroecap.com", "monroecap.com", "flast", [r"monroe capital"]),
    ("churchill", "Churchill Asset Mgmt", "PC", "churchillam.com", "churchillam.com", "first.last", [r"churchill asset"]),
    ("mainst", "Main Street Capital", "PC", "mainstcapital.com", "mainstcapital.com", "flast", [r"main street capital"]),
    ("crestline", "Crestline Investors", "PC", "crestlineinvestors.com", "crestlineinvestors.com", "first.last", [r"crestline"]),
]
INDEED_Q = {"gs": "Goldman Sachs", "jpm": "JPMorgan", "bofa": "Bank of America", "citi": "Citi", "rbc": "RBC Capital Markets",
            "moelis": "Moelis", "pwp": "Perella Weinberg", "gugg": "Guggenheim Securities", "bx": "Blackstone", "owl": "Blue Owl",
            "hps": "HPS Investment Partners", "kkr": "KKR", "carlyle": "Carlyle", "oaktree": "Oaktree Capital",
            "churchill": "Churchill Asset Management", "mainst": "Main Street Capital", "crestline": "Crestline Investors",
            "mizuho": "Mizuho", "truist": "Truist Securities", "bmo": "BMO Capital Markets", "roth": "Rothschild",
            "cvp": "Centerview Partners", "apollo": "Apollo Global Management", "ares": "Ares Management",
            "sixth": "Sixth Street", "antares": "Antares Capital", "monroe": "Monroe Capital"}
PATS = {
    "first.last": lambda f, l: f"{f}.{l}", "flast": lambda f, l: f[0] + l, "firstlast": lambda f, l: f + l,
    "f.last": lambda f, l: f"{f[0]}.{l}", "first": lambda f, l: f, "first_last": lambda f, l: f"{f}_{l}",
    "last": lambda f, l: l, "firstl": lambda f, l: f + l[0], "last.first": lambda f, l: f"{l}.{f}", "lastf": lambda f, l: l + f[0],
}
PAT_ORDER = list(PATS)
TITLES = ["Investment Banking Analyst", "Investment Banking Associate", "Summer Analyst",
          "Private Credit Analyst", "Private Credit Associate", "Direct Lending Associate"]
JRX = {
    "intern": re.compile(r"\b(intern|internship|summer|co-?op|insight|sophomore|freshman|fellowship|early careers?)\b", re.I),
    "excl": re.compile(r"\b(audit|compliance|engineer\w*|developer|software|technologist|operations|ops|human resources|recruiter|talent acquisition|legal|counsel|paralegal|assistant|administrative|receptionist|wealth|tax|accountant|accounting|controller|data|security|facilities|marketing|sales|trader|trading|risk|kyc|aml|cyber|payroll|investor relations|client service|fund administration|reporting|treasury|research|trainer|annotation|consultant|consulting|commission based)\b", re.I),
    "pcStrong": re.compile(r"\b(private credit|direct lending|private debt|clo|asset[- ]based|specialty finance|special situations|opportunistic credit)\b", re.I),
    "ib": re.compile(r"\b(investment bank\w*|ib|m&a|mergers|advisory|capital markets|ecm|dcm|leveraged finance|levfin|restructuring|coverage|financial institutions|fig|tmt|sponsors?|capital solutions|corporate finance|private capital|strategic|generalist)\b", re.I),
    "pc": re.compile(r"\b(credit|lending|loans?|debt|underwrit\w*|distressed|mezzanine)\b", re.I),
}
# People search results sometimes include roles outside banking and credit; skip those as leads.
NOT_BANKER = re.compile(r"\b(wealth|pwm|private wealth|sales|trading|s&t|quant\w*|risk|strateg(y|ist)|equity research|research|real estate|operations|technology|engineer\w*|audit|compliance|chief financial|cfo|marketing|human resources|product|data|mortgage|loan officer|mentor|candidate|student|teaching)\b", re.I)
BANKER = re.compile(r"\b(investment bank\w*|banking|m&a|mergers|capital markets|credit|lending|leveraged|restructuring|advisory|coverage|private equity|private capital|financial analyst|summer (analyst|associate)|analyst|associate|vice president|director|managing director)\b", re.I)
# Only postings with clearly IB or credit titles make a firm a wish-list candidate, and never staffing or gig sites.
STRONG_ROLE = re.compile(r"\b(investment bank\w*|m&a|mergers|private credit|direct lending|leveraged finance|restructuring|private debt)\b", re.I)
NOT_FIRM = re.compile(r"\b(selby jennings|afterquery|dataannotation|nepternship|recruit\w*|staffing|talent|search partners|experts|careers?)\b", re.I)
EMAIL_RE = re.compile(r"[A-Z0-9._%+'-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
WATCH = ("lead", "drafted", "emailed", "fu1", "fu2")
RANK = {"lead": 0, "drafted": 0, "emailed": 1, "fu1": 2, "fu2": 3}
RESEND_MS = 10 * 60_000
MAX_CLAY_BATCHES = 6      # 10 firms per Clay search
MAX_INDEED_FIRMS = 10     # 2 Indeed searches per firm
MAX_FALLBACK_BATCHES = 2  # banker-title searches without the school filter, only when alumni searches come up short
MAX_JOB_FIRMS = 5         # hiring firms added to the wish list per day
OPEN_SEARCHES = [         # nationwide and Texas IB / credit searches, saved as jobs/_open
    ("investment banking summer analyst", "United States"),
    ("private credit summer analyst", "United States"),
    ("direct lending analyst", "United States"),
    ("investment banking analyst", "Houston, TX"),
    ("investment banking analyst", "Dallas, TX"),
    ("investment banking", "Austin, TX"),
]


# ------------------------------------------------------------------ helpers

def now_ms():
    return int(time.time() * 1000)


def day_start(ms):
    dt = datetime.fromtimestamp(ms / 1000, TZ) if TZ else datetime.fromtimestamp(ms / 1000)
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def norm_name(s):
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower())


def split_name(full):
    s = re.sub(r"\(.*?\)", " ", str(full or ""))
    s = re.sub(r",.*$", "", s)
    s = re.sub(r"\b(CFA|CPA|CAIA|MBA|PhD|Jr\.?|Sr\.?|II|III|IV)\b", " ", s, flags=re.I)
    p = s.split()
    return (p[0] if p else "", p[-1] if len(p) > 1 else "")


def slug(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", s))[:100]


def contact_id(url, name, firm):
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", url or "", re.I)
    if m:
        from urllib.parse import unquote
        return "li-" + slug(unquote(m.group(1)))
    return "n-" + slug(f"{name}-{firm}")


def emails_in(s):
    return [e.lower() for e in EMAIL_RE.findall(str(s or ""))]


def msg_ms(m):
    for k in ("internalDate",):
        if str(m.get(k, "")).isdigit():
            return int(m[k])
    try:
        return int(datetime.fromisoformat(str(m.get("date", "")).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def iso_ms(v):
    try:
        return int(datetime.fromisoformat(str(v or "").replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def ms_iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def outlook_messages(data):
    """Every message object in a saved outlook_email_search result, whatever wrapper the session saved it in:
    a list of messages, MCP content blocks whose text is JSON, or several JSON objects in one text file."""
    out = {}

    def take(j):
        if isinstance(j, list):
            for x in j:
                take(x)
        elif isinstance(j, dict):
            if j.get("id") and any(k in j for k in ("subject", "sender", "recipients")):
                out[str(j["id"])] = j
                return
            if isinstance(j.get("text"), str):
                take_text(j["text"])
            for v in j.values():
                if isinstance(v, (list, dict)):
                    take(v)
        elif isinstance(j, str):
            take_text(j)

    def take_text(t):
        t = t.strip()
        if not t:
            return
        try:
            take(json.loads(t))
            return
        except ValueError:
            pass
        dec, i = json.JSONDecoder(), 0
        while True:
            i = t.find("{", i)
            if i < 0:
                break
            try:
                obj, end = dec.raw_decode(t, i)
            except ValueError:
                i += 1
                continue
            take(obj)
            i = end

    take(data)
    return list(out.values())


NDR_SUBJ = re.compile(r"^(undeliverable|undelivered|delivery (status notification|has failed)|mail delivery (failed|subsystem)|returned mail)", re.I)
AUTO_SUBJ = re.compile(r"^(automatic reply|auto(matic)?[- ]?reply|auto:|out of (the )?office)", re.I)


def add_log(c, text, t):
    return ([{"t": t, "d": text[:200]}] + list(c.get("log") or []))[:40]


def load_dir(path):
    out = {}
    if os.path.isdir(path):
        for fn in os.listdir(path):
            if fn.endswith(".json"):
                with open(os.path.join(path, fn), encoding="utf-8") as f:
                    out[fn[:-5]] = json.load(f)
    return out


def load_json_or_text(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    try:
        return json.loads(raw)
    except ValueError:
        return raw


# ------------------------------------------------------------------ state

class Desk:
    def __init__(self, db_dir):
        self.contacts = load_dir(os.path.join(db_dir, "contacts"))
        self.targets = load_dir(os.path.join(db_dir, "targets"))
        self.config = load_dir(os.path.join(db_dir, "config"))
        self.jobs = load_dir(os.path.join(db_dir, "jobs"))
        self.profile = {"schoolNames": ["The University of Texas at Austin"], "schoolKeyword": "McCombs",
                        "school": "UT Austin McCombs", "dailyLeadGoal": 50, **self.config.get("profile", {})}
        self.firms = []
        for fid, name, typ, dom, mail, pat, rx in LIB:
            t = self.targets.get(fid, {})
            f = {"id": fid, "name": name, "type": typ, "domain": dom, "mail": mail, "pat": pat, "rx": [re.compile(r, re.I) for r in rx]}
            f.update({k: v for k, v in (t.get("over") or {}).items() if k in ("type", "domain", "mail", "pat")})
            f["on"] = bool(t.get("on"))
            self.firms.append(f)
        for fid, t in self.targets.items():
            if t.get("custom") and t.get("name"):
                self.firms.append({"id": fid, "name": t["name"], "type": t.get("type", "MM"), "domain": (t.get("domain") or "").lower(),
                                   "mail": (t.get("mail") or t.get("domain") or "").lower(), "pat": t.get("pat") or "first.last",
                                   "rx": [re.compile(p, re.I) for p in (t.get("m") or [re.escape(t["name"])])], "on": bool(t.get("on"))})
        self.by_id = {f["id"]: f for f in self.firms}
        self._index()

    def match_firm(self, name):
        n = str(name or "").strip()
        if not n:
            return None
        return next((f for f in self.firms if any(r.search(n) for r in f["rx"])), None)

    def firm_of(self, c):
        return self.by_id.get(c.get("firmId") or "") or self.match_firm(c.get("firm"))

    def _index(self):
        self.count = collections.Counter()
        ok, bad = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
        for c in self.contacts.values():
            f = self.firm_of(c)
            if not f:
                continue
            self.count[f["id"]] += 1
            first, last = norm_name(c.get("first")), norm_name(c.get("last"))
            if c.get("email") and c.get("emailSrc") in ("clay", "import", "replied", "manual"):
                p = infer_pattern(c["email"], first, last)
                if p:
                    ok[f["id"]][p] += 1
            for b in c.get("bounced") or []:
                p = infer_pattern(b, first, last)
                if p:
                    bad[f["id"]][p] += 1
        self.pat = {}
        for f in self.firms:
            o, b = ok[f["id"]], bad[f["id"]]
            if o:
                p = o.most_common(1)[0][0]
            elif b[f["pat"]] >= 2:
                p = next((x for x in PAT_ORDER if not b[x]), f["pat"])
            else:
                p = f["pat"]
            self.pat[f["id"]] = (p, b)

    def guess_email(self, c, avoid=()):
        f = self.firm_of(c)
        first, last = norm_name(c.get("first")), norm_name(c.get("last"))
        if not f or not f.get("mail") or not first or len(last) < 2:
            return ""
        pat, bad = self.pat.get(f["id"], (f["pat"], {}))
        avoid = {a.lower() for a in avoid}
        for p in [pat] + [x for x in PAT_ORDER if x != pat and not bad.get(x)]:
            e = f"{PATS[p](first, last)}@{f['mail']}"
            if e not in avoid:
                return e
        return ""


def infer_pattern(email, first, last):
    local = str(email or "").lower().split("@")[0]
    if not first or not last:
        return None
    return next((p for p in PAT_ORDER if PATS[p](first, last) == local), None)


def classify(title, firm, company=""):
    t = str(title or "")
    intern = bool(JRX["intern"].search(t))
    cat = "other"
    if firm is None and company:
        # Unknown employer: only clearly IB or private-credit titles count, and never staffing or gig sites.
        if not JRX["excl"].search(t) and not NOT_FIRM.search(company):
            if JRX["pcStrong"].search(t):
                cat = "PC"
            elif STRONG_ROLE.search(t):
                cat = "IB"
    elif not JRX["excl"].search(t):
        if JRX["pcStrong"].search(t):
            cat = "PC"
        elif JRX["ib"].search(t):
            cat = "PC" if firm and firm["type"] == "PC" else "IB"
        elif JRX["pc"].search(t):
            cat = "PC"
        elif intern and re.search(r"analyst|associate", t, re.I):
            cat = "PC" if firm and firm["type"] == "PC" else "IB"
    if intern:
        level = "Intern"
    elif re.search(r"vice president|\bvp\b|director|principal|managing director|partner", t, re.I):
        level = "VP+"
    elif re.search(r"associate", t, re.I):
        level = "Associate"
    elif re.search(r"analyst", t, re.I):
        level = "Analyst"
    else:
        level = ""
    return cat, level


def parse_indeed(raw):
    text = raw.get("result", "") if isinstance(raw, dict) else str(raw)
    jobs = []
    for b in text.split("**Job Title:**")[1:]:
        def g(k):
            m = re.search(r"\*\*" + re.escape(k) + r":\*\*\s*([^\n]+)", b)
            return m.group(1).strip() if m else ""
        m = re.search(r"https?://\S+", g("View Job URL"))
        url = m.group(0) if m else ""
        jobs.append({"title": b.split("\n")[0].strip(), "jobId": g("Job Id"), "company": g("Company"), "location": g("Location"),
                     "posted": g("Posted on"), "type": g("Job Type"), "pay": g("Compensation"), "url": url})
    return jobs


def q(s):
    return '"' + re.sub(r'["\\]', "", str(s)) + '"'


def build_dsl(profile, alumni=True, names=None):
    titles = f"experiences.any(is_current = true and job_title is_similar_to ({', '.join(q(t) for t in TITLES)}))"
    if not alumni:
        return f"select from people where {titles}"
    if names is not None:  # an explicit school list (the Texas-college search) skips the home-school keyword
        kw = ""
    else:
        names = [n for n in profile.get("schoolNames") or [] if n]
        kw = profile.get("schoolKeyword") or ""
    edu = []
    if names:
        edu.append(f"school_name in ({', '.join(q(n) for n in names)})")
    if kw:
        edu.append(f"school_name contains {q(kw)}")
    return f"select from people where education.any({' or '.join(edu)}) and {titles}"


VERIFIED = ("clay", "import", "replied", "manual")


def job_key(j):
    # Indeed hands out a new short link on every search, so the link can't identify a posting.
    return re.sub(r"\s+", " ", f"{j.get('title')}|{j.get('company')}|{j.get('location')}").strip().lower()


def posted_ts(j):
    try:
        return datetime.strptime(j.get("posted") or "", "%B %d, %Y").timestamp()
    except ValueError:
        return 0


def verify_queue(d, now):
    """Leads whose email is a guess or missing, best first, capped at the daily verify budget."""
    budget = int(d.profile.get("verifyPerDay") if d.profile.get("verifyPerDay") is not None else 15)
    rows = []
    for cid, c in d.contacts.items():
        f = d.firm_of(c)
        if (c.get("stage") or "lead") != "lead" or (c.get("email") and c.get("emailSrc") in VERIFIED):
            continue
        if not f or not f.get("domain") or len(norm_name(c.get("last"))) < 2:
            continue
        if c.get("verifyTried") and now - c["verifyTried"] < 14 * DAY:
            continue
        rows.append((-len(c.get("ties") or []), c.get("createdAt") or 0, cid, c, f))
    rows.sort(key=lambda r: r[:2])
    return [(cid, c, f) for _, _, cid, c, f in rows[:max(0, budget)]]


# ------------------------------------------------------------------ plan

def plan(db_dir):
    d, now = Desk(db_dir), now_ms()
    sod = day_start(now)
    watch = sorted({c["email"].lower() for c in d.contacts.values() if c.get("email") and (c.get("stage") or "lead") in WATCH})
    chunks = [watch[i:i + 12] for i in range(0, len(watch), 12)]
    gmail, outlook = [], []
    mail = "gmail" if d.profile.get("mail") == "gmail" else "outlook"
    if mail == "outlook" and watch:
        last = int((d.config.get("lastRun") or {}).get("mailAt") or 0)
        sent_since = last - 3 * DAY if last else now - 120 * DAY
        inbox_since = max(sent_since, now - 14 * DAY)
        outlook = [{"kind": "outlook_sent", "args": {"folderName": "Sent Items", "afterDateTime": ms_iso(sent_since), "limit": 25}},
                   {"kind": "outlook_inbox", "args": {"folderName": "Inbox", "afterDateTime": ms_iso(inbox_since), "limit": 25}}]
        chunks = []
    for ch in chunks:
        ors = " OR ".join(ch)
        gmail.append({"kind": "gmail_sent", "query": f"in:sent to:({ors}) newer_than:180d", "view": "THREAD_VIEW_METADATA_ONLY"})
        gmail.append({"kind": "gmail_reply", "query": f"from:({ors}) newer_than:180d", "view": "THREAD_VIEW_METADATA_ONLY"})
    if watch and mail == "gmail":
        gmail.append({"kind": "gmail_bounce", "query": "from:(mailer-daemon OR postmaster) newer_than:60d", "view": "THREAD_VIEW_MINIMAL"})

    added_today = sum(1 for c in d.contacts.values() if (c.get("createdAt") or 0) >= sod)
    quota = max(0, int(d.profile.get("dailyLeadGoal") or 50) - added_today)
    targets = sorted([f for f in d.firms if f["on"] and f.get("domain")], key=lambda f: (d.count[f["id"]], f["name"]))
    batches, fallback, texas = [], [], []
    if quota:
        dsl = build_dsl(d.profile)
        for i in range(0, min(len(targets), MAX_CLAY_BATCHES * 10), 10):
            ch = targets[i:i + 10]
            batches.append({"firm_ids": [f["id"] for f in ch], "companyIdentifiers": [f["domain"] for f in ch], "dslQuery": dsl})
        # Alumni of other Texas colleges: a separate search, because Clay results do not say which school someone attended.
        home = {n.lower() for n in d.profile.get("schoolNames") or []}
        tx_names = [n for n in d.profile.get("texasSchools") or [] if n and n.lower() not in home]
        if tx_names:
            tx_dsl = build_dsl(d.profile, names=tx_names)
            for i in range(0, min(len(targets), MAX_CLAY_BATCHES * 10), 10):
                ch = targets[i:i + 10]
                texas.append({"firm_ids": [f["id"] for f in ch], "companyIdentifiers": [f["domain"] for f in ch], "dslQuery": tx_dsl})
        any_dsl = build_dsl(d.profile, alumni=False)
        for i in range(0, min(len(targets), MAX_FALLBACK_BATCHES * 10), 10):
            ch = targets[i:i + 10]
            fallback.append({"firm_ids": [f["id"] for f in ch], "companyIdentifiers": [f["domain"] for f in ch], "dslQuery": any_dsl})

    vq = verify_queue(d, now)
    verify = [{"chunk": i // 10, "contact_ids": [cid for cid, _, _ in vq[i:i + 10]],
               "contactIdentifiers": [{"contactName": c["name"], "companyIdentifier": f["domain"]} for _, c, f in vq[i:i + 10]]}
              for i in range(0, len(vq), 10)]

    stale = sorted([f for f in d.firms if f["on"]], key=lambda f: (d.jobs.get(f["id"], {}).get("pulledAt") or 0))
    indeed = []
    for f in stale:
        if now - (d.jobs.get(f["id"], {}).get("pulledAt") or 0) < 20 * 3600_000 or len(indeed) >= MAX_INDEED_FIRMS:
            continue
        name = INDEED_Q.get(f["id"], f["name"])
        qs = [f"{name} credit", f"{name} analyst"] if f["type"] == "PC" else [f"{name} investment banking", f"{name} analyst"]
        indeed.append({"firm_id": f["id"], "queries": [{"search": s, "location": "United States", "country_code": "US"} for s in qs]})

    open_jobs = [{"search": s, "location": loc, "country_code": "US"} for s, loc in OPEN_SEARCHES]
    return {"now": now, "mail": mail, "watched_emails": len(watch), "outlook": outlook, "gmail": gmail,
            "clay": {"quota": quota, "batches": batches, "texas_batches": texas, "fallback_batches": fallback},
            "verify": verify, "indeed": indeed, "indeed_open": open_jobs}


# ------------------------------------------------------------------ apply

def apply(db_dir, work):
    d, now = Desk(db_dir), now_ms()
    inputs = []
    path = os.path.join(work, "inputs.jsonl")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            inputs = [json.loads(l) for l in f if l.strip()]
    sent, replies, bounced = collections.defaultdict(list), collections.defaultdict(list), set()
    o_sent, o_inbox, o_seen = [], [], False
    clay_payloads, indeed_by_firm, open_items = [], collections.defaultdict(list), []
    lookups, verifies = {}, []
    for inp in inputs:
        try:
            data = load_json_or_text(inp["path"])
        except OSError:
            continue
        kind = inp.get("kind")
        threads = data.get("threads", []) if isinstance(data, dict) else []
        if kind == "outlook_sent":
            o_sent.extend(outlook_messages(data))
            o_seen = True
        elif kind == "outlook_inbox":
            o_inbox.extend(outlook_messages(data))
            o_seen = True
        elif kind == "gmail_sent":
            for t in threads:
                for m in t.get("messages", []):
                    if "SENT" not in (m.get("labelIds") or []):
                        continue
                    for r in (m.get("toRecipients") or []) + (m.get("ccRecipients") or []):
                        for e in emails_in(r):
                            sent[e].append({"at": msg_ms(m), "id": m.get("id"), "threadId": m.get("threadId") or t.get("id")})
        elif kind == "gmail_reply":
            for t in threads:
                for m in t.get("messages", []):
                    for e in emails_in(m.get("sender")):
                        replies[e].append(msg_ms(m))
        elif kind == "gmail_bounce":
            for t in threads:
                for m in t.get("messages", []):
                    bounced.update(emails_in(f"{m.get('snippet', '')} {m.get('subject', '')}"))
        elif kind in ("clay", "clay_tx", "clay_any") and isinstance(data, dict):
            clay_payloads.append((data, {"clay": "home", "clay_tx": "texas"}.get(kind, "")))
        elif kind == "clay_lookup" and isinstance(data, dict):
            lookups[int(inp.get("chunk") or 0)] = data
        elif kind == "clay_verify" and isinstance(data, dict):
            verifies.append((int(inp.get("chunk") or 0), data))
        elif kind == "indeed":
            indeed_by_firm[inp.get("firm_id")].extend(parse_indeed(data))
        elif kind == "indeed_open":
            open_items.extend(parse_indeed(data))

    writes, summary = [], collections.Counter()
    os.makedirs(os.path.join(work, "writes"), exist_ok=True)

    def emit(op, coll, doc_id, body, existing):
        fp = os.path.join(work, "writes", f"{coll}__{doc_id}.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(body, f)
        writes.append({"op": op, "collection": coll, "doc_id": doc_id, "file_path": fp, "existing": existing})

    # One update per contact per run, so a single if_version covers it.
    cpatch = collections.OrderedDict()

    def patch_contact(cid, patch):
        cur = cpatch.setdefault(cid, {})
        logs = [l for l in patch.pop("log", []) if l.get("t") == now]
        cur.update(patch)
        if logs:
            base = cur.get("log") or list(d.contacts[cid].get("log") or [])
            cur["log"] = (logs + base)[:40]

    # Mail: sent, replies, bounces (same rules as applyMailSync in the page), keyed by contact id
    watch = {cid: c for cid, c in d.contacts.items() if c.get("email") and (c.get("stage") or "lead") in WATCH}
    by_email = {c["email"].lower(): cid for cid, c in watch.items()}
    s_by, r_by, b_by, fix, via = collections.defaultdict(list), collections.defaultdict(list), set(), {}, "gmail"
    if o_seen:
        via = "outlook"

        def by_name(e):
            local, _, dom = e.partition("@")
            l, hits = norm_name(local), []
            for cid, c in watch.items():
                f = d.firm_of(c) if hasattr(d, "firm_of") else None
                if dom not in ((c.get("email") or "").split("@")[-1], (f or {}).get("mail")):
                    continue
                last, first = norm_name(c.get("last")), norm_name(c.get("first"))
                if len(last) >= 3 and first and last in l and (first in l or l.startswith(first[0])):
                    hits.append(cid)
            return hits[0] if len(hits) == 1 else None

        for m in o_sent:
            at = iso_ms(m.get("sentDateTime") or m.get("receivedDateTime"))
            if not at:
                continue
            rc = set()
            for r in (m.get("recipients") or []) + [x.get("address") for x in (m.get("namedRecipients") or []) if isinstance(x, dict)]:
                rc.update(emails_in(r))
            for e in rc:
                cid = by_email.get(e)
                if not cid:
                    cid = by_name(e)
                    if cid:
                        fix[cid] = e
                        by_email[e] = cid
                if cid:
                    s_by[cid].append({"at": at, "id": m.get("id"), "threadId": m.get("conversationId") or "", "subject": m.get("subject") or ""})
        for m in o_inbox:
            at, subj = iso_ms(m.get("receivedDateTime") or m.get("sentDateTime")), str(m.get("subject") or "")
            frm = (emails_in(m.get("sender")) or [""])[0]
            if NDR_SUBJ.search(subj) or re.search(r"postmaster|mailer-daemon|microsoftexchange", frm, re.I):
                for e in emails_in(f"{m.get('summary', '')} {subj}"):
                    cid = by_email.get(e)
                    if cid and (s_by.get(cid) or watch[cid].get("emailedAt")):
                        b_by.add(cid)
                continue
            if AUTO_SUBJ.search(subj):
                continue
            cid = by_email.get(frm)
            if cid and at:
                r_by[cid].append(at)
    else:
        for e, lst in sent.items():
            if e in by_email:
                s_by[by_email[e]].extend(lst)
        for e, lst in replies.items():
            if e in by_email:
                r_by[by_email[e]].extend(lst)
        b_by = {by_email[e] for e in bounced if e in by_email}
    label = "Outlook" if via == "outlook" else "Gmail"

    for cid, c in watch.items():
        stage, patch = c.get("stage") or "lead", {}
        logs = []
        rp = sorted(x for x in r_by.get(cid, []) if x)
        used = fix.get(cid)
        if used and used != c["email"].lower() and cid not in b_by:
            patch["email"] = used
            logs.append(f"Using {used}, the address you emailed")
        e = (patch.get("email") or c["email"]).lower()
        if cid in b_by and not rp:
            bl = sorted(set((c.get("bounced") or []) + [e]))
            nxt = next((a for a in c.get("altEmails") or [] if a not in bl), "") or d.guess_email(c, bl)
            patch.update({"bounced": bl, "email": nxt, "emailSrc": "pattern" if nxt else "", "stage": "lead"})
            logs.append(f"Bounced: {e}" + (f". Trying {nxt}" if nxt else ""))
            summary["bounces"] += 1
        else:
            s_ = []
            for x in sorted((x for x in s_by.get(cid, []) if x["at"]), key=lambda x: x["at"]):
                # A resend within 10 minutes (say, to add a forgotten attachment) is the same email.
                if s_ and x["at"] - s_[-1]["at"] < RESEND_MS:
                    s_[-1] = x
                else:
                    s_.append(x)
            if s_:
                first, last = s_[0], s_[-1]
                if not c.get("emailedAt") or first["at"] < c["emailedAt"]:
                    patch["emailedAt"] = first["at"]
                if not c.get("lastSentAt") or last["at"] > c["lastSentAt"] or (not c.get("lastMsgId") and last["at"] >= c["lastSentAt"]):
                    patch.update(lastSentAt=last["at"], lastMsgId=last["id"], threadId=last["threadId"], lastVia=via)
                # Later runs look back only a few days, so count messages newer than the last one already counted.
                total = int(c.get("sentCount") or 0) + sum(1 for x in s_ if x["at"] > (c.get("lastSentAt") or 0) + RESEND_MS)
                if total != int(c.get("sentCount") or 0):
                    patch["sentCount"] = total
                if not c.get("subject"):
                    subj = re.sub(r"^(re|fw|fwd):\s*", "", last.get("subject") or (c.get("draft") or {}).get("subject") or "", flags=re.I)
                    if subj:
                        patch["subject"] = subj
                want = "fu2" if total >= 3 else "fu1" if total == 2 else "emailed"
                if RANK[want] > RANK.get(stage, 0):
                    patch["stage"] = want
                    if RANK.get(stage, 0) == 0:
                        logs.append(f"Sent from {label}")
                        summary["newlySent"] += 1
            emailed_at = patch.get("emailedAt") or c.get("emailedAt")
            if rp and (not emailed_at or rp[-1] >= emailed_at - DAY):
                patch.update(stage="replied", repliedAt=rp[-1], emailSrc="replied")
                logs.append("They replied")
                summary["replies"] += 1
        if logs:
            log = list(c.get("log") or [])
            for text in logs:
                log = add_log({"log": log}, text, now)
            patch["log"] = log
        if patch:
            patch_contact(cid, patch)

    # Clay: new leads, spread across the firms with the fewest people first
    quota = max(0, int(d.profile.get("dailyLeadGoal") or 50) - sum(1 for c in d.contacts.values() if (c.get("createdAt") or 0) >= day_start(now)))
    seen, per_firm = set(d.contacts), collections.defaultdict(list)
    for p, alum in clay_payloads:
        for c in p.get("contacts") or []:
            company = c.get("latest_experience_company") or ""
            dom = str(c.get("domain") or c.get("companyIdentifier") or "").lower()
            # Trust the employer name first; fall back to the search domain only when Clay gave no employer.
            f = d.match_firm(company) or (None if company else next((x for x in d.firms if x.get("domain") == dom), None))
            cid = contact_id(c.get("url"), c.get("name"), company)
            if cid in seen or not f or not c.get("name"):
                continue
            title = c.get("latest_experience_title") or ""
            if NOT_BANKER.search(title) or not BANKER.search(title) or re.search(r"\b(business|data) analyst\b", title, re.I):
                summary["skippedTitles"] += 1
                continue
            seen.add(cid)
            per_firm[f["id"]].append((cid, c, f, p.get("taskId"), alum))
    order = sorted(per_firm, key=lambda fid: d.count[fid])
    picked = []
    while len(picked) < quota and any(per_firm[f] for f in order):
        for fid in order:
            if per_firm[fid] and len(picked) < quota:
                picked.append(per_firm[fid].pop(0))
    school = (d.profile.get("schoolNames") or [d.profile.get("school")])[0]
    for i, (cid, c, f, task, alum) in enumerate(picked):
        first, last = split_name(c["name"])
        doc = {"name": c["name"], "first": first, "last": last, "firm": c.get("latest_experience_company") or f["name"], "firmId": f["id"],
               "title": c.get("latest_experience_title") or "", "location": c.get("location_name") or "", "linkedin": c.get("url") or "",
               "startedRole": c.get("latest_experience_start_date") or "", "school": school if alum == "home" else "",
               # Texas-college people get no school claim: the search does not say which Texas school they attended.
               "ties": [f"{d.profile.get('school')} alum"] if alum == "home" else (["Texas college alum (school unconfirmed)"] if alum == "texas" else []),
               "stage": "lead", "clayTask": task or "",
               "source": {"home": "Daily Clay search", "texas": "Daily Clay Texas-college search"}.get(alum, "Daily Clay banker search"),
               "clayEntity": str(c.get("entityId") or c.get("profile_id") or ""),
               "createdAt": now + i, "log": [{"t": now, "d": "Added by the daily update (" + {"home": "alumni", "texas": "Texas-college alumni"}.get(alum, "banker") + " search)"}]}
        g = d.guess_email(doc)
        if g:
            doc.update(email=g, emailSrc="pattern")
        emit("set", "contacts", cid, doc, False)
        summary["newLeads"] += 1

    # Indeed: one document per firm, same shape the page writes. firstSeen marks new drops.
    def merge_seen(prev_items, items):
        seen_at = {job_key(j): j.get("firstSeen") or 0 for j in prev_items or []}
        out = []
        for j in items:
            fs = seen_at.get(job_key(j)) or now
            if fs == now:
                summary["newRoles" if j.get("cat") in ("IB", "PC") else "newOther"] += 1
            out.append({**j, "firstSeen": fs})
        return out

    for fid, items in indeed_by_firm.items():
        f = d.by_id.get(fid)
        if not f:
            continue
        name = INDEED_Q.get(fid, f["name"])
        name_rx = re.compile(r"\b" + re.escape(name) + r"\b", re.I)
        keep, seen_j = [], set()
        for j in items:
            m = d.match_firm(j["company"])
            if not ((m and m["id"] == fid) or (not m and name_rx.search(j["company"] or ""))):
                continue
            k = job_key(j)
            if not j["title"] or k in seen_j:
                continue
            seen_j.add(k)
            cat, level = classify(j["title"], f)
            keep.append({**j, "cat": cat, "level": level})
        keep.sort(key=posted_ts, reverse=True)
        keep = merge_seen((d.jobs.get(fid) or {}).get("items"), keep[:40])
        emit("set", "jobs", fid, {"pulledAt": now, "items": keep}, fid in d.jobs)
        summary["firmsPulled"] += 1
        summary["ibpcRoles"] += sum(1 for j in keep if j["cat"] in ("IB", "PC"))
        summary["internships"] += sum(1 for j in keep if j["level"] == "Intern")

    # Indeed: IB and credit roles at any employer (jobs/_open), merged with what the page already has
    candidates = collections.OrderedDict()
    if open_items:
        prev = (d.jobs.get("_open") or {}).get("items") or []
        fresh, seen_j = [], set()
        for j in open_items:
            k = job_key(j)
            if not j["title"] or k in seen_j:
                continue
            seen_j.add(k)
            cat, level = classify(j["title"], d.match_firm(j["company"]), j["company"])
            fresh.append({**j, "cat": cat, "level": level})
        fresh.sort(key=posted_ts, reverse=True)
        fresh = merge_seen(prev, fresh)
        merged = fresh + [p for p in prev if job_key(p) not in seen_j]
        emit("set", "jobs", "_open", {"pulledAt": now, "items": merged[:150]}, "_open" in d.jobs)
        for j in fresh:
            if j["cat"] not in ("IB", "PC") or not STRONG_ROLE.search(j["title"]) or NOT_FIRM.search(j["company"] or ""):
                continue
            m = d.match_firm(j["company"])
            if m and m["on"]:
                continue
            nm = re.sub(r"\s+(LLC|L\.L\.C\.|Inc\.?|LP|L\.P\.)$", "", str(j["company"] or ""), flags=re.I).strip()
            if not nm:
                continue
            key = m["id"] if m else "c-" + slug(nm)
            cand = candidates.setdefault(key, {"name": m["name"] if m else nm, "firm_id": m["id"] if m else "", "roles": 0, "type": "PC" if j["cat"] == "PC" else ""})
            cand["roles"] += 1

    # Hiring firms go on the wish list (library firms now; others after the session resolves their websites)
    job_firms = []
    if d.profile.get("autoAddJobFirms", True) is not False:
        for key, cand in list(candidates.items())[:MAX_JOB_FIRMS]:
            if cand["firm_id"] and not cand["firm_id"].startswith("c-"):
                t = d.targets.get(cand["firm_id"], {})
                emit("set", "targets", cand["firm_id"], {**t, "on": True, "addedFrom": "jobs", "addedAt": now}, cand["firm_id"] in d.targets)
                summary["firmsFromJobs"] += 1
            elif key in d.targets and d.targets[key].get("domain"):
                emit("set", "targets", key, {**d.targets[key], "on": True}, True)
                summary["firmsFromJobs"] += 1
            else:
                job_firms.append({"id": key, "name": cand["name"], "type": cand["type"], "roles": cand["roles"]})
    with open(os.path.join(work, "firm_candidates.json"), "w", encoding="utf-8") as f:
        json.dump(job_firms, f, indent=1)

    # Clay email verification: map looked-up people back to contacts, then read the Email data point
    plan_path = os.path.join(work, "plan.json")
    plan_doc = load_json_or_text(plan_path) if os.path.exists(plan_path) else {}
    chunks = {v["chunk"]: v for v in (plan_doc.get("verify") or [])} if isinstance(plan_doc, dict) else {}
    ent_to_cid = {}
    for ch, data in lookups.items():
        want = {norm_name(d.contacts.get(cid, {}).get("name")): cid for cid in chunks.get(ch, {}).get("contact_ids", [])}
        for r in data.get("contacts") or []:
            cid = want.get(norm_name(r.get("name")))
            ent = str(r.get("entityId") or r.get("profile_id") or "")
            if cid and ent:
                ent_to_cid[(ch, ent)] = cid
    found, blocked = {}, set()
    for ch, data in verifies:
        for r in data.get("contacts") or data.get("entities") or data.get("results") or []:
            ent = str(r.get("entityId") or r.get("profile_id") or r.get("id") or "")
            cid = ent_to_cid.get((ch, ent))
            if not cid:
                continue
            hits = [e for e in (r.get("enrichments") or []) if re.search(r"email", str(e.get("name") or e.get("type") or e.get("dataPointName") or e.get("title") or ""), re.I)]
            if hits and all(str(h.get("state") or "").lower() == "error" for h in hits):
                blocked.add(cid)  # Clay refused (plan limit or outage): try again another day
                continue
            c = d.contacts[cid]
            f = d.firm_of(c)
            emails = [e for e in emails_in(json.dumps([h.get("value") for h in hits])) if e not in (c.get("bounced") or [])]
            best = next((e for e in emails if f and f.get("mail") and e.endswith("@" + f["mail"])), emails[0] if emails else "")
            if best:
                found[cid] = best
    tried = {cid for ch, _ in verifies for cid in chunks.get(ch, {}).get("contact_ids", [])}
    summary["verifyBlocked"] += len(blocked)
    for cid in tried - blocked:
        c = d.contacts.get(cid)
        if not c:
            continue
        if cid in found:
            patch_contact(cid, {"email": found[cid], "emailSrc": "clay", "verifyTried": now,
                                "log": add_log(c, f"Clay verified {found[cid]} (daily update)", now)})
            summary["verified"] += 1
        else:
            patch_contact(cid, {"verifyTried": now})
            summary["verifyMissed"] += 1

    for cid, patch in cpatch.items():
        emit("update", "contacts", cid, patch, True)

    for k in ("newLeads", "verified", "replies", "newlySent", "bounces", "newRoles", "ibpcRoles", "internships", "firmsPulled", "firmsFromJobs"):
        summary.setdefault(k, 0)
    run = {"at": now, "ok": True, "summary": dict(summary)}
    prev_mail = (d.config.get("lastRun") or {}).get("mailAt")
    if o_seen:
        run["mailAt"] = now
    elif prev_mail:
        run["mailAt"] = prev_mail
    emit("set", "config", "lastRun", run, "lastRun" in d.config)
    with open(os.path.join(work, "writes.json"), "w", encoding="utf-8") as f:
        json.dump(writes, f, indent=1)
    return {"summary": dict(summary), "writes": len(writes), "existing_needing_version": sum(1 for w in writes if w["existing"]),
            "firm_candidates": job_firms}


def add_job_firms(db_dir, work, domains_path):
    """Adds the hiring firms the session resolved to websites. domains.json: {"Firm name": {"domain": "x.com", "type": "MM"}}."""
    d, now = Desk(db_dir), now_ms()
    cands = load_json_or_text(os.path.join(work, "firm_candidates.json"))
    doms = load_json_or_text(domains_path) if os.path.exists(domains_path) else {}
    with open(os.path.join(work, "writes.json"), encoding="utf-8") as f:
        writes = json.load(f)
    added = []
    for cand in cands if isinstance(cands, list) else []:
        info = doms.get(cand["name"]) if isinstance(doms, dict) else None
        dom = str((info or {}).get("domain") or "").lower().strip()
        dom = re.sub(r"^https?://", "", dom).removeprefix("www.").split("/")[0]
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", dom):
            continue
        typ = (info or {}).get("type") or cand.get("type") or "MM"
        if typ not in ("BB", "EB", "MM", "TX", "PC"):
            typ = "MM"
        if any(f["on"] and (f.get("domain") == dom or f.get("mail") == dom) for f in d.firms):
            continue
        body = {**d.targets.get(cand["id"], {}), "custom": True, "name": cand["name"][:80], "type": typ, "domain": dom, "mail": dom,
                "pat": "first.last", "on": True, "addedFrom": "jobs", "addedAt": now, "m": [re.escape(cand["name"])],
                "note": f"Added by the daily update: posting {cand['roles']} IB or credit role" + ("s" if cand["roles"] != 1 else "")}
        fp = os.path.join(work, "writes", f"targets__{cand['id']}.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(body, f)
        writes.append({"op": "set", "collection": "targets", "doc_id": cand["id"], "file_path": fp, "existing": cand["id"] in d.targets})
        added.append(cand["name"])
    lr = os.path.join(work, "writes", "config__lastRun.json")
    if added and os.path.exists(lr):
        with open(lr, encoding="utf-8") as f:
            run = json.load(f)
        run["summary"]["firmsFromJobs"] = run["summary"].get("firmsFromJobs", 0) + len(added)
        run["summary"]["firmsAdded"] = added
        with open(lr, "w", encoding="utf-8") as f:
            json.dump(run, f)
    with open(os.path.join(work, "writes.json"), "w", encoding="utf-8") as f:
        json.dump(writes, f, indent=1)
    return {"added": added, "writes": len(writes)}


def main(argv):
    if len(argv) >= 3 and argv[1] == "plan":
        print(json.dumps(plan(argv[2]), indent=1))
    elif len(argv) >= 4 and argv[1] == "apply":
        print(json.dumps(apply(argv[2], argv[3]), indent=1))
    elif len(argv) >= 5 and argv[1] == "firms":
        print(json.dumps(add_job_firms(argv[2], argv[3], argv[4]), indent=1))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
