# Compass 🧭

**Text your expenses and to-dos to a Telegram bot. It keeps the books — on your own machine.**

Compass is a Telegram bot that keeps your spending records and your to-do list
up to date just by texting it. Message it the way you'd text a friend —
*"spent 400 on groceries"*, *"remind me to call the electrician tomorrow"* —
and it files the expense or sets the reminder. No forms, no app-switching,
no spreadsheet at the end of the month.

Everything runs on your own computer. Your bank details, your receipts, and
every message you send stay on hardware you control.

## Why Compass exists

Most expense tracking fails the same way: the app is one more thing to open,
with one more form to fill in, and after two weeks you stop. Compass removes
that friction — you're already in Telegram a dozen times a day, and logging a
purchase is one short message sent from the queue at the till.

The other half is privacy. Finance apps generally want your data in their
cloud. Compass takes the opposite approach: the message is understood by a
small AI model running **locally on your machine** (nothing is sent to OpenAI,
Google, or any other AI service), and the records are stored in your own
copies of [Firefly III](https://www.firefly-iii.org/) (money) and
[Vikunja](https://vikunja.io/) (tasks) — two well-regarded open-source apps
that give you proper web dashboards whenever you want the big picture.

Compass was built for and shaped by real daily use, not as a tech demo. The
rough edges that only show up in week three of actually living with a tool —
corrections, receipts, card bills, nagging reminders — are the parts that got
the most attention.

## What it feels like

```text
You:      spent 400 on groceries at the supermarket, paid by card

Compass:  💸 Expense  ₹400
          From: Rewards Card
          To:   Supermarket
          📁 Groceries   🏷 personal
          [ ✅ Confirm ] [ ✏️ Edit ] [ 🗑 Cancel ] [ 📎 Attach File ]

You:      (tap ✅ Confirm)

Compass:  ✅ Logged. Transaction #214
```

Made a mistake? Tap **✏️ Edit** and reply in plain words — *"actually it was
450, and that was the travel card"* — and the card updates before anything is
saved. Got the receipt? Tap **📎 Attach File** and send the photo or PDF; it's
stored with the transaction.

To-dos work the same way:

```text
You:      remind me to call the electrician tomorrow

Compass:  📝 Call the electrician
          📅 Tomorrow, 04 Jul
          [ ✅ Confirm ] [ ✏️ Edit ] [ 🗑 Cancel ]

You:      (tap ✅ Confirm)

Compass:  ✅ Created. Task #12
```

Every morning Compass sends a short digest of what's overdue and what's due
today, with **Done / Defer / Delete** buttons right on each task — so "defer
to Friday" is one tap and three words.

Nothing is ever written without your confirmation. If you ignore a card, it
quietly expires.

## What it can do

**Money**
- Log expenses, income, transfers, and credit-card bill payments in plain language
- It knows your account nicknames — "card", "savings", "cash" — and asks when it isn't sure
- Attach receipts (photos or PDFs, several per transaction)
- Ask for reports any time: `/balances`, `/today`, `/thisweek`, `/thismonth`, `/search coffee`
- Tag spending as personal or work, and filter reports either way

**Tasks**
- Create to-dos with due dates, priorities, and repeats ("every 5th")
- A daily digest of what's overdue and due today
- One-tap Done / Defer / Delete from any task message
- Optional gentle nudges during the day to log what you spent

**Your rules**
- Works in any currency — you pick it once during setup
- Only the Telegram accounts you allowlist can talk to the bot; everyone else gets silence
- Full web dashboards (Firefly III and Vikunja) for charts, budgets, and bulk editing

## What you need

- **A Telegram account** — free, on your phone already.
- **A machine that can run Docker** — a home server, mini-PC, or an always-on
  spare computer. Linux is the tested platform. Plan for roughly **6 GB of
  RAM and 15 GB of disk** (most of that is the local AI model).
- **About 30–60 minutes** for first-time setup — honestly. This is a real
  self-hosted stack, not a 10-second install; most of that time is downloads,
  and a setup wizard does the thinking for you.

No graphics card is needed. On a modest CPU-only machine the AI takes a
minute or two to read each message — fine for "text it and pocket the phone"
use. With a GPU it's near-instant.

## Getting started

```bash
git clone https://github.com/zappymussel380/Compass.git compass
cd compass
./install.sh
```

The interactive installer walks you through everything: it checks your
machine, helps you create a Telegram bot (a two-minute chat with
[@BotFather](https://t.me/BotFather)), asks who's allowed to use it, which
currency you spend in, and what you call your bank accounts, then generates
passwords, downloads the AI model, and starts the whole stack. Two short
steps happen in your browser (creating access tokens in Firefly and Vikunja) —
the installer tells you exactly when and how.

You can re-run `./install.sh` any time to resume, reconfigure, or uninstall.

Deliberately, there is no `curl | bash` one-liner: for software that handles
your finances, you should be able to read what you run.

Full walkthrough (including fully manual setup): **[docs/INSTALL.md](docs/INSTALL.md)**

## How your data stays private

This matters more for a finance tool than for anything else you self-host,
so here is the plain version:

- **Everything lives on your machine.** The databases, the receipts, the AI
  model — all of it. There is no Compass server, no account, no telemetry.
- **No AI cloud.** Messages are parsed by a local model via
  [Ollama](https://ollama.com/). Your "spent 12,000 at the clinic" never
  leaves the house.
- **Closed to strangers by default.** The bot answers only the Telegram user
  IDs you allowlist and ignores everyone else completely. The web dashboards
  are reachable only from the machine itself unless you deliberately open
  them up.
- **Your bank names stay out of the code.** Real account names live in a
  local file that git is told to ignore, so publishing or forking the repo
  never leaks them.
- **Encrypted backups.** The optional backup script encrypts everything
  before it leaves the machine, so you can push backups to any cloud drive
  without trusting it.

Details, hardening notes, and known limitations: **[SECURITY.md](SECURITY.md)**

## Everyday commands

Most use is just plain messages, but a few commands are handy:

```text
/balances            all account balances
/today  /thisweek  /thismonth   spending reports (add "personal" or "firm" to filter)
/tasks               open tasks (also /tasks work, /tasks personal)
/search <keyword>    find past transactions
/edit <id>           fix a transaction that's already saved
/help                the full list
```

## Going further

- **[docs/INSTALL.md](docs/INSTALL.md)** — full install walkthrough, manual
  setup, architecture, and every configuration setting.
- **[docs/CUSTOMIZATION.md](docs/CUSTOMIZATION.md)** — account nicknames,
  currency, categories, and tuning the AI prompts to your spending language.
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — day-2 care: logs, backups,
  attachments, scheduled messages.
- **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** — running the test suite and
  local development checks.

## Project status

Compass is young. It's in real daily use and the core flows are tested live
(logging, corrections, receipts, card payments, reports, tasks, reminders),
but before trusting it with important data you should run through those flows
yourself on your own install — and set up backups early. Issues
and bug reports are welcome.

## Contributing & license

Contributions are welcome. Keep private data out of the repository and
preserve the local-first design — those two rules cover most of it. See
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for tests and checks to run before
a pull request.

Licensed under the [MIT License](LICENSE).
