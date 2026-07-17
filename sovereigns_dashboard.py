"""
Sovereign Market Monitor — a reading room for sovereign_market_monitor.xlsx.

Run it:      streamlit run app.py
Every two weeks: refresh the workbook, drop it in, hit "Save this pull",
download the PDF. The next report will open with what changed since this one.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import pandas as pd
import streamlit as st

matplotlib.use("Agg")

from monitor import charts, report, snapshots
from monitor import theme as T
from monitor.loader import METRIC_LABELS, load_workbook, status, stress_score

DEFAULT_WORKBOOK = Path("sovereign_market_monitor.xlsx")

st.set_page_config(page_title="Sovereign Market Monitor", page_icon="◧",
                   layout="wide", initial_sidebar_state="expanded")
T.apply()

st.markdown(f"""
<style>
  .stApp {{ background: {T.PAPER}; }}
  section[data-testid="stSidebar"] {{ background: {T.CARD};
      border-right: 1px solid {T.RULE}; }}
  h1, h2, h3 {{ color: {T.INK}; letter-spacing: -0.01em; }}
  .kicker {{ font-size: 0.68rem; letter-spacing: 0.14em; font-weight: 700;
      color: {T.INK_FAINT}; text-transform: uppercase; }}
  .lede {{ color: {T.INK_SOFT}; font-size: 0.95rem; }}
  .stat {{ background: {T.CARD}; border: 1px solid {T.RULE};
      border-radius: 3px; padding: 0.85rem 1rem; height: 100%; }}
  .stat .v {{ font-size: 1.65rem; font-weight: 600; color: {T.INK};
      line-height: 1.15; }}
  .stat .n {{ font-size: 0.75rem; color: {T.INK_SOFT}; }}
  .flag {{ border-left: 3px solid {T.WATCH}; background: {T.STATUS_FILLS['amber']};
      padding: 0.55rem 0.8rem; margin-bottom: 0.4rem; font-size: 0.85rem;
      color: {T.INK}; border-radius: 0 3px 3px 0; }}
  hr {{ border-color: {T.RULE}; }}
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner="Reading the workbook…")
def _load(payload: bytes, _name: str):
    import io
    return load_workbook(io.BytesIO(payload))


@st.cache_data(show_spinner="Typesetting the PDF…")
def _pdf(payload: bytes, _name: str, prev, banks: bool) -> bytes:
    import io
    md = load_workbook(io.BytesIO(payload))
    return report.build_pdf(md, prev_snapshot=prev, include_banks=banks)


# ---------------------------------------------------------------- sidebar

with st.sidebar:
    st.markdown('<div class="kicker">Workbook</div>', unsafe_allow_html=True)
    upload = st.file_uploader("Refreshed monitor", type=["xlsx", "xlsm"],
                              label_visibility="collapsed")
    if upload is not None:
        payload, source_name = upload.getvalue(), upload.name
    elif DEFAULT_WORKBOOK.exists():
        payload, source_name = DEFAULT_WORKBOOK.read_bytes(), DEFAULT_WORKBOOK.name
        st.caption(f"Using {DEFAULT_WORKBOOK.name} from this folder.")
    else:
        st.info("Drop in sovereign_market_monitor.xlsx to start.")
        st.stop()

try:
    md = _load(payload, source_name)
except Exception as exc:                                  # noqa: BLE001
    st.error(f"That workbook did not parse: {exc}")
    st.stop()

with st.sidebar:
    st.markdown("---")
    st.markdown('<div class="kicker">This pull</div>', unsafe_allow_html=True)
    st.write(f"**As of {md.as_of}**")
    prev = snapshots.previous(md)
    st.caption(f"Last saved pull: {prev['as_of']}" if prev
               else "No earlier pull saved yet.")
    if st.button("Save this pull", width="stretch"):
        path = snapshots.save(md)
        st.success(f"Saved {path.name}")
        st.cache_data.clear()

    st.markdown("---")
    st.markdown('<div class="kicker">View</div>', unsafe_allow_html=True)
    regions = st.multiselect("Regions", md.dashboard["region"].unique().tolist(),
                             default=md.dashboard["region"].unique().tolist())
    include_banks = st.toggle("Include banks in the PDF", value=True)

    st.markdown("---")
    st.markdown('<div class="kicker">Report</div>', unsafe_allow_html=True)
    st.download_button(
        "Download the PDF",
        data=_pdf(payload, source_name, prev, include_banks),
        file_name=f"sovereign-monitor-{md.as_of}.pdf",
        mime="application/pdf", type="primary", width="stretch")
    st.caption("A4 landscape · summary, tape, threshold grid, movers, banks, "
               "and what changed since your last pull.")

view = md.dashboard[md.dashboard["region"].isin(regions)].copy()
view["score"] = view.apply(lambda r: stress_score(r, md.thresholds), axis=1)
quoted = view.dropna(subset=["cds_now"])

# ---------------------------------------------------------------- header

st.markdown('<div class="kicker">Sovereign market monitor</div>',
            unsafe_allow_html=True)
st.title("Where credit sits")
st.markdown(
    f'<p class="lede">{len(quoted)} sovereigns quoted · 5-year CDS mid, bps · '
    f'as of <b>{md.as_of}</b> · thresholds read from the workbook</p>',
    unsafe_allow_html=True)

wider = int((quoted["cds_2w"] > 0).sum())
tighter = int((quoted["cds_2w"] < 0).sum())
breaches = int(sum(status(r.get(m), m, md.thresholds) == "red"
                   for _, r in view.iterrows()
                   for m in METRIC_LABELS if m != "cds_now"))
worst = quoted.nlargest(1, "cds_2w")

cards = [
    ("Median CDS", f"{quoted['cds_now'].median():,.1f}", "bps, 5-year"),
    ("Two weeks", f"{wider} wider", f"{tighter} tighter"),
    ("Biggest widening",
     worst["country"].iloc[0] if len(worst) else "–",
     f"{worst['cds_2w'].iloc[0]:+.1f} bps" if len(worst) else ""),
    ("Threshold breaches", f"{breaches}", "red cells on the grid"),
]
for col, (label, value, note) in zip(st.columns(4), cards):
    col.markdown(
        f'<div class="stat"><div class="kicker">{label}</div>'
        f'<div class="v">{value}</div><div class="n">{note}</div></div>',
        unsafe_allow_html=True)

st.write("")

tabs = st.tabs(["Tape", "Threshold grid", "Movers", "Level vs direction",
                "Regional paths", "Banks", "Since last pull", "Data quality"])

with tabs[0]:
    st.pyplot(charts.tape(md, only=quoted["country"].tolist()), width="stretch")

with tabs[1]:
    st.pyplot(charts.heatmap(md, only=view["country"].tolist()), width="stretch")

with tabs[2]:
    metric = st.selectbox("Metric", [m for m in METRIC_LABELS if m != "cds_now"],
                          format_func=lambda m: METRIC_LABELS[m], index=1)
    st.pyplot(charts.movers(md, metric=metric, n=16), width="stretch")

with tabs[3]:
    st.pyplot(charts.risk_map(md), width="stretch")

with tabs[4]:
    st.pyplot(charts.region_paths(md), width="stretch")

with tabs[5]:
    if md.bank_dashboard.empty:
        st.info("No bank block found in this workbook.")
    else:
        st.pyplot(charts.tape(md, block="bank"), width="stretch")

with tabs[6]:
    if not prev:
        st.info("Nothing to compare against yet. Hit **Save this pull** in the "
                "sidebar; the next workbook you load will open with a "
                "side-by-side against this one.")
    else:
        st.caption(f"This workbook ({md.as_of}) against your saved pull "
                   f"({prev['as_of']}).")
        st.pyplot(charts.since_last(md, prev.get("sovereign_cds", {})),
                  width="stretch")
    saved = snapshots.list_all()
    if saved:
        st.markdown("**Saved pulls**")
        st.dataframe(pd.DataFrame([
            {"As of": s["as_of"], "Saved": s.get("saved_at", ""),
             "Sovereigns": len(s.get("sovereign_cds", {})),
             "Banks": len(s.get("bank_cds", {}))} for s in saved]),
            hide_index=True, width="stretch")

with tabs[7]:
    st.markdown("**Flags raised while reading this workbook**")
    if md.warnings:
        for w in md.warnings:
            st.markdown(f'<div class="flag">{w}</div>', unsafe_allow_html=True)
    else:
        st.success("Nothing looked wrong.")
    st.markdown("**Ranked by stress score**")
    table = view[["region", "country", "cds_now", "cds_2w", "eq_2w",
                  "fx_3m", "score"]].sort_values("score", ascending=False)
    st.dataframe(
        table.rename(columns={
            "region": "Region", "country": "Country", "cds_now": "CDS now",
            "cds_2w": "CDS 2w Δ", "eq_2w": "Equity 2w %", "fx_3m": "FX 3m %",
            "score": "Stress"}),
        hide_index=True, width="stretch",
        column_config={
            "Stress": st.column_config.ProgressColumn(
                "Stress", min_value=0, max_value=100, format="%.0f"),
            "CDS now": st.column_config.NumberColumn(format="%.1f"),
            "CDS 2w Δ": st.column_config.NumberColumn(format="%+.2f"),
            "Equity 2w %": st.column_config.NumberColumn(format="%+.2f"),
            "FX 3m %": st.column_config.NumberColumn(format="%+.2f")})
    st.caption("Stress is the average of each tracked metric scored from 0 at "
               "its green line to 100 at its red line, for names with at least "
               "five live metrics. It ranks names for attention; it does not "
               "price risk.")
