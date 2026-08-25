CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS semesters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    semester_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    schedule TEXT,
    colour TEXT DEFAULT 'berry',

    FOREIGN KEY (semester_id)
        REFERENCES semesters(id)
);
CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    weight REAL NOT NULL,
    score REAL,
    due_date TEXT,

    FOREIGN KEY (course_id)
        REFERENCES courses(id)
);
CREATE TABLE IF NOT EXISTS study_plan_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    earliest_time TEXT NOT NULL DEFAULT '09:00',
    latest_time TEXT NOT NULL DEFAULT '21:00',
    weekly_target_hours REAL NOT NULL DEFAULT 8,
    preferred_session_minutes INTEGER NOT NULL DEFAULT 45,
    break_minutes INTEGER NOT NULL DEFAULT 10,
    include_weekends INTEGER NOT NULL DEFAULT 1,
    available_days TEXT NOT NULL DEFAULT
        'monday,tuesday,wednesday,thursday,friday,saturday,sunday',
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS study_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    course_id INTEGER,
    assessment_id INTEGER,
    title TEXT NOT NULL,
    notes TEXT,
    scheduled_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'completed', 'missed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE SET NULL,
    FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE SET NULL
);