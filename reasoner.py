"""
reasoner.py — Finds alternative suppliers and uses Gemini to explain recommendations.

Flow:
1. User mentions a delayed ingredient (e.g. "Ashland MCC has a 10 day delay")
2. We query DB → get CURRENT NETWORK suppliers + DISCOVERED alternatives
3. We score each discovered supplier (compliance, certifications)
4. Gemini reads both lists and writes the recommendation with evidence
"""

import os
import math
from google import genai
from google.genai import types
from dotenv import load_dotenv
from schema import Product, Supplier, Substitute, SupplierProduct, ComplianceType
from db import find_all_suppliers_for_ingredient, extract_ingredient_keyword, approve_supplier

load_dotenv()

# ── Gemini setup — lazy so .env is loaded before client is created ────────────
_gemini_client = None

def get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY not found. "
                "Make sure your .env file is in the same folder as the Python files "
                "and contains: GEMINI_API_KEY=\"your_key_here\""
            )
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client

# ── In-memory store (unchanged from original) ─────────────────────────────────
loaded_products = []
loaded_suppliers = []
loaded_substitutes = []
loaded_supplier_products = []


def load_data_to_ontology(products, suppliers, substitutes, supplier_products=None):
    global loaded_products, loaded_suppliers, loaded_substitutes, loaded_supplier_products
    loaded_products = products
    loaded_suppliers = suppliers
    loaded_substitutes = substitutes
    loaded_supplier_products = supplier_products or []
    print(f"Loaded {len(products)} products, {len(suppliers)} suppliers into memory")


# ── Scoring helpers ───────────────────────────────────────────────────────────

def score_supplier(supplier_row: dict) -> dict:
    """
    Score a supplier row (from DB query) on 3 dimensions.
    Returns scores between 0.0 and 1.0.
    """
    certs = (supplier_row.get("Certifications") or "").lower()
    tier = supplier_row.get("ComplianceTier", "pending")
    notes = supplier_row.get("Notes") or ""

    # Compliance score — based on how many standards they meet
    compliance = 0.5
    if "usp/nf" in certs:   compliance += 0.1
    if "ep" in certs:        compliance += 0.1
    if "jp" in certs:        compliance += 0.1
    if "gmp" in certs:       compliance += 0.1
    if "fda" in certs:       compliance += 0.1
    compliance = min(compliance, 1.0)

    # Certification score — kosher, halal, iso
    cert_score = 0.5
    if "kosher" in certs:    cert_score += 0.15
    if "halal" in certs:     cert_score += 0.15
    if "iso" in certs:       cert_score += 0.1
    if "non-gmo" in certs:   cert_score += 0.1
    cert_score = min(cert_score, 1.0)

    # Trust score — verified tier with no warnings
    trust = 1.0 if tier == "verified" else 0.5
    if "error" in notes.lower():   trust = 0.0
    if "warning" in notes.lower(): trust *= 0.6

    return {
        "compliance_score": round(compliance, 3),
        "cert_score": round(cert_score, 3),
        "trust_score": round(trust, 3),
        "total_score": round((compliance * 0.4 + cert_score * 0.3 + trust * 0.3), 3),
    }


def rank_with_topsis(candidates: list) -> list:
    """
    TOPSIS ranking — finds which supplier is closest to the ideal.
    Works the same as before but now on compliance/cert/trust instead of cost.
    """
    if not candidates:
        return []
    if len(candidates) == 1:
        candidates[0]["topsis_score"] = 1.0
        candidates[0]["rank"] = 1
        return candidates

    criteria = ["compliance_score", "cert_score", "trust_score"]
    weights = [0.4, 0.3, 0.3]

    # Normalize
    denom = {}
    for c in criteria:
        denom[c] = math.sqrt(sum(row[c] ** 2 for row in candidates)) or 1.0

    normalized = []
    for row in candidates:
        n = {c: (row[c] / denom[c]) * weights[i] for i, c in enumerate(criteria)}
        n["_original"] = row
        normalized.append(n)

    # Ideal best and worst
    ideal_best = {c: max(n[c] for n in normalized) for c in criteria}
    ideal_worst = {c: min(n[c] for n in normalized) for c in criteria}

    # TOPSIS score
    for i, n in enumerate(normalized):
        d_plus = math.sqrt(sum((n[c] - ideal_best[c]) ** 2 for c in criteria))
        d_minus = math.sqrt(sum((n[c] - ideal_worst[c]) ** 2 for c in criteria))
        score = d_minus / (d_plus + d_minus) if (d_plus + d_minus) > 0 else 0.0
        row = n["_original"].copy()
        row["topsis_score"] = round(score, 4)
        candidates[i] = row

    candidates.sort(key=lambda x: x["topsis_score"], reverse=True)
    for i, c in enumerate(candidates, 1):
        c["rank"] = i

    return candidates


# ── Main function: find supplier alternatives ─────────────────────────────────

def find_supplier_alternatives(user_message: str) -> dict:
    """
    Given a user message like 'Ashland MCC has a 10 day delay, find alternatives',
    returns current network + ranked discovered alternatives.
    """
    # Step 1: Extract what ingredient they're asking about
    ingredient_keyword = extract_ingredient_keyword(user_message)
    if not ingredient_keyword:
        return {"error": "Could not identify ingredient. Try mentioning: MCC, gelatin, vitamin E, vitamin A, or calcium carbonate."}

    # Step 2: Query DB — get current network AND discovered suppliers
    data = find_all_suppliers_for_ingredient(ingredient_keyword)

    # Step 3: Score each discovered supplier
    scored_discovered = []
    for supplier in data["discovered"]:
        scores = score_supplier(supplier)
        scored_discovered.append({**supplier, **scores})

    # Step 4: TOPSIS rank the discovered suppliers
    ranked = rank_with_topsis(scored_discovered)

    return {
        "ingredient": ingredient_keyword,
        "current_network": data["current_network"],
        "discovered": ranked,
    }


# ── Gemini: generate the explanation ─────────────────────────────────────────

SYSTEM_PROMPT = """You are a procurement intelligence assistant for CPG (Consumer Packaged Goods) supply chains.

You will receive structured data about suppliers for a specific ingredient split into two groups:
- CURRENT NETWORK: suppliers already approved and connected in the company's procurement system
- DISCOVERED ALTERNATIVES: new suppliers found through external enrichment, not yet approved

Your response must ALWAYS follow this exact structure:

1. **Situation Summary** — What is the problem? Which supplier is affected?
2. **Current Network Status** — How many current suppliers exist? Any risks?
3. **Recommended Alternatives** — Top 3 discovered suppliers with compliance evidence
4. **Compliance Check** — For each recommendation: EFSA status, certifications, any warnings

Rules:
- Never mix current network suppliers with discovered alternatives
- Always cite the source of compliance data
- If a supplier has a warning or DB error, exclude them from recommendations
- Be concise — procurement managers are busy
- Format scores clearly
"""

def gemini_explain(result: dict, user_message: str) -> str:
    """
    Send the DB query results to Gemini and get a natural language explanation.
    """
    if "error" in result:
        return result["error"]

    # Format the data clearly for Gemini
    current_text = ""
    for s in result["current_network"]:
        flag = "⚠️ WARNING" if s.get("Notes") else "✅"
        current_text += f"  {flag} {s['Name']} | Tier: {s.get('ComplianceTier', 'unknown')}\n"
        if s.get("Notes"):
            current_text += f"      Note: {s['Notes']}\n"

    discovered_text = ""
    for s in result["discovered"][:5]:  # top 5 only
        discovered_text += (
            f"  #{s.get('rank', '?')} {s['Name']} | "
            f"Score: {s.get('topsis_score', 0):.3f} | "
            f"Compliance: {s.get('compliance_score', 0):.2f} | "
            f"Certs: {s.get('cert_score', 0):.2f} | "
            f"Trust: {s.get('trust_score', 0):.2f}\n"
        )
        if s.get("Certifications"):
            certs_short = s["Certifications"][:120]
            discovered_text += f"      Certs: {certs_short}...\n"
        if s.get("Notes"):
            discovered_text += f"      ⚠️ {s['Notes']}\n"

    prompt = f"""{SYSTEM_PROMPT}

USER QUESTION: {user_message}

INGREDIENT: {result['ingredient']}

[CURRENT NETWORK — {len(result['current_network'])} suppliers]
{current_text or '  None found.'}

[DISCOVERED ALTERNATIVES — {len(result['discovered'])} suppliers, ranked by TOPSIS score]
{discovered_text or '  None found.'}

Respond now following the structure above. Be direct and specific.
"""

    try:
        response = get_gemini_client().models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"Gemini error: {str(e)}\n\nRaw data:\n{discovered_text}"


# ── Approval handler ──────────────────────────────────────────────────────────

def handle_approval(supplier_name: str, ingredient_keyword: str) -> str:
    """
    Called when user says 'Yes, add Roquette' in the chat.
    Finds the supplier ID and creates the Supplier_Product links.
    """
    import sqlite3
    con = sqlite3.connect("db.sqlite")
    cur = con.cursor()
    cur.execute("SELECT Id FROM Supplier WHERE Name LIKE ?", (f"%{supplier_name}%",))
    row = cur.fetchone()
    con.close()

    if not row:
        return f"Could not find supplier '{supplier_name}' in the database."

    supplier_id = row[0]
    count = approve_supplier(supplier_id, ingredient_keyword)
    return f"✅ {supplier_name} has been added to your approved supplier network. {count} product links created."


# ── Legacy functions (kept so app.py doesn't break) ──────────────────────────

def get_product_by_id(product_id):
    return next((p for p in loaded_products if p.id == product_id), None)

def compute_compliance_score(product, requested_requirements):
    if not requested_requirements:
        return 1.0
    satisfied = [r for r in requested_requirements if r in [c.value for c in product.compliance_requirements]]
    return len(satisfied) / len(requested_requirements)

def compute_quality_score(product):
    return min(max(product.assay_purity / 100.0, 0.0), 1.0)

def find_substitutes(product_id, compliance_reqs):
    """Legacy function — kept for backward compatibility."""
    return []

def run_reasoner():
    print("Reasoner ready — using SQLite + Gemini")
