# Cloud-Based Library Management System

A digital, centralized, and efficient approach to modern library operations — built with Flask and SQLite.

Presented by S Aakash (24BIT001) · Department of BSc IT

## Features (from project scope)

- **Book Catalog Management** — add, edit, delete, and browse titles with author, ISBN, category, and copy counts
- **Member Registration** — register members and create linked member login accounts
- **Admin & Member Login** — admins manage the system; members browse books, view loans, and update profiles
- **Issue & Return Tracking** — issue books, mark returns, automatic due-date and fine calculation
- **Reservations** — members can reserve unavailable books; admins can fulfill or cancel requests
- **Loan Renewal** — members can renew active loans once when no other member is waiting
- **Reports & CSV Export** — admin summaries for circulation, stock, fines, overdue members, and downloadable transaction data
- **Search & Filter** — search books by title/author/ISBN, filter by category; search members by name/email/ID
- **Cloud-style Access** — a small JSON API (`/api/books`) demonstrates remote/anywhere data access
- **Dashboard** — live counts of titles, copies, members, issued loans, and overdue items
- **User-Friendly Interface** — clean, responsive web UI, no page-heavy frameworks required

## System Architecture

| Layer | Technology |
|---|---|
| Frontend | HTML5, CSS3 (custom, no external framework), Jinja2 templates |
| Backend | Python 3, Flask |
| Database | SQLite (file-based, zero-config) |
| Cloud Platform | Deployable as-is to any Python-friendly host (Render, PythonAnywhere, Railway, etc.) |

## Getting Started

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the app

```bash
python app.py
```

The first run automatically creates `library.db`, seeds an admin account, and adds a few sample books/members so the demo isn't empty.

### 3. Open in your browser

```
http://127.0.0.1:5000
```

**Admin demo login:** `admin` / `admin123`

**Member demo login:** `mem001` / `member123`

## Project Structure

```
library_management_system/
├── app.py                 # Flask application (routes + business logic)
├── schema.sql              # Database schema
├── requirements.txt
├── README.md
├── library.db               # Created automatically on first run
├── static/
│   └── css/style.css
└── templates/
    ├── base.html
    ├── login.html
    ├── dashboard.html
    ├── books.html
    ├── book_form.html
    ├── members.html
    ├── member_form.html
    ├── transactions.html
    └── issue_form.html
```

## Business Rules

- Loan period: **14 days** from issue date
- Renewal period: **7 days**, limited to **1 renewal** per loan
- Fine: **₹5 per day** overdue (edit `FINE_PER_DAY` / `LOAN_PERIOD_DAYS` in `app.py` to change)
- A book can't be deleted while copies are currently issued
- A member can't be deleted while they have books currently issued
- Overdue status is computed automatically whenever the dashboard or transactions page loads
- Admin users can add/edit/delete books, manage members, issue books, and mark returns
- Admin users can manage reservations, fulfill requests, view reports, and export transaction CSV files
- Member users can browse the catalog, reserve unavailable books, renew eligible loans, see loan history, and update contact/password details

## Future Scope (per project plan)

- Mobile app integration
- RFID-based book tracking
- AI-based book recommendations
- Online notification system (due-date reminders, email/SMS)

## Notes for Submission / Demo

- To reset the demo data, stop the server, delete `library.db`, and restart — it will reseed automatically.
- To deploy to the cloud, this app works as-is behind any WSGI server (e.g., `gunicorn app:app`) on platforms like Render, Railway, or PythonAnywhere. Swap `app.run(debug=True)` for a production run when doing so.
