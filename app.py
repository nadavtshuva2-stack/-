
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="מנתח מניות", page_icon="🔍", layout="wide")

st.markdown(
    """
    <style>
    .block-container {direction: rtl; text-align: right; padding-top: 2rem;}
    [data-testid="stMetricValue"] {direction: ltr; text-align: right;}
    [data-testid="stDataFrame"] {direction: ltr;}
    .hero {text-align: center; margin: 2rem 0 1rem 0;}
    .hero h1 {font-size: 2.6rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

METRICS = [
    "הכנסות",
    "רווח למניה",
    "שולי רווח נקי",
    "מכפיל רווח",
    "מכפיל רווח עתידי",
    "יחס חוב לתזרים (שנתי)",
    "הכנסה לעובד (שנתי)",
]
QUARTERLY = {"הכנסות", "רווח למניה", "שולי רווח נקי", "מכפיל רווח"}
ANNUAL = {"יחס חוב לתזרים (שנתי)", "הכנסה לעובד (שנתי)"}


def fmt(v, kind="num"):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    try:
        if kind == "x":
            return f"{v:.2f}x"
        if kind == "pct":
            return f"{v:.1f}%"
        if kind == "money":
            a = abs(v)
            if a >= 1e12:
                return f"{v / 1e12:.2f}T"
            if a >= 1e9:
                return f"{v / 1e9:.2f}B"
            if a >= 1e6:
                return f"{v / 1e6:.1f}M"
            return f"{v:,.0f}"
        return f"{v:,.2f}"
    except Exception:
        return "—"


def safe_div(a, b):
    try:
        if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
            return None
        return a / b
    except Exception:
        return None


def row(df, names):
    """סדרה לפי תאריכים (מהישן לחדש) לפי רשימת שמות אפשריים."""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            return df.loc[n].sort_index().dropna()
    return None


def draw(s, label, kind, window, unit, note=None):
    """גרף קווי עם קו ממוצע נע, כרטיסי סיכום וטבלה עם שינוי באחוזים."""
    s = s.dropna()
    if s.empty:
        st.warning("אין נתונים להצגה.")
        return

    ma = s.rolling(window).mean()
    avg_name = f"ממוצע נע ({window} {unit})"

    chg = (s.diff() / s.shift().abs() * 100).replace([np.inf, -np.inf], np.nan)
    chg_name = "שינוי מהתקופה הקודמת (%)"
    avg_chg_name = f"ממוצע שינוי (%) — {window - 1} שינויים"
    avg_chg = chg.rolling(window - 1).mean()

    chart_df = pd.DataFrame({label: s, avg_name: ma})
    table_df = pd.DataFrame({
        label: s,
        avg_name: ma,
        chg_name: chg,
        avg_chg_name: avg_chg,
    })
    chart_df.index = [d.strftime("%Y-%m") for d in chart_df.index]
    table_df.index = [d.strftime("%Y-%m") for d in table_df.index]

    m1, m2, m3 = st.columns(3)
    if ma.notna().any():
        m1.metric(f"ממוצע אחרון ({window} {unit})", fmt(ma.dropna().iloc[-1], kind))
    else:
        m1.metric(f"ממוצע אחרון ({window} {unit})", "—")
        st.warning(
            f"יש רק {len(s)} תקופות זמינות, פחות מהחלון שבחרת ({window}). "
            "הקטן את הטווח כדי לראות קו ממוצע."
        )
    m2.metric("הערך האחרון", fmt(s.iloc[-1], kind))
    last_avg_chg = avg_chg.dropna()
    m3.metric(
        f"ממוצע שינוי ({window - 1} שינויים)",
        fmt(last_avg_chg.iloc[-1], "pct") if not last_avg_chg.empty else "—",
    )

    st.line_chart(chart_df)
    st.dataframe(table_df.round(2).T, use_container_width=True)
    if note:
        st.caption(note)


@st.cache_data(ttl=3600, show_spinner=False)
def search_tickers(q):
    try:
        res = yf.Search(q, max_results=8).quotes
        return [
            (r.get("symbol"), r.get("shortname") or r.get("longname") or "", r.get("exchDisp", ""))
            for r in res
            if r.get("quoteType") in ("EQUITY", "ETF")
        ]
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def load(ticker):
    t = yf.Ticker(ticker)
    info = t.info or {}
    qinc = t.quarterly_income_stmt
    eps = None
    try:
        ed = t.get_earnings_dates(limit=16)
        s = ed["Reported EPS"].dropna()
        if s.index.tz is not None:
            s.index = s.index.tz_localize(None)
        s.index = s.index.normalize()
        s = s.sort_index()
        if len(s) >= 4:
            eps = s
    except Exception:
        eps = None
    if eps is None:
        eps = row(qinc, ["Diluted EPS", "Basic EPS"])
    hist = t.history(period="6y")
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)
    return {
        "info": info,
        "qinc": qinc,
        "inc": t.income_stmt,
        "bal": t.balance_sheet,
        "cf": t.cashflow,
        "eps": eps,
        "hist": hist,
    }


def pe_series(eps, hist):
    if eps is None or len(eps) < 4 or hist.empty:
        return None
    ttm = eps.rolling(4).sum().dropna()
    out = {}
    for d, e in ttm.items():
        if e > 0:
            px = hist["Close"][hist.index <= d]
            if not px.empty:
                out[d] = px.iloc[-1] / e
    return pd.Series(out) if out else None


# ---------- דף הבית ----------

if "ticker" not in st.session_state:
    st.session_state.ticker = None

st.markdown('<div class="hero"><h1>🔍 מנתח מניות</h1></div>', unsafe_allow_html=True)

_, mid, _ = st.columns([1, 2, 1])
with mid:
    query = st.text_input(
        "חיפוש",
        placeholder="🔍  הקלד שם חברה או טיקר (למשל AAPL, Nvidia)",
        label_visibility="collapsed",
    )
    if query:
        results = search_tickers(query)
        if not results:
            st.warning("לא נמצאו תוצאות. נסה טיקר מדויק.")
            if st.button(f"נסה בכל זאת: {query.upper()}"):
                st.session_state.ticker = query.upper()
        else:
            labels = [f"{s} — {n} ({e})" for s, n, e in results]
            choice = st.selectbox("תוצאות", labels, label_visibility="collapsed")
            if st.button("הצג נתונים", type="primary", use_container_width=True):
                st.session_state.ticker = results[labels.index(choice)][0]

ticker = st.session_state.ticker
if not ticker:
    st.info("חפש מנייה כדי להתחיל.")
    st.stop()

with st.spinner(f"טוען נתונים עבור {ticker}..."):
    d = load(ticker)

info, qinc, inc, bal, cf, eps, hist = (
    d["info"], d["qinc"], d["inc"], d["bal"], d["cf"], d["eps"], d["hist"],
)
if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
    st.error("לא הצלחתי למשוך נתונים לטיקר הזה.")
    st.stop()

cur = info.get("currency", "")
employees = info.get("fullTimeEmployees")

st.divider()
st.subheader(f"{info.get('longName', ticker)} ({ticker})")
c1, c2, c3 = st.columns(3)
c1.metric("מחיר", fmt(info.get("currentPrice") or info.get("regularMarketPrice")))
c2.metric("שווי שוק", fmt(info.get("marketCap"), "money"))
c3.metric("עובדים", fmt(employees, "money"))

metric = st.radio("בחר נתון", METRICS, horizontal=True)

n = 4
if metric in QUARTERLY:
    n = st.slider("ממוצע נע (מספר רבעונים)", 2, 8, 4)
elif metric in ANNUAL:
    n = st.slider("ממוצע נע (מספר שנים)", 2, 4, 2)

st.markdown(f"### {metric}")

# ---------- הגרפים ----------

if metric == "הכנסות":
    s = row(qinc, ["Total Revenue"])
    if s is None:
        st.warning("אין נתוני הכנסות רבעוניות.")
    else:
        draw(s / 1e6, "הכנסות", "num", n, "רבעונים", f"במיליונים ({cur})")

elif metric == "רווח למניה":
    if eps is None or len(eps) == 0:
        st.warning("אין נתוני רווח למניה.")
    else:
        draw(eps, "רווח למניה", "num", n, "רבעונים", "רווח למניה רבעוני כפי שדווח, לפי תאריך הדיווח")

elif metric == "שולי רווח נקי":
    rev = row(qinc, ["Total Revenue"])
    ni = row(qinc, ["Net Income"])
    if rev is None or ni is None:
        st.warning("אין נתונים לחישוב שולי רווח נקי.")
    else:
        s = (ni / rev * 100).dropna()
        draw(s, "שולי רווח נקי", "pct", n, "רבעונים", "רווח נקי באחוזים מההכנסות")

elif metric == "מכפיל רווח":
    pe = pe_series(eps, hist)
    if pe is None:
        st.warning("אין מספיק נתוני רווח למניה לחישוב מכפיל (נדרשים 4 רבעונים לפחות).")
    else:
        draw(pe, "מכפיל רווח", "x", n, "רבעונים",
             "מחיר ביום הדיווח חלקי סכום רווח למניה של 4 רבעונים")

elif metric == "מכפיל רווח עתידי":
    fwd = info.get("forwardPE")
    m1, m2 = st.columns(2)
    m1.metric("מכפיל עתידי (הערכת אנליסטים)", fmt(fwd, "x"))
    m2.metric("מכפיל נוכחי", fmt(info.get("trailingPE"), "x"))
    pe = pe_series(eps, hist)
    if pe is not None:
        df = pd.DataFrame({"מכפיל היסטורי": pe})
        if fwd is not None and not pd.isna(fwd):
            df["מכפיל עתידי (נוכחי)"] = fwd
        df.index = [d.strftime("%Y-%m") for d in df.index]
        st.line_chart(df)
    st.caption("yfinance לא מספק היסטוריה של מכפיל עתידי. הקו השטוח הוא ערכו הנוכחי, להשוואה בלבד.")

elif metric == "יחס חוב לתזרים (שנתי)":
    debt = row(bal, ["Total Debt"])
    fcf = row(cf, ["Free Cash Flow"])
    if debt is None or fcf is None:
        st.warning("אין נתוני חוב או תזרים חופשי שנתיים.")
    else:
        df = pd.concat([debt, fcf], axis=1, keys=["debt", "fcf"]).dropna()
        df = df[df["fcf"] > 0]
        if df.empty:
            st.warning("התזרים החופשי שלילי בכל השנים הזמינות, ולכן היחס אינו בעל משמעות.")
        else:
            draw(df["debt"] / df["fcf"], "חוב / תזרים חופשי", "x", n, "שנים",
                 "חוב כולל חלקי תזרים חופשי: כמה שנות תזרים נדרשות כדי לפרוע את החוב. שנים עם תזרים שלילי לא מוצגות.")

elif metric == "הכנסה לעובד (שנתי)":
    rev = row(inc, ["Total Revenue"])
    if rev is None or not employees:
        st.warning("אין נתוני הכנסות שנתיות או מספר עובדים.")
    else:
        draw(rev / employees, "הכנסה לעובד", "money", n, "שנים",
             f"הכנסה שנתית חלקי מספר העובדים הנוכחי ({employees:,}). לשנים קודמות זה קירוב, כי אין היסטוריית עובדים.")

st.caption("הנתונים מגיעים מ-yfinance (לא רשמי) ועשויים להיות חסרים או לא מדויקים. אין לראות בזה ייעוץ השקעות.")
