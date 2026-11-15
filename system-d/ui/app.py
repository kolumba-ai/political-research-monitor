"""System D dashboard: simulated polling vs social sentiment, read-only from Neon.

Run from system-d/:  streamlit run ui/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import data

st.set_page_config(page_title="System D — simulated polling", layout="wide")


@st.cache_data(ttl=15, show_spinner=False)
def load(sql: str, params: tuple = ()) -> list[dict]:
    return data.fetch(data.database_url(), sql, params)


st.title("System D: polled vs social sentiment")
st.warning(
    "**Simulated respondents.** These results come from LLM-generated personas, not real people. "
    "They show how the pipeline behaves; they are not a measurement of Mexican public opinion.",
    icon="⚠️",
)

runs = load(data.RUNS_SQL)
if not runs:
    st.info("No simulation runs in the database yet.")
    st.stop()

run_labels = {r["sim_run"]: f"{r['sim_run']} ({r['terminal']}/{r['claims']} claims finished)" for r in runs}
sim_run = st.sidebar.selectbox("Simulation run", list(run_labels), format_func=run_labels.get)
if st.sidebar.button("Refresh"):
    st.cache_data.clear()
st.sidebar.caption("Data refreshes every 15 seconds. Read-only connection to Neon.")

claims = load(data.CLAIMS_SQL, (sim_run,))
rows = []
for c in claims:
    rows.append(
        {
            "Politician": c["politician"],
            "Claim": c["canonical_text"],
            "Status": data.status_label(c["status"]),
            "n": c["n_completed"],
            "p_believe (95% CI)": data.fmt_share_ci(c["p_believe"], c["ci_low"], c["ci_high"]),
            "Polled sentiment [95% CI]": data.fmt_signed_ci(
                c["sentiment_mean"], c["sentiment_ci_low"], c["sentiment_ci_high"]
            ),
            "Social sentiment": data.fmt_signed(c["social_sentiment"]),
            "Gap (polled − social)": data.fmt_signed(data.gap(c["sentiment_mean"], c["social_sentiment"])),
        }
    )

finished = sum(1 for c in claims if c["status"] != "polling")
cols = st.columns(4)
cols[0].metric("Claims", len(claims))
cols[1].metric("Finished", finished)
cols[2].metric("Completed interviews", sum(c["n_completed"] or 0 for c in claims))
cols[3].metric("Still polling", len(claims) - finished)

st.subheader("Claims")
st.dataframe(
    pd.DataFrame(rows),
    use_container_width=True,
    hide_index=True,
    column_config={"Claim": st.column_config.TextColumn(width="large")},
)
st.caption(
    "p_believe is the post-stratified share answering «sí» («no sé» counts as not believing), with a Wilson 95% "
    "interval. Sentiment runs from −1 (muy negativa) to +1 (muy positiva). A claim stops when it is decisive "
    "(the social sentiment falls outside the polled sentiment CI), converged (p_believe CI within ±7 points) or "
    "capped (400 interviews, shown as inconclusive). See system-d/README.md, «Stopping criteria»."
)

chart_rows = []
for i, c in enumerate(claims, start=1):
    label = f"{i}. {c['politician'].split()[0]}: {c['canonical_text'][:60]}…"
    if c["sentiment_mean"] is not None:
        chart_rows.append(
            {
                "claim": label,
                "source": "Polled (simulated)",
                "value": c["sentiment_mean"],
                "low": c["sentiment_ci_low"],
                "high": c["sentiment_ci_high"],
            }
        )
    if c["social_sentiment"] is not None:
        chart_rows.append(
            {"claim": label, "source": "Social media", "value": c["social_sentiment"], "low": None, "high": None}
        )
if chart_rows:
    st.subheader("Polled vs social sentiment")
    df = pd.DataFrame(chart_rows)
    base = alt.Chart(df).encode(
        y=alt.Y("claim:N", title=None, sort=None, axis=alt.Axis(labelLimit=420)),
        color=alt.Color("source:N", title=None, legend=alt.Legend(orient="top")),
    )
    points = base.mark_point(filled=True, size=90).encode(
        x=alt.X("value:Q", title="Sentiment (−1 to +1)", scale=alt.Scale(domain=[-1, 1]))
    )
    bars = base.transform_filter("datum.low != null").mark_rule(strokeWidth=2).encode(x="low:Q", x2="high:Q")
    st.altair_chart(
        (bars + points).properties(height=max(220, 42 * len(claims))),
        use_container_width=True,
    )
    st.caption("Lines show the polled sentiment's 95% CI.")

st.subheader("Claim detail")
if not claims:
    st.stop()
claim_options = {c["claim_id"]: f"{c['politician']}: {c['canonical_text']}" for c in claims}
claim_id = st.selectbox("Claim", list(claim_options), format_func=claim_options.get)
claim = next(c for c in claims if c["claim_id"] == claim_id)

detail = st.columns(4)
detail[0].metric("Status", data.status_label(claim["status"]))
detail[1].metric("n completed", claim["n_completed"])
detail[2].metric("p_believe", data.fmt_share_ci(claim["p_believe"], claim["ci_low"], claim["ci_high"]))
detail[3].metric("Polled − social", data.fmt_signed(data.gap(claim["sentiment_mean"], claim["social_sentiment"])))

tab_strata, tab_transcripts, tab_social = st.tabs(["By stratum", "Transcripts", "Social comments"])

with tab_strata:
    strata = load(data.STRATA_SQL, (claim_id, sim_run))
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Region": s["region"],
                    "Age": s["age_band"],
                    "Gender": s["gender"],
                    "Urban/rural": s["urban_rural"] or "—",
                    "Population share": f"{s['share']:.1%}",
                    "Attempted": s["attempted"],
                    "Completed": s["completed"],
                    "Share «sí» (unweighted)": "—" if s["share_believe"] is None else f"{s['share_believe']:.0%}",
                    "Mean sentiment": data.fmt_signed(
                        None if s["sentiment_mean"] is None else float(s["sentiment_mean"])
                    ),
                }
                for s in strata
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

with tab_transcripts:
    limit = st.slider("Transcripts to load", 10, 400, 50, step=10)
    only_completed = st.checkbox("Completed interviews only", value=False)
    transcripts = load(data.TRANSCRIPTS_SQL, (claim_id, sim_run, limit))
    if only_completed:
        transcripts = [t for t in transcripts if t["completed"]]
    st.caption(f"Showing {len(transcripts)} interviews, oldest first. All respondents are simulated.")
    for t in transcripts:
        title = (
            f"Batch {t['batch_no']} · {data.stratum_label(t)} · "
            f"believes: {data.belief_label(t['believes_claim'], t['completed'])} · "
            f"sentiment: {data.fmt_signed(t['sentiment'])}"
        )
        with st.expander(title):
            st.caption(("Simulated · " if t["is_simulated"] else "") + data.persona_summary(t["persona"]))
            for message in t["transcript"] or []:
                role = "assistant" if message.get("role") == "agent" else "user"
                with st.chat_message(role):
                    st.write(message.get("text", ""))

with tab_social:
    comments = load(data.SOCIAL_SQL, (claim_id,))
    st.caption("Hand-curated demo comments from the seed, scored by the social sentiment agent.")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Platform": c["platform"],
                    "Comment": c["comment_text"],
                    "Sentiment": data.fmt_signed(c["sentiment"]),
                    "Stance": c["stance"],
                }
                for c in comments
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
