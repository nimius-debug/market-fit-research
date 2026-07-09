# Hosting on GitHub Actions with state committed to a public repo, using Issues as the review UI

We considered a local script on a cron/scheduled task versus a small cloud deployment. We chose a middle path: a Python script with SQLite storage, scheduled via GitHub Actions, with the SQLite DB and Digest file committed back to the repo each run (GitHub Actions runners are ephemeral, so persistence has to be explicit). This avoids depending on a personal machine being on, without taking on a hosted database or server.

Each Opportunity is surfaced as a GitHub Issue; closing/labeling it marks the Opportunity Rejected (see [[Rejected]] in CONTEXT.md), which reuses GitHub's own UI (including mobile) instead of building a review dashboard.

The repo is **public** — an explicit choice, not the default. It costs nothing extra on GitHub Actions and the user doesn't mind the resulting Opportunity/idea list being visible to anyone who finds the repo.
