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
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

st.set_page_config(page_title="Sovereign Market Monitor", page_icon="🌐", layout="wide")

DEFAULT_FILE = Path(__file__).with_name("sovereign_market_monitor.xlsx")
REGION_NAMES = {"America", "Euro Area", "Other Europe", "Asia-Pacific", "Africa"}
PERIODS = ["5 day", "2 week", "3 month", "1 year"]

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
            current_region = first
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


def risk_score(df: pd.DataFrame, period: str) -> pd.Series:
    components = []
    for metric in ("CDS", "Equity", "FX"):
        col = METRIC_INFO[metric]["columns"][period]
        values = df[col]
        if values.notna().sum() < 2:
            continue
        ranks = values.rank(pct=True, method="average")
        risk = ranks if METRIC_INFO[metric]["positive_is_risk"] else 1 - ranks
        components.append(risk.rename(metric))
    if not components:
        return pd.Series(np.nan, index=df.index)
    return pd.concat(components, axis=1).mean(axis=1, skipna=True) * 100


def fmt(value: float, unit: str = "", decimals: int = 1) -> str:
    if pd.isna(value):
        return "N/A"
    sign = "+" if value > 0 else ""
    suffix = f" {unit}" if unit else ""
    return f"{sign}{value:,.{decimals}f}{suffix}"


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
        orientation="h",
        labels={col: f"Change ({METRIC_INFO[metric]['unit']})", "Country": ""},
        title=f"Largest {metric} moves — {period}",
        hover_data={"Region": True, col: ":.2f"},
    )
    fig.add_vline(x=0, line_width=1, line_color="gray")
    fig.update_layout(height=max(430, 31 * len(plot_df)), legend_title_text="Region", margin=dict(l=10, r=10, t=60, b=10))
    return fig


def chart_scatter(df: pd.DataFrame, period: str) -> go.Figure:
    x_col = f"CDS {period}"
    y_col = f"Equity {period}"
    plot_df = df.dropna(subset=[x_col, y_col]).copy()
    fig = px.scatter(
        plot_df,
        x=x_col,
        y=y_col,
        color="Region",
        size="CDS Now",
        hover_name="Country",
        labels={x_col: "CDS change (bps)", y_col: "Equity return (%)", "CDS Now": "CDS now"},
        title=f"Credit versus equity performance — {period}",
    )
    fig.add_hline(y=0, line_width=1, line_dash="dot", line_color="gray")
    fig.add_vline(x=0, line_width=1, line_dash="dot", line_color="gray")
    fig.update_layout(height=520, margin=dict(l=10, r=10, t=60, b=10))
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

    ranked = df.assign(**{"Risk Score": risk_score(df, period)}).dropna(subset=["Risk Score"])
    if not ranked.empty:
        top = ranked.nlargest(3, "Risk Score")
        names = ", ".join(f"{r.Country} ({r['Risk Score']:.0f})" for _, r in top.iterrows())
        statements.append(f"The highest composite market-risk scores were {names}.")
    return statements


def make_pdf(df: pd.DataFrame, period: str, selected_regions: Iterable[str]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        rightMargin=28,
        leftMargin=28,
        topMargin=28,
        bottomMargin=28,
        title="Sovereign Market Monitor Report",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8.5, leading=11))
    story = [
        Paragraph("Sovereign Market Monitor", styles["Title"]),
        Paragraph(f"Market snapshot | Selected period: {period} | Regions: {', '.join(selected_regions)}", styles["Small"]),
        Spacer(1, 10),
        Paragraph("Executive summary", styles["Heading2"]),
    ]
    for sentence in generate_summary(df, period):
        story.extend([Paragraph(f"• {sentence}", styles["Small"]), Spacer(1, 4)])

    story.extend([Spacer(1, 8), Paragraph("Market indicators", styles["Heading2"])])
    cols = ["Country", "Region", "CDS Now", f"CDS {period}", f"Equity {period}", f"FX {period}"]
    report = df[cols].copy()
    report["Risk Score"] = risk_score(df, period)
    report = report.sort_values("Risk Score", ascending=False)

    headers = ["Country", "Region", "CDS now", f"CDS {period}", f"Equity {period}", f"FX {period}", "Risk score"]
    table_data = [headers]
    for _, r in report.iterrows():
        table_data.append([
            r["Country"], r["Region"],
            "" if pd.isna(r["CDS Now"]) else f"{r['CDS Now']:.1f}",
            "" if pd.isna(r[f"CDS {period}"]) else f"{r[f'CDS {period}']:+.1f}",
            "" if pd.isna(r[f"Equity {period}"]) else f"{r[f'Equity {period}']:+.1f}%",
            "" if pd.isna(r[f"FX {period}"]) else f"{r[f'FX {period}']:+.1f}%",
            "" if pd.isna(r["Risk Score"]) else f"{r['Risk Score']:.0f}",
        ])
    table = Table(table_data, repeatRows=1, colWidths=[1.3*inch, 1.1*inch, .8*inch, .9*inch, .95*inch, .85*inch, .8*inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#17365D")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 7.5),
        ("GRID", (0,0), (-1,-1), .25, colors.lightgrey),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F4F6F8")]),
        ("ALIGN", (2,1), (-1,-1), "RIGHT"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(table)
    story.extend([
        Spacer(1, 8),
        Paragraph("Interpretation note: A higher composite score indicates relatively greater market stress within the selected peer group. It is a screening indicator, not a probability of default or formal credit rating.", styles["Small"]),
    ])
    doc.build(story)
    return buffer.getvalue()


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

filtered["Risk Score"] = risk_score(filtered, period)

cds_col, eq_col, fx_col = f"CDS {period}", f"Equity {period}", f"FX {period}"
worst_cds = filtered.loc[filtered[cds_col].idxmax()] if filtered[cds_col].notna().any() else None
worst_eq = filtered.loc[filtered[eq_col].idxmin()] if filtered[eq_col].notna().any() else None
worst_fx = filtered.loc[filtered[fx_col].idxmax()] if filtered[fx_col].notna().any() else None
worst_risk = filtered.loc[filtered["Risk Score"].idxmax()] if filtered["Risk Score"].notna().any() else None

k1, k2, k3, k4 = st.columns(4)
k1.metric("Countries monitored", len(filtered))
k2.metric("Largest CDS widening", worst_cds["Country"] if worst_cds is not None else "N/A", fmt(worst_cds[cds_col], "bps") if worst_cds is not None else None)
k3.metric("Weakest equity market", worst_eq["Country"] if worst_eq is not None else "N/A", fmt(worst_eq[eq_col], "%") if worst_eq is not None else None, delta_color="inverse")
k4.metric("Highest risk score", worst_risk["Country"] if worst_risk is not None else "N/A", f"{worst_risk['Risk Score']:.0f}/100" if worst_risk is not None else None)

summary_tab, charts_tab, heatmap_tab, data_tab, report_tab = st.tabs(["Overview", "Ranked charts", "Heatmap", "Data explorer", "Report"])

with summary_tab:
    st.subheader(f"Executive summary — {period}")
    for statement in generate_summary(filtered, period):
        st.markdown(f"- {statement}")
    st.info("Composite risk score combines percentile ranks for CDS widening, negative equity returns, and local-currency depreciation. It is relative to the countries currently selected.")
    st.plotly_chart(chart_scatter(filtered, period), use_container_width=True)

with charts_tab:
    metric = st.radio("Metric", ["CDS", "Equity", "FX"], horizontal=True)
    st.caption(METRIC_INFO[metric]["description"])
    st.plotly_chart(chart_ranked(filtered, metric, period, top_n), use_container_width=True)

with heatmap_tab:
    heat_metric = st.selectbox("Heatmap metric", ["CDS", "Equity", "FX", "Risk Score"])
    if heat_metric == "Risk Score":
        heat = filtered[["Country", "Risk Score"]].set_index("Country").sort_values("Risk Score", ascending=False)
        zmid = 50
        title = f"Composite risk score — {period}"
    else:
        columns = [METRIC_INFO[heat_metric]["columns"][p] for p in PERIODS]
        heat = filtered.set_index("Country")[columns]
        heat.columns = PERIODS
        heat = heat.loc[heat.abs().max(axis=1).sort_values(ascending=False).index]
        zmid = 0
        title = f"{heat_metric} changes across periods"
    fig = px.imshow(heat, aspect="auto", color_continuous_scale="RdYlGn_r", color_continuous_midpoint=zmid, text_auto=".1f", title=title)
    fig.update_layout(height=max(520, 25 * len(heat)), margin=dict(l=10, r=10, t=60, b=10))
    st.plotly_chart(fig, use_container_width=True)

with data_tab:
    display_cols = ["Country", "Region", "Rating", "CDS Now", cds_col, eq_col, fx_col, "Risk Score"]
    table = filtered[display_cols].sort_values("Risk Score", ascending=False)
    st.dataframe(
        table.style.format({
            "CDS Now": "{:.1f}", cds_col: "{:+.1f}", eq_col: "{:+.1f}%", fx_col: "{:+.1f}%", "Risk Score": "{:.0f}",
        }, na_rep="—").background_gradient(subset=["Risk Score"], cmap="RdYlGn_r"),
        use_container_width=True,
        hide_index=True,
    )
    csv = table.to_csv(index=False).encode("utf-8")
    st.download_button("Download filtered data (CSV)", csv, "sovereign_market_snapshot.csv", "text/csv")

with report_tab:
    st.subheader("Downloadable monitoring report")
    st.write("The report includes the executive summary, selected-period indicators, and composite risk ranking for the current filters.")
    pdf_bytes = make_pdf(filtered, period, selected_regions)
    st.download_button("Download PDF report", pdf_bytes, "sovereign_market_report.pdf", "application/pdf", type="primary")
    with st.expander("Methodology and interpretation"):
        st.markdown("""
        **CDS:** Higher spreads or positive spread changes normally signal increased perceived sovereign credit risk.  
        **Equity:** Negative returns indicate weaker domestic market sentiment.  
        **FX:** For USD/local-currency quotations, a positive change generally indicates local-currency depreciation.  
        **Composite score:** The average risk percentile across available CDS, equity, and FX measures. Scores depend on the selected peer group and period.
        """)

st.caption("Data are read from the cached values saved in the Excel workbook. Refresh the workbook through its market-data plug-ins before uploading it to update the dashboard.")
