import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

try:
    from tvscreener import get_financials
    TV_IMPORT_ERR = None
except Exception as _e:
    get_financials = None
    TV_IMPORT_ERR = f"{type(_e).__name__}: {_e}"

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
REV_EXC = ("growth", "%", "per share", "cost", "margin", "ratio")
NI_OPTS = [["net income"]]
NI_EXC = ("per share", "before", "margin", "%", "growth", "discontinued")
EPS_OPTS = [["diluted", "eps"], ["diluted", "earnings per share"], ["eps"], ["earnings per share"]]
EPS_EXC = ("growth", "%", "estimate", "surprise")
DEBT_OPTS = [["total debt"]]
DEBT_EXC = ("ratio", "%", "net", "growth", "/")
FCF_OPTS = [["free cash flow"]]
FCF_EXC = ("margin", "%", "per share", "growth", "ratio", "yield")


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


def by_year(s):
    if s is None or s.empty:
        return None
    if isinstance(s.index, pd.DatetimeIndex):
        s = s.groupby(s.index.year).last()
    return s


def col_series(df, options, exclude=()):
    for words in options:
        for c in df.columns:
            n = str(c).lower()
            if all(k in n for k in words) and not any(x in n for x in exclude):
                s = pd.to_numeric(df[c], errors="coerce").dropna()
                if not s.empty:
                    return s
    return None


def to_usd(s):
    """אם הערכים קטנים מאוד, מניחים שהם במיליונים."""
    if s is not None and not s.empty and s.abs().max() < 1e6:
        return s * 1e6
    return s


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


# ---------- טעינה עמידה לכשלים ----------
# הפונקציות עם המטמון זורקות שגיאה בכשל, ולכן כשלים לא נשמרים במטמון הכללי.
# get() שומר גם כשלים בסשן, כדי שלא נפנה שוב ושוב למקור חסום בכל לחיצה.


def get(key, fn):
    store = st.session_state.setdefault("store", {})
    k = (st.session_state.get("ticker"), key)
    if k not in store:
        try:
            store[k] = ("ok", fn())
        except Exception as e:
            store[k] = ("err", f"{type(e).__name__}: {str(e)[:200]}")
    status, val = store[k]
    return (val, None) if status == "ok" else (None, val)


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


@st.cache_data(ttl=3600, show_spinner=False)
def load_tv(ticker, stmt, period):
    if get_financials is None:
        raise RuntimeError(f"ספריית tvscreener לא נטענה: {TV_IMPORT_ERR}")
    sym = ticker.upper().replace("-", ".")
    last = None
    for ex in EXCHANGES:
        try:
            df = get_financials(f"{ex}:{sym}", stmt, period)
            if df is not None and len(df) > 0:
                return tv_prepare(df)
        except Exception as e:
            last = e
    raise RuntimeError(f"TradingView לא החזיר נתונים ({type(last).__name__ if last else 'ריק'})")


@st.cache_data(ttl=3600, show_spinner=False)
def load_hist(ticker):
    h = yf.Ticker(ticker).history(period="max")
    if h is None or h.empty:
        raise RuntimeError("Yahoo לא החזיר היסטוריית מחירים")
    if h.index.tz is not None:
        h.index = h.index.tz_localize(None)
    return h


@st.cache_data(ttl=3600, show_spinner=False)
def load_info(ticker):
    info = yf.Ticker(ticker).info
    if not info or len(info) < 5:
        raise RuntimeError("Yahoo לא החזיר פרטי חברה")
    return info


@st.cache_data(ttl=3600, show_spinner=False)
def load_fast(ticker):
    fi = yf.Ticker(ticker).fast_info
    out = {}
    for k_out, k_in in (("price", "last_price"), ("market_cap", "market_cap")):
        try:
            out[k_out] = fi[k_in]
        except Exception:
            out[k_out] = None
    if out["price"] is None and out["market_cap"] is None:
        raise RuntimeError("fast_info ריק")
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def load_yf_stmt(ticker, kind):
    names = {
        "qinc": "quarterly_income_stmt",
        "inc": "income_stmt",
        "bal": "balance_sheet",
        "cf": "cashflow",
    }
    df = getattr(yf.Ticker(ticker), names[kind])
    if df is None or df.empty:
        raise RuntimeError("Yahoo לא החזיר דוחות")
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def _search(q):
    res = yf.Search(q, max_results=8).quotes
    return [
        (r.get("symbol"), r.get("shortname") or r.get("longname") or "", r.get("exchDisp", ""))
        for r in res
        if r.get("quoteType") in ("EQUITY", "ETF")
    ]


def search_tickers(q):
    try:
        return _search(q), None
    except Exception as e:
        return [], f"{type(e).__name__}"


def pe_series(eps_q, hist):
    """מכפיל TTM: מחיר בסוף הרבעון חלקי סכום EPS של 4 רבעונים."""
    try:
        if (eps_q is None or hist is None or len(eps_q) < 4 or hist.empty
                or not isinstance(eps_q.index, pd.DatetimeIndex)):
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
        results, s_err = search_tickers(query)
        if results:
            labels = [f"{s} — {n} ({e})" for s, n, e in results]
            choice = st.selectbox("תוצאות", labels, label_visibility="collapsed")
            if st.button("הצג נתונים", type="primary", use_container_width=True):
                st.session_state.ticker = results[labels.index(choice)][0]
        else:
            msg = "לא נמצאו תוצאות בחיפוש."
            if s_err:
                msg = f"החיפוש של Yahoo לא זמין כרגע ({s_err})."
            st.warning(msg + " אפשר להשתמש בטיקר ישירות.")
        if st.button(f"השתמש בטיקר {query.strip().upper()} ישירות", use_container_width=True):
            st.session_state.ticker = query.strip().upper()

ticker = st.session_state.ticker
if not ticker:
    st.info("חפש מנייה כדי להתחיל.")
    st.stop()

# ---------- טעינת הנתונים ----------

with st.spinner(f"טוען נתונים עבור {ticker}..."):
    hist, hist_err = get("hist", lambda: load_hist(ticker))
    info, info_err = get("info", lambda: load_info(ticker))
    fast = None
    if info is None:
        fast, _ = get("fast", lambda: load_fast(ticker))
    tv_q, tv_q_err = get("tv_q", lambda: load_tv(ticker, "income_statement", "FQ"))

info = info or {}

# ---------- נתונים רבעוניים: TradingView, ואם חסר אז yfinance ----------

src = {}
rev_q = ni_q = eps_q = None
if tv_q is not None:
    rev_q = to_usd(col_series(tv_q, REV_OPTS, REV_EXC))
    ni_q = to_usd(col_series(tv_q, NI_OPTS, NI_EXC))
    eps_q = col_series(tv_q, EPS_OPTS, EPS_EXC)
    if rev_q is not None:
        src["rev"] = "TradingView"
    if ni_q is not None:
        src["ni"] = "TradingView"
    if eps_q is not None:
        src["eps"] = "TradingView"

if rev_q is None or ni_q is None or eps_q is None:
    qinc, _ = get("yf_qinc", lambda: load_yf_stmt(ticker, "qinc"))
    if qinc is not None:
        if rev_q is None:
            rev_q = row(qinc, ["Total Revenue"])
            src["rev"] = "yfinance (עד 5 רבעונים)"
        if ni_q is None:
            ni_q = row(qinc, ["Net Income"])
            src["ni"] = "yfinance (עד 5 רבעונים)"
        if eps_q is None:
            eps_q = row(qinc, ["Diluted EPS", "Basic EPS"])
            src["eps"] = "yfinance (עד 5 רבעונים)"

# ---------- כותרת המנייה ----------

price = info.get("currentPrice") or info.get("regularMarketPrice")
if price is None and fast:
    price = fast.get("price")
if price is None and hist is not None:
    price = float(hist["Close"].iloc[-1])
mcap = info.get("marketCap") or (fast.get("market_cap") if fast else None)
employees = info.get("fullTimeEmployees")

if price is None and rev_q is None and eps_q is None:
    st.error("לא הצלחתי למשוך נתונים לטיקר הזה משום מקור. פתח את 'מצב המקורות' למטה לפרטים.")

st.divider()
st.subheader(f"{info.get('longName', ticker)} ({ticker})")
c1, c2, c3 = st.columns(3)
c1.metric("מחיר", fmt(price))
c2.metric("שווי שוק", fmt(mcap, "money"))
c3.metric("עובדים", fmt(employees, "money"))

if info_err and fast is None and hist is None:
    st.warning("Yahoo חוסם כרגע את האפליקציה, ולכן מחיר, שווי שוק ומספר עובדים חסרים. נתוני TradingView מוצגים כרגיל.")
elif info_err:
    st.caption("חלק מהנתונים של Yahoo (שווי שוק, עובדים, מכפילים) לא זמינים כרגע.")

# ---------- בחירת נתון ----------

metric = st.radio("בחר נתון", METRICS, horizontal=True)

n = 4
if metric in QUARTERLY:
    n = st.slider("ממוצע נע (מספר רבעונים)", 2, 8, 4)
elif metric in ANNUAL:
    n = st.slider("ממוצע נע (מספר שנים)", 2, 5, 2)

st.markdown(f"### {metric}")

cur_pe = info.get("trailingPE")
if cur_pe is None and price is not None and eps_q is not None and len(eps_q) >= 4:
    ttm_eps = eps_q.tail(4).sum()
    if ttm_eps and ttm_eps > 0:
        cur_pe = price / ttm_eps

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
             f"B = מיליארדים, M = מיליונים. מקור: {src.get('rev', '—')}.")

elif metric == "רווח למניה":
    if eps_q is None:
        st.warning("אין נתוני רווח למניה לטיקר הזה.")
    else:
        draw(eps_q, "רווח למניה", "num", n, "רבעונים", SHOW_Q, f"מקור: {src.get('eps', '—')}.")

elif metric == "שולי רווח נקי":
    if rev_q is None or ni_q is None:
        st.warning("אין נתונים לחישוב שולי רווח נקי.")
    else:
        df = pd.concat([to_usd(ni_q), to_usd(rev_q)], axis=1, keys=["ni", "rev"]).dropna()
        draw(df["ni"] / df["rev"] * 100, "שולי רווח נקי", "pct", n, "רבעונים", SHOW_Q,
             f"רווח נקי באחוזים מההכנסות. מקור: {src.get('ni', '—')}.")

elif metric == "מכפיל רווח":
    st.metric("מכפיל רווח נוכחי", fmt(cur_pe, "x"))
    pe = pe_series(eps_q, hist)
    if pe is None:
        st.warning("אין מספיק נתונים למכפיל היסטורי: נדרשים 4 רבעונים של רווח למניה והיסטוריית מחירים (Yahoo).")
    else:
        draw(pe, "מכפיל רווח", "x", n, "רבעונים", SHOW_Q,
             f"מחיר בסוף הרבעון (Yahoo) חלקי סכום רווח למניה של 4 רבעונים ({src.get('eps', '—')}).")

elif metric == "מכפיל רווח עתידי":
    fwd = info.get("forwardPE")
    m1, m2 = st.columns(2)
    m1.metric("מכפיל עתידי (הערכת אנליסטים)", fmt(fwd, "x"))
    m2.metric("מכפיל נוכחי", fmt(cur_pe, "x"))
    if fwd is None:
        st.warning("המכפיל העתידי מגיע רק מ-Yahoo, והוא לא זמין כרגע.")
    pe = pe_series(eps_q, hist)
    if pe is not None:
        df = pd.DataFrame({"מכפיל היסטורי": pe}).tail(SHOW_Q)
        if fwd is not None and not pd.isna(fwd):
            df["מכפיל עתידי (נוכחי)"] = fwd
        df.index = [lab(d) for d in df.index]
        st.line_chart(df)
    st.caption("אין היסטוריה של מכפיל עתידי. הקו השטוח הוא ערכו הנוכחי, להשוואה בלבד.")

elif metric == "יחס חוב לתזרים (שנתי)":
    debt = fcf = None
    d_src = "TradingView"
    bs, _ = get("tv_bs", lambda: load_tv(ticker, "balance_sheet", "FY"))
    cfs, _ = get("tv_cf", lambda: load_tv(ticker, "cash_flow", "FY"))
    if bs is not None:
        debt = by_year(col_series(bs, DEBT_OPTS, DEBT_EXC))
    if cfs is not None:
        fcf = by_year(col_series(cfs, FCF_OPTS, FCF_EXC))
    if debt is None or fcf is None:
        # משתמשים ב-yfinance לשניהם יחד, כדי שהיחידות יהיו עקביות
        bal, _ = get("yf_bal", lambda: load_yf_stmt(ticker, "bal"))
        cf, _ = get("yf_cf", lambda: load_yf_stmt(ticker, "cf"))
        debt = by_year(row(bal, ["Total Debt"])) if bal is not None else None
        fcf = by_year(row(cf, ["Free Cash Flow"])) if cf is not None else None
        d_src = "yfinance"
    if debt is None or fcf is None:
        st.warning("אין נתוני חוב או תזרים חופשי שנתיים לטיקר הזה.")
    else:
        df = pd.concat([debt, fcf], axis=1, keys=["debt", "fcf"]).dropna()
        df = df[df["fcf"] > 0]
        if df.empty:
            st.warning("אין שנים עם תזרים חופשי חיובי ונתוני חוב תואמים, ולכן היחס אינו בעל משמעות.")
        else:
            draw(df["debt"] / df["fcf"], "חוב / תזרים חופשי", "x", n, "שנים", SHOW_Y,
                 f"חוב כולל חלקי תזרים חופשי. שנים עם תזרים שלילי לא מוצגות. מקור: {d_src}.")

elif metric == "הכנסה לעובד (שנתי)":
    rev_a, r_src = None, "TradingView"
    inc_tv, _ = get("tv_inc_fy", lambda: load_tv(ticker, "income_statement", "FY"))
    if inc_tv is not None:
        rev_a = by_year(to_usd(col_series(inc_tv, REV_OPTS, REV_EXC)))
    if rev_a is None:
        inc_yf, _ = get("yf_inc", lambda: load_yf_stmt(ticker, "inc"))
        if inc_yf is not None:
            rev_a = by_year(row(inc_yf, ["Total Revenue"]))
            r_src = "yfinance"
    emp = employees
    if not emp:
        st.info("מספר העובדים מגיע מ-Yahoo ולא זמין כרגע. אפשר להזין אותו ידנית.")
        manual = st.number_input("מספר עובדים", min_value=0, value=0, step=1000)
        emp = manual or None
    if rev_a is None:
        st.warning("אין נתוני הכנסות שנתיות לטיקר הזה.")
    elif not emp:
        st.warning("חסר מספר עובדים.")
    else:
        draw(rev_a / emp, "הכנסה לעובד", "money", n, "שנים", SHOW_Y,
             f"הכנסה שנתית ({r_src}) חלקי מספר העובדים הנוכחי ({int(emp):,}). לשנים קודמות זה קירוב.")

# ---------- אבחון ----------

with st.expander("מצב המקורות (לאבחון תקלות)"):
    store = st.session_state.get("store", {})
    rows = [
        {"נתון": k[1], "מצב": "תקין" if v[0] == "ok" else "נכשל", "פירוט": "" if v[0] == "ok" else v[1]}
        for k, v in store.items()
        if k[0] == ticker
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.write(f"מקורות בשימוש: {src}")
    if tv_q is not None:
        st.write("עמודות שהתקבלו מ-TradingView (רבעוני):")
        st.write([str(c) for c in tv_q.columns])
    if st.button("נסה שוב טעינה"):
        for k in [k for k in store if k[0] == ticker]:
            del store[k]
        st.cache_data.clear()
        st.rerun()

st.caption("הכנסות, רווח למניה, שולי רווח נקי, חוב ותזרים מ-TradingView (ואם חסר, מ-yfinance). מחיר, עובדים ומכפיל עתידי מ-Yahoo. ייתכנו חוסרים או אי-דיוקים. אין לראות בזה ייעוץ השקעות.")
