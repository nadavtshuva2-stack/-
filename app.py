import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

try:
    from tvscreener import get_financials
except Exception:
    get_financials = None

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

SHOW_Q = 12
SHOW_Y = 8
EXCHANGES = ["NASDAQ", "NYSE", "AMEX"]

REV_OPTS = [["total revenue"], ["revenue"]]
NI_OPTS = [["net income"]]
NI_EXCLUDE = ("per share", "before", "margin", "%", "growth", "discontinued")
EPS_OPTS = [["diluted", "eps"], ["diluted", "earnings per share"], ["eps"], ["earnings per share"]]


# ---------- עזרים ----------


def fmt(v, kind="num"):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    try:
        if kind == "x":
            return f"{v:.2f}x"
        if kind == "pct":
            return f"{v:.1f}%"
        if kind == "B":
            return f"{v:,.2f}B"
        if kind == "M":
            return f"{v:,.1f}M"
        if kind == "money":
            a = abs(v)
            if a >= 1e9:
                return f"{v / 1e9:.2f}B"
            if a >= 1e6:
                return f"{v / 1e6:.1f}M"
            return f"{v:,.0f}"
        return f"{v:,.2f}"
    except Exception:
        return "—"


def lab(d):
    try:
        return d.strftime("%Y-%m")
    except Exception:
        return str(d)


def row(df, names):
    """סדרה לפי תאריכים (מהישן לחדש) מדוחות yfinance."""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            return df.loc[n].sort_index().dropna()
    return None


def draw(s, label, kind, window, unit, show, note=None):
    """גרף קווי עם ממוצע נע, כרטיסי סיכום וטבלה. החישוב על כל ההיסטוריה, התצוגה על האחרונים."""
    s = s.dropna()
    if s.empty:
        st.warning("אין נתונים להצגה.")
        return

    ma = s.rolling(window).mean()
    chg = (s.diff() / s.shift().abs() * 100).replace([np.inf, -np.inf], np.nan)
    avg_chg = chg.rolling(window - 1).mean()

    avg_name = f"ממוצע נע ({window} {unit})"
    chg_name = "שינוי מהתקופה הקודמת (%)"
    avg_chg_name = f"ממוצע שינוי (%) — {window - 1} שינויים"

    chart_df = pd.DataFrame({label: s, avg_name: ma}).tail(show)
    table_df = pd.DataFrame({
        label: s,
        avg_name: ma,
        chg_name: chg,
        avg_chg_name: avg_chg,
    }).tail(show)
    chart_df.index = [lab(d) for d in chart_df.index]
    table_df.index = [lab(d) for d in table_df.index]

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


# ---------- TradingView ----------


@st.cache_data(ttl=3600, show_spinner=False)
def tv_income_quarterly(ticker):
    """דוח רווח והפסד רבעוני מ-TradingView. זורק שגיאה בכשל, ולכן כשלים לא נשמרים במטמון."""
    if get_financials is None:
        raise RuntimeError("ספריית tvscreener לא מותקנת")
    sym = ticker.upper().replace("-", ".")
    last = None
    for ex in EXCHANGES:
        try:
            df = get_financials(f"{ex}:{sym}", "income_statement", "FQ")
            if df is not None and len(df) > 0:
                return df
        except Exception as e:
            last = e
    raise RuntimeError(f"TradingView לא החזיר נתונים ({type(last).__name__ if last else 'ריק'})")


def tv_prepare(df):
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        dcol = next(
            (c for c in df.columns
             if any(k in str(c).lower() for k in ("date", "period", "time"))),
            None,
        )
        try:
            df.index = pd.to_datetime(df[dcol]) if dcol is not None else pd.to_datetime(df.index)
        except Exception:
            pass
    try:
        df = df.sort_index()
    except Exception:
        pass
    return df


def col_series(df, options, exclude=()):
    for inc_words in options:
        for c in df.columns:
            n = str(c).lower()
            if all(k in n for k in inc_words) and not any(x in n for x in exclude):
                s = pd.to_numeric(df[c], errors="coerce").dropna()
                if not s.empty:
                    return s
    return None


def to_usd(s):
    """אם הערכים קטנים מאוד, מניחים שהם במיליונים."""
    if s is not None and not s.empty and s.abs().max() < 1e6:
        return s * 1e6
    return s


# ---------- yfinance ----------


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
def load_market(ticker):
    t = yf.Ticker(ticker)
    info = t.info or {}
    hist = t.history(period="max")
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)
    return {
        "info": info,
        "hist": hist,
        "qinc": t.quarterly_income_stmt,
        "inc": t.income_stmt,
        "bal": t.balance_sheet,
        "cf": t.cashflow,
    }


def pe_series(eps_q, hist):
    """מכפיל TTM: מחיר בסוף הרבעון חלקי סכום EPS של 4 רבעונים."""
    try:
        if eps_q is None or len(eps_q) < 4 or hist.empty:
            return None
        ttm = eps_q.rolling(4).sum().dropna()
        out = {}
        for d, e in ttm.items():
            if e and e > 0:
                px = hist["Close"][hist.index <= d]
                if not px.empty:
                    out[d] = px.iloc[-1] / e
        return pd.Series(out) if out else None
    except Exception:
        return None


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
    m = load_market(ticker)
    tv_err, tvdf = None, None
    try:
        tvdf = tv_prepare(tv_income_quarterly(ticker))
    except Exception as e:
        tv_err = str(e)

info, hist = m["info"], m["hist"]
if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
    st.error("לא הצלחתי למשוך נתוני מחיר לטיקר הזה.")
    st.stop()

# ---------- נתונים רבעוניים: TradingView, ואם חסר אז yfinance ----------

src = {}
rev_q = ni_q = eps_q = None
if tvdf is not None:
    rev_q = to_usd(col_series(tvdf, REV_OPTS))
    ni_q = to_usd(col_series(tvdf, NI_OPTS, NI_EXCLUDE))
    eps_q = col_series(tvdf, EPS_OPTS)
    src = {"rev": "TradingView", "ni": "TradingView", "eps": "TradingView"}

if rev_q is None:
    rev_q, src["rev"] = row(m["qinc"], ["Total Revenue"]), "yfinance"
if ni_q is None:
    ni_q, src["ni"] = row(m["qinc"], ["Net Income"]), "yfinance"
if eps_q is None:
    eps_q, src["eps"] = row(m["qinc"], ["Diluted EPS", "Basic EPS"]), "yfinance"

if tv_err:
    st.warning(f"TradingView לא זמין כרגע: {tv_err}. מוצגים נתוני yfinance (עד 5 רבעונים).")

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
    if rev_q is None:
        st.warning("אין נתוני הכנסות רבעוניות לטיקר הזה.")
    else:
        if rev_q.tail(SHOW_Q).abs().max() >= 1e9:
            scale, tag = 1e9, "B"
        else:
            scale, tag = 1e6, "M"
        draw(rev_q / scale, f"הכנסות ({tag})", tag, n, "רבעונים", SHOW_Q,
             f"B = מיליארדים, M = מיליונים. מקור: {src['rev']}.")

elif metric == "רווח למניה":
    if eps_q is None:
        st.warning("אין נתוני רווח למניה לטיקר הזה.")
    else:
        draw(eps_q, "רווח למניה", "num", n, "רבעונים", SHOW_Q, f"מקור: {src['eps']}.")

elif metric == "שולי רווח נקי":
    if rev_q is None or ni_q is None:
        st.warning("אין נתונים לחישוב שולי רווח נקי.")
    else:
        df = pd.concat([ni_q, rev_q], axis=1, keys=["ni", "rev"]).dropna()
        draw(df["ni"] / df["rev"] * 100, "שולי רווח נקי", "pct", n, "רבעונים", SHOW_Q,
             f"רווח נקי באחוזים מההכנסות. מקור: {src['ni']}.")

elif metric == "מכפיל רווח":
    st.metric("מכפיל רווח נוכחי (yfinance)", fmt(info.get("trailingPE"), "x"))
    pe = pe_series(eps_q, hist)
    if pe is None:
        st.warning("אין מספיק נתוני רווח למניה לחישוב מכפיל היסטורי (נדרשים 4 רבעונים לפחות).")
    else:
        draw(pe, "מכפיל רווח", "x", n, "רבעונים", SHOW_Q,
             f"מחיר בסוף הרבעון (yfinance) חלקי סכום רווח למניה של 4 רבעונים ({src['eps']}).")

elif metric == "מכפיל רווח עתידי":
    fwd = info.get("forwardPE")
    m1, m2 = st.columns(2)
    m1.metric("מכפיל עתידי (הערכת אנליסטים)", fmt(fwd, "x"))
    m2.metric("מכפיל נוכחי", fmt(info.get("trailingPE"), "x"))
    pe = pe_series(eps_q, hist)
    if pe is not None:
        df = pd.DataFrame({"מכפיל היסטורי": pe}).tail(SHOW_Q)
        if fwd is not None and not pd.isna(fwd):
            df["מכפיל עתידי (נוכחי)"] = fwd
        df.index = [lab(d) for d in df.index]
        st.line_chart(df)
    st.caption("אין היסטוריה של מכפיל עתידי. הקו השטוח הוא ערכו הנוכחי, להשוואה בלבד. מקור: yfinance.")

elif metric == "יחס חוב לתזרים (שנתי)":
    debt = row(m["bal"], ["Total Debt"])
    fcf = row(m["cf"], ["Free Cash Flow"])
    if debt is None or fcf is None:
        st.warning("אין נתוני חוב או תזרים חופשי שנתיים.")
    else:
        df = pd.concat([debt, fcf], axis=1, keys=["debt", "fcf"]).dropna()
        df = df[df["fcf"] > 0]
        if df.empty:
            st.warning("התזרים החופשי שלילי בכל השנים הזמינות, ולכן היחס אינו בעל משמעות.")
        else:
            draw(df["debt"] / df["fcf"], "חוב / תזרים חופשי", "x", n, "שנים", SHOW_Y,
                 "חוב כולל חלקי תזרים חופשי. שנים עם תזרים שלילי לא מוצגות. מקור: yfinance.")

elif metric == "הכנסה לעובד (שנתי)":
    rev_a = row(m["inc"], ["Total Revenue"])
    if rev_a is None or not employees:
        st.warning("אין נתוני הכנסות שנתיות או מספר עובדים.")
    else:
        draw(rev_a / employees, "הכנסה לעובד", "money", n, "שנים", SHOW_Y,
             f"הכנסה שנתית חלקי מספר העובדים הנוכחי ({employees:,}). לשנים קודמות זה קירוב. מקור: yfinance.")

if tvdf is not None:
    with st.expander("מידע טכני: העמודות שהתקבלו מ-TradingView"):
        st.write(list(tvdf.columns))
        st.write(f"מקורות בשימוש: {src}")

st.caption("הכנסות, רווח למניה ושולי רווח נקי מ-TradingView (ואם לא זמין, yfinance). שאר הנתונים מ-yfinance. ייתכנו חוסרים או אי-דיוקים. אין לראות בזה ייעוץ השקעות.")
