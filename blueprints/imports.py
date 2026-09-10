import re
from datetime import datetime
from io import BytesIO

from flask import Blueprint, redirect, render_template, request, session, url_for

from db import get_db

imports_bp = Blueprint("imports", __name__)


@imports_bp.route("/semester/import-timetable")
def import_timetable():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    semester_id = session.get("active_semester_id")
    if semester_id is None:
        return redirect(url_for("courses.semester"))

    connection = get_db()
    active_semester = connection.execute(
        "SELECT id, name FROM semesters WHERE id = ? AND user_id = ?",
        (semester_id, session["user_id"])
    ).fetchone()

    if active_semester is None:
        session.pop("active_semester_id", None)
        return redirect(url_for("courses.semester"))

    return render_template(
        "import_timetable.html",
        name=session["first_name"],
        active_semester=active_semester
    )



@imports_bp.route("/semester/import-timetable/confirm", methods=["POST"])
def confirm_timetable_import():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    semester_id = session.get("active_semester_id")
    if semester_id is None:
        return redirect(url_for("courses.semester"))

    codes = request.form.getlist("course_code")
    names = request.form.getlist("course_name")
    schedules = request.form.getlist("schedule")
    colours = request.form.getlist("colour")
    allowed_colours = {"berry", "sage", "gold"}

    connection = get_db()
    owned_semester = connection.execute(
        "SELECT id FROM semesters WHERE id = ? AND user_id = ?",
        (semester_id, session["user_id"])
    ).fetchone()
    if owned_semester is None:
        session.pop("active_semester_id", None)
        return redirect(url_for("courses.semester"))

    existing_codes = {
        row["code"].strip().upper()
        for row in connection.execute(
            "SELECT code FROM courses WHERE semester_id = ?",
            (semester_id,)
        ).fetchall()
    }

    added = 0
    seen = set(existing_codes)
    for index, raw_code in enumerate(codes):
        code = " ".join(raw_code.strip().upper().split())
        if not code:
            continue

        name = names[index].strip() if index < len(names) else ""
        schedule = schedules[index].strip() if index < len(schedules) else ""
        colour = colours[index] if index < len(colours) else "berry"
        if colour not in allowed_colours:
            colour = "berry"

        if code not in seen:
            connection.execute(
                """
                INSERT INTO courses (semester_id, code, name, schedule, colour)
                VALUES (?, ?, ?, ?, ?)
                """,
                (semester_id, code, name or code, schedule, colour)
            )
            seen.add(code)
            added += 1

    connection.commit()

    message = f"{added} course{'s' if added != 1 else ''} imported."
    return redirect(url_for("courses.semester", saved=message))



def _course_outline_assessments(text):
    """Extract reviewable assessment rows from a plain-text course outline."""
    rows = []
    assessment_terms = (
        "assignment", "quiz", "test", "midterm", "final", "exam", "laboratory", "lab",
        "tutorial", "project", "presentation", "participation", "attendance", "essay",
        "report", "reflection", "case study", "portfolio", "discussion", "homework",
        "worksheet", "problem set", "proposal", "capstone", "practical", "simulation",
        "coding exercise", "coursework", "term work"
    )
    blocked_terms = (
        "prerequisite", "anti-requisite", "corequisite", "academic consideration",
        "accommodation", "policy", "textbook", "course material", "learning outcome",
        "support service", "scholastic offence", "copyright", "contact information"
    )

    def likely_assessment(name):
        lowered = name.lower()
        return (
            2 < len(name) <= 120
            and not any(term in lowered for term in blocked_terms)
            and any(term in lowered for term in assessment_terms)
        )

    evaluation = re.search(
        r"(?:(?:Method\s+of\s+)?Evaluation\s*:|"
        r"Methods\s+of\s+Evaluation\s+Grading\s+Scheme\s+and\s+Assessment\s+Dates|"
        r"Grading\s+Scheme\s+and\s+Assessment\s+Dates|"
        r"Assessment\s+(?:and|&)\s+Evaluation\s*:|Grading\s+Breakdown\s*:|"
        r"Grade\s+Breakdown\s*:|Assessment\s+Breakdown\s*:|Course\s+Assessment\s*:|"
        r"Course\s+Components\s*:|Marking\s+Scheme\s*:|Distribution\s+of\s+Marks\s*:|"
        r"Basis\s+of\s+Evaluation\s*:|Evaluation\s+Criteria\s*:|"
        r"Assessment\s+Summary\s*:|Assessment\s+Plan\s*:)\s*(.*?)"
        r"(?:\s+Notes\s*:|To obtain a passing grade|Course Component Details|I will post a sheet|"
        r"Use of Generative AI Tools|General information about missed coursework|Course Policies|"
        r"Academic Consideration|Missed Assessments|Assessment Flexibility|Academic Integrity|"
        r"Scholastic Offences|Accommodation|Support Services|Required Materials|Course Materials|"
        r"Learning Outcomes|Attendance Policy|Late Policy|Submission Policy|Additional Statements|"
        r"Copyright|Contact Information)",
        text,
        flags=re.IGNORECASE | re.DOTALL
    )
    evaluation_text = evaluation.group(1) if evaluation else text
    evaluation_text = re.sub(
        r"^.*?overall course grade will be calculated as listed below\s*:\s*",
        "",
        evaluation_text,
        flags=re.IGNORECASE | re.DOTALL
    )
    evaluation_text = re.sub(
        r"(\d+(?:\.\d+)?\s*%)\s+each\s+",
        r"\1\n",
        evaluation_text,
        flags=re.IGNORECASE
    )
    evaluation_text = re.sub(
        r"(%(?:\s*/\s*\d+(?:\.\d+)?\s*%)?)\s+(?=[A-Z][A-Za-z])",
        r"\1\n",
        evaluation_text
    )

    # Some outlines wrap the percentage in brackets, e.g. "Assignment 1 [5%]".
    # Strip the bracket (even if OCR dropped the closing one) so the normal
    # "name  weight%" matching below can handle it the same as everything else.
    evaluation_text = re.sub(
        r"\[\s*(\d+(?:\.\d+)?\s*%)\]?",
        r"\1",
        evaluation_text
    )

    lines = [" ".join(l.split()) for l in evaluation_text.splitlines() if l.strip()]

    # Multi-column tables (Course Component | % Worth | CEAB GAs | Assessed)
    # often get scrambled by OCR so a weight ends up separated from its own
    # name by unrelated columns -- but the overall ORDER of names and the
    # order of weights is usually still preserved. When the count of
    # assessment-looking names exactly matches the count of standalone
    # weights, pair them up positionally rather than requiring strict
    # adjacency, which recovers rows the sequential matching below would
    # otherwise silently drop.
    # A bare column header (just the word "Lab", "Quiz", "Test", etc. with
    # nothing else) trivially contains an assessment term too, but it's a
    # table header, not a real assessment name -- exclude exact matches to
    # these so they don't inflate the name count below.
    generic_header_words = {
        "lab", "labs", "assignment", "assignments", "quiz", "quizzes",
        "test", "tests", "exam", "exams", "assessment", "assessments",
        "activity", "activities", "component", "components"
    }
    name_lines = [
        l for l in lines
        if likely_assessment(l) and "%" not in l
        and l.strip().lower() not in generic_header_words
    ]
    weight_line_pattern = re.compile(
        r"^(\d+(?:\.\d+)?)\s*%\s*(?:/\s*(\d+(?:\.\d+)?)\s*%)?$"
    )
    weight_line_matches = [
        m for m in (weight_line_pattern.match(l) for l in lines) if m
    ]

    if name_lines and len(name_lines) == len(weight_line_matches):
        for name, weight_match in zip(name_lines, weight_line_matches):
            rows.append({
                "name": re.sub(r"\s+\)", ")", name.strip(" :-")),
                "weight": weight_match.group(1),
                "alternative_weight": weight_match.group(2) or "",
                "due_date": ""
            })
        return rows

    pending_name = None
    for line in lines:
        match = re.match(
            r"(.+?)\s+(\d+(?:\.\d+)?)\s*%\s*(?:/\s*(\d+(?:\.\d+)?)\s*%)?",
            line
        )
        if not match:
            standalone_weight = weight_line_pattern.match(line)
            if standalone_weight and pending_name and likely_assessment(pending_name):
                rows.append({
                    "name": pending_name,
                    "weight": standalone_weight.group(1),
                    "alternative_weight": standalone_weight.group(2) or "",
                    "due_date": ""
                })
                pending_name = None
            elif "%" not in line and line.lower() not in {
                "course component", "weight", "ceab gas assessed"
            }:
                pending_name = re.sub(r"\s+\)", ")", line.strip(" :-"))
            continue
        name = re.sub(r"\s+\)", ")", match.group(1).strip(" :-"))
        if name.lower() in {"course component", "weight"} or not likely_assessment(name):
            continue
        rows.append({
            "name": name,
            "weight": match.group(2),
            "alternative_weight": match.group(3) or "",
            "due_date": ""
        })
        pending_name = None

    month_names = (
        "January|February|March|April|May|June|July|August|"
        "September|October|November|December"
    )
    midterm_date = re.search(
        rf"Midterm(?: Test)?[^.]*?(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?,?\s*"
        rf"({month_names})\s+(\d{{1,2}}),?\s+(\d{{4}})",
        text,
        flags=re.IGNORECASE
    )
    if midterm_date:
        try:
            parsed_date = datetime.strptime(
                " ".join(midterm_date.groups()), "%B %d %Y"
            ).date().isoformat()
            for row in rows:
                if "midterm" in row["name"].lower():
                    row["due_date"] = parsed_date
        except ValueError:
            pass
    return rows



@imports_bp.route("/courses/<int:course_id>/import-outline", methods=["GET", "POST"])
def import_course_outline(course_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()
    course = connection.execute(
        """
        SELECT courses.* FROM courses
        JOIN semesters ON semesters.id = courses.semester_id
        WHERE courses.id = ? AND semesters.user_id = ?
        """,
        (course_id, session["user_id"])
    ).fetchone()
    if course is None:
        return redirect(url_for("courses.semester"))

    assessments = []
    error = None
    warning = None
    if request.method == "POST":
        extracted_text = request.form.get("extracted_text", "").strip()
        outline = request.files.get("course_outline")
        if extracted_text:
            if len(extracted_text) > 100000:
                error = "That image contained too much text to review safely."
            else:
                assessments = _course_outline_assessments(extracted_text)
                if not assessments:
                    error = "Planet could not find assessment names and percentages. You can add review rows manually."
        elif not outline or not outline.filename:
            error = "Choose a PDF or screenshot of the evaluation section first."
        elif not outline.filename.lower().endswith(".pdf"):
            error = "For images, choose PNG, JPG or WEBP and wait for the screenshot reader to finish."
        else:
            contents = outline.read(8 * 1024 * 1024 + 1)
            if len(contents) > 8 * 1024 * 1024:
                error = "Choose a PDF smaller than 8 MB."
            else:
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(BytesIO(contents))
                    text = "\n".join(page.extract_text() or "" for page in reader.pages)
                    assessments = _course_outline_assessments(text)
                    if not assessments:
                        error = "Planet could not find an evaluation table. You can add review rows manually."
                except Exception:
                    error = "Planet could not read that PDF. Try exporting it again as a text-based PDF."

    if assessments:
        primary_total = round(
            sum(float(row["weight"]) for row in assessments),
            1
        )
        if primary_total < 95 or primary_total > 105:
            warning = (
                f"Planet detected {primary_total:g}% of course weight, not approximately 100%. "
                "It may have missed a component, or a weight marked 'each' may need to be split or multiplied."
            )
        elif len(assessments) > 12:
            warning = (
                "Planet found an unusually large number of assessment rows. "
                "Review them carefully before importing."
            )

    return render_template(
        "import_course_outline.html",
        name=session["first_name"],
        course=course,
        assessments=assessments,
        error=error,
        warning=warning
    )



@imports_bp.route("/courses/<int:course_id>/import-outline/confirm", methods=["POST"])
def confirm_course_outline_import(course_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()
    course = connection.execute(
        """
        SELECT courses.id FROM courses
        JOIN semesters ON semesters.id = courses.semester_id
        WHERE courses.id = ? AND semesters.user_id = ?
        """,
        (course_id, session["user_id"])
    ).fetchone()
    if course is None:
        return redirect(url_for("courses.semester"))

    names = request.form.getlist("assessment_name")
    weights = request.form.getlist("weight")
    due_dates = request.form.getlist("due_date")
    existing = {
        row["name"].strip().lower()
        for row in connection.execute(
            "SELECT name FROM assessments WHERE course_id = ?", (course_id,)
        ).fetchall()
    }
    added = 0
    for index, raw_name in enumerate(names):
        name = raw_name.strip()
        if not name or name.lower() in existing:
            continue
        try:
            weight = float(weights[index])
        except (IndexError, TypeError, ValueError):
            continue
        if weight < 0 or weight > 100:
            continue
        due_date = due_dates[index].strip() if index < len(due_dates) else ""
        connection.execute(
            "INSERT INTO assessments (course_id, name, weight, score, due_date) VALUES (?, ?, ?, NULL, ?)",
            (course_id, name, weight, due_date or None)
        )
        existing.add(name.lower())
        added += 1

    connection.commit()
    return redirect(url_for("courses.course_details", course_id=course_id, imported=added))