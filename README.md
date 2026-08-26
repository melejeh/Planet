# Planet

**A responsive student planning platform that turns course information into an actionable semester plan.**

[Live beta](https://melejeh.pythonanywhere.com) · [Report an issue](https://github.com/melejeh/Planet/issues)

Planet brings courses, assessments, grades, study sessions, tasks, goals, calendar events, and focus tools into one workspace. It also reduces manual setup through OCR-assisted timetable and course-outline imports, with an editable review step before any detected information is saved.

> Planet is currently in private beta. Imported information should always be reviewed because document and timetable layouts vary.

## Highlights

- **Course and grade management** — Organize semesters, courses, assessments, due dates, weights, and grades.
- **OCR-assisted setup** — Read timetable screenshots and course-outline screenshots using Tesseract.js.
- **PDF outline extraction** — Extract assessment information from text-based course-outline PDFs with `pypdf`.
- **Human-in-the-loop validation** — Review, edit, add, or remove detected information before importing it.
- **Priority-aware study plans** — Generate sessions around existing calendar events while prioritizing closer and higher-weight deadlines.
- **Weekly calendar** — Create, edit, move, repeat, search, and delete events.
- **Productivity workspace** — Manage tasks, goals, focus sessions, gratitude entries, and study history.
- **Responsive interface** — Use Planet across desktop, tablet, and mobile layouts.
- **Multi-user data isolation** — Every query is scoped to the authenticated user so accounts cannot access one another's information.

## Why I Built It

Student information is often scattered across course outlines, timetable systems, calendars, task lists, and grade calculators. Planet explores how those sources can become one connected planning workflow: import the semester, verify the data, and use it to decide what to work on next.

## How the Import Workflow Works

1. A user uploads a timetable screenshot, course-outline screenshot, or text-based PDF.
2. Tesseract.js performs OCR on images in the browser; `pypdf` extracts embedded text from PDFs on the server.
3. Planet parses course codes, meeting times, assessment names, weights, and dates using layout analysis, regular expressions, and validation rules.
4. The user reviews and corrects the detected information.
5. Confirmed records are saved to the user's semester in SQLite.

The review step is intentional: OCR is useful for creating a draft, but different schools and document layouts make fully automatic imports unreliable.

## Study-Plan Prioritization

When a user selects upcoming assessments, Planet scores each one using:

- Time remaining until its due date
- Percentage of the final course grade
- Number of study sessions it has already received

Urgent, high-weight assessments receive more sessions, while a diminishing priority score prevents lower-priority deadlines from being ignored. Generated sessions respect the user's available days and hours, preferred session length, breaks, existing calendar events, and weekly study target.

## Technical Decisions

| Decision | Reason |
| --- | --- |
| Flask with server-rendered Jinja templates | Keeps the application architecture approachable while supporting authentication, forms, validation, and dynamic user data. |
| SQLite | Provides a lightweight relational database for the current beta and makes local development simple. |
| Tesseract.js in the browser | Enables screenshot text recognition without requiring a separate OCR service. |
| `pypdf` for text-based outlines | Uses embedded PDF text when available instead of performing unnecessary image OCR. |
| Editable import review screens | Protects data quality when OCR or document parsing is uncertain. |
| Session-based authentication and hashed passwords | Provides authenticated user workflows without storing plaintext passwords. |
| User-scoped database queries | Enforces ownership boundaries between accounts. |
| Responsive CSS rather than a separate mobile app | Keeps the same product usable across desktop, tablet, and phone screens. |

## Technology

- Python 3.10+
- Flask and Jinja
- SQLite
- JavaScript
- Tesseract.js
- `pypdf`
- HTML and CSS
- Werkzeug password hashing
- Gunicorn
- PythonAnywhere

## Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/melejeh/Planet.git
cd Planet
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

On Windows:

```bash
venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure a development secret

macOS/Linux:

```bash
export SECRET_KEY="replace-with-a-random-development-secret"
```

Windows PowerShell:

```powershell
$env:SECRET_KEY="replace-with-a-random-development-secret"
```

### 5. Initialize and run Planet

```bash
python init_db.py
python app.py
```

Open `http://127.0.0.1:5001` in your browser.

## Current Limitations

- OCR accuracy varies with screenshot quality, colours, fonts, and timetable layout.
- Timetable importing currently works best with a clear Monday–Friday weekly view.
- Scanned PDFs may work better when the relevant grading section is uploaded as an image.
- Alternative grading schemes and weights described as “each” can require manual correction.
- Email-based forgotten-password recovery is not implemented yet.
- SQLite and the current PythonAnywhere deployment are suitable for beta usage, not high-traffic production workloads.

## Roadmap

- Collect and respond to private-beta feedback
- Add email-based password recovery
- Add automated tests for authentication, ownership, grade calculations, imports, and study-plan prioritization
- Refactor the Flask application into blueprints and service modules
- Improve support for additional timetable and course-outline layouts
- Prepare a production database migration path

## Privacy

Planet is a beta project. Testers should avoid entering sensitive personal information. Passwords are hashed before storage, and application records are scoped to the authenticated user.

## Author

Built by [Mel Ejeh](https://github.com/melejeh), a Software Engineering student at Western University.

