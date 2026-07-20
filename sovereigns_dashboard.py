from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, KeepTogether
from reportlab.platypus import Image as RLImage

st.set_page_config(page_title="Sovereign Market Monitor", page_icon="🌐", layout="wide")

DEFAULT_FILE = Path(__file__).with_name("sovereign_market_monitor.xlsx")
REGION_NAMES = {"America", "Euro Area", "Other Europe", "Asia/Pacific", "Africa"}
PERIODS = ["5 day", "2 week", "3 month", "1 year"]

# --- Shared visual identity -------------------------------------------------
NAVY = "#17365D"
INK = "#33404D"
MUTED = "#7A8794"
RULE = "#D9DEE4"
GRID = "#EDF0F3"

REGION_COLORS = {
    "America": "#17365D",
    "Euro Area": "#2E75B6",
    "Other Europe": "#8FB8DE",
    "Asia/Pacific": "#C55A11",
    "Africa": "#548235",
    "Other": "#7F7F7F",
}

COLUMN_MAP = {
    0: "Country",
    1: "Rating",
    2: "CDS Now",
    3: "CDS 5 day",
    4: "CDS 2 week",
    5: "CDS 3 month",
    6: "CDS 1 year",
    7: "Equity 5 day",
    8: "Equity 2 week",
    9: "Equity 3 month",
    10: "Equity 1 year",
    11: "FX 5 day",
    12: "FX 2 week",
    13: "FX 3 month",
    14: "FX 1 year",
}

METRIC_INFO = {
    "CDS": {
        "columns": {p: f"CDS {p}" for p in PERIODS},
        "unit": "bps",
        "positive_is_risk": True,
        "description": "Positive changes indicate wider sovereign CDS spreads and higher perceived credit risk.",
    },
    "Equity": {
        "columns": {p: f"Equity {p}" for p in PERIODS},
        "unit": "%",
        "positive_is_risk": False,
        "description": "Negative returns indicate weaker stock-market performance.",
    },
    "FX": {
        "columns": {p: f"FX {p}" for p in PERIODS},
        "unit": "%",
        "positive_is_risk": True,
        "description": "For USD/local-currency quotations, positive changes generally indicate local-currency depreciation.",
    },
}


def _source_bytes(uploaded_file) -> bytes:
    if uploaded_file is not None:
        return uploaded_file.getvalue()
    if DEFAULT_FILE.exists():
        return DEFAULT_FILE.read_bytes()
    raise FileNotFoundError("Upload the sovereign market monitor Excel workbook.")


@st.cache_data(show_spinner=False)
def load_monitor(file_bytes: bytes) -> pd.DataFrame:
    raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name="Sovereigns", header=None, engine="openpyxl")
    raw = raw.iloc[:, :15].copy()

    rows: list[dict] = []
    current_region: str | None = None
    for _, row in raw.iterrows():
        first = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
        if first in REGION_NAMES:
            current_region = REGION_ALIASES[first]
            continue
        if not first or first in {"Country", "Threshold  (Green)", "Threshold Red", "Sovereign Market Monitoring Model"}:
            continue

        cds_now = pd.to_numeric(row.iloc[2], errors="coerce")
        metric_values = pd.to_numeric(row.iloc[3:15], errors="coerce")
        if pd.isna(cds_now) and metric_values.isna().all():
            continue

        record = {COLUMN_MAP[i]: row.iloc[i] for i in range(15)}
        record["Region"] = current_region or "Other"
        rows.append(record)

    df = pd.DataFrame(rows)
    numeric_cols = [c for c in df.columns if c not in {"Country", "Rating", "Region"}]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
    df["Rating"] = df["Rating"].fillna("—").astype(str)
    return df


def fmt(value: float, unit: str = "", decimals: int = 1) -> str:
    if pd.isna(value):
        return "N/A"
    sign = "+" if value > 0 else ""
    suffix = f" {unit}" if unit else ""
    return f"{sign}{value:,.{decimals}f}{suffix}"


# --- Charts -----------------------------------------------------------------
def chart_ranked(df: pd.DataFrame, metric: str, period: str, top_n: int) -> go.Figure:
    col = METRIC_INFO[metric]["columns"][period]
    plot_df = df[["Country", "Region", col]].dropna().copy()
    plot_df = plot_df.reindex(plot_df[col].abs().sort_values(ascending=False).index).head(top_n)
    plot_df = plot_df.sort_values(col)
    fig = px.bar(
        plot_df,
        x=col,
        y="Country",
        color="Region",
        color_discrete_map=REGION_COLORS,
        orientation="h",
        labels={col: f"Change ({METRIC_INFO[metric]['unit']})", "Country": ""},
        title=f"Largest {metric} moves — {period}",
        hover_data={"Region": True, col: ":.2f"},
    )
    fig.add_vline(x=0, line_width=1, line_color=MUTED)
    fig.update_layout(height=max(430, 31 * len(plot_df)), legend_title_text="Region", margin=dict(l=10, r=10, t=60, b=10))
    return fig


def chart_scatter(df: pd.DataFrame, period: str, label_top: int = 0) -> go.Figure:
    x_col = f"CDS {period}"
    y_col = f"Equity {period}"
    plot_df = df.dropna(subset=[x_col, y_col, "CDS Now"]).copy()
    fig = px.scatter(
        plot_df,
        x=x_col,
        y=y_col,
        color="Region",
        color_discrete_map=REGION_COLORS,
        size="CDS Now",
        size_max=26,
        hover_name="Country",
        labels={x_col: "CDS change (bps)", y_col: "Equity return (%)", "CDS Now": "CDS now"},
        title=f"Credit versus equity performance — {period}",
    )
    fig.add_hline(y=0, line_width=1, line_dash="dot", line_color=MUTED)
    fig.add_vline(x=0, line_width=1, line_dash="dot", line_color=MUTED)
    if label_top and not plot_df.empty:
        # rank by normalized distance from the origin so both axes count equally
        x_scale = plot_df[x_col].abs().max() or 1
        y_scale = plot_df[y_col].abs().max() or 1
        mag = ((plot_df[x_col] / x_scale) ** 2 + (plot_df[y_col] / y_scale) ** 2) ** 0.5
        for _, r in plot_df.loc[mag.nlargest(label_top).index].iterrows():
            fig.add_annotation(
                x=r[x_col], y=r[y_col], text=r["Country"],
                showarrow=False, yshift=13, font=dict(size=9.5, color=INK),
            )
    fig.update_layout(height=520, legend_title_text="Region", margin=dict(l=10, r=10, t=60, b=10))
    return fig


def _exec_takeaways(df: pd.DataFrame, period: str) -> list[str]:
    """Three one-line, data-driven takeaways for the summary page."""
    cds_col, eq_col = f"CDS {period}", f"Equity {period}"
    cds = df[["Country", cds_col]].dropna()
    eq = df[["Country", eq_col]].dropna()
    out: list[str] = []

    if not cds.empty:
        lo, hi = cds[cds_col].min(), cds[cds_col].max()
        widen = cds.loc[cds[cds_col].idxmax()]
        out.append(
            f"Sovereign CDS moves stayed within a narrow band ({lo:+.1f} to {hi:+.1f} bps), "
            "pointing to broadly stable credit risk."
        )
        out.append(
            f"{widen['Country']} saw the largest deterioration in sovereign credit risk, "
            f"with CDS widening {widen[cds_col]:+.1f} bps."
        )
    if not eq.empty:
        strong = eq.loc[eq[eq_col].idxmax()]
        weak = eq.loc[eq[eq_col].idxmin()]
        louder = "Equity markets were more volatile than CDS" if (not cds.empty and eq[eq_col].std() > cds[cds_col].std()) else "Equity markets diverged sharply"
        out.append(
            f"{louder}, led by strength in {strong['Country']} ({strong[eq_col]:+.1f}%) "
            f"and weakness in {weak['Country']} ({weak[eq_col]:+.1f}%)."
        )
    return out[:3]


def generate_key_takeaways_preview(df: pd.DataFrame, period: str) -> list[str]:
    """Public helper so the app can pre-fill the editable takeaways box."""
    return _exec_takeaways(df, period)


# ISO codes for the badge tokens; falls back to the first two letters.
_ISO = {
    "South Africa": "ZA", "South Korea": "KR", "United States": "US", "United Kingdom": "GB",
    "Hong Kong": "HK", "Czech Republic": "CZ", "Saudi Arabia": "SA", "New Zealand": "NZ",
    "Japan": "JP", "China": "CN", "India": "IN", "Brazil": "BR", "Mexico": "MX", "Ukraine": "UA",
    "Russia": "RU", "Thailand": "TH", "Taiwan": "TW", "Malaysia": "MY", "Singapore": "SG",
    "France": "FR", "Germany": "DE", "Italy": "IT", "Spain": "ES", "Portugal": "PT", "Ireland": "IE",
    "Belgium": "BE", "Netherlands": "NL", "Austria": "AT", "Finland": "FI", "Slovenia": "SI",
    "Luxembourg": "LU", "Poland": "PL", "Norway": "NO", "Sweden": "SE", "Denmark": "DK",
    "Switzerland": "CH", "Canada": "CA", "Australia": "AU",
}

POS = "#C0392B"  # deterioration / risk-up  (red)
NEG = "#2E7D32"  # improvement / risk-down  (green)


def _draw_arrow(c, cx, top_y, up: bool, color: str, w: float = 15, h: float = 18):
    c.setFillColor(colors.HexColor(color))
    c.setStrokeColor(colors.HexColor(color))
    p = c.beginPath()
    if up:
        p.moveTo(cx, top_y); p.lineTo(cx - w / 2, top_y - h); p.lineTo(cx + w / 2, top_y - h)
    else:
        p.moveTo(cx, top_y - h); p.lineTo(cx - w / 2, top_y); p.lineTo(cx + w / 2, top_y)
    p.close()
    c.drawPath(p, fill=1, stroke=0)


def _kpi_card(c, x, y, w, h, *, label, country, region, num_str, unit_str, value_color, up,
              label_size=10.5):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    # subtle shadow then white rounded card
    c.setFillColor(colors.HexColor("#E8EBEF"))
    c.roundRect(x + 2, y - 3, w, h, 12, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.HexColor("#EDEFF2"))
    c.setLineWidth(0.75)
    c.roundRect(x, y, w, h, 12, stroke=1, fill=1)

    cx = x + w / 2
    # metric label
    c.setFillColor(colors.HexColor(MUTED))
    c.setFont("Helvetica", label_size)
    c.drawCentredString(cx, y + h - 24, label.upper())

    # country badge + name
    code = _ISO.get(country, (country[:2] or "?").upper())
    badge_r = 11
    name_w = stringWidth(country, "Helvetica-Bold", 15)
    badge_cx = cx - (name_w / 2) - badge_r - 6
    badge_cy = y + h - 52
    c.setFillColor(colors.HexColor(REGION_COLORS.get(region, "#7F7F7F")))
    c.circle(badge_cx, badge_cy, badge_r, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(badge_cx, badge_cy - 3, code)
    c.setFillColor(colors.HexColor(INK))
    c.setFont("Helvetica-Bold", 15)
    c.drawString(badge_cx + badge_r + 6, badge_cy - 5, country)

    # big value with arrow — auto-fit so number + unit + arrow stay inside the card
    inner = w - 30
    arrow_w, arrow_gap, num_unit_gap = 13, 7, 3
    num_size, unit_size = 42, 18
    while True:
        num_w = stringWidth(num_str, "Helvetica-Bold", num_size)
        unit_w = stringWidth(unit_str, "Helvetica-Bold", unit_size)
        group_w = arrow_w + arrow_gap + num_w + num_unit_gap + unit_w
        if group_w <= inner or num_size <= 24:
            break
        num_size -= 1
        unit_size = max(12, int(num_size * 0.44))

    baseline = y + 24
    ax = cx - group_w / 2
    _draw_arrow(c, ax + arrow_w / 2, baseline + num_size * 0.72, up, value_color)
    c.setFillColor(colors.HexColor(value_color))
    c.setFont("Helvetica-Bold", num_size)
    c.drawString(ax + arrow_w + arrow_gap, baseline, num_str)
    c.setFont("Helvetica-Bold", unit_size)
    c.drawString(ax + arrow_w + arrow_gap + num_w + num_unit_gap, baseline, unit_str)


def _kpi_card_mini(c, x, y, w, h, *, label, country, region, num_str, unit_str, value_color, up,
                   label_size=7.5):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    c.setFillColor(colors.HexColor("#EAEDF1"))
    c.roundRect(x + 1.5, y - 2, w, h, 9, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.HexColor("#EDEFF2"))
    c.setLineWidth(0.75)
    c.roundRect(x, y, w, h, 9, stroke=1, fill=1)

    cx = x + w / 2
    # label (top, centered)
    c.setFillColor(colors.HexColor(MUTED))
    c.setFont("Helvetica", label_size)
    c.drawCentredString(cx, y + h - 15, label.upper())

    # country badge + name (middle, centered as a group)
    code = _ISO.get(country, (country[:2] or "?").upper())
    badge_r = 7
    name_w = stringWidth(country, "Helvetica-Bold", 10.5)
    badge_cx = cx - (name_w / 2) - badge_r - 5
    badge_cy = y + h - 33
    c.setFillColor(colors.HexColor(REGION_COLORS.get(region, "#7F7F7F")))
    c.circle(badge_cx, badge_cy, badge_r, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 6)
    c.drawCentredString(badge_cx, badge_cy - 2, code)
    c.setFillColor(colors.HexColor(INK))
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(badge_cx + badge_r + 5, badge_cy - 4, country)

    # value with arrow (bottom, centered), auto-fit
    inner = w - 20
    arrow_w, arrow_gap, num_unit_gap = 9, 5, 2
    num_size, unit_size = 21, 11
    while True:
        num_w = stringWidth(num_str, "Helvetica-Bold", num_size)
        unit_w = stringWidth(unit_str, "Helvetica-Bold", unit_size)
        group_w = arrow_w + arrow_gap + num_w + num_unit_gap + unit_w
        if group_w <= inner or num_size <= 13:
            break
        num_size -= 1
        unit_size = max(9, int(num_size * 0.5))
    baseline = y + 9
    ax = cx - group_w / 2
    _draw_arrow(c, ax + arrow_w / 2, baseline + num_size * 0.7, up, value_color, w=9, h=11)
    c.setFillColor(colors.HexColor(value_color))
    c.setFont("Helvetica-Bold", num_size)
    c.drawString(ax + arrow_w + arrow_gap, baseline, num_str)
    c.setFont("Helvetica-Bold", unit_size)
    c.drawString(ax + arrow_w + arrow_gap + num_w + num_unit_gap, baseline, unit_str)


def _build_cards(df: pd.DataFrame, per: str) -> list[dict]:
    cds_col, eq_col = f"CDS {per}", f"Equity {per}"
    cds = df[["Country", "Region", cds_col]].dropna()
    eq = df[["Country", "Region", eq_col]].dropna()
    cards = []
    if not cds.empty:
        w = cds.loc[cds[cds_col].idxmax()]
        t = cds.loc[cds[cds_col].idxmin()]
        cards.append(dict(label="Largest CDS Widening", country=w["Country"], region=w["Region"],
                          num_str=f"{w[cds_col]:+.1f}", unit_str="bps", value_color=POS, up=True))
        cards.append(dict(label="Largest CDS Tightening", country=t["Country"], region=t["Region"],
                          num_str=f"{t[cds_col]:+.1f}", unit_str="bps", value_color=NEG, up=False))
    if not eq.empty:
        s = eq.loc[eq[eq_col].idxmax()]
        k = eq.loc[eq[eq_col].idxmin()]
        cards.append(dict(label="Strongest Equity Market", country=s["Country"], region=s["Region"],
                          num_str=f"{s[eq_col]:+.1f}", unit_str="%", value_color=NEG, up=True))
        cards.append(dict(label="Weakest Equity Market", country=k["Country"], region=k["Region"],
                          num_str=f"{k[eq_col]:+.1f}", unit_str="%", value_color=POS, up=False))
    return cards


def _draw_card_row(c, cards, W, mx, row_top, card_h, suffix, *, mini=False):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    n = len(cards)
    if not n:
        return
    gap = 18
    card_w = (W - 2 * mx - (n - 1) * gap) / n
    drawer = _kpi_card_mini if mini else _kpi_card

    # Uniform label size that fits the longest label into a card (keeps the row aligned).
    labels = [f"{cd['label']} · {suffix}".upper() for cd in cards]
    base = 7.5 if mini else 10.5
    floor = 6.0 if mini else 7.5
    avail = card_w - 12
    longest = max((stringWidth(t, "Helvetica", base) for t in labels), default=0)
    label_size = base if longest <= avail else max(floor, base * avail / longest)

    for i, cd in enumerate(cards):
        cd2 = dict(cd)
        cd2["label"] = f"{cd['label']} · {suffix}"
        drawer(c, mx + i * (card_w + gap), row_top - card_h, card_w, card_h,
               label_size=label_size, **cd2)


def _exec_summary_pdf(df: pd.DataFrame, period: str, footer_note: str,
                      custom_takeaways: list[str] | None = None) -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    W, H = PAGE_SIZE
    c = canvas.Canvas(buf, pagesize=PAGE_SIZE)
    mx = 40

    # header
    c.setFillColor(colors.HexColor(NAVY))
    c.setFont("Helvetica-Bold", 30)
    c.drawString(mx, H - 62, f"Executive Summary — Last {period}")
    c.setFillColor(colors.HexColor(MUTED))
    c.setFont("Helvetica", 13)
    c.drawString(mx, H - 84, "Largest market movements across sovereign credit and equity markets.")

    show_5day = period != "5 day"
    sel_cards = _build_cards(df, period)

    if show_5day:
        # primary row (selected period) + compact 5-day row
        primary_top = H - 106
        card_h1 = 122
        _draw_card_row(c, sel_cards, W, mx, primary_top, card_h1, period)
        mini_top = primary_top - card_h1 - 14
        card_h2 = 76
        _draw_card_row(c, _build_cards(df, "5 day"), W, mx, mini_top, card_h2, "5 day", mini=True)
        sep_y = mini_top - card_h2 - 18
    else:
        primary_top = H - 118
        card_h1 = 150
        _draw_card_row(c, sel_cards, W, mx, primary_top, card_h1, period)
        sep_y = H - 320

    # separator
    c.setStrokeColor(colors.HexColor(RULE))
    c.setLineWidth(0.75)
    c.line(mx, sep_y, W - mx, sep_y)

    # key takeaways
    c.setFillColor(colors.HexColor(NAVY))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(mx, sep_y - 32, "Key Takeaways")

    from reportlab.pdfbase.pdfmetrics import stringWidth
    take_size = 12
    take_font = "Helvetica"
    text_left = mx + 15
    max_w = W - mx - text_left

    def _wrap(text: str) -> list[str]:
        words, lines, cur = text.split(), [], ""
        for word in words:
            trial = f"{cur} {word}".strip()
            if stringWidth(trial, take_font, take_size) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    takeaways = [t.strip() for t in (custom_takeaways or []) if t and t.strip()]
    if not takeaways:
        takeaways = _exec_takeaways(df, period)

    ty = sep_y - 60
    for line in takeaways:
        wrapped = _wrap(line)
        c.setFillColor(colors.HexColor(NAVY))
        c.circle(mx + 4, ty + 4, 2.3, stroke=0, fill=1)
        c.setFillColor(colors.HexColor(INK))
        c.setFont(take_font, take_size)
        for seg in wrapped:
            c.drawString(text_left, ty, seg)
            ty -= take_size + 4
        ty -= 8  # gap between bullets

    # footer
    c.setStrokeColor(colors.HexColor(RULE)); c.setLineWidth(0.5)
    c.line(mx, 28, W - mx, 28)
    c.setFont("Helvetica", 7.5); c.setFillColor(colors.HexColor(MUTED))
    c.drawString(mx, 17, footer_note)
    c.drawRightString(W - mx, 17, "1")

    c.showPage(); c.save()
    return buf.getvalue()


def chart_heatmap(df: pd.DataFrame, metric: str, period: str, max_rows: int | None = None,
                  annotate: bool | None = None) -> go.Figure:
    columns = [METRIC_INFO[metric]["columns"][p] for p in PERIODS]
    heat = df.set_index("Country")[columns]
    heat.columns = PERIODS
    heat = heat.loc[heat.abs().max(axis=1).sort_values(ascending=False).index]
    zmid = 0
    title = f"{metric} changes across periods ({METRIC_INFO[metric]['unit']})"

    if max_rows is not None:
        heat = heat.head(max_rows)
    if annotate is None:
        annotate = len(heat) <= 26

    fig = px.imshow(
        heat,
        aspect="auto",
        color_continuous_scale="RdYlGn_r",
        color_continuous_midpoint=zmid,
        text_auto=".1f" if annotate else False,
        title=title,
    )
    fig.update_layout(height=max(520, 25 * len(heat)), margin=dict(l=10, r=10, t=60, b=10))
    fig.update_coloraxes(colorbar_title_text="")
    return fig


def generate_summary(df: pd.DataFrame, period: str) -> list[str]:
    statements: list[str] = []
    for metric in ("CDS", "Equity", "FX"):
        col = METRIC_INFO[metric]["columns"][period]
        valid = df.dropna(subset=[col])
        if valid.empty:
            continue
        if metric == "Equity":
            worst = valid.loc[valid[col].idxmin()]
            best = valid.loc[valid[col].idxmax()]
            statements.append(
                f"{worst['Country']} had the weakest equity performance at {fmt(worst[col], '%')}, while {best['Country']} was strongest at {fmt(best[col], '%')}."
            )
        else:
            worst = valid.loc[valid[col].idxmax()]
            best = valid.loc[valid[col].idxmin()]
            label = "CDS widening" if metric == "CDS" else "local-currency depreciation"
            statements.append(
                f"{worst['Country']} showed the largest {label} at {fmt(worst[col], METRIC_INFO[metric]['unit'])}; {best['Country']} moved most in the opposite direction at {fmt(best[col], METRIC_INFO[metric]['unit'])}."
            )
    return statements


# --- PDF: visualizations only ----------------------------------------------
PAGE_SIZE = landscape(letter)
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 32, 30, 40
PDF_MAX_ROWS = 26  # keep one chart legible on a single landscape page
TITLE_H, TITLE_GAP = 15, 8


def _prepare_for_print(fig: go.Figure, width: int, height: int) -> go.Figure:
    """Copy a screen figure and restyle it for a fixed-size printed panel."""
    f = go.Figure(fig)
    is_heat = bool(f.data) and isinstance(f.data[0], go.Heatmap)
    f.update_layout(
        title=None,
        template="plotly_white",
        width=width,
        height=height,
        font=dict(family="Helvetica, Arial, sans-serif", size=10.5, color=INK),
        paper_bgcolor="white",
        plot_bgcolor="white",
        showlegend=not is_heat,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1.0,
            title_text="", font=dict(size=9.5), bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(l=8, r=76 if is_heat else 8, t=10 if is_heat else 30, b=34),
        coloraxis_colorbar=dict(thickness=10, len=0.65, outlinewidth=0, tickfont=dict(size=9)),
    )
    f.update_xaxes(automargin=True, gridcolor=GRID, zeroline=False, linecolor=RULE,
                   ticks="outside", tickcolor=RULE, ticklen=4, title_font=dict(size=10))
    f.update_yaxes(automargin=True, gridcolor=GRID, zeroline=False, linecolor=RULE,
                   title_font=dict(size=10))
    return f


def _fig_to_png(fig: go.Figure, width: int, height: int, scale: int = 2) -> bytes:
    return _prepare_for_print(fig, width, height).to_image(
        format="png", width=width, height=height, scale=scale
    )


def _footer(canvas, doc):
    canvas.saveState()
    page_w, _ = PAGE_SIZE
    canvas.setStrokeColor(colors.HexColor(RULE))
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN_X, 28, page_w - MARGIN_X, 28)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor(MUTED))
    canvas.drawString(MARGIN_X, 17, doc.footer_note)
    canvas.drawRightString(page_w - MARGIN_X, 17, str(canvas.getPageNumber() + 1))
    canvas.restoreState()


def make_pdf(df: pd.DataFrame, period: str, selected_regions: Iterable[str], top_n: int,
             custom_takeaways: list[str] | None = None) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title="Sovereign Market Monitor — Chart Pack",
    )
    doc.footer_note = f"Sovereign Market Monitor  ·  {period}  ·  {', '.join(selected_regions)}"

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        name="ChartTitle", parent=styles["BodyText"], fontName="Helvetica-Bold",
        fontSize=13, leading=TITLE_H, textColor=colors.HexColor(NAVY),
        spaceBefore=0, spaceAfter=0,
    )

    # Shrink the chart to well under the frame so the title always shares its page.
    SHRINK = 0.86
    panel_w = int(doc.width * SHRINK)
    panel_h = int((doc.height - TITLE_H - TITLE_GAP) * SHRINK)

    cap = min(top_n, PDF_MAX_ROWS)
    add_5day = period != "5 day"

    figures = [chart_scatter(df, period, label_top=6)]
    if add_5day:
        figures.append(chart_scatter(df, "5 day", label_top=6))
    figures.append(chart_ranked(df, "CDS", period, cap))
    if add_5day:
        figures.append(chart_ranked(df, "CDS", "5 day", cap))
    figures.append(chart_ranked(df, "Equity", period, cap))
    if add_5day:
        figures.append(chart_ranked(df, "Equity", "5 day", cap))
    figures.append(chart_heatmap(df, "CDS", period, max_rows=PDF_MAX_ROWS))
    figures.append(chart_heatmap(df, "Equity", period, max_rows=PDF_MAX_ROWS))

    story = []
    for i, fig in enumerate(figures):
        heading = (fig.layout.title.text or "").strip()
        png = _fig_to_png(fig, panel_w, panel_h)
        img = RLImage(io.BytesIO(png), width=panel_w, height=panel_h)
        img.hAlign = "CENTER"
        story.append(KeepTogether([
            Paragraph(heading, title_style),
            Spacer(1, TITLE_GAP),
            img,
        ]))
        if i < len(figures) - 1:
            story.append(PageBreak())

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)

    # Prepend the natively-drawn executive summary page.
    from pypdf import PdfReader, PdfWriter
    exec_pdf = _exec_summary_pdf(df, period, doc.footer_note, custom_takeaways=custom_takeaways)
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(exec_pdf)).pages:
        writer.add_page(page)
    for page in PdfReader(io.BytesIO(buffer.getvalue())).pages:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# --- App --------------------------------------------------------------------
st.title("🌐 Sovereign Market Monitor")
st.caption("Interactive monitoring of sovereign CDS, local equity markets, and foreign-exchange movements.")

with st.sidebar:
    st.header("Data and filters")
    uploaded = st.file_uploader("Upload updated Excel workbook", type=["xlsx"])
    try:
        data = load_monitor(_source_bytes(uploaded))
    except Exception as exc:
        st.error(f"Could not read the workbook: {exc}")
        st.stop()

    available_regions = list(dict.fromkeys(data["Region"].dropna().tolist()))
    selected_regions = st.multiselect("Regions", available_regions, default=available_regions)
    period = st.selectbox("Comparison period", PERIODS, index=1)
    top_n = st.slider("Countries in ranked charts", 5, min(30, len(data)), min(15, len(data)))
    search = st.text_input("Search country")

filtered = data[data["Region"].isin(selected_regions)].copy()
if search:
    filtered = filtered[filtered["Country"].str.contains(search, case=False, na=False)]
if filtered.empty:
    st.warning("No countries match the selected filters.")
    st.stop()

cds_col, eq_col, fx_col = f"CDS {period}", f"Equity {period}", f"FX {period}"
worst_cds = filtered.loc[filtered[cds_col].idxmax()] if filtered[cds_col].notna().any() else None
worst_eq = filtered.loc[filtered[eq_col].idxmin()] if filtered[eq_col].notna().any() else None
worst_fx = filtered.loc[filtered[fx_col].idxmax()] if filtered[fx_col].notna().any() else None

k1, k2, k3 = st.columns(3)
k1.metric("Countries monitored", len(filtered))
k2.metric("Largest CDS widening", worst_cds["Country"] if worst_cds is not None else "N/A", fmt(worst_cds[cds_col], "bps") if worst_cds is not None else None)
k3.metric("Weakest equity market", worst_eq["Country"] if worst_eq is not None else "N/A", fmt(worst_eq[eq_col], "%") if worst_eq is not None else None, delta_color="inverse")

summary_tab, charts_tab, heatmap_tab, data_tab, report_tab = st.tabs(["Overview", "Ranked charts", "Heatmap", "Data explorer", "Report"])

with summary_tab:
    st.subheader(f"Executive summary — {period}")
    for statement in generate_summary(filtered, period):
        st.markdown(f"- {statement}")
    st.plotly_chart(chart_scatter(filtered, period), use_container_width=True)

with charts_tab:
    metric = st.radio("Metric", ["CDS", "Equity", "FX"], horizontal=True)
    st.caption(METRIC_INFO[metric]["description"])
    st.plotly_chart(chart_ranked(filtered, metric, period, top_n), use_container_width=True)

with heatmap_tab:
    heat_metric = st.selectbox("Heatmap metric", ["CDS", "Equity", "FX"])
    st.plotly_chart(chart_heatmap(filtered, heat_metric, period), use_container_width=True)

with data_tab:
    display_cols = ["Country", "Region", "Rating", "CDS Now", cds_col, eq_col, fx_col]
    table = filtered[display_cols].sort_values(cds_col, ascending=False)
    st.dataframe(
        table.style.format({
            "CDS Now": "{:.1f}", cds_col: "{:+.1f}", eq_col: "{:+.1f}%", fx_col: "{:+.1f}%",
        }, na_rep="—"),
        use_container_width=True,
        hide_index=True,
    )
    csv = table.to_csv(index=False).encode("utf-8")
    st.download_button("Download filtered data (CSV)", csv, "sovereign_market_snapshot.csv", "text/csv")

with report_tab:
    st.subheader("Downloadable chart pack")
    st.write(
        "A print-ready PDF of the visualizations only — one full-width chart per landscape page, "
        "using the current filters."
    )

    use_custom = st.checkbox("Write my own Key Takeaways", value=False)
    custom_takeaways = None
    if use_custom:
        auto_preview = "\n".join(generate_key_takeaways_preview(filtered, period))
        raw = st.text_area(
            "One takeaway per line — these replace the auto-generated ones on the summary page.",
            value=auto_preview,
            height=140,
            help="Leave a line blank to skip it. Long lines wrap automatically.",
        )
        custom_takeaways = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        st.caption(f"{len(custom_takeaways)} takeaway(s) will be used.")

    if st.button("Build PDF", type="primary"):
        with st.spinner("Rendering charts…"):
            try:
                st.session_state["pdf_bytes"] = make_pdf(
                    filtered, period, selected_regions, top_n, custom_takeaways=custom_takeaways
                )
            except Exception as exc:
                st.session_state.pop("pdf_bytes", None)
                st.error(
                    f"Could not render the charts to PDF: {exc}\n\n"
                    "Static image export needs the `kaleido` package — install it with `pip install kaleido`."
                )
    if st.session_state.get("pdf_bytes"):
        st.download_button(
            "Download PDF", st.session_state["pdf_bytes"],
            "sovereign_market_charts.pdf", "application/pdf",
        )
    with st.expander("Methodology and interpretation"):
        st.markdown("""
        **CDS:** Higher spreads or positive spread changes normally signal increased perceived sovereign credit risk.  
        **Equity:** Negative returns indicate weaker domestic market sentiment.  
        **FX:** For USD/local-currency quotations, a positive change generally indicates local-currency depreciation.
        """)

st.caption("Data are read from the cached values saved in the Excel workbook. Refresh the workbook through its market-data plug-ins before uploading it to update the dashboard.")
