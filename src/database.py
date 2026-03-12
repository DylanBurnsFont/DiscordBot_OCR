"""
SQLite database helpers.

Schema
------
game_guilds
    id               INTEGER PRIMARY KEY AUTOINCREMENT
    name             TEXT    UNIQUE NOT NULL
    discord_server_id TEXT
    created_at       TEXT    DEFAULT (datetime('now'))

players
    id               INTEGER PRIMARY KEY AUTOINCREMENT
    discord_user_id  TEXT    UNIQUE NOT NULL
    username         TEXT    NOT NULL
    game_guild_id    INTEGER REFERENCES game_guilds(id)
    joined_guild_at  TEXT    DEFAULT (datetime('now'))

mi_scans
    id               INTEGER PRIMARY KEY AUTOINCREMENT
    submitted_by     TEXT                             -- discord_user_id who ran /mi
    scan_date        TEXT    NOT NULL                 -- DD_MM_YYYY, e.g. "11_03_2026"
    scanned_at       TEXT    DEFAULT (datetime('now'))

mi_scores
    id               INTEGER PRIMARY KEY AUTOINCREMENT
    scan_id          INTEGER NOT NULL REFERENCES mi_scans(id) ON DELETE CASCADE
    scan_date        TEXT    NOT NULL                 -- DD_MM_YYYY, denormalised from mi_scans for easy grouping
    rank             INTEGER NOT NULL                 -- 1-based position in this scan
    player_name      TEXT    NOT NULL                 -- in-game name as detected by OCR
    score            TEXT    NOT NULL                 -- raw score string e.g. "1.23B"
    player_id        INTEGER REFERENCES players(id)   -- auto-linked when username matches exactly
    guild_id         INTEGER REFERENCES game_guilds(id) -- guild of the /mi submitter
    -- UNIQUE (player_name, scan_date) enforced in save_scores upsert logic
"""

import calendar
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path


_DB_PATH: Path | None = None


def _score_to_float(score: str) -> float:
    """Convert a raw OCR score string to a comparable float.
    e.g. '1.23B' -> 1_230_000_000.0, '500K' -> 500_000.0
    """
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}
    if not score:
        return 0.0
    suffix = score[-1].upper()
    if suffix in multipliers:
        try:
            return float(score[:-1]) * multipliers[suffix]
        except ValueError:
            return 0.0
    try:
        return float(score)
    except ValueError:
        return 0.0


def init_db(db_path: Path) -> None:
    """Create tables if they don't exist. Call once at startup."""
    global _DB_PATH
    _DB_PATH = db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with _connect() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS game_guilds (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT    UNIQUE NOT NULL,
                discord_server_id TEXT,
                created_at        TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS players (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_user_id  TEXT    UNIQUE NOT NULL,
                username         TEXT    NOT NULL,
                game_guild_id    INTEGER REFERENCES game_guilds(id),
                joined_guild_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS mi_scans (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                submitted_by TEXT,
                scan_date    TEXT NOT NULL DEFAULT '',
                scanned_at   TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS mi_scores (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id     INTEGER NOT NULL REFERENCES mi_scans(id) ON DELETE CASCADE,
                scan_date   TEXT    NOT NULL DEFAULT '',
                rank        INTEGER NOT NULL,
                player_name TEXT    NOT NULL,
                score       TEXT    NOT NULL,
                player_id   INTEGER REFERENCES players(id),
                guild_id    INTEGER REFERENCES game_guilds(id)
            );

            CREATE INDEX IF NOT EXISTS idx_mi_scores_player_name ON mi_scores(player_name);
            CREATE INDEX IF NOT EXISTS idx_mi_scores_scan_id     ON mi_scores(scan_id);
        """)

        # Migrations for columns added after initial release
        for migration in [
            "ALTER TABLE mi_scans  ADD COLUMN scan_date TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE mi_scores ADD COLUMN scan_date TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE mi_scores ADD COLUMN guild_id  INTEGER REFERENCES game_guilds(id)",
        ]:
            try:
                con.execute(migration)
            except sqlite3.OperationalError:
                pass  # column already exists

        # Backfill scan_date on mi_scores rows that predate this column
        con.execute("""
            UPDATE mi_scores
            SET scan_date = (SELECT scan_date FROM mi_scans WHERE mi_scans.id = mi_scores.scan_id)
            WHERE scan_date = ''
        """)


@contextmanager
def _connect():
    if _DB_PATH is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# game_guilds
# ---------------------------------------------------------------------------

def add_guild(name: str, discord_server_id: str | None = None) -> int:
    """Insert a new guild. Returns its new id. Raises if name already exists."""
    with _connect() as con:
        cur = con.execute(
            "INSERT INTO game_guilds (name, discord_server_id) VALUES (?, ?)",
            (name, discord_server_id),
        )
        return cur.lastrowid


def get_guild_by_name(name: str) -> sqlite3.Row | None:
    """Return the game_guilds row for *name*, or None."""
    with _connect() as con:
        return con.execute(
            "SELECT * FROM game_guilds WHERE name = ?", (name,)
        ).fetchone()


def get_all_guilds() -> list[sqlite3.Row]:
    """Return all rows from game_guilds."""
    with _connect() as con:
        return con.execute("SELECT * FROM game_guilds ORDER BY name").fetchall()


# ---------------------------------------------------------------------------
# players
# ---------------------------------------------------------------------------

def add_player(discord_user_id: str, username: str, game_guild_id: int | None = None) -> int:
    """Insert a new player. Returns their new id. Raises if discord_user_id already exists."""
    with _connect() as con:
        cur = con.execute(
            "INSERT INTO players (discord_user_id, username, game_guild_id) VALUES (?, ?, ?)",
            (discord_user_id, username, game_guild_id),
        )
        return cur.lastrowid


def get_player_by_discord_id(discord_user_id: str) -> sqlite3.Row | None:
    """Return the players row for *discord_user_id*, or None."""
    with _connect() as con:
        return con.execute(
            "SELECT * FROM players WHERE discord_user_id = ?", (discord_user_id,)
        ).fetchone()


def get_all_players() -> list[sqlite3.Row]:
    """Return all players, joined with their guild name."""
    with _connect() as con:
        return con.execute("""
            SELECT p.*, g.name AS guild_name
            FROM players p
            LEFT JOIN game_guilds g ON g.id = p.game_guild_id
            ORDER BY p.username
        """).fetchall()


# ---------------------------------------------------------------------------
# mi_scans / mi_scores
# ---------------------------------------------------------------------------

def create_scan(submitted_by: str | None = None, scan_date: str | None = None) -> int:
    """Insert a new scan record. Returns its id.
    scan_date defaults to today in DD_MM_YYYY format if not supplied.
    """
    if scan_date is None:
        scan_date = datetime.now().strftime("%d_%m_%Y")
    with _connect() as con:
        cur = con.execute(
            "INSERT INTO mi_scans (submitted_by, scan_date) VALUES (?, ?)",
            (submitted_by, scan_date),
        )
        return cur.lastrowid


def save_scores(scan_id: int, scores: dict[str, str], guild_id: int | None = None) -> tuple[int, int]:
    """
    Upsert OCR scores for a scan.

    For each player on a given scan_date:
      - No existing row for that player on that date → INSERT.
      - Existing row found and new score is higher → UPDATE (scan_id, rank, score).
      - Existing row found and new score is equal or lower → skip.

    guild_id: game_guilds.id of the player who submitted /mi; applied to every row.
    Returns (inserted, updated) counts.
    """
    with _connect() as con:
        scan_row = con.execute(
            "SELECT scan_date FROM mi_scans WHERE id = ?", (scan_id,)
        ).fetchone()
        scan_date = scan_row["scan_date"] if scan_row else ""

        # Build a name→id lookup for registered players
        player_rows = con.execute("SELECT id, username FROM players").fetchall()
        name_to_player_id: dict[str, int] = {r["username"]: r["id"] for r in player_rows}

        inserted = 0
        updated = 0

        # Sort by score descending so rank reflects actual leaderboard position
        sorted_scores = sorted(scores.items(), key=lambda kv: _score_to_float(kv[1]), reverse=True)

        for rank, (player_name, score) in enumerate(sorted_scores, start=1):
            player_id = name_to_player_id.get(player_name)

            # Look for an existing score entry for this player on the same date
            existing = con.execute(
                """
                SELECT id, score
                FROM   mi_scores
                WHERE  player_name = ?
                  AND  scan_date   = ?
                """,
                (player_name, scan_date),
            ).fetchone()

            if existing:
                if _score_to_float(score) > _score_to_float(existing["score"]):
                    con.execute(
                        """
                        UPDATE mi_scores
                        SET scan_id = ?, rank = ?, score = ?, player_id = ?, guild_id = ?
                        WHERE id = ?
                        """,
                        (scan_id, rank, score, player_id, guild_id, existing["id"]),
                    )
                    updated += 1
                # else: existing score is >= new score, leave it unchanged
            else:
                con.execute(
                    """
                    INSERT INTO mi_scores (scan_id, scan_date, rank, player_name, score, player_id, guild_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (scan_id, scan_date, rank, player_name, score, player_id, guild_id),
                )
                inserted += 1

        return inserted, updated


def get_scores_by_player(player_name: str) -> list[sqlite3.Row]:
    """All historical score rows for a given in-game player name, newest first."""
    with _connect() as con:
        return con.execute("""
            SELECT rank, score, player_name, scan_date
            FROM mi_scores
            WHERE player_name = ?
            ORDER BY scan_date DESC
        """, (player_name,)).fetchall()


def get_today_score(player_name: str) -> sqlite3.Row | None:
    """Return today's mi_scores row for *player_name*, or None."""
    today = datetime.now().strftime("%d_%m_%Y")
    with _connect() as con:
        return con.execute(
            "SELECT rank, score, scan_date FROM mi_scores WHERE player_name = ? AND scan_date = ?",
            (player_name, today),
        ).fetchone()


def get_today_guild_scores(guild_name: str) -> list[dict]:
    """
    All scores recorded today for the given guild, sorted by rank.
    Includes both registered and unregistered players detected by OCR.
    Each dict contains: rank, player_name, score, player_id (None if unregistered).
    """
    today = datetime.now().strftime("%d_%m_%Y")
    with _connect() as con:
        guild_row = con.execute(
            "SELECT id FROM game_guilds WHERE name = ?", (guild_name,)
        ).fetchone()
        if guild_row is None:
            return []

        rows = con.execute("""
            SELECT rank, player_name, score, player_id
            FROM mi_scores
            WHERE guild_id = ? AND scan_date = ?
            ORDER BY rank ASC
        """, (guild_row["id"], today)).fetchall()

    return [dict(row) for row in rows]


def get_daily_scan_count(discord_user_id: str) -> int:
    """Return how many /mi scans the user has submitted today."""
    today = datetime.now().strftime("%d_%m_%Y")
    with _connect() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM mi_scans WHERE submitted_by = ? AND scan_date = ?",
            (discord_user_id, today),
        ).fetchone()
        return row[0] if row else 0


def get_latest_scan_scores() -> list[sqlite3.Row]:
    """All scores from the most recent scan date, ordered by rank."""
    with _connect() as con:
        return con.execute("""
            SELECT rank, player_name, score, scan_date
            FROM mi_scores
            WHERE scan_date = (SELECT MAX(scan_date) FROM mi_scores)
            ORDER BY rank
        """).fetchall()

def get_total_weekly_leaderboard(guild_name: str, ref_date: datetime | None = None) -> list[dict]:
    """
    Individual scores for each player in the given guild plus any unregistered
    players detected by OCR that week. Sorted by total_score descending.
    Returns a list of dicts: player_name, total_score (float), days_present,
    and one key per day e.g. '11_03_2026' -> raw score string or None.
    """
    dates = _week_dates(ref_date)
    placeholders = ",".join("?" * len(dates))
    with _connect() as con:
        guild_row = con.execute(
            "SELECT id FROM game_guilds WHERE name = ?", (guild_name,)
        ).fetchone()
        if guild_row is None:
            return []
        guild_id = guild_row["id"]

        rows = con.execute(f"""
            SELECT player_name, scan_date, score
            FROM mi_scores
            WHERE guild_id = ?
              AND scan_date IN ({placeholders})
            ORDER BY player_name, scan_date
        """, [guild_id] + dates).fetchall()

    players: dict[str, dict] = {}
    for row in rows:
        name = row["player_name"]
        if name not in players:
            players[name] = {"player_name": name, "total_score": 0.0, "days_present": 0}
            for d in dates:
                players[name][d] = None
        players[name][row["scan_date"]] = row["score"]
        players[name]["total_score"] += _score_to_float(row["score"])
        players[name]["days_present"] += 1

    return sorted(players.values(), key=lambda r: r["total_score"], reverse=True)


def _week_dates(ref_date: datetime | None = None) -> list[str]:
    """Return DD_MM_YYYY strings for Mon–Sun of the week containing ref_date."""
    if ref_date is None:
        ref_date = datetime.now()
    monday = ref_date - timedelta(days=ref_date.weekday())
    return [(monday + timedelta(days=i)).strftime("%d_%m_%Y") for i in range(7)]


def get_total_monthly_leaderboard(guild_name: str, year: int, month: int) -> list[dict]:
    """
    Sum all scores for players in a guild for the given year/month.
    Sorted by total_score descending.
    Returns list of dicts: player_name, total_score (float), days_present.
    """
    _, num_days = calendar.monthrange(year, month)
    dates = [f"{d:02d}_{month:02d}_{year}" for d in range(1, num_days + 1)]
    placeholders = ",".join("?" * len(dates))
    with _connect() as con:
        guild_row = con.execute(
            "SELECT id FROM game_guilds WHERE name = ?", (guild_name,)
        ).fetchone()
        if guild_row is None:
            return []
        guild_id = guild_row["id"]

        rows = con.execute(f"""
            SELECT player_name, scan_date, score
            FROM mi_scores
            WHERE guild_id = ?
              AND scan_date IN ({placeholders})
            ORDER BY player_name, scan_date
        """, [guild_id] + dates).fetchall()

    players: dict[str, dict] = {}
    for row in rows:
        name = row["player_name"]
        if name not in players:
            players[name] = {"player_name": name, "total_score": 0.0, "days_present": 0}
        players[name]["total_score"] += _score_to_float(row["score"])
        players[name]["days_present"] += 1

    return sorted(players.values(), key=lambda r: r["total_score"], reverse=True)


def weekday_dates_for_month(year: int, month: int, weekday: int) -> list[str]:
    """Return DD_MM_YYYY strings for every occurrence of weekday (0=Mon–6=Sun) in year/month."""
    _, num_days = calendar.monthrange(year, month)
    return [
        f"{d:02d}_{month:02d}_{year}"
        for d in range(1, num_days + 1)
        if datetime(year, month, d).weekday() == weekday
    ]


def get_player_weekly_scores(player_name: str, ref_date: datetime | None = None) -> dict:
    """
    Scores for a single player across Mon–Sun of the week containing ref_date.
    Returns dict: total_score (float), days_present, plus one key per date -> score string or None.
    """
    dates = _week_dates(ref_date)
    placeholders = ",".join("?" * len(dates))
    with _connect() as con:
        rows = con.execute(f"""
            SELECT scan_date, score FROM mi_scores
            WHERE player_name = ? AND scan_date IN ({placeholders})
        """, [player_name] + dates).fetchall()

    result: dict = {"total_score": 0.0, "days_present": 0}
    for d in dates:
        result[d] = None
    for row in rows:
        result[row["scan_date"]] = row["score"]
        result["total_score"] += _score_to_float(row["score"])
        result["days_present"] += 1
    return result


def get_player_monthly_scores(player_name: str, year: int, month: int) -> dict:
    """
    Total scores for a single player across an entire month.
    Returns dict: total_score (float), days_present.
    """
    _, num_days = calendar.monthrange(year, month)
    dates = [f"{d:02d}_{month:02d}_{year}" for d in range(1, num_days + 1)]
    placeholders = ",".join("?" * len(dates))
    with _connect() as con:
        rows = con.execute(f"""
            SELECT scan_date, score FROM mi_scores
            WHERE player_name = ? AND scan_date IN ({placeholders})
        """, [player_name] + dates).fetchall()

    result: dict = {"total_score": 0.0, "days_present": 0}
    for row in rows:
        result["total_score"] += _score_to_float(row["score"])
        result["days_present"] += 1
    return result


def get_player_weekday_scores_for_month(player_name: str, year: int, month: int, weekday: int) -> dict:
    """
    Scores for a single player on each occurrence of weekday (0=Mon) in year/month.
    Returns dict: total_score (float), days_present, plus one key per date -> score string or None.
    """
    dates = weekday_dates_for_month(year, month, weekday)
    if not dates:
        return {"total_score": 0.0, "days_present": 0}
    placeholders = ",".join("?" * len(dates))
    with _connect() as con:
        rows = con.execute(f"""
            SELECT scan_date, score FROM mi_scores
            WHERE player_name = ? AND scan_date IN ({placeholders})
        """, [player_name] + dates).fetchall()

    result: dict = {"total_score": 0.0, "days_present": 0}
    for d in dates:
        result[d] = None
    for row in rows:
        result[row["scan_date"]] = row["score"]
        result["total_score"] += _score_to_float(row["score"])
        result["days_present"] += 1
    return result


def get_weekday_scores_for_month(guild_name: str, year: int, month: int, weekday: int) -> list[dict]:
    """
    Per-player scores for each occurrence of weekday (0=Mon) in year/month.
    Sorted by total_score descending.
    Returns list of dicts: player_name, total_score (float), days_present,
    and one key per date e.g. '02_03_2026' -> score string or None.
    """
    dates = weekday_dates_for_month(year, month, weekday)
    if not dates:
        return []
    placeholders = ",".join("?" * len(dates))
    with _connect() as con:
        guild_row = con.execute(
            "SELECT id FROM game_guilds WHERE name = ?", (guild_name,)
        ).fetchone()
        if guild_row is None:
            return []
        guild_id = guild_row["id"]

        rows = con.execute(f"""
            SELECT player_name, scan_date, score
            FROM mi_scores
            WHERE guild_id = ?
              AND scan_date IN ({placeholders})
            ORDER BY player_name, scan_date
        """, [guild_id] + dates).fetchall()

    players: dict[str, dict] = {}
    for row in rows:
        name = row["player_name"]
        if name not in players:
            players[name] = {"player_name": name, "total_score": 0.0, "days_present": 0}
            for d in dates:
                players[name][d] = None
        players[name][row["scan_date"]] = row["score"]
        players[name]["total_score"] += _score_to_float(row["score"])
        players[name]["days_present"] += 1

    return sorted(players.values(), key=lambda r: r["total_score"], reverse=True)


def get_all_time_leaderboard() -> list[sqlite3.Row]:
    """
    One row per unique player_name: their best score scan row (by rank, then latest).
    Returns columns: player_name, best_rank, score, scanned_at, guild_name (if linked).
    """
    with _connect() as con:
        return con.execute("""
            SELECT
                ms.player_name,
                ms.rank      AS best_rank,
                ms.score,
                ms.scan_date,
                g.name       AS guild_name
            FROM mi_scores ms
            LEFT JOIN players p ON p.id = ms.player_id
            LEFT JOIN game_guilds g ON g.id = p.game_guild_id
            WHERE ms.id IN (
                -- best rank (lowest number) per player, tie-break on newest scan_date
                SELECT id FROM mi_scores ms2
                WHERE ms2.player_name = ms.player_name
                ORDER BY ms2.rank ASC, ms2.scan_date DESC
                LIMIT 1
            )
            ORDER BY ms.rank ASC
        """).fetchall()

def update_player_username(discord_user_id: str, new_username: str) -> None:
    with _connect() as con:
        row = con.execute(
            "SELECT username FROM players WHERE discord_user_id = ?",
            (discord_user_id,),
        ).fetchone()
        old_username = row["username"] if row else None

        con.execute(
            "UPDATE players SET username = ? WHERE discord_user_id = ?",
            (new_username, discord_user_id),
        )

        if old_username:
            con.execute(
                """UPDATE mi_scores SET player_id = (SELECT id FROM players WHERE discord_user_id = ?)
                   WHERE player_name = ?""",
                (discord_user_id, old_username),
            )
def get_streak(discord_user_id: str) -> int:
    with _connect() as con:
        rows = con.execute("""
            SELECT DISTINCT date(scanned_at) as day
            FROM mi_scans
            WHERE submitted_by = ?
            ORDER BY day DESC
        """, (discord_user_id,)).fetchall()

    streak = 0
    if not rows:
        return 0
    today = datetime.now().date()
    last_day = datetime.strptime(rows[0]["day"], "%Y-%m-%d").date()
    # If the most recent scan is older than yesterday, streak is already broken
    if (today - last_day).days > 1:
        return 0
    # Start counting from the most recent scan day (today or yesterday)
    for i, row in enumerate(rows):
        expected = last_day - timedelta(days=i)
        if datetime.strptime(row["day"], "%Y-%m-%d").date() != expected:
            break
        streak += 1
    return streak


def get_guild_streaks(guild_name: str) -> list[dict]:
    """
    Returns the consecutive-day submission streak for every player registered
    to *guild_name*.  Each entry contains 'username', 'discord_user_id', and
    'streak'.  Results are sorted by streak descending.
    """
    with _connect() as con:
        guild_row = con.execute(
            "SELECT id FROM game_guilds WHERE name = ?", (guild_name,)
        ).fetchone()
        if guild_row is None:
            return []

        players = con.execute("""
            SELECT discord_user_id, username
            FROM players
            WHERE game_guild_id = ?
        """, (guild_row["id"],)).fetchall()

        if not players:
            return []

        discord_ids = [p["discord_user_id"] for p in players]
        placeholders = ",".join("?" * len(discord_ids))
        scan_rows = con.execute(f"""
            SELECT submitted_by, date(scanned_at) AS day
            FROM mi_scans
            WHERE submitted_by IN ({placeholders})
            GROUP BY submitted_by, date(scanned_at)
            ORDER BY submitted_by, day DESC
        """, discord_ids).fetchall()

    # Group distinct days per player (already ordered newest-first per player)
    days_by_user: dict[str, list[str]] = {}
    for row in scan_rows:
        days_by_user.setdefault(row["submitted_by"], []).append(row["day"])

    today = datetime.now().date()
    results = []
    for p in players:
        uid = p["discord_user_id"]
        days = days_by_user.get(uid, [])
        streak = 0
        if days:
            last_day = datetime.strptime(days[0], "%Y-%m-%d").date()
            if (today - last_day).days <= 1:
                for i, day_str in enumerate(days):
                    if datetime.strptime(day_str, "%Y-%m-%d").date() != last_day - timedelta(days=i):
                        break
                    streak += 1
        results.append({
            "username": p["username"],
            "discord_user_id": uid,
            "streak": streak,
        })

    return sorted(results, key=lambda r: r["streak"], reverse=True)