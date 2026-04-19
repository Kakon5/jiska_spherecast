"""
ingest_data.py — Loads data from SQLite DB into Python objects.

BEFORE: Was reading from a CSV/TSV file (v2/suppliers.xlsx)
NOW:    Reads directly from db.sqlite using our 5-table schema
"""

import sqlite3
from schema import (
    Product, ProductType, BOM, BOMComponent,
    Supplier, SupplierProduct, Substitute, ComplianceType
)

DB_PATH = "db.sqlite"


def _parse_certifications(cert_text: str):
    """
    Read the certifications text from Supplier_Knowledge
    and return a list of ComplianceType enums.
    e.g. "USP/NF + EP + GMP. Kosher ✅ Halal ✅" → [GMP, HALAL, KOSHER]
    """
    if not cert_text:
        return []
    text = cert_text.lower()
    result = []
    if "efsa" in text:
        result.append(ComplianceType.EFSA)
    if "fda" in text:
        result.append(ComplianceType.FDA)
    if "gmp" in text:
        result.append(ComplianceType.GMP)
    if "halal" in text:
        result.append(ComplianceType.HALAL)
    if "kosher" in text:
        result.append(ComplianceType.KOSHER)
    return result


def load_from_sqlite():
    """
    Load everything from SQLite into Python objects.
    Returns the same tuple the rest of the app expects:
    (products, suppliers, bom, substitutes, supplier_products)
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # ── 1. Load raw-material Products ────────────────────────────────────────
    cur.execute("""
        SELECT p.Id, p.SKU, p.Type, c.Name as CompanyName
        FROM Product p
        JOIN Company c ON p.CompanyId = c.Id
        WHERE p.Type = 'raw-material'
        ORDER BY p.Id
    """)
    db_products = cur.fetchall()

    products = []
    for row in db_products:
        # Extract a human-readable ingredient name from the SKU
        # e.g. "RM-C17-microcrystalline-cellulose-557e108b" → "microcrystalline cellulose"
        sku_parts = row["SKU"].split("-")
        # Remove prefix (RM, C17) and suffix (hash), join middle parts
        name_parts = sku_parts[2:-1] if len(sku_parts) > 4 else sku_parts[2:]
        ingredient_name = " ".join(name_parts).replace("-", " ").title()

        products.append(Product(
            id=row["Id"],
            sku=row["SKU"],
            name=f"{ingredient_name} ({row['CompanyName']})",
            type=ProductType.RAW_MATERIAL,
            assay_purity=99.0,  # default — real value in Supplier_Knowledge
            compliance_requirements=[],
        ))

    # ── 2. Load Suppliers (current network only — from Supplier_Product) ─────
    cur.execute("""
        SELECT DISTINCT
            s.Id, s.Name,
            sk.ComplianceTier,
            sk.Certifications,
            sk.EFSAStatus,
            sk.Notes,
            sk.SourceURL
        FROM Supplier s
        JOIN Supplier_Product sp ON s.Id = sp.SupplierId
        LEFT JOIN Supplier_Knowledge sk ON sk.SupplierId = s.Id
    """)
    db_suppliers = cur.fetchall()

    suppliers = []
    seen_supplier_ids = set()
    for row in db_suppliers:
        if row["Id"] in seen_supplier_ids:
            continue
        seen_supplier_ids.add(row["Id"])
        suppliers.append(Supplier(
            id=row["Id"],
            name=row["Name"],
            certifications=_parse_certifications(row["Certifications"] or ""),
            lead_time_days=21,      # no lead time in DB — default
            reliability_score=0.9,
            source="internal",
            compliance_tier=row["ComplianceTier"] or "verified",
            certifications_text=row["Certifications"] or "",
            efsa_status=row["EFSAStatus"] or "",
            notes=row["Notes"] or "",
            source_url=row["SourceURL"] or "",
        ))

    # ── 3. Load Supplier_Product links ────────────────────────────────────────
    cur.execute("SELECT SupplierId, ProductId FROM Supplier_Product")
    db_sp = cur.fetchall()

    supplier_products = []
    for row in db_sp:
        supplier_products.append(SupplierProduct(
            supplier_id=row["SupplierId"],
            product_id=row["ProductId"],
            unit_cost=50.0,         # no cost in DB — default
            supplier_name="",
            availability=True,
        ))

    # ── 4. Load a sample BOM (first finished good) ────────────────────────────
    cur.execute("""
        SELECT b.Id, b.ProducedProductId, bc.ConsumedProductId
        FROM BOM b
        JOIN BOM_Component bc ON b.Id = bc.BOMId
        LIMIT 20
    """)
    bom_rows = cur.fetchall()

    bom_components = []
    bom_id = bom_rows[0]["Id"] if bom_rows else 1
    produced_id = bom_rows[0]["ProducedProductId"] if bom_rows else 1

    product_lookup = {p.id: p for p in products}
    for row in bom_rows:
        if row["Id"] != bom_id:
            break
        rm = product_lookup.get(row["ConsumedProductId"])
        if rm:
            bom_components.append(BOMComponent(
                bom_id=row["Id"],
                consumed_product_id=row["ConsumedProductId"],
                raw_material=rm,
            ))

    bom = BOM(
        id=bom_id,
        produced_product_id=produced_id,
        components=bom_components,
    )

    # ── 5. No pre-defined substitutes — reasoner builds these dynamically ─────
    substitutes = []

    con.close()
    print(f"✅ Loaded from SQLite: {len(products)} products, "
          f"{len(suppliers)} suppliers, {len(supplier_products)} links")
    return products, suppliers, bom, substitutes, supplier_products


def ingest_data(enable_enrichment=False):
    """Main entry point — same signature as before."""
    products, suppliers, bom, substitutes, supplier_products = load_from_sqlite()

    from reasoner import load_data_to_ontology
    load_data_to_ontology(products, suppliers, substitutes, supplier_products)

    return products, suppliers, bom, substitutes, supplier_products


if __name__ == "__main__":
    ingest_data()
