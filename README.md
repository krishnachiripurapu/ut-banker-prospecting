# UT banker prospecting

Tools for networking into investment banking and private credit from UT Austin.

| Piece | What it does | Where it runs |
| --- | --- | --- |
| `desk/coverage-desk.html` | **Coverage Desk**. It opens each day on **Today**, a checklist built from everything below: replies to answer, calls to prep, thank-yous, application deadlines and interviews, Gmail drafts to send, follow-ups due, the day's cold emails (sized to your daily goal), new internship postings, and one-time setup, plus tasks you add yourself (today, tomorrow or every day). Each task has a time estimate and a button that does the work; doing the work clears the task, and check marks are kept per day. Then five steps. **Firms**: your wish list (type a name, search by city, or add firms that are posting IB and credit roles). **People**: bankers at those firms from Clay, alumni first, with emails guessed from each firm's format, verified with Clay, and exported to CSV with LinkedIn and more. **Outreach**: writes your 5-sentence cold emails and follow-ups in a review list, then saves them to Gmail drafts with your resume attached; Gmail tracks sends, replies and bounces. **Jobs**: per-firm and nationwide postings from Indeed, new drops flagged, statuses tracked. **Resumes**: a master resume, a one-page version tailored to each role (pulls the posting, reads the group, level and must-haves, downloads as PDF), a line-by-line fixer and an answer bank. | Published as a private Claude artifact. This file is the source. |
| `desk/automation/desk_daily.py` + `DAILY_RUNBOOK.md` | The **morning run**. A scheduled Claude task runs at 6:51 AM Central: checks Gmail for sends, replies and bounces; finds new people at your wish-list firms (alumni first) up to your daily goal; verifies up to your daily number of emails with Clay; pulls new job postings and flags new drops; and adds up to 5 firms a day that are posting IB or credit roles. | Claude scheduled task (cloud). Read-only on Gmail; never sends. |
| `email_finder.py` | Finds people with search-engine X-ray queries, learns firm email formats from confirmed emails and SEC EDGAR filings, and fills missing emails. | Your computer (it needs `BRAVE_API_KEY` and access to sec.gov). |
| `IB_Target_List.csv` | The 80-firm target list. Already loaded into the desk with Texas offices and notes. | Reference. |

## Using email_finder.py with the desk

1. In the desk, **Settings → Export for email_finder.py** downloads `UT_Alumni_Bankers.csv` in this script's column format, plus a `DeskId` column.
2. Run it:
   ```
   python email_finder.py UT_Alumni_Bankers.csv --discover --targets IB_Target_List.csv --per-firm 5
   ```
   Confirmed emails go out filled so the script can learn each firm's pattern; guesses go out blank so the script fills them.
3. In the desk, **Add people → Upload a CSV** and pick the same file. Rows match back by `DeskId` (or LinkedIn URL for people the script discovered). Verified emails are never overwritten by guesses, and addresses that already bounced are skipped.

`UT_Alumni_Bankers.csv` holds real names and emails, so it is in `.gitignore`. Keep it off GitHub.

## Updating the desk

Edit `desk/coverage-desk.html`, then ask Claude to republish it to the same artifact URL. The daily task reads `automation/desk_daily.py` from the published artifact, so republish after changing the script too.
