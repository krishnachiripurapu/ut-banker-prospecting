# UT banker prospecting

Tools for networking into investment banking and private credit from UT Austin.

| Piece | What it does | Where it runs |
| --- | --- | --- |
| `desk/coverage-desk.html` | **Coverage Desk**: alumni search by school (Clay), email guessing and verification, Gmail drafts in your voice with your resume attached, reply/bounce tracking, firm discovery, per-firm job pulls (Indeed), applications, and a line-by-line resume fixer. | Published as a private Claude artifact. This file is the source. |
| `desk/automation/desk_daily.py` + `DAILY_RUNBOOK.md` | The **daily update**. A scheduled Claude task runs at 6:51 AM Central: checks Gmail for sends, replies and bounces, adds new UT alumni from Clay up to your daily lead goal, refreshes job postings, and writes a summary the desk shows on Today. | Claude scheduled task (cloud). Read-only on Gmail; never sends. |
| `email_finder.py` | Finds people with search-engine X-ray queries, learns firm email formats from confirmed emails and SEC EDGAR filings, and fills missing emails. | Your computer (it needs `BRAVE_API_KEY` and access to sec.gov). |
| `IB_Target_List.csv` | The 80-firm target list. Already loaded into the desk with Texas offices and notes. | Reference. |

## Using email_finder.py with the desk

1. In the desk, **Setup → Export leads for your email finder** downloads `UT_Alumni_Bankers.csv` in this script's column format, plus a `DeskId` column.
2. Run it:
   ```
   python email_finder.py UT_Alumni_Bankers.csv --discover --targets IB_Target_List.csv --per-firm 5
   ```
   Confirmed emails go out filled so the script can learn each firm's pattern; guesses go out blank so the script fills them.
3. In the desk, **Bulk add → Import CSV** and pick the same file. Rows match back by `DeskId` (or LinkedIn URL for people the script discovered). Verified emails are never overwritten by guesses, and addresses that already bounced are skipped.

`UT_Alumni_Bankers.csv` holds real names and emails, so it is in `.gitignore`. Keep it off GitHub.

## Updating the desk

Edit `desk/coverage-desk.html`, then ask Claude to republish it to the same artifact URL. The daily task reads `automation/desk_daily.py` from the published artifact, so republish after changing the script too.
