# TEAM JISKA_DYNAMICS
## Agnes Oracle — CPG Sourcing Intelligence
## TUM.ai x Spherecast Makeathon 2026

## What we built

A chat-based sourcing tool that answers one question:
"Our supplier just delayed — who else can supply this ingredient?"

You describe the problem in plain English. The system identifies the ingredient,
pulls your current approved suppliers from the database, finds alternatives
discovered through external enrichment, scores and ranks them using TOPSIS
across compliance and certification dimensions, then uses Gemini to explain
the recommendation with evidence.

You can also approve a discovered supplier directly in chat ("Yes, add Roquette")
and it writes back to the database — adding them to your live supplier network.

## How it works

1. User types a message — e.g. "Nature Made company wants to find alternative suppliers for Vitamin E"
2. System extracts the ingredient keyword (Microcrystalline Cellulose, Gelatin, Vitamin E, Vitamin A, Calcium Carbonate)
3. DB query splits results into two groups:
   - Current Network — suppliers already approved in Supplier_Product
   - Discovered — suppliers in Supplier_Knowledge but not yet approved
4. Each discovered supplier is scored on 3 dimensions:
   - Compliance (40%) — USP/NF, EP, JP, GMP, FDA
   - Certifications (30%) — Kosher, Halal, ISO, Non-GMO
   - Trust (30%) — compliance tier + warning flags
5. TOPSIS ranking determines final order
6. Gemini writes the recommendation with evidence trails
7. Streamlit UI shows current network vs alternatives side by side,
   full scoring table, and expandable compliance detail per supplier

## Stack

- Python + Streamlit — UI
- SQLite — provided Spherecast database
- Google Gemini 2.5 Flash — recommendation reasoning
- TOPSIS — multi-criteria supplier ranking
- owlready2 / rdflib — ontology layer

## Files

- `db.sqlite` — Spherecast database (BOMs, suppliers, components)
- `app.py` — Streamlit chat UI
- `reasoner.py` — scoring, TOPSIS ranking, Gemini integration
- `db.py` — all database queries
- `ingest_data.py` — loads SQLite into Python objects
- `schema.py` — data models
- `main.py` — entry point

## Setup

```bash
pip install -r requirements.txt
# Create a .env file with:
# GEMINI_API_KEY="your_key_here"
streamlit run app.py
```

## What worked

- TOPSIS ranking produced defensible, explainable ordering
- Gemini explanation quality was high when certification data was present
- Supplier approval writing back to the DB made it feel like a real tool
- Splitting current network vs discovered made the output immediately actionable

## What didn't work

- Suppliers with no certification data score zero — needs a fallback enrichment path
- Lead time and price data not in the DB

## What we'd improve

- Live web scraping to fill missing certification data automatically
- Add lead time and price as scoring dimensions
- Extend ingredient coverage beyond the current 5
- Wire Agnes API endpoint so recommendations feed directly into procurement execution
