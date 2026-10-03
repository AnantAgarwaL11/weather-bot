"""Minimal Streamlit chat UI: `streamlit run frontend/streamlit_app.py`."""
from __future__ import annotations

import os
import uuid

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="Weather-Advisory Bot", page_icon="🌦️")
st.title("🌦️ Weather-Advisory Support Bot")
st.caption("Answers are grounded in live Open-Meteo data and the configured SOPs only.")

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
    st.session_state.turns = []

with st.sidebar:
    st.write(f"Session: `{st.session_state.session_id[:8]}`")
    if st.button("New session"):
        st.session_state.session_id = uuid.uuid4().hex
        st.session_state.turns = []
        st.rerun()
    st.markdown("Try: *Is it safe to cycle in Bhopal today?* then *What about this evening?*")


def render_meta(meta: dict) -> None:
    if meta.get("selected_sop"):
        st.info(f"SOP references: {', '.join(meta['sop_ids'])} · selected **{meta['selected_sop']}** "
                f"(severity: {meta['severity']})")
    else:
        st.caption(f"Outcome: {meta.get('outcome')}")
    if meta.get("weather_facts"):
        with st.expander("Weather data used"):
            st.write(f"{meta.get('location')} — {meta.get('period', {}).get('label')} "
                     f"({meta.get('period', {}).get('start')} → {meta.get('period', {}).get('end')}, "
                     f"{meta.get('period', {}).get('timezone')})")
            st.table({f["label"]: f"{f['value']} {f['unit']}".strip() for f in meta["weather_facts"]})
            if meta.get("resolution_rationale"):
                st.caption(meta["resolution_rationale"])


for turn in st.session_state.turns:
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])
        if turn.get("meta"):
            render_meta(turn["meta"])

if prompt := st.chat_input("Ask about an outdoor activity…"):
    st.session_state.turns.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Checking live weather and SOPs…"):
            try:
                resp = httpx.post(f"{BACKEND_URL}/chat", json={"session_id": st.session_state.session_id,
                                                              "message": prompt}, timeout=60)
                resp.raise_for_status()
                meta = resp.json()
                content = meta["response"]
            except httpx.HTTPError as exc:
                meta, content = None, f"⚠️ Could not reach the advisory backend ({type(exc).__name__}). Is it running at {BACKEND_URL}?"
        st.markdown(content)
        if meta:
            render_meta(meta)
    st.session_state.turns.append({"role": "assistant", "content": content, "meta": meta})
