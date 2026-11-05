# RUNBACK
Find fighters. Make rivals. Run it back.

A working, independent fighting-game social web app for desktop and mobile. No publisher artwork or APIs required. Python backend; JavaScript frontend; SQLite for development and PostgreSQL for deployment.

## Run locally

Requires Python 3.10+.

```bash
python -m pip install -r requirements.txt
python main.py
```

Open the URL printed by the server (typically `http://localhost:3000`). Local development uses SQLite by default. Use a separate test database and do not reuse production credentials.

## Publish with persistent data

1. Install requirements: `pip install -r requirements.txt`.
2. Provision a PostgreSQL database and set `DATABASE_URL` in the deployment environment to its connection string. Use a production database separate from your development database. Tables are initialized automatically; existing SQLite records are not migrated.
3. Set `ADMIN_EMAIL` to your moderator's registered email; leave `DEMO_MODE` off.
4. Use a dynamic Python deployment, with build command `pip install -r requirements.txt` and run command:

   `RUNBACK_PRODUCTION=1 gunicorn --preload --bind 0.0.0.0:3000 --workers 2 --threads 4 main:application`

5. Expose local port 3000. Use the host's HTTPS URL.

Configure these commands in your hosting environment. `RUNBACK_PRODUCTION=1` refuses to start without PostgreSQL so real accounts do not silently end up in an ephemeral SQLite file. The development server is for previews; Gunicorn is the deployment server. Database credentials are Secrets, never frontend settings. If using the single-file option, upload `requirements.txt` and copy the deployment configuration too.

Official references: https://docs.replit.com/references/project-setup/configuration and https://replit.com/blog/deployments-launch (deployment filesystems are not persistent storage).

## Working features

- Email/password accounts, sign-in/sign-out, editable player profiles, account deletion.
- PBKDF2-SHA256 password hashing with legacy scrypt verification; opaque expiring server sessions; HTTP-only cookies; CSRF headers; parameterized database queries; escaped frontend content; restrictive Content Security Policy.
- City/game/rank/ready filters, swipe gestures and accessible challenge/pass buttons; reset passed profiles.
- Incoming challenges, mutual friendships, member-only chat refreshed every three seconds.
- Ready-to-fight presence expires after two hours; no GPS collection or exact distance claims.
- Online/local sessions with dates, capacity, join/leave, and host cancellation.
- Crews with member roster counts, join/leave, and owner deletion.
- Rivalry wins count only after the other player confirms submitted results; disputed results can be rejected.
- Blocking/unblocking, report submission, moderator review and account suspension.
- Adults-only registration self-attestation. Ranks, character choices, and gamertags are self-reported.

## Boundaries of this first release

This is a functional MVP, not a fully operated social platform. Voice/video, push notifications, photo uploads, automated game-stat synchronization, crew discussion feeds, official venue feeds, and geographic radius matching are not implemented. City filters are exact text matches, ignoring case. Crews organize membership; conversations are between matched friends. Public venues are entered by hosts, not automatically verified or suggested.

Email verification is not included. Password-reset email delivery requires the Brevo setup described in RECOVERY-SETUP.md. Do not advertise verified identities. Before a broad public launch, add email verification, stronger global signup/abuse controls, monitoring/backups, community rules/privacy terms appropriate to your operation, and staffed moderation. The existing login limiter is account-based; it is not a complete anti-abuse system.

Chats are stored in the database, not end-to-end encrypted. Blocking hides discovery, friendships, and chat; it does not erase historical group membership counts. Account deletion removes linked chats, results, sessions/crews hosted by that account, and reports connected to that account. Suspended accounts are hidden and lose access; their historical content remains until deleted. Ready state only refreshes when you save your profile. No online-status tracking claims.

## Verification

Run `python3 -m unittest -v test_app test_recovery` from the project root. These focused regression tests use temporary SQLite databases and cover suspension through the moderator API, session-token revocation, chat reads/writes, score submission/confirmation, session and crew joins, and blocking/unblocking. They do not constitute a full application test suite. PostgreSQL concurrency and browser layouts were not tested for this patch.

Run `node --check public/app.js` for frontend syntax.

## Project files

- `main.py`: API, persistence, authentication, WSGI app.
- `public/`: frontend and phone installation assets.
- `requirements.txt`: production server/database driver.
- `test_app.py`: focused suspension regression tests.
- `UPDATE-NOTES.md`: installation instructions for this patch.

Independent community project; not affiliated with Capcom or any game publisher.

## Password recovery

Password recovery requires `BREVO_API_KEY`, `EMAIL_FROM`, and a public HTTPS `APP_URL`. The Forgot password screen sends a single-use 30-minute link through Brevo after configuration. Resetting revokes all sign-in tokens. Existing accounts and suspension protections are preserved. Automated tests mock email delivery; production delivery and PostgreSQL concurrency still need verification.

## Portfolio notes

This repository is an independent project demonstrating Python web backend development, JavaScript UI, authentication, moderation, and database-backed community features. Review the implementation and security settings before production use.

## Security and deployment checklist

- Never commit `.env` files, real API keys, connection strings, or production databases.
- Set `RUNBACK_PRODUCTION=1` and a persistent PostgreSQL `DATABASE_URL` in production.
- Configure `ADMIN_EMAIL` to a trusted account; set HTTPS `APP_URL` and Brevo settings only if enabling password recovery.
- Inspect **all Git commits and branches** for leaked secrets before publishing; removing a file from the current ZIP does not erase Git history. Rotate any credentials ever exposed.
- Review your deployment host, user data retention, and access controls before inviting real users.

## Tests

```bash
python -m unittest -v test_app test_recovery
```
