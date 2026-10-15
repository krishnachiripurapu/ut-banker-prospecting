#!/usr/bin/env python3
"""Coverage Desk daily update helper.

A scheduled Claude session makes the tool calls (ArtifactData, Gmail, Clay, Indeed).
This script does the bookkeeping, so every run applies the same rules as the page:

    python3 desk_daily.py plan  DB_DIR        > WORK/plan.json
    python3 desk_daily.py apply DB_DIR WORK   > WORK/summary.json

DB_DIR   ArtifactData `list` dumps made with out_dir:
         DB_DIR/contacts/*.json, DB_DIR/targets/*.json, DB_DIR/config/*.json, DB_DIR/jobs/*.json
WORK/inputs.jsonl
         one line per connector result the session saved to a file:
         {"kind": "gmail_sent" | "gmail_reply" | "gmail_bounce" | "clay" | "indeed", "path": "...", "firm_id": "<indeed only>"}
apply writes WORK/writes/*.json (one document or patch each) and WORK/writes.json:
         [{"op": "set" | "update", "collection": ..., "doc_id": ..., "file_path": ..., "existing": bool}]
         Entries with existing=true need the document's current version as if_version.

Nothing here sends email or spends Clay credits; it only reads what the session already fetched.
"""
import collections
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime

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
    "excl": re.compile(r"\b(audit|compliance|engineer\w*|developer|software|technologist|operations|ops|human resources|recruit\w*|legal|counsel|paralegal|assistant|administrative|receptionist|wealth|tax|accountant|accounting|controller|data|security|facilities|marketing|sales|trader|trading|risk|kyc|aml|cyber|payroll|investor relations|client service|fund administration|reporting|treasury|research)\b", re.I),
    "pcStrong": re.compile(r"\b(private credit|direct lending|private debt|clo|asset[- ]based|specialty finance|special situations|opportunistic credit)\b", re.I),
    "ib": re.compile(r"\b(investment bank\w*|ib|m&a|mergers|advisory|capital markets|ecm|dcm|leveraged finance|levfin|restructuring|coverage|financial institutions|fig|tmt|sponsors?|capital solutions|corporate finance|private capital|strategic|generalist)\b", re.I),
    "pc": re.compile(r"\b(credit|lending|loans?|debt|underwrit\w*|distressed|mezzanine)\b", re.I),
}
EMAIL_RE = re.compile(r"[A-Z0-9._%+'-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
WATCH = ("lead", "drafted", "emailed", "fu1", "fu2")
RANK = {"lead": 0, "drafted": 0, "emailed": 1, "fu1": 2, "fu2": 3}
MAX_CLAY_BATCHES = 6      # 10 firms per Clay search
MAX_INDEED_FIRMS = 10     # 2 Indeed searches per firm


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


def classify(title, firm):
    t = str(title or "")
    intern = bool(JRX["intern"].search(t))
    cat = "other"
    if not JRX["excl"].search(t):
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
        jobs.append({"title": b.split("\n")[0].strip(), "company": g("Company"), "location": g("Location"),
                     "posted": g("Posted on"), "type": g("Job Type"), "pay": g("Compensation"), "url": url})
    return jobs


def q(s):
    return '"' + re.sub(r'["\\]', "", str(s)) + '"'


def build_dsl(profile):
    names = [n for n in profile.get("schoolNames") or [] if n]
    kw = profile.get("schoolKeyword") or ""
    edu = []
    if names:
        edu.append(f"school_name in ({', '.join(q(n) for n in names)})")
    if kw:
        edu.append(f"school_name contains {q(kw)}")
    return (f"select from people where education.any({' or '.join(edu)}) and experiences.any(is_current = true and "
            f"job_title is_similar_to ({', '.join(q(t) for t in TITLES)}))")


# ------------------------------------------------------------------ plan

def plan(db_dir):
    d, now = Desk(db_dir), now_ms()
    sod = day_start(now)
    watch = sorted({c["email"].lower() for c in d.contacts.values() if c.get("email") and (c.get("stage") or "lead") in WATCH})
    chunks = [watch[i:i + 12] for i in range(0, len(watch), 12)]
    gmail = []
    for ch in chunks:
        ors = " OR ".join(ch)
        gmail.append({"kind": "gmail_sent", "query": f"in:sent to:({ors}) newer_than:180d", "view": "THREAD_VIEW_METADATA_ONLY"})
        gmail.append({"kind": "gmail_reply", "query": f"from:({ors}) newer_than:180d", "view": "THREAD_VIEW_METADATA_ONLY"})
    if watch:
        gmail.append({"kind": "gmail_bounce", "query": "from:(mailer-daemon OR postmaster) newer_than:60d", "view": "THREAD_VIEW_MINIMAL"})

    added_today = sum(1 for c in d.contacts.values() if (c.get("createdAt") or 0) >= sod)
    quota = max(0, int(d.profile.get("dailyLeadGoal") or 50) - added_today)
    targets = sorted([f for f in d.firms if f["on"] and f.get("domain")], key=lambda f: (d.count[f["id"]], f["name"]))
    batches = []
    if quota:
        dsl = build_dsl(d.profile)
        for i in range(0, min(len(targets), MAX_CLAY_BATCHES * 10), 10):
            ch = targets[i:i + 10]
            batches.append({"firm_ids": [f["id"] for f in ch], "companyIdentifiers": [f["domain"] for f in ch], "dslQuery": dsl})

    stale = sorted([f for f in d.firms if f["on"]], key=lambda f: (d.jobs.get(f["id"], {}).get("pulledAt") or 0))
    indeed = []
    for f in stale:
        if now - (d.jobs.get(f["id"], {}).get("pulledAt") or 0) < 20 * 3600_000 or len(indeed) >= MAX_INDEED_FIRMS:
            continue
        name = INDEED_Q.get(f["id"], f["name"])
        qs = [f"{name} credit", f"{name} analyst"] if f["type"] == "PC" else [f"{name} investment banking", f"{name} analyst"]
        indeed.append({"firm_id": f["id"], "queries": [{"search": s, "location": "United States", "country_code": "US"} for s in qs]})

    return {"now": now, "watched_emails": len(watch), "gmail": gmail,
            "clay": {"quota": quota, "batches": batches}, "indeed": indeed}


# ------------------------------------------------------------------ apply

def apply(db_dir, work):
    d, now = Desk(db_dir), now_ms()
    inputs = []
    path = os.path.join(work, "inputs.jsonl")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            inputs = [json.loads(l) for l in f if l.strip()]
    sent, replies, bounced = collections.defaultdict(list), collections.defaultdict(list), set()
    clay_payloads, indeed_by_firm = [], collections.defaultdict(list)
    for inp in inputs:
        try:
            data = load_json_or_text(inp["path"])
        except OSError:
            continue
        kind = inp.get("kind")
        threads = data.get("threads", []) if isinstance(data, dict) else []
        if kind == "gmail_sent":
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
        elif kind == "clay" and isinstance(data, dict):
            clay_payloads.append(data)
        elif kind == "indeed":
            indeed_by_firm[inp.get("firm_id")].extend(parse_indeed(data))

    writes, summary = [], collections.Counter()
    os.makedirs(os.path.join(work, "writes"), exist_ok=True)

    def emit(op, coll, doc_id, body, existing):
        fp = os.path.join(work, "writes", f"{coll}__{doc_id}.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(body, f)
        writes.append({"op": op, "collection": coll, "doc_id": doc_id, "file_path": fp, "existing": existing})

    # Gmail: sent, replies, bounces (same rules as syncGmail in the page)
    for cid, c in d.contacts.items():
        e = (c.get("email") or "").lower()
        if not e or (c.get("stage") or "lead") not in WATCH:
            continue
        stage, patch = c.get("stage") or "lead", {}
        rp = sorted(x for x in replies.get(e, []) if x)
        if e in bounced and not rp:
            bl = sorted(set((c.get("bounced") or []) + [e]))
            nxt = next((a for a in c.get("altEmails") or [] if a not in bl), "") or d.guess_email(c, bl)
            patch = {"bounced": bl, "email": nxt, "emailSrc": "pattern" if nxt else "", "stage": "lead",
                     "log": add_log(c, f"Bounced: {e}" + (f". Trying {nxt}" if nxt else ""), now)}
            summary["bounces"] += 1
        else:
            s = sorted((x for x in sent.get(e, []) if x["at"]), key=lambda x: x["at"])
            if s:
                first, last = s[0], s[-1]
                if not c.get("emailedAt") or first["at"] < c["emailedAt"]:
                    patch["emailedAt"] = first["at"]
                if not c.get("lastSentAt") or last["at"] > c["lastSentAt"]:
                    patch.update(lastSentAt=last["at"], lastMsgId=last["id"], threadId=last["threadId"])
                if c.get("sentCount") != len(s):
                    patch["sentCount"] = len(s)
                want = "fu2" if len(s) >= 3 else "fu1" if len(s) == 2 else "emailed"
                if RANK[want] > RANK.get(stage, 0):
                    patch["stage"] = want
                    if RANK.get(stage, 0) == 0:
                        patch["log"] = add_log(c, "Sent from Gmail", now)
                        summary["newlySent"] += 1
            emailed_at = patch.get("emailedAt") or c.get("emailedAt")
            if rp and (not emailed_at or rp[-1] >= emailed_at - DAY):
                patch.update(stage="replied", repliedAt=rp[-1], emailSrc="replied", log=add_log(c, "They replied", now))
                summary["replies"] += 1
        if patch:
            emit("update", "contacts", cid, patch, True)

    # Clay: new leads, spread across the firms with the fewest people first
    quota = max(0, int(d.profile.get("dailyLeadGoal") or 50) - sum(1 for c in d.contacts.values() if (c.get("createdAt") or 0) >= day_start(now)))
    seen, per_firm = set(d.contacts), collections.defaultdict(list)
    for p in clay_payloads:
        for c in p.get("contacts") or []:
            company = c.get("latest_experience_company") or ""
            dom = str(c.get("domain") or c.get("companyIdentifier") or "").lower()
            f = d.match_firm(company) or next((x for x in d.firms if x.get("domain") == dom), None)
            cid = contact_id(c.get("url"), c.get("name"), company)
            if cid in seen or not f or not c.get("name"):
                continue
            seen.add(cid)
            per_firm[f["id"]].append((cid, c, f, p.get("taskId")))
    order = sorted(per_firm, key=lambda fid: d.count[fid])
    picked = []
    while len(picked) < quota and any(per_firm[f] for f in order):
        for fid in order:
            if per_firm[fid] and len(picked) < quota:
                picked.append(per_firm[fid].pop(0))
    school = (d.profile.get("schoolNames") or [d.profile.get("school")])[0]
    for i, (cid, c, f, task) in enumerate(picked):
        first, last = split_name(c["name"])
        doc = {"name": c["name"], "first": first, "last": last, "firm": c.get("latest_experience_company") or f["name"], "firmId": f["id"],
               "title": c.get("latest_experience_title") or "", "location": c.get("location_name") or "", "linkedin": c.get("url") or "",
               "startedRole": c.get("latest_experience_start_date") or "", "school": school, "ties": [f"{d.profile.get('school')} alum"],
               "stage": "lead", "source": "Daily Clay search", "clayTask": task or "", "clayEntity": str(c.get("entityId") or c.get("profile_id") or ""),
               "createdAt": now + i, "log": [{"t": now, "d": "Added by the daily update (Clay school search)"}]}
        g = d.guess_email(doc)
        if g:
            doc.update(email=g, emailSrc="pattern")
        emit("set", "contacts", cid, doc, False)
        summary["newLeads"] += 1

    # Indeed: one document per firm, same shape the page writes
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
            k = (j["url"] or j["title"]).lower()
            if not j["title"] or k in seen_j:
                continue
            seen_j.add(k)
            cat, level = classify(j["title"], f)
            keep.append({**j, "cat": cat, "level": level})
        def posted(j):
            try:
                return datetime.strptime(j["posted"], "%B %d, %Y").timestamp()
            except ValueError:
                return 0
        keep.sort(key=posted, reverse=True)
        emit("set", "jobs", fid, {"pulledAt": now, "items": keep[:40]}, fid in d.jobs)
        summary["firmsPulled"] += 1
        summary["ibpcRoles"] += sum(1 for j in keep if j["cat"] in ("IB", "PC"))
        summary["internships"] += sum(1 for j in keep if j["level"] == "Intern")

    for k in ("newLeads", "replies", "newlySent", "bounces", "ibpcRoles", "internships", "firmsPulled"):
        summary.setdefault(k, 0)
    emit("set", "config", "lastRun", {"at": now, "ok": True, "summary": dict(summary)}, "lastRun" in d.config)
    with open(os.path.join(work, "writes.json"), "w", encoding="utf-8") as f:
        json.dump(writes, f, indent=1)
    return {"summary": dict(summary), "writes": len(writes), "existing_needing_version": sum(1 for w in writes if w["existing"])}


def main(argv):
    if len(argv) >= 3 and argv[1] == "plan":
        print(json.dumps(plan(argv[2]), indent=1))
    elif len(argv) >= 4 and argv[1] == "apply":
        print(json.dumps(apply(argv[2], argv[3]), indent=1))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
