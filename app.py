
import streamlit as st
import mysql.connector
import pandas as pd
from datetime import date, datetime
import calendar
import os
from decimal import Decimal

st.set_page_config(page_title="The Wisdom Game", page_icon="📚", layout="wide")

# ============================================================
# MYSQL CONFIGURATION
# ============================================================
# Local development:
#   set MYSQL_HOST=localhost
#   set MYSQL_PORT=3306
#   set MYSQL_USER=root
#   set MYSQL_PASSWORD=your_password
#   set MYSQL_DATABASE=trader_quest
#
# Streamlit Cloud:
# Add the same values to .streamlit/secrets.toml:
#
# [mysql]
# host = "your-host"
# port = 3306
# user = "your-user"
# password = "your-password"
# database = "your-database"
#
# The app checks Streamlit secrets first, then environment variables.

def _mysql_config():
    try:
        if "mysql" in st.secrets:
            cfg = st.secrets["mysql"]
            return {
                "host": cfg.get("host", "localhost"),
                "port": int(cfg.get("port", 3306)),
                "user": cfg.get("user", "root"),
                "password": cfg.get("password", ""),
                "database": cfg.get("database", "trader_quest"),
            }
    except Exception:
        pass

    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "trader_quest"),
    }


def get_conn():
    return mysql.connector.connect(**_mysql_config())


def q(sql, params=()):
    """Run a SELECT query and return a pandas DataFrame."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        df = pd.DataFrame(rows, columns=columns)
        # MySQL DECIMAL columns are returned as Decimal objects. Streamlit and
        # some pandas operations expect native int/float values, so normalize
        # Decimal values at the database boundary.
        for col in df.columns:
            if df[col].dtype == object and df[col].map(lambda v: isinstance(v, Decimal)).any():
                df[col] = df[col].map(lambda v: float(v) if isinstance(v, Decimal) else v)
        return df
    finally:
        cur.close()
        conn.close()


def execute(sql, params=()):
    """Run INSERT/UPDATE/DELETE and commit the transaction."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def scalar(sql, params=()):
    """Return the first column of the first row."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        value = row[0] if row else 0
        return float(value) if isinstance(value, Decimal) else value
    finally:
        cur.close()
        conn.close()

def month_start(d):
    return d.replace(day=1)

def month_end(d):
    last = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=last)

def fmt_pct(x):
    # Always return a native float so Streamlit never receives Decimal values.
    x = float(x or 0)
    return f"{max(0.0, x)*100:.0f}%"

def sync_book_from_log(book_title):
    if not book_title:
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(pages_read),0) FROM reading_log WHERE book=%s",
        (book_title,)
    )
    total = cur.fetchone()[0]

    cur.execute(
        "SELECT total_pages,start_date,status FROM books WHERE title=%s ORDER BY book_id LIMIT 1",
        (book_title,)
    )
    row = cur.fetchone()
    if row:
        total_pages, start_date, old_status = row
        status = old_status
        finish = None
        if total_pages and total >= total_pages:
            status = "Completed"
            finish = date.today().isoformat()
        elif total > 0 and old_status == "Not Started":
            status = "Reading"
        cur.execute(
            "UPDATE books SET pages_read=%s, status=%s, finish_date=COALESCE(%s,finish_date) WHERE title=%s",
            (total, status, finish, book_title)
        )
    conn.commit()
    conn.close()

def live_month_metrics(target):
    m = monthly_stats(target)
    today = date.today()
    start = month_start(target)
    end = month_end(target)
    if target.year == today.year and target.month == today.month:
        elapsed = max(1, (today - start).days + 1)
        days_left = max(0, (end - today).days)
    elif target < month_start(today):
        elapsed = max(1, (end - start).days + 1)
        days_left = 0
    else:
        elapsed = 0
        days_left = (end - start).days + 1

    if target <= month_start(today):
        page_pace = m["pages"] / elapsed if elapsed else 0
        projected_pages = m["pages"] if target < month_start(today) else page_pace * ((end-start).days+1)
    else:
        page_pace = 0
        projected_pages = 0

    remaining_pages = max(0, m["pages_goal"] - m["pages"])
    remaining_books = max(0, m["books_goal"] - m["completed"])
    required_daily_pages = remaining_pages / max(1, days_left + 1) if days_left >= 0 else 0
    return {**m, "elapsed": elapsed, "days_left": days_left,
            "page_pace": page_pace, "projected_pages": projected_pages,
            "remaining_pages": remaining_pages, "remaining_books": remaining_books,
            "required_daily_pages": required_daily_pages}

def level_for_xp(xp):
    levels = q("SELECT level,xp,title FROM levels ORDER BY level")
    if levels.empty:
        return (1, 0, "Reader", 0)
    row = levels[levels["xp"] <= xp].iloc[-1]
    return int(row["level"]), float(row["xp"]), str(row["title"]), xp-float(row["xp"])

def monthly_stats(target):
    start = month_start(target)
    end = month_end(target)
    books_goal, pages_goal, xp_goal = q(
        "SELECT books_goal,pages_goal,xp_goal FROM monthly_goals WHERE target_month=%s",
        (start.isoformat(),)
    ).iloc[0].tolist() if not q("SELECT 1 FROM monthly_goals WHERE target_month=%s",(start.isoformat(),)).empty else (0,0,0)
    completed = scalar("""SELECT COUNT(*) FROM books
                         WHERE status='Completed' AND finish_date>=%s AND finish_date<%s""",
                       (start.isoformat(), (end + pd.Timedelta(days=1)).isoformat()))
    pages = scalar("""SELECT COALESCE(SUM(pages_read),0) FROM reading_log
                      WHERE date>=%s AND date<%s""",
                   (start.isoformat(), (end + pd.Timedelta(days=1)).isoformat()))
    xp = pages * 2
    book_pct = completed/books_goal if books_goal else 0
    page_pct = pages/pages_goal if pages_goal else 0
    xp_pct = xp/xp_goal if xp_goal else 0
    overall = (book_pct+page_pct+xp_pct)/3 if (books_goal or pages_goal or xp_goal) else 0
    return dict(books_goal=books_goal,pages_goal=pages_goal,xp_goal=xp_goal,
                completed=completed,pages=pages,xp=xp,
                book_pct=book_pct,page_pct=page_pct,xp_pct=xp_pct,overall=overall)


def current_streak():
    daily=q("SELECT DISTINCT date FROM reading_log WHERE pages_read>0")
    if daily.empty:return 0
    dates=set(pd.to_datetime(daily["date"]).dt.date); cur=date.today()
    if cur not in dates:return 0
    n=0
    while cur in dates:
        n+=1; cur=date.fromordinal(cur.toordinal()-1)
    return n

def wisdom_coins():
    pages=scalar("SELECT COALESCE(SUM(pages_read),0) FROM reading_log")
    books=scalar("SELECT COUNT(*) FROM books WHERE status='Completed'")
    return int(pages//10+books*25+current_streak()*2)

def monthly_rank(x):
    if x>=1:return "👑 MASTER","100%+"
    if x>=.9:return "💎 PLATINUM","90%+"
    if x>=.75:return "🥇 GOLD","75%+"
    if x>=.5:return "🥈 SILVER","50%+"
    return "🥉 BRONZE","<50%"

def achievements():
    pages=scalar("SELECT COALESCE(SUM(pages_read),0) FROM reading_log")
    sessions=scalar("SELECT COUNT(*) FROM reading_log WHERE pages_read>0")
    books=scalar("SELECT COUNT(*) FROM books WHERE status='Completed'")
    streak=current_streak()
    return [
        ("📖 First Chapter",sessions>=1,"Complete your first reading session"),
        ("📚 First Book",books>=1,"Finish your first book"),
        ("📄 1,000 Pages",pages>=1000,"Read 1,000 total pages"),
        ("🔥 7-Day Streak",streak>=7,"Read 7 consecutive days"),
        ("⚔️ 30-Day Streak",streak>=30,"Read 30 consecutive days"),
        ("📚 10 Books",books>=10,"Finish 10 books"),
        ("🧠 10,000 Pages",pages>=10000,"Read 10,000 pages"),
        ("⚡ 10,000 XP",pages*2>=10000,"Earn 10,000 XP"),
        ("🏛️ 100 Sessions",sessions>=100,"Complete 100 sessions"),
    ]

# ---------- CSS ----------
st.markdown("""
<style>
.block-container{padding:1.2rem 2rem 3rem;max-width:1450px}
[data-testid="stSidebar"]{background:linear-gradient(180deg,#080b12,#111827)}
.hero{padding:2rem 2.2rem;border-radius:24px;border:1px solid #374151;background:radial-gradient(circle at 80% 20%,rgba(245,196,81,.18),transparent 28%),linear-gradient(135deg,#0b1020,#182235 60%,#101827);box-shadow:0 18px 60px rgba(0,0,0,.28);margin-bottom:1.1rem}
.hero h1{margin:0;font-size:2.55rem}.hero p{margin:.55rem 0 0;color:#cbd5e1}
.eyebrow{text-transform:uppercase;letter-spacing:.18em;font-size:.72rem;color:#f5c451;font-weight:800}
.panel{padding:1.15rem 1.25rem;border-radius:18px;border:1px solid #293548;background:linear-gradient(145deg,#101827,#0e1522);box-shadow:0 8px 28px rgba(0,0,0,.16)}
.quest{padding:1rem 1.1rem;border-radius:16px;background:linear-gradient(90deg,#172033,#101827);border:1px solid #2b3748;margin:.45rem 0}.quest-title{font-weight:800}
.badge{display:inline-block;padding:.25rem .55rem;border-radius:999px;background:#263449;color:#f8fafc;font-size:.72rem;font-weight:700}.rank{font-size:1.8rem;font-weight:900}
.map-node{padding:.9rem;border-radius:16px;border:1px solid #334155;background:#101827;text-align:center;margin:.45rem 0}.map-node strong{display:block}.map-node span{color:#94a3b8;font-size:.78rem}
.quote{padding:1rem 1.2rem;border-left:3px solid #f5c451;background:#101827;border-radius:0 14px 14px 0;color:#dbe4f0;font-style:italic}
div[data-testid="stMetric"]{background:#0e1624;border:1px solid #293548;padding:.65rem .8rem;border-radius:14px}
</style>
""", unsafe_allow_html=True)

st.sidebar.title("📚 THE WISDOM GAME")
page = st.sidebar.radio("Navigate", [
    "🏠 Dashboard", "⚔️ Daily Quest", "📖 Book Library", "📝 Reading Log",
    "📜 Wisdom Journal", "🗺️ Knowledge Map", "🏆 Achievements",
    "🎯 Monthly Quest", "📈 Monthly History", "⚡ XP & Levels", "ℹ️ How To Use"
])

total_xp = scalar("SELECT COALESCE(SUM(pages_read),0)*2 FROM reading_log")
lvl, lvl_base, title, xp_into = level_for_xp(total_xp)

# ---------- Dashboard ----------
if page == "🏠 Dashboard":
    st.markdown('<div class="hero"><div class="eyebrow">YOUR PERSONAL WISDOM RPG</div><h1>📚 The Wisdom Game</h1><p>Read. Learn. Level up. Build a mind that compounds.</p></div>', unsafe_allow_html=True)

    m = live_month_metrics(date.today().replace(day=1))
    streak = current_streak()
    coins = wisdom_coins()
    rank, rank_rule = monthly_rank(m["overall"])
    c1,c2,c3,c4,c5,c6=st.columns(6)
    c1.metric("⚡ Level",lvl); c2.metric("XP",f"{int(total_xp):,}")
    c3.metric("🔥 Streak",f"{streak} days"); c4.metric("💎 Coins",f"{coins:,}")
    c5.metric("📄 This Month",f"{int(m['pages']):,}"); c6.metric("🏆 Rank",rank.split(" ",1)[-1])

    st.markdown("### 🧙 Character")
    a,b,c=st.columns([1,2,1])
    with a: st.markdown(f'<div class="panel"><div class="eyebrow">LEVEL</div><div class="rank">LVL {lvl}</div><div class="badge">{title}</div></div>',unsafe_allow_html=True)
    with b:
        st.markdown('<div class="panel"><div class="eyebrow">NEXT LEVEL</div>',unsafe_allow_html=True)
        nxt=q("SELECT xp FROM levels WHERE level=%s",(lvl+1,))
        nx=float(nxt["xp"].iloc[0]) if not nxt.empty else lvl_base+1000
        st.progress(min(1,max(0,xp_into/max(1,nx-lvl_base))),text=f"{int(xp_into):,} XP toward Level {lvl+1}")
        st.markdown(f'<div class="small">{max(0,int(nx-total_xp)):,} XP remaining</div></div>',unsafe_allow_html=True)
    with c: st.markdown(f'<div class="panel"><div class="eyebrow">MONTHLY RANK</div><div class="rank">{rank}</div><div class="small">Threshold: {rank_rule}</div></div>',unsafe_allow_html=True)
    st.subheader("🎯 Current Month Quest — LIVE")
    lm = live_month_metrics(date.today().replace(day=1))
    q1,q2,q3,q4 = st.columns(4)
    q1.metric("Books", f"{int(lm['completed'])} / {int(lm['books_goal'])}")
    q2.metric("Pages", f"{int(lm['pages'])} / {int(lm['pages_goal'])}")
    q3.metric("XP", f"{int(lm['xp'])} / {int(lm['xp_goal'])}")
    q4.metric("Days Left", int(lm["days_left"]))

    st.progress(float(min(1.0, float(lm["page_pct"]))), text=f"📄 Pages — {fmt_pct(lm['page_pct'])}")
    st.progress(float(min(1.0, float(lm["book_pct"]))), text=f"📚 Books — {fmt_pct(lm['book_pct'])}")
    st.progress(float(min(1.0, float(lm["xp_pct"]))), text=f"⚡ XP — {fmt_pct(lm['xp_pct'])}")

    pace1,pace2,pace3 = st.columns(3)
    pace1.metric("Pages/day so far", f"{lm['page_pace']:.1f}")
    pace2.metric("Required pages/day", f"{lm['required_daily_pages']:.1f}")
    pace3.metric("Projected month pages", f"{lm['projected_pages']:.0f}")

    if lm["page_pct"] >= 1 and lm["book_pct"] >= 1:
        st.success("🏆 MONTHLY QUEST COMPLETE — keep reading to build Wisdom XP!")
    elif lm["required_daily_pages"] <= lm["page_pace"] and lm["pages_goal"] > 0:
        st.success("🔥 You are currently on pace to hit the monthly page target.")
    elif lm["pages_goal"] > 0:
        st.warning(f"⚔️ You need about {lm['required_daily_pages']:.0f} pages/day to finish the month on target.")

    st.subheader("📊 Category Breakdown")
    cat = q("""SELECT category, SUM(pages_read) pages, SUM(pages_read)*2 xp
               FROM reading_log WHERE category IS NOT NULL AND category<>'' GROUP BY category ORDER BY pages DESC""")
    if not cat.empty:
        st.bar_chart(cat.set_index("category")["pages"])
    else:
        st.info("No reading data yet.")

    st.subheader("📚 Active Books")
    active = q("""SELECT book_id,title,author,category,total_pages,pages_read,
                         CASE WHEN total_pages=0 THEN 0 ELSE pages_read*1.0/total_pages END completion
                  FROM books WHERE status<>'Completed' AND title<>'' ORDER BY book_id""")
    if not active.empty:
        active["completion"] = active["completion"].map(fmt_pct)
        st.dataframe(active, use_container_width=True, hide_index=True)
    else:
        st.success("No active books. Add your next book in Book Library.")

# ---------- Book Library ----------
elif page == "⚔️ Daily Quest":
    st.title("⚔️ Daily Quest")
    today=date.today()
    pages_today=scalar("SELECT COALESCE(SUM(pages_read),0) FROM reading_log WHERE date=%s",(today.isoformat(),))
    target=20
    a,b,c,d=st.columns(4)
    a.metric("📄 Target",f"{target} pages"); b.metric("📖 Read",f"{int(pages_today)} pages")
    c.metric("🔥 Streak",f"{current_streak()} days"); d.metric("🎁 Bonus","+50 XP" if pages_today>=target else "Locked")
    daily_progress = min(1.0, float(pages_today) / float(target))
    st.progress(daily_progress, text=f"Daily Quest: {int(pages_today)} / {target} pages")
    if pages_today>=target: st.success("⚔️ DAILY QUEST COMPLETE!")
    else: st.warning(f"Read {max(0,target-pages_today):.0f} more pages.")
    st.markdown("### 🗡️ Missions")
    for name,done,reward in [
        ("📖 Read 20 pages",pages_today>=20,"+50 XP"),
        ("🧠 Capture one lesson",scalar("SELECT COUNT(*) FROM reading_log WHERE date=%s AND notes<>''",(today.isoformat(),))>0,"+20 XP"),
        ("🔥 Protect your streak",pages_today>0,"+10 XP")]:
        st.markdown(f'<div class="quest"><span class="quest-title">{"✅" if done else "⬜"} {name}</span><span class="badge" style="float:right">{reward}</span></div>',unsafe_allow_html=True)
    st.markdown(f'<div class="quote">💎 You have <b>{wisdom_coins():,} Wisdom Coins</b>.</div>',unsafe_allow_html=True)

elif page == "📖 Book Library":
    st.title("📖 Book Database")
    st.caption("Same core fields as the Excel Book Database, with live calculations.")

    with st.form("add_book"):
        st.subheader("➕ Add Book")
        a,b,c,d = st.columns(4)
        title_in = a.text_input("Title")
        author_in = b.text_input("Author")
        category_in = c.text_input("Category")
        pages_in = d.number_input("Total Pages", min_value=0.0, step=1.0)
        start_in = st.date_input("Start Date", value=date.today())
        submitted = st.form_submit_button("Add Book")
        if submitted and title_in.strip():
            next_id = int(scalar("SELECT COALESCE(MAX(book_id),0)+1 FROM books"))
            execute("""INSERT INTO books(book_id,title,author,category,total_pages,start_date,status,pages_read)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,0)""",
                    (next_id,title_in.strip(),author_in.strip(),category_in.strip(),pages_in,start_in.isoformat(),"Not Started"))
            st.success("Book added.")
            st.rerun()

    st.divider()
    books_df = q("""SELECT book_id AS ID,title AS Title,author AS Author,category AS Category,
                           total_pages AS 'Total Pages',start_date AS 'Start Date',status AS Status,
                           finish_date AS 'Finish Date',pages_read AS 'Pages Read',
                           CASE WHEN total_pages=0 THEN 0 ELSE pages_read*1.0/total_pages END AS 'Completion %',
                           pages_read*2 AS 'Book XP'
                    FROM books ORDER BY book_id""")
    if not books_df.empty:
        books_df["Completion %"] = books_df["Completion %"].map(fmt_pct)
        books_df["Book XP"] = books_df["Book XP"].round(0)
        st.dataframe(books_df, use_container_width=True, hide_index=True)

    st.subheader("✏️ Update Book")
    ids = q("SELECT book_id,title FROM books WHERE title<>'' ORDER BY book_id")
    if not ids.empty:
        selected = st.selectbox("Book", ids["book_id"].tolist(),
                                format_func=lambda x: f"{x} — {ids.loc[ids.book_id==x,'title'].iloc[0]}")
        current = q("SELECT * FROM books WHERE book_id=%s",(selected,)).iloc[0]
        with st.form("edit_book"):
            stt = st.text_input("Title", current["title"])
            auth = st.text_input("Author", current["author"] or "")
            cat = st.text_input("Category", current["category"] or "")
            tp = st.number_input("Total Pages", min_value=0.0, value=float(current["total_pages"] or 0), step=1.0)
            sd = st.text_input("Start Date", current["start_date"] or "")
            status = st.selectbox("Status", ["Not Started","Reading","Completed"],
                                  index=["Not Started","Reading","Completed"].index(current["status"]) if current["status"] in ["Not Started","Reading","Completed"] else 0)
            fr = st.text_input("Finish Date", current["finish_date"] or "")
            pr = st.number_input("Pages Read", min_value=0.0, value=float(current["pages_read"] or 0), step=1.0)
            save = st.form_submit_button("Save Book")
            if save:
                # Finish Date is optional while a book is still being read.
                # MySQL DATE columns do not accept an empty string, so store
                # NULL until the book is actually completed.
                fr = fr.strip() if isinstance(fr, str) else fr
                if status == "Completed":
                    fr = fr or date.today().isoformat()
                else:
                    fr = fr or None

                execute("""UPDATE books SET title=%s,author=%s,category=%s,total_pages=%s,start_date=%s,
                           status=%s,finish_date=%s,pages_read=%s WHERE book_id=%s""",
                        (stt,auth,cat,tp,sd,status,fr,pr,selected))
                st.success("Book updated.")
                st.rerun()

# ---------- Reading Log ----------
elif page == "📝 Reading Log":
    st.title("📝 Reading Log")
    st.caption("Every reading session earns 2 XP per page, matching the workbook.")

    book_options = q("SELECT title,category FROM books WHERE title<>'' ORDER BY title")
    with st.form("log_session"):
        a,b,c,d = st.columns(4)
        dt = a.date_input("Date", date.today())
        book = b.selectbox("Book", book_options["title"].tolist() if not book_options.empty else [""])
        cat_default = ""
        if book and not book_options.empty:
            rr = book_options[book_options["title"]==book]
            cat_default = rr["category"].iloc[0] if not rr.empty else ""
        category = c.text_input("Category", cat_default)
        pages_from = d.number_input("Pages From", min_value=0.0, step=1.0)
        e,f = st.columns(2)
        pages_read = e.number_input("Pages Read", min_value=0.0, step=1.0)
        session_type = f.selectbox("Session Type", ["Normal","Deep Reading","Review"])
        notes = st.text_area("Notes / Wisdom captured")
        save = st.form_submit_button("📖 Log Reading")
        if save:
            execute("""INSERT INTO reading_log(date,book,category,pages_from,pages_read,session_type,notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (dt.isoformat(),book,category,pages_from,pages_read,session_type,notes))
            sync_book_from_log(book)
            st.success(f"+{pages_read*2:.0f} XP earned. Monthly progress updated automatically.")
            st.rerun()

    logs_df = q("""SELECT id,date AS Date,book AS Book,category AS Category,pages_from AS 'Pages From',
                           pages_read AS 'Pages Read',pages_read*2 AS XP,session_type AS 'Session Type',notes AS Notes
                    FROM reading_log ORDER BY date DESC,id DESC""")
    st.dataframe(logs_df, use_container_width=True, hide_index=True)

    st.subheader("📈 Daily Pages")
    daily = q("""SELECT date,SUM(pages_read) pages FROM reading_log GROUP BY date ORDER BY date""")
    if not daily.empty:
        st.line_chart(daily.set_index("date")["pages"])
        st.subheader("🔥 Reading Streak")
        dates = pd.to_datetime(daily["date"]).dt.date.tolist()
        streak = 0
        cursor = date.today()
        date_set = set(dates)
        while cursor in date_set:
            streak += 1
            cursor = cursor.fromordinal(cursor.toordinal()-1)
        st.metric("Current consecutive reading days", streak)

# ---------- Monthly Quest ----------
elif page == "📜 Wisdom Journal":
    st.title("📜 Wisdom Journal")
    st.caption("Turn reading into permanent knowledge.")
    logs=q("""SELECT date,book,pages_read,notes FROM reading_log WHERE notes IS NOT NULL AND TRIM(notes)<>'' ORDER BY date DESC""")
    search=st.text_input("🔎 Search wisdom")
    if search and not logs.empty: logs=logs[logs.apply(lambda r:search.lower() in " ".join(map(str,r.values)).lower(),axis=1)]
    if logs.empty: st.info("No wisdom captured yet.")
    for _,r in logs.iterrows():
        st.markdown(f'<div class="panel" style="margin:.6rem 0"><div class="eyebrow">{r["date"]} • {r["book"]}</div><div style="font-size:1.05rem">{r["notes"]}</div><div class="small">📄 {int(r["pages_read"])} pages • ⚡ {int(r["pages_read"]*2)} XP</div></div>',unsafe_allow_html=True)

elif page == "🗺️ Knowledge Map":
    st.title("🗺️ Knowledge Map")
    st.caption("Reading categories become territories.")
    cat=q("""SELECT COALESCE(NULLIF(category,''),'Uncategorized') category,SUM(pages_read) pages,COUNT(DISTINCT book) books,SUM(pages_read)*2 xp FROM reading_log GROUP BY category ORDER BY pages DESC""")
    if cat.empty: st.info("Start reading to reveal territories.")
    else:
        mx=max(1,float(cat["pages"].max())); cols=st.columns(3)
        for i,(_,r) in enumerate(cat.iterrows()):
            strength=float(r["pages"])/mx
            stage="👑 MASTERED" if strength>=.8 else "🏰 FORTIFIED" if strength>=.5 else "🌱 DEVELOPING" if strength>=.2 else "🌑 UNEXPLORED"
            icon="👑" if strength>=.8 else "🏰" if strength>=.5 else "🌱" if strength>=.2 else "🌑"
            with cols[i%3]:
                st.markdown(f'<div class="map-node"><div style="font-size:1.8rem">{icon}</div><strong>{r["category"]}</strong><span>{stage}</span><div>📄 {int(r["pages"]):,} pages</div><div class="small">📚 {int(r["books"])} books • ⚡ {int(r["xp"]):,} XP</div></div>',unsafe_allow_html=True)
                st.progress(strength)

elif page == "🏆 Achievements":
    st.title("🏆 Achievements")
    ach=achievements(); unlocked=sum(x[1] for x in ach)
    st.progress(unlocked/len(ach),text=f"{unlocked} / {len(ach)} unlocked")
    cols=st.columns(3)
    for i,(name,done,desc) in enumerate(ach):
        with cols[i%3]:
            st.markdown(f'<div class="panel" style="margin:.5rem 0;opacity:{1 if done else .42}"><div style="font-size:1.6rem">{"🏆" if done else "🔒"}</div><div style="font-weight:800">{name}</div><div class="small">{desc}</div></div>',unsafe_allow_html=True)

elif page == "🎯 Monthly Quest":
    st.title("🎯 Monthly Reading Quest")
    st.caption("Your progress is calculated live from the Reading Log and completed books.")

    available_months = q("SELECT target_month FROM monthly_goals ORDER BY target_month DESC")
    default_month = date.today().replace(day=1)
    if not available_months.empty:
        try:
            default_month = pd.Timestamp(available_months.iloc[0,0]).date()
        except Exception:
            pass
    selected_month = st.date_input("View Month", value=default_month).replace(day=1)
    live = live_month_metrics(selected_month)

    with st.form("goal_form"):
        target = st.date_input("Target Month", value=date.today().replace(day=1))
        target = target.replace(day=1)
        a,b = st.columns(2)
        bg = a.number_input("Books Goal", min_value=0.0, step=1.0)
        pg = b.number_input("Pages Goal", min_value=0.0, step=50.0)
        save = st.form_submit_button("Save Monthly Goal")
        if save:
            execute("""INSERT INTO monthly_goals(target_month,books_goal,pages_goal,xp_goal)
                       VALUES (%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE
                       books_goal=VALUES(books_goal),
                       pages_goal=VALUES(pages_goal),
                       xp_goal=VALUES(xp_goal)""",
                    (target.isoformat(),bg,pg,pg*2))
            st.success("Monthly quest saved.")
            st.rerun()

    goals = q("SELECT * FROM monthly_goals ORDER BY target_month DESC")
    st.dataframe(goals, use_container_width=True, hide_index=True)

    st.subheader("📊 Live Monthly Progress")
    a,b,c,d = st.columns(4)
    a.metric("Books", f"{int(live['completed'])} / {int(live['books_goal'])}")
    b.metric("Pages", f"{int(live['pages'])} / {int(live['pages_goal'])}")
    c.metric("XP", f"{int(live['xp'])} / {int(live['xp_goal'])}")
    d.metric("Overall", fmt_pct(live["overall"]))

    st.progress(float(min(1.0, float(live["book_pct"]))), text=f"Books: {fmt_pct(live['book_pct'])}")
    st.progress(float(min(1.0, float(live["page_pct"]))), text=f"Pages: {fmt_pct(live['page_pct'])}")
    st.progress(float(min(1.0, float(live["xp_pct"]))), text=f"XP: {fmt_pct(live['xp_pct'])}")

    if selected_month.year == date.today().year and selected_month.month == date.today().month:
        st.info(f"📅 {live['days_left']} days left • {live['required_daily_pages']:.0f} pages/day needed from now")

    st.subheader("Quest Rules")
    st.markdown("""
    - 📖 **Books:** complete the monthly book target.
    - 📄 **Pages:** complete the monthly page target.
    - ⚡ **XP Goal:** calculated as **Pages Goal × 2**.
    - 🏆 The workbook's overall monthly score is the average of Book %, Page %, and XP %.
    """)

# ---------- Monthly History ----------
elif page == "📈 Monthly History":
    st.title("📈 Monthly History")
    months = q("SELECT target_month,books_goal,pages_goal,xp_goal FROM monthly_goals ORDER BY target_month")
    rows=[]
    for _,r in months.iterrows():
        d = pd.Timestamp(r["target_month"]).date()
        m = monthly_stats(d)
        rows.append([d.isoformat(),r["books_goal"],m["completed"],r["pages_goal"],m["pages"],
                     r["xp_goal"],m["xp"],m["book_pct"],m["page_pct"],m["xp_pct"],m["overall"]])
    hist=pd.DataFrame(rows,columns=["Month","Books Goal","Books Completed","Pages Goal","Pages Read",
                                     "XP Goal","XP Earned","Book %","Page %","XP %","Overall %"])
    if not hist.empty:
        for c in ["Book %","Page %","XP %","Overall %"]:
            hist[c]=hist[c].map(fmt_pct)
        st.dataframe(hist,use_container_width=True,hide_index=True)
        raw=hist.copy()
        st.subheader("Overall Progress")
        # chart from numeric recalculation
        chart=pd.DataFrame(rows,columns=["Month","Books Goal","Books Completed","Pages Goal","Pages Read",
                                          "XP Goal","XP Earned","Book %","Page %","XP %","Overall %"])
        chart["Overall %"]=chart["Overall %"]*100
        st.line_chart(chart.set_index("Month")["Overall %"])
    st.subheader("🔥 Reading Heatmap")
    heat=q("SELECT date,SUM(pages_read) pages FROM reading_log WHERE pages_read>0 GROUP BY date ORDER BY date")
    if not heat.empty:
        heat["date"]=pd.to_datetime(heat["date"]); heat["day"]=heat["date"].dt.day_name().str[:3]; heat["week"]=heat["date"].dt.to_period("W-MON").astype(str)
        grid=heat.pivot_table(index="day",columns="week",values="pages",aggfunc="sum",fill_value=0)
        st.dataframe(grid,use_container_width=True)
    st.subheader("🔮 Pace Forecast")
    lm=live_month_metrics(date.today().replace(day=1))
    pct=(lm["projected_pages"]/lm["pages_goal"]*100) if lm["pages_goal"] else 0
    st.info(f"Current pace projects **{lm['projected_pages']:.0f} pages** this month ({pct:.0f}% of target).")

# ---------- XP Rules ----------
elif page == "⚡ XP & Levels":
    st.title("⚡ XP Rules & Levels")
    a,b=st.columns(2)
    with a:
        st.subheader("XP Rules")
        st.dataframe(q("SELECT action AS Action,xp AS XP FROM xp_rules"),use_container_width=True,hide_index=True)
    with b:
        st.subheader("Levels")
        st.dataframe(q("SELECT level AS Level,xp AS XP,title AS Title FROM levels ORDER BY level"),
                     use_container_width=True,hide_index=True)

    st.info("Core implemented rule: **1 page = 2 XP**. Session, completion and monthly bonus rules are preserved here as the game's rulebook.")

# ---------- How To Use ----------
else:
    st.title("ℹ️ Book Quest — How To Use")
    steps = [
        "Set your monthly target in Monthly Quest.",
        "Add books to Book Library with title, author, category and total pages.",
        "Every day add a row to Reading Log and enter Pages Read.",
        "XP automatically = 2 XP per page.",
        "Use XP & Levels for the game's progression rules.",
        "Change Book Library status to Completed and enter Finish Date when finished.",
        "Dashboard shows your monthly quest, total XP and category breakdown.",
        "Monthly History stores month-by-month performance.",
    ]
    for i,s in enumerate(steps,1):
        st.markdown(f"**{i}.** {s}")

st.sidebar.divider()
st.sidebar.markdown(f"### ⚡ Level {lvl}")
st.sidebar.caption(title)
st.sidebar.markdown(f"🔥 **{current_streak()} day streak**")
st.sidebar.markdown(f"💎 **{wisdom_coins():,} Wisdom Coins**")
