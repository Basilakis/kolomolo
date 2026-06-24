"""
SleepMedCorp CPAP GraphRAG — frontend.

Shows the three required things: the ANSWER, the SUPPORTING SUBGRAPH, and CITATIONS
(source document + page). Optional toggle runs the vector-only baseline side by side
to make the GraphRAG advantage visible live.

    streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# make src/ importable when run via `streamlit run`
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="CPAP GraphRAG", layout="wide")
st.title("🫁 SleepMedCorp — CPAP GraphRAG")
st.caption("Comparison & patient-tailored recommendation with audit-grade citations.")

with st.sidebar:
    st.header("Options")
    show_baseline = st.checkbox("Show vector-only baseline", value=False)
    show_plan = st.checkbox("Show query plan (debug)", value=True)
    st.markdown("**Try:**")
    for ex in [
        "What is the pressure range of the AirSense 11?",
        "Default ramp time and configurable range for the DreamStation 2?",
        "Does the AirCurve 10 VAuto support integrated humidification?",
        "How do AirSense 11 and DreamStation 2 differ in humidification, connectivity, noise?",
        "Which devices support BiPAP ST?",
        "Devices delivering >20 cmH2O, under 1.5 kg, with cellular connectivity?",
        "Recommend a CPAP for severe OSA up to 18 cmH2O, frequent travel, with humidification",
    ]:
        st.markdown(f"- {ex}")

question = st.text_input("Ask a question", value="What is the pressure range of the AirSense 11?")
go = st.button("Answer", type="primary")


@st.cache_data(show_spinner=False)
def _render_pdf_page(doc_name: str, page: int) -> bytes | None:
    """Render a cited PDF page to PNG so the citation literally resolves in the UI."""
    from cpap_graphrag.config import settings
    try:
        import fitz
    except Exception:
        return None
    matches = list(settings.data_dir.rglob(doc_name))
    if not matches:
        return None
    try:
        doc = fitz.open(matches[0])
        pg = doc[max(0, page - 1)]
        pix = pg.get_pixmap(dpi=120)
        data = pix.tobytes("png")
        doc.close()
        return data
    except Exception:
        return None


def render_citations(result: dict) -> None:
    cites = result.get("citations", [])
    if not cites:
        st.info("No citations.")
        return
    for c in cites:
        with st.expander(f"📄 {c['source_doc']} — p.{c['page']}"):
            img = _render_pdf_page(c["source_doc"], c["page"])
            if img:
                st.image(img, caption=f"{c['source_doc']} p.{c['page']}", use_container_width=True)
            else:
                st.caption("Source page preview unavailable (corpus not local?).")


def render_subgraph(result: dict) -> None:
    """Render the supporting subgraph with pyvis -> embedded HTML."""
    try:
        import networkx as nx
        from pyvis.network import Network
    except Exception:
        st.json(result.get("subgraph", {}))
        return

    G = nx.DiGraph()
    rows = result.get("subgraph", {}).get("rows", [])
    for r in rows:
        dev = r.get("device", "device")
        G.add_node(dev, color="#4C8BF5", title=dev)
        label = r.get("parameter") or r.get("feature") or r.get("mode") or "value"
        val = r.get("max") or r.get("value") or r.get("min")
        node = f"{label}: {val} {r.get('unit','')}".strip()
        G.add_node(node, color="#34A853", title=f"{r.get('source_doc')} p.{r.get('page')}")
        G.add_edge(dev, node)
    if not G.nodes:
        st.info("No subgraph for this query type.")
        return
    net = Network(height="380px", width="100%", directed=True)
    net.from_nx(G)
    html = net.generate_html(notebook=False)
    st.components.v1.html(html, height=400)


if go and question.strip():
    from cpap_graphrag.agent.answer import answer_question as graph_answer

    cols = st.columns(2 if show_baseline else 1)

    with cols[0]:
        st.subheader("GraphRAG")
        with st.spinner("Planning + querying the graph…"):
            try:
                result = graph_answer(question)
            except Exception as e:
                st.error(f"Error: {e}")
                result = None
        if result:
            if result.get("refused"):
                st.warning(result["answer"])
            else:
                st.markdown(result["answer"])
            # hallucination guard status
            v = result.get("verification")
            if v is not None:
                if v["grounded"]:
                    st.caption(f"✅ value/unit check: {v['checked']} numeric claim(s) all grounded")
                else:
                    st.error(f"⚠️ {len(v['unverified_claims'])} numeric claim(s) not grounded in evidence")
            if result.get("cost_usd") is not None:
                st.caption(f"💲 query cost ≈ ${result['cost_usd']:.4f} · type: {result.get('query_type')}")
            if show_plan:
                with st.expander("Query plan"):
                    st.json(result.get("plan", {}))
            st.markdown("**Citations** (click to view the source page)")
            render_citations(result)
            st.markdown("**Supporting subgraph**")
            render_subgraph(result)

    if show_baseline:
        with cols[1]:
            st.subheader("Vector-only baseline")
            from cpap_graphrag.baseline.vector_rag import answer_question as vec_answer
            with st.spinner("Retrieving chunks…"):
                try:
                    vres = vec_answer(question)
                    st.markdown(vres["answer"])
                    st.markdown("**Chunk citations**")
                    for m in vres.get("citations", []):
                        st.markdown(f"- `{m['source_doc']}` — p.{m['page']}")
                except Exception as e:
                    st.error(f"Baseline error: {e}")
