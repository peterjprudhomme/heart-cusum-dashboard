#!/usr/bin/env python3
"""HEART h=5 Streamlit dashboard: uses exactly ONE input DuckDB database.

    pip install duckdb streamlit pandas plotly
    streamlit run HEART_h5_dashboard_package/heart_h5_dashboard.py

The dashboard never opens the original CUSUM/FDA Parquets or CSVs.
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="HEART | CUSUM h=5", page_icon="🧬", layout="wide")


@st.cache_data(show_spinner=False)
def query(db_path: str, sql: str, params: tuple = ()) -> pd.DataFrame:
    with duckdb.connect(db_path, read_only=True) as conn:
        return conn.execute(sql, list(params)).df()


def show_lead(x) -> str:
    if pd.isna(x):
        return "—"
    v = float(x)
    return f"{v:+g} quarters"


def csv_button(frame: pd.DataFrame, label: str, filename: str) -> None:
    if not frame.empty:
        st.download_button(label, frame.to_csv(index=False).encode("utf-8-sig"),
                           file_name=filename, mime="text/csv", use_container_width=False)


DEFAULT_DB = os.environ.get("HEART_H5_DB", "data/heart_dashboard_h5.duckdb")
st.sidebar.title("HEART settings")
db_path = st.sidebar.text_input("Single dashboard data file", value=DEFAULT_DB)
if not Path(db_path).is_file():
    st.title("HEART | CUSUM h=5")
    st.error("Dashboard database not found. Run build_h5_dashboard_db.py to create the single .duckdb file.")
    st.code("python HEART_h5_dashboard_package/build_h5_dashboard_db.py --output data/heart_dashboard_h5.duckdb")
    st.stop()

try:
    overview = query(db_path, "SELECT * FROM kpis")
except Exception as exc:
    st.error(f"Could not read this dashboard database: {exc}")
    st.stop()
if len(overview) != 1:
    st.error("Expected exactly one row in the database kpis table")
    st.stop()
k = overview.iloc[0]

st.title("HEART | FAERS ingredient–reaction surveillance")
st.caption("CUSUM threshold h=5 · 2021Q1–2026Q2 monitoring · Exploratory reporting associations, not confirmed adverse drug reactions")

k1,k2,k3,k4 = st.columns(4)
k1.metric("Alarmed ingredient–reaction pairs", f"{int(k.alarmed_pairs_h5):,}")
k2.metric("Overall alarm rate", f"{k.overall_alarm_rate_h5:.2%}", help="h=5 alarmed pairs divided by ALL scored pairs, including nonalarmed pairs not stored in the dashboard catalog")
k3.metric("FDA listings matched", f"{int(k.matched_listings):,} / {int(k.mapped_listings):,}", help="Only the FDA listings in the monitoring window that have a candidate mapping")
k4.metric("Median FDA-listing lead time", show_lead(k.median_listing_signed_lead_quarters), help="Among matched FDA listings; positive means CUSUM alarm came before the first listing found in the supplied archive")

st.caption(f"Reference archive: {int(k.total_archive_listings):,} FDA listings in total, "
           f"{int(k.listings_in_monitoring_window):,} within the monitoring window, "
           f"{int(k.mapped_listings):,} with candidate mappings. "
           f"Scored CUSUM universe: {int(k.total_cusum_scored_pairs):,} pairs; "
           "the searchable catalog intentionally includes **only the h=5 alarmed subset**.")

search_tab, fda_tab, overview_tab = st.tabs([
    "Search alarmed pairs", "FDA vs CUSUM comparisons", "Alarms by quarter"
])

with search_tab:
    st.subheader("Search alarmed active ingredient–reaction pairs")
    x,y = st.columns([1,1])
    with x:
        ingredient_query = st.text_input("Ingredient contains", placeholder="e.g. DUPILUMAB or METRONIDAZOLE")
    with y:
        reaction_query = st.text_input("Reaction contains", placeholder="e.g. HEADACHE or RASH")
    limit = st.select_slider("Max search results", options=[25,50,100,200], value=50)
    if not ingredient_query.strip() and not reaction_query.strip():
        st.info("Enter at least part of an ingredient or reaction to search the h=5 alarmed pairs.")
    else:
        candidates = query(db_path, """
            SELECT pair_id, ingredient_key, reaction, baseline_type, first_alarm_quarter,
                   monitored_pair_cases, ROUND(max_cusum_score, 2) AS max_cusum_score
            FROM pairs
            WHERE (?='' OR CONTAINS(ingredient_key, UPPER(?)))
              AND (?='' OR CONTAINS(reaction, UPPER(?)))
            ORDER BY
                CASE WHEN ingredient_key = UPPER(?) THEN 0 ELSE 1 END,
                monitored_pair_cases DESC NULLS LAST, ingredient_key, reaction
            LIMIT ?
        """, (ingredient_query.strip(), ingredient_query.strip(),
              reaction_query.strip(), reaction_query.strip(),
              ingredient_query.strip(), int(limit)))
        if candidates.empty:
            st.warning("No h=5 alarmed pairs match this search. A nonalarmed FDA reference pair may still appear in the FDA explorer.")
        else:
            st.dataframe(candidates.drop(columns=["pair_id"]), hide_index=True, use_container_width=True)
            choices = candidates.index.tolist()
            pick = st.selectbox("View quarterly history for", choices,
                                format_func=lambda i: f"{candidates.loc[i, 'ingredient_key']}  ·  {candidates.loc[i, 'reaction']}")
            pair_id = str(candidates.loc[pick,"pair_id"])
            pair = query(db_path, "SELECT * FROM pairs WHERE pair_id=?", (pair_id,)).iloc[0]
            st.markdown(f"#### {pair.ingredient_key} — {pair.reaction}")
            m1,m2,m3,m4=st.columns(4)
            m1.metric("First h=5 alarm",pair.first_alarm_quarter)
            m2.metric("Maximum CUSUM",f"{float(pair.max_cusum_score):.2f}")
            m3.metric("Pair monitoring reports",f"{int(pair.monitored_pair_cases):,}")
            m4.metric("Baseline",str(pair.baseline_type).replace("_"," "))
            st.caption("A 'pair' means one prod_ai active ingredient and one MedDRA reaction. Quarterly n_pair and n_ai are FAERS reporting counts.")
            timeline = query(db_path, """
                SELECT quarter, n_pair, n_ai, observed_rate, cusum_score,
                       cumulative_monitor_pair_cases, above_decision_limit,
                       first_alarm_this_quarter
                FROM quarterly_scores WHERE pair_id=? ORDER BY quarter
            """,(pair_id,))
            links = query(db_path, """
                SELECT signal_id,fda_quarter,product_name,safety_signal,
                       cusum_first_alarm, signed_lead_quarters,
                       user_approved_ingredient_correction,
                       provisional_fda_mapping,source_url
                FROM fda_links WHERE alarmed_pair_id=?
                ORDER BY fda_quarter,signal_id
            """, (pair_id,))
            if not timeline.empty:
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=timeline.quarter, y=timeline.cusum_score,
                                         name="CUSUM score", mode="lines+markers",
                                         line={"width":3}, marker={"size":5}))
                fig.add_hline(y=5.0, line_dash="dash", line_color="gray",
                              annotation_text="h=5 decision limit", annotation_position="top left")
                at_alarm = timeline[timeline.first_alarm_this_quarter]
                if not at_alarm.empty:
                    fig.add_trace(go.Scatter(x=at_alarm.quarter,y=at_alarm.cusum_score,
                                             name="First CUSUM alarm",mode="markers",
                                             marker={"size":14,"symbol":"star"}))
                linked_quarters = sorted(set(links.fda_quarter.tolist()) & set(timeline.quarter.tolist())) if not links.empty else []
                if linked_quarters:
                    fig.add_trace(go.Scatter(x=linked_quarters, y=[5.0]*len(linked_quarters),
                                             name="FDA listing quarter", mode="markers",
                                             marker={"size":12,"symbol":"diamond"}))
                fig.update_layout(title="Quarterly CUSUM score vs h=5", xaxis_title="Quarter",
                                  yaxis_title="CUSUM score", height=410, hovermode="x unified")
                st.plotly_chart(fig, use_container_width=True)
                fig2=px.bar(timeline, x="quarter", y="n_pair", title="Quarterly ingredient–reaction report counts",
                            labels={"quarter":"Quarter","n_pair":"Reports mentioning pair"})
                fig2.update_layout(height=290)
                st.plotly_chart(fig2, use_container_width=True)
                with st.expander("Quarterly values: counts, proportions and scores"):
                    st.dataframe(timeline, hide_index=True, use_container_width=True)
            st.markdown("#### FDA listings mapped to this pair")
            if links.empty:
                st.caption("This alarmed pair has no mapped FDA listing in the supplied archive. It is not a validated false positive.")
            else:
                st.dataframe(links,hide_index=True,use_container_width=True,
                             column_config={"source_url":st.column_config.LinkColumn("FDA source")})
                csv_button(links,"Export this pair's FDA links", "selected_pair_fda_links.csv")

with fda_tab:
    st.subheader("FDA reference coverage at h=5")
    listing_breakdown = query(db_path,"""
        SELECT match_status, COUNT(*) AS listings
        FROM fda_listings WHERE in_monitoring_window
        GROUP BY match_status
    """)
    fig = px.bar(listing_breakdown,x="match_status",y="listings",
                 text="listings",title="Original FDA listings (monitoring window)")
    fig.update_layout(xaxis_title="",yaxis_title="Listings",height=355)
    st.plotly_chart(fig,use_container_width=True)
    st.caption(f"{int(k.matched_listings):,} matched listings, of "
               f"{int(k.mapped_listings):,} that have a candidate mapping; "
               f"{int(k.listings_in_monitoring_window):,} FDA listings total in the monitoring window.")

    time_df=query(db_path,"""
       SELECT CASE WHEN signed_lead_quarters>0 THEN 'Before FDA'
                   WHEN signed_lead_quarters=0 THEN 'Same quarter'
                   WHEN signed_lead_quarters<0 THEN 'After FDA'
                   ELSE 'Unknown' END AS timing,
              COUNT(*) AS listings
       FROM fda_listings
       WHERE in_monitoring_window AND match_status='matched'
       GROUP BY timing
    """)
    chart_order=["Before FDA","Same quarter","After FDA","Unknown"]
    time_df["timing"]=pd.Categorical(time_df.timing,categories=chart_order,ordered=True)
    time_df=time_df.sort_values("timing")
    st.plotly_chart(px.bar(time_df,x="timing",y="listings",text="listings",
                           title="First CUSUM alarm relative to first FDA listing in supplied archive"),
                    use_container_width=True)
    st.caption("Positive signed lead = CUSUM first alarm before the FDA archive's first-listing quarter. "
               "The median lead time uses matched listings only.")

    st.markdown("#### Explore every FDA listing")
    f1,f2,f3 = st.columns([1,1,2])
    with f1:
        scope=st.selectbox("FDA listing scope",["Monitoring window (2021Q1–2026Q2)",
                                                 "All FDA archived listings",
                                                 "Outside monitoring window"])
    with f2:
        status=st.selectbox("CUSUM comparison status",["All","Matched","No exact alarm match",
                                                      "No candidate mapping","Outside monitoring window"])
    with f3:
        search=st.text_input("FDA product, safety concern or ID contains",placeholder="e.g. pancreatitis")
    clause_scope={"Monitoring window (2021Q1–2026Q2)":"in_monitoring_window",
                  "All FDA archived listings":"TRUE",
                  "Outside monitoring window":"NOT in_monitoring_window"}[scope]
    status_map={"All":"TRUE","Matched":"match_status='matched'",
                "No exact alarm match":"match_status='no_exact_alarm_match'",
                "No candidate mapping":"match_status='no_candidate_mapping'",
                "Outside monitoring window":"match_status='outside_monitoring_window'"}
    frame=query(db_path,f"""
        SELECT signal_id,fda_quarter,product_name,safety_signal,match_status,
               candidate_mapping_count,matched_mapping_count,first_alarm_quarter,
               signed_lead_quarters,matching_ingredient,matching_reaction,source_url
        FROM fda_listings WHERE {clause_scope} AND {status_map[status]}
          AND (?='' OR CONTAINS(UPPER(product_name), UPPER(?))
                     OR CONTAINS(UPPER(safety_signal), UPPER(?))
                     OR CONTAINS(signal_id, ?))
        ORDER BY fda_quarter,signal_id
    """, (search.strip(),search.strip(),search.strip(),search.strip()))
    st.caption(f"{len(frame):,} FDA listings in selection")
    st.dataframe(frame,hide_index=True,use_container_width=True,
                 column_config={"source_url":st.column_config.LinkColumn("FDA source")})
    csv_button(frame,"Export selected FDA listing view","heart_fda_listings.csv")

    with st.expander("Breakdown of reference pair matches and misses"):
        sub = query(db_path,"""
            SELECT CASE
                WHEN alarmed_h5 THEN 'Matched to h=5 alarm'
                WHEN pair_universe_status='monitored' THEN 'Monitored but no alarm'
                ELSE 'Not monitored / not in filtered CUSUM universe' END AS coverage,
                COUNT(*) AS reference_pairs
            FROM fda_reference_pairs GROUP BY coverage ORDER BY reference_pairs DESC
        """)
        st.dataframe(sub,hide_index=True,use_container_width=True)
        st.caption("Nonalarmed FDA reference pairs are kept as FDA labels only; they are not searchable CUSUM catalog pairs.")

with overview_tab:
    st.subheader("First CUSUM alarms by quarter")
    series = query(db_path,"SELECT quarter, first_alarms FROM alarms_by_quarter ORDER BY quarter")
    fig=px.line(series,x="quarter",y="first_alarms",markers=True,
                title="New ingredient–reaction pair alarms (h=5)",
                labels={"quarter":"Quarter","first_alarms":"First alarms"})
    fig.update_layout(height=440,xaxis_tickangle=-45)
    st.plotly_chart(fig,use_container_width=True)
    st.dataframe(series,hide_index=True,use_container_width=True)
    csv_button(series,"Export quarterly alarm counts","heart_alarms_by_quarter.csv")
    st.caption("Each pair is counted exactly once, in its FIRST h=5 alarm quarter. "
               f"The counts sum to {int(series.first_alarms.sum()):,} pairs.")

with st.expander("Methodology and data provenance"):
    metadata = query(db_path,"SELECT key,value FROM metadata ORDER BY key")
    st.dataframe(metadata,hide_index=True,use_container_width=True)
    st.markdown("**Important:** FDA listings are not exhaustive positives or negatives, and report counts "
                "do not establish causation. Ingredients were normalized using project-approved "
                "corrections; many FDA-to-MedDRA mappings remain provisional. The FDA source archive "
                "is missing four monitoring quarters, and historical pair selection was retrospective.")
