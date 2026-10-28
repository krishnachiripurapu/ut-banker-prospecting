# Set up your own Coverage Desk

You get your own copy of the desk with an empty database. Nothing of the original owner's (contacts, drafts, emails) comes with it.

## What you need

- A paid Claude plan (Pro or Max) with Claude Code on the web: claude.ai/code
- Connectors in Claude (Settings → Connectors):
  - **Clay**: finds bankers and checks emails. It needs a Clay account with credits.
  - **Microsoft 365** (Outlook) or **Gmail**: lets the daily run see your sends, replies and bounces. It only reads; it never sends.
  - **Indeed**: job postings.
- Your resume as a PDF.

## Setup (about 10 minutes)

1. Go to claude.ai/code and start a session on this repository:
   `https://github.com/krishnachiripurapu/ut-banker-prospecting`
2. Paste the prompt below, filling in the brackets.
3. Answer Claude's questions. When it finishes, it gives you your desk link. Bookmark it.

```
Set up my own copy of the Coverage Desk from this repo.

About me: [name], [school and major], [year], interested in [IB / private credit / etc.],
in [cities]. I use [Outlook / Gmail] for email.

1. Publish desk/coverage-desk.html as a new private artifact for me, and publish
   desk/automation/desk_daily.py with it at the path automation/desk_daily.py.
2. Open the desk and help me fill in Settings (profile, target firms, daily goals).
3. Make my copy of desk/automation/DAILY_RUNBOOK.md: replace the original owner's name
   and desk link with mine, and use my email account.
4. Create a scheduled task that runs that runbook every morning at about 7 AM my time.
5. Run it once now so I can see it work.
Never send email for me. Keep my data out of git.
```

## Day to day

- Open your desk each morning. The daily run will have added people, jobs and any replies.
- Write emails on the **Outreach** tab and send them from your own email.
- Ask Claude in any chat to change the desk ("add a column", "fix this"). It updates your copy only.

## Good to know

- Emails guessed from a firm's format can bounce. Wait until one address at a firm is confirmed before sending to lots of people there.
- Don't commit exported CSVs or anything with real names or emails to GitHub.
