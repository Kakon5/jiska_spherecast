"""
app.py — Streamlit chat UI for CPG Sourcing Decision Support.
"""

import streamlit as st
import pandas as pd
import re
from ingest_data import ingest_data
from reasoner import find_supplier_alternatives, gemini_explain, handle_approval
from db import extract_ingredient_keyword

# ── Emoji stripping helper ───────────────────────────────────────────────────
import re

def clean_note(text: str) -> str:
    """Remove emojis from notes text — data should speak, not decorations."""
    if not text:
        return ""
    # Remove common emojis used in this dataset
    text = text.replace("⚠️", "").replace("✅", "").replace("❌", "")
    # Strip "Warning: " prefix — we already show it visually
    text = text.replace("Warning: ", "").strip()
    return text

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Agnes Oracle — CPG Sourcing Intelligence",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── Load data once ────────────────────────────────────────────────────────────
@st.cache_resource
def load_data():
    return ingest_data(enable_enrichment=False)

products, suppliers, bom, substitutes, supplier_products = load_data()

# ── Sidebar data ─────────────────────────────────────────────────────────────
@st.cache_data
def load_sidebar_data():
    import sqlite3
    con = sqlite3.connect("db.sqlite")
    cur = con.cursor()

    ingredients = [
        ("microcrystalline-cellulose", "Microcrystalline Cellulose", "%microcrystalline-cellulose%"),
        ("gelatin",                    "Gelatin",                    "%gelatin%"),
        ("vitamin-e",                  "Vitamin E",                  "%vitamin-e%"),
        ("vitamin-a",                  "Vitamin A",                  "%vitamin-a%"),
        ("calcium-carbonate",          "Calcium Carbonate",          "%calcium-carbonate%"),
    ]

    result = []
    for slug, label, pattern in ingredients:
        # Count discovered alternatives
        cur.execute("""
            SELECT COUNT(DISTINCT s.Id)
            FROM Supplier_Knowledge sk
            JOIN Supplier s ON sk.SupplierId = s.Id
            LEFT JOIN Supplier_Product sp ON s.Id = sp.SupplierId
            WHERE sk.IngredientCategory = ?
            AND sp.SupplierId IS NULL
            AND (sk.Notes IS NULL OR sk.Notes NOT LIKE '%ERROR%')
        """, (slug,))
        alt_count = cur.fetchone()[0]

        # Companies using this ingredient
        cur.execute("""
            SELECT DISTINCT co.Name
            FROM Company co
            JOIN Product fg ON fg.CompanyId = co.Id
            JOIN BOM b ON b.ProducedProductId = fg.Id
            JOIN BOM_Component bc ON bc.BOMId = b.Id
            JOIN Product rm ON bc.ConsumedProductId = rm.Id
            WHERE rm.SKU LIKE ?
            ORDER BY co.Name
        """, (pattern,))
        companies = [r[0] for r in cur.fetchall()]
        result.append({
            "slug":      slug,
            "label":     label,
            "alt_count": alt_count,
            "companies": companies,
        })

    con.close()
    return result

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:

    # Header
    st.markdown("## Agnes Oracle")
    st.caption("CPG Sourcing Intelligence")
    st.divider()

    # ── Ingredient list ───────────────────────────────────────────────────────
    st.markdown("**Ingredients with alternatives**")

    sidebar_data = load_sidebar_data()

    with st.container(height=310):
        for item in sidebar_data:
            st.markdown(f"- {item['label']}")

    st.divider()

    # ── Status / approval reminder ─────────────────────────────────────────────
    active = st.session_state.get("last_ingredient")
    if active:
        ingredient_name = active.replace("-", " ").title()
        st.markdown(f"**Active:** {ingredient_name}")
        st.info("To add a supplier to your network, type:  **Yes, add [supplier name]**")
    else:
        st.markdown(
            "<div style='font-size:12px;color:var(--color-text-secondary);line-height:1.6'>"
            "Select an ingredient above or type a question in the chat."
            "</div>",
            unsafe_allow_html=True
        )

# ── Main area ─────────────────────────────────────────────────────────────────
st.title("Agnes Oracle — CPG Sourcing Intelligence")
st.caption("Ask about supplier delays, find alternatives, check compliance")

# ── Session state ─────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_ingredient" not in st.session_state:
    st.session_state.last_ingredient = None
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "prefill_query" not in st.session_state:
    st.session_state.prefill_query = None


# ── Score tooltip helper ──────────────────────────────────────────────────────
def build_score_tooltip(s: dict) -> str:
    """
    Explains exactly how each score was calculated.
    Starts from 0 — no fake base score.
    """
    certs = (s.get("Certifications") or "").lower()
    tier  = s.get("ComplianceTier", "pending")
    notes = s.get("Notes") or ""
    no_data = len(certs.strip()) == 0

    lines = ["**How this score was calculated:**", ""]

    if no_data:
        lines.append("⚠️ No certification data available — all scores are 0")
        lines.append("")
    else:
        # Compliance breakdown
        comp = 0.0
        breakdown = ["Starts at: 0.00"]
        if "usp/nf" in certs: comp += 0.25; breakdown.append("USP/NF found: +0.25")
        if "ep" in certs:     comp += 0.20; breakdown.append("EP found: +0.20")
        if "jp" in certs:     comp += 0.15; breakdown.append("JP found: +0.15")
        if "gmp" in certs:    comp += 0.20; breakdown.append("GMP found: +0.20")
        if "fda" in certs:    comp += 0.20; breakdown.append("FDA found: +0.20")
        comp = min(comp, 1.0)
        lines.append(f"**Compliance Score: {comp:.2f}** (weight 40%)")
        lines += [f"  - {b}" for b in breakdown]
        lines.append("")

        # Certification breakdown
        cert = 0.0
        cbreakdown = ["Starts at: 0.00"]
        if "kosher" in certs:  cert += 0.30; cbreakdown.append("Kosher found: +0.30")
        if "halal" in certs:   cert += 0.30; cbreakdown.append("Halal found: +0.30")
        if "iso" in certs:     cert += 0.25; cbreakdown.append("ISO found: +0.25")
        if "non-gmo" in certs: cert += 0.15; cbreakdown.append("Non-GMO found: +0.15")
        cert = min(cert, 1.0)
        lines.append(f"**Certification Score: {cert:.2f}** (weight 30%)")
        lines += [f"  - {b}" for b in cbreakdown]
        lines.append("")

        # Trust breakdown
        trust = 1.0 if tier == "verified" else 0.3
        tbreakdown = [f"Tier '{tier}': {trust:.2f}"]
        if "error" in notes.lower():   trust = 0.0;             tbreakdown.append("DB Error: forced to 0")
        elif "warning" in notes.lower(): trust = round(trust*0.5,3); tbreakdown.append("Warning flag: ×0.5")
        lines.append(f"**Trust Score: {trust:.2f}** (weight 30%)")
        lines += [f"  - {b}" for b in tbreakdown]
        lines.append("")

        total = round(comp * 0.4 + cert * 0.3 + trust * 0.3, 3)
        lines.append(f"**Total = (0.40 × {comp:.2f}) + (0.30 × {cert:.2f}) + (0.30 × {trust:.2f}) = {total}**")

    lines.append("")
    lines.append("*TOPSIS ranks all suppliers relative to each other — "
                 "final score = distance from ideal vs distance from worst.*")

    return "\n".join(lines)


# ── Scoring table ─────────────────────────────────────────────────────────────
def render_scoring_table(discovered: list):
    """
    Shows the full scoring breakdown as a table.
    Only for discovered suppliers — current network is already approved.
    """
    if not discovered:
        return

    st.markdown("#### 📊 Scoring Breakdown")
    st.caption("How each alternative was evaluated — TOPSIS ranking across 3 dimensions (weights: Compliance 40%, Certifications 30%, Trust 30%)")

    rows = []
    for s in discovered:
        note = s.get("Notes") or ""
        if "ERROR" in note.upper():
            continue
        tier = s.get("ComplianceTier", "pending")
        tier_label = "✅ Verified" if tier == "verified" else "⚠️ Pending"
        rows.append({
            "Rank":          s.get("rank", "?"),
            "Supplier":      s["Name"],
            "Compliance":    s.get("compliance_score", 0),
            "Certifications": s.get("cert_score", 0),
            "Trust":         s.get("trust_score", 0),
            "TOPSIS Score":  s.get("topsis_score", 0),
            "Tier":          tier_label,
        })

    if not rows:
        return

    df = pd.DataFrame(rows).set_index("Rank")

    # Colour the TOPSIS score column
    def colour_score(val):
        if isinstance(val, float):
            if val >= 0.8:   return "background-color: #d4edda; color: #155724"
            elif val >= 0.5: return "background-color: #fff3cd; color: #856404"
            else:            return "background-color: #f8d7da; color: #721c24"
        return ""

    styled = df.style.applymap(colour_score, subset=["TOPSIS Score"])
    st.dataframe(styled, use_container_width=True)


# ── Expandable compliance detail ──────────────────────────────────────────────
def render_compliance_expanders(discovered: list):
    """
    Each discovered supplier gets an expandable section showing:
    - Full certifications text
    - Score tooltip (why this score)
    - Source URL
    """
    if not discovered:
        return

    st.markdown("#### 🔎 Compliance Detail")
    st.caption("Click any supplier to see full evidence and score explanation")

    for s in discovered:
        note = s.get("Notes") or ""
        if "ERROR" in note.upper():
            continue

        rank  = s.get("rank", "?")
        score = s.get("topsis_score", 0)
        tier  = s.get("ComplianceTier", "pending")
        icon  = "✅" if tier == "verified" else "⚠️"

        with st.expander(f"{icon} #{rank} {s['Name']} — TOPSIS: {score:.3f}"):

            col1, col2 = st.columns([3, 2])

            with col1:
                st.markdown("**Full Certifications**")
                certs = s.get("Certifications") or "Not available"
                st.markdown(certs)

                if s.get("EFSAStatus"):
                    st.markdown(f"**EFSA Status:** {s['EFSAStatus']}")

                if note:
                    st.warning(clean_note(note))

                if s.get("SourceURL"):
                    st.markdown(f"**Source:** [{s['SourceURL']}]({s['SourceURL']})")

            with col2:
                st.markdown(build_score_tooltip(s))


def render_company_table(ingredient_keyword: str, companies: list):
    """Show which CPG brands use this ingredient, ranked by product count."""

    st.markdown(f"#### 🏭 Companies Using `{ingredient_keyword.replace('-', ' ').title()}`")
    st.caption(f"Found {len(companies)} brands with this ingredient in their BOM")

    rows = []
    for c in companies:
        rows.append({
            "Company": c["company"],
            "Products Using This Ingredient": c["product_count"],
        })

    df = pd.DataFrame(rows)

    # Colour by product count
    def colour_count(val):
        if isinstance(val, int):
            if val >= 5:   return "background-color: #d4edda; color: #155724"
            elif val >= 2: return "background-color: #fff3cd; color: #856404"
        return ""

    styled = df.style.applymap(colour_count, subset=["Products Using This Ingredient"])
    st.dataframe(styled, use_container_width=True, hide_index=True)


# ── Main render function ──────────────────────────────────────────────────────
def render_result(result: dict):
    """Renders all three sections for a query result."""

    # 1 — Two columns: current network vs discovered
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**✅ Current Network**")
        if result.get("current_network"):
            seen = set()
            for s in result["current_network"]:
                name = s["Name"]
                if name in seen:
                    continue
                seen.add(name)
                note = s.get("Notes") or ""
                if note:
                    st.warning(f"{name} — {clean_note(note)}")
                else:
                    st.success(f"✅ {name}")
        else:
            st.info("No current suppliers found")

    with col2:
        st.markdown("**🔍 Discovered Alternatives**")
        if result.get("discovered"):
            for s in result["discovered"][:5]:
                note = s.get("Notes") or ""
                if "ERROR" in note.upper():
                    continue
                score = s.get("topsis_score", 0)
                rank  = s.get("rank", "?")
                tier  = s.get("ComplianceTier", "")
                if tier == "verified":
                    st.success(f"#{rank} {s['Name']} — Score: {score:.3f}")
                else:
                    st.warning(f"#{rank} {s['Name']} — Score: {score:.3f} ⚠️")
        else:
            st.info("No new alternatives found")

    st.markdown("---")

    # 2 — Scoring table
    render_scoring_table(result.get("discovered", []))

    st.markdown("---")

    # 3 — Expandable compliance detail per supplier
    render_compliance_expanders(result.get("discovered", []))


# ── Display chat history ──────────────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("result"):
            render_result(msg["result"])

# ── Chat input ────────────────────────────────────────────────────────────────
# Pick up any prefill from sidebar button click
_prefill = st.session_state.pop("prefill_query", None)

user_input = st.chat_input(
    "Ask about a supplier delay, ingredient, or compliance check...",
    key="chat_input"
)

# Use sidebar prefill if no manual input this turn
if _prefill and not user_input:
    user_input = _prefill

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    approval_keywords = ["yes, add", "yes add", "add ", "approve "]
    is_approval = any(kw in user_input.lower() for kw in approval_keywords)

    with st.chat_message("assistant"):
        if is_approval and st.session_state.last_ingredient:
            words = user_input.replace(",", "").split()
            try:
                add_idx = next(i for i, w in enumerate(words) if w.lower() == "add")
                supplier_name = " ".join(words[add_idx + 1:])
            except StopIteration:
                supplier_name = " ".join(words[1:])

            with st.spinner("Updating supplier network..."):
                response_text = handle_approval(supplier_name, st.session_state.last_ingredient)
            st.markdown(response_text)
            st.session_state.messages.append({"role": "assistant", "content": response_text})

        else:
            ingredient = extract_ingredient_keyword(user_input)

            if not ingredient:
                response_text = (
                    "I couldn't identify a specific ingredient. "
                    "Try mentioning: **MCC**, **gelatin**, **vitamin E**, "
                    "**vitamin A**, or **calcium carbonate**."
                )
                st.markdown(response_text)
                st.session_state.messages.append({"role": "assistant", "content": response_text})

            else:
                with st.spinner("Querying supplier database..."):
                    result = find_supplier_alternatives(user_input)
                    st.session_state.last_ingredient = ingredient
                    st.session_state.last_result = result

                with st.spinner("Gemini analysing compliance..."):
                    gemini_response = gemini_explain(result, user_input)

                render_result(result)

                st.markdown("---")
                st.markdown(gemini_response)

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": gemini_response,
                    "result": result
                })
