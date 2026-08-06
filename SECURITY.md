# Security Policy

## Reporting a vulnerability

If you find a security issue in this project, please report it privately rather than
opening a public GitHub issue — this tool handles Adobe admin credentials and real user
data, so a public issue could point at a live exposure before it's fixed.

Open a [GitHub Security Advisory](../../security/advisories/new) on this repository
("Report a vulnerability" under the Security tab). That reaches maintainers privately and
lets us coordinate a fix and disclosure timeline with you.

Please include:
- What you found and why it's exploitable (a reproduction, if possible)
- Which mode it applies to (mock / live read / live write) if relevant
- Your assessment of impact (e.g. "requires an existing admin's cooperation" vs. "any
  user of a shared deployment can trigger this")

## Scope

In scope: the application code in this repository (`app.py`, `adobe_access/**`).

Out of scope: vulnerabilities in Adobe's own UMAPI/IMS services, or in third-party
dependencies (report those upstream — see `requirements.txt`; Dependabot is enabled here
to track known-vulnerable versions).

## What this app already does

Before reporting "there's no login screen" or "the SQLite file isn't encrypted" — these
are known, deliberate scope limits, not oversights:

- This is a trusted-admin tool, not a multi-tenant service. It has no built-in
  authentication; "Signed in as" is a free-text attribution field, not a login. Anyone
  who can run the app already has the access it grants.
- `ADOBE_WRITE_ENABLED` and all Adobe credentials are `.env`-only and never exposed in
  the UI — see the README's "Editable settings vs. secrets" and "Production readiness
  checklist" sections.
- The local SQLite database and log file hold real data (emails, group memberships,
  Adobe API responses) and are permission-restricted to the owning user on POSIX systems
  at startup (`harden_file_permissions()` in `adobe_access/utils.py`) — but are not
  encrypted at rest. If you're deploying this somewhere other than a single admin's own
  machine, add disk encryption and don't skip the permissions note in the README.

If you're unsure whether something is a genuine vulnerability or one of the above,
report it anyway — worst case we point you at this file.
