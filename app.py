import numpy as np
import pandas as pd
import requests
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

# SEC דורש זיהוי אמיתי. החלף לשם ולכתובת המייל שלך (השאר את הפורמט: שם ואז מייל).
SEC_HEADERS = {
    "Nadav": "Nadav nadavtshuva2@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

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

SHOW_Q = 16  # כמה רבעונים מוצגים בגרף
SHOW_Y = 8   # כמה שנים מוצגות בגרף

REV_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
]
NI_TAGS = ["NetIncomeLoss", "ProfitLoss"]
EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasic"]
OCF_TAGS = ["NetCashProvidedByUsedInOperatingActivities"]
CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsForCapitalImprovements",
]


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


def draw(s, label, kind, window, unit, show, note=None):
    """גרף קווי עם ממוצע נע, כרטיסי סיכום וטבלה. החישובים על כל ההיסטוריה, התצוגה על האחרונים."""
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


# ---------- SEC EDGAR ----------
# הפונקציות עם המטמון זורקות שגיאה בכשל, ולכן כשלים לא נשמרים.


@st.cache_data(ttl=86400, show_spinner=False)
def sec_ticker_map():
    r = requests.get("https://www.sec.gov/files/company_tickers.json",
                     headers=SEC_HEADERS, timeout=20)
    r.raise_for_status()
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in r.json().values()}


@st.cache_data(ttl=3600, show_spinner=False)
def sec_company_facts(cik):
    r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                     headers=SEC_HEADERS, timeout=40)
    r.raise_for_status()
    return r.json()


def get_sec(ticker):
    """מחזיר (נתונים, הודעת שגיאה)."""
    try:
        cik = sec_ticker_map().get(ticker.upper().replace(".", "-"))
        if not cik:
            return None, "הטיקר לא נמצא ברשימת SEC (ייתכן שזו חברה לא אמריקאית)"
        return sec_company_facts(cik), None
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        return None, f"SEC החזיר שגיאה {code}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _rows(facts, tag, unit):
    gaap = facts.get("facts", {}).get("us-gaap", {})
    if tag not in gaap:
        return None
    vals = gaap[tag].get("units", {}).get(unit)
    if not vals:
        return None
    rows = [
        {
            "end": pd.to_datetime(v.get("end")),
            "start": pd.to_datetime(v.get("start")),
            "val": v.get("val"),
            "filed": v.get("filed", ""),
        }
        for v in vals
        if v.get("form") in ("10-Q", "10-K") and v.get("end")
    ]
    if not rows:
        return None
    return pd.DataFrame(rows).sort_values(["end", "start", "filed"])


def _merge(series_list):
    """מאחד סדרות מכמה תגיות: הסדרה העדכנית ביותר קודמת, האחרות ממלאות חורים."""
    series_list = [s for s in series_list if s is not None and not s.empty]
    if not series_list:
        return None
    series_list.sort(key=lambda s: s.index.max(), reverse=True)
    out = series_list[0]
    for s in series_list[1:]:
        out = out.combine_first(s)
    return out.sort_index()


def _quarterly(df):
    df = df[df["start"].notna()]
    if df.empty:
        return None
    dur = (df["end"] - df["start"]).dt.days
    q = (df[dur.between(70, 100)].drop_duplicates("end", keep="last")
         .set_index("end")["val"].sort_index())
    a = df[dur.between(350, 380)].drop_duplicates("end", keep="last")
    extra = {}
    for _, r in a.iterrows():
        if r["end"] in q.index:
            continue
        inside = q[(q.index > r["start"]) & (q.index < r["end"])]
        if len(inside) == 3:
            extra[r["end"]] = r["val"] - inside.sum()
    if extra:
        q = pd.concat([q, pd.Series(extra)]).sort_index()
    return q if not q.empty else None


def _annual(df):
    df = df[df["start"].notna()]
    if df.empty:
        return None
    dur = (df["end"] - df["start"]).dt.days
    a = df[dur.between(350, 380)].drop_duplicates("end", keep="last")
    return a.set_index("end")["val"].sort_index() if not a.empty else None


def _instant(df):
    df = df[df["start"].isna()].drop_duplicates("end", keep="last")
    return df.set_index("end")["val"].sort_index() if not df.empty else None


def sec_series(facts, tags, kind, unit="USD"):
    if not facts:
        return None
    fn = {"q": _quarterly, "a": _annual, "i": _instant}[kind]
    out = []
    for tag in tags:
        df = _rows(facts, tag, unit)
        if df is not None:
            out.append(fn(df))
    return _merge(out)


def sec_total_debt(facts):
    s = sec_series(facts, ["DebtLongtermAndShorttermCombinedAmount"], "i")
    if s is not None:
        return s
    lt = sec_series(facts, ["LongTermDebtNoncurrent"], "i")
    sh = sec_series(facts, ["LongTermDebtCurrent", "DebtCurrent"], "i")
    if lt is None and sh is None:
        return None
    if lt is None:
        return sh
    if sh is None:
        return lt
    return pd.concat([lt, sh], axis=1).fillna(0).sum(axis=1)


def sec_fcf_annual(facts):
    ocf = sec_series(facts, OCF_TAGS, "a")
    capex = sec_series(facts, CAPEX_TAGS, "a")
    if ocf is None or capex is None:
        return None
    df = pd.concat([ocf, capex], axis=1, keys=["ocf", "capex"]).dropna()
    if df.empty:
        return None
    return df["ocf"] - df["capex"].abs()


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
    return info, hist


def pe_series(eps_q, hist):
    """מכפיל TTM: מחיר בסוף הרבעון חלקי סכום EPS של 4 רבעונים."""
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
    st.info("חפש מנייה כדי להתחיל. הנתונים הפונדמנטליים מגיעים מ-SEC EDGAR, ולכן מכוסות חברות הרשומות בארה\"ב.")
    st.stop()

with st.spinner(f"טוען נתונים עבור {ticker}..."):
    info, hist = load_market(ticker)
    facts, sec_err = get_sec(ticker)

if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
    st.error("לא הצלחתי למשוך נתוני מחיר לטיקר הזה.")
    st.stop()

if facts is None:
    st.warning(f"לא נמצאו נתוני SEC לטיקר הזה. סיבה: {sec_err}")

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
    n = st.slider("ממוצע נע (מספר שנים)", 2, 5, 2)

st.markdown(f"### {metric}")

rev_q = sec_series(facts, REV_TAGS, "q")
ni_q = sec_series(facts, NI_TAGS, "q")
eps_q = sec_series(facts, EPS_TAGS, "q", "USD/shares")
rev_a = sec_series(facts, REV_TAGS, "a")
debt_i = sec_total_debt(facts)
fcf_a = sec_fcf_annual(facts)

# ---------- הגרפים ----------

if metric == "הכנסות":
    if rev_q is None:
        st.warning("אין נתוני הכנסות רבעוניות מ-SEC לטיקר הזה.")
    else:
        if rev_q.tail(SHOW_Q).abs().max() >= 1e9:
            scale, tag = 1e9, "B"
        else:
            scale, tag = 1e6, "M"
        draw(rev_q / scale, f"הכנסות ({tag})", tag, n, "רבעונים", SHOW_Q,
             "B = מיליארדים, M = מיליונים. מקור: SEC EDGAR.")

elif metric == "רווח למניה":
    if eps_q is None:
        st.warning("אין נתוני רווח למניה מ-SEC לטיקר הזה.")
    else:
        draw(eps_q, "רווח למניה", "num", n, "רבעונים", SHOW_Q,
             "רבעון 4 מחושב כרווח שנתי פחות שלושת הרבעונים הראשונים, ולכן הוא קירוב. מקור: SEC EDGAR.")

elif metric == "שולי רווח נקי":
    if rev_q is None or ni_q is None:
        st.warning("אין נתונים לחישוב שולי רווח נקי.")
    else:
        df = pd.concat([ni_q, rev_q], axis=1, keys=["ni", "rev"]).dropna()
        draw(df["ni"] / df["rev"] * 100, "שולי רווח נקי", "pct", n, "רבעונים", SHOW_Q,
             "רווח נקי באחוזים מההכנסות. מקור: SEC EDGAR.")

elif metric == "מכפיל רווח":
    pe = pe_series(eps_q, hist)
    if pe is None:
        st.warning("אין מספיק נתוני רווח למניה לחישוב מכפיל (נדרשים 4 רבעונים לפחות).")
    else:
        draw(pe, "מכפיל רווח", "x", n, "רבעונים", SHOW_Q,
             "מחיר בסוף הרבעון (yfinance) חלקי סכום רווח למניה של 4 רבעונים (SEC).")

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
        df.index = [dte.strftime("%Y-%m") for dte in df.index]
        st.line_chart(df)
    st.caption("אין היסטוריה של מכפיל עתידי. הקו השטוח הוא ערכו הנוכחי, להשוואה בלבד. מקור: yfinance.")

elif metric == "יחס חוב לתזרים (שנתי)":
    if debt_i is None or fcf_a is None:
        st.warning("אין נתוני חוב או תזרים חופשי שנתיים מ-SEC לטיקר הזה.")
    else:
        df = pd.concat([debt_i, fcf_a], axis=1, keys=["debt", "fcf"]).dropna()
        df = df[df["fcf"] > 0]
        if df.empty:
            st.warning("אין שנים עם תזרים חופשי חיובי ונתוני חוב תואמים, ולכן היחס אינו בעל משמעות.")
        else:
            draw(df["debt"] / df["fcf"], "חוב / תזרים חופשי", "x", n, "שנים", SHOW_Y,
                 "חוב כולל חלקי תזרים חופשי (תזרים תפעולי פחות השקעות הוניות). מקור: SEC EDGAR.")

elif metric == "הכנסה לעובד (שנתי)":
    if rev_a is None or not employees:
        st.warning("אין נתוני הכנסות שנתיות מ-SEC או מספר עובדים.")
    else:
        draw(rev_a / employees, "הכנסה לעובד", "money", n, "שנים", SHOW_Y,
             f"הכנסה שנתית (SEC) חלקי מספר העובדים הנוכחי ({employees:,}, yfinance). לשנים קודמות זה קירוב.")

st.caption("נתונים פונדמנטליים מ-SEC EDGAR, מחיר ומכפיל עתידי מ-yfinance. ייתכנו חוסרים או אי-דיוקים. אין לראות בזה ייעוץ השקעות.")
