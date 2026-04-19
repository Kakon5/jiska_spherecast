"""
db.py — All SQLite database queries live here.
The rest of the app just calls these functions and gets back clean data.
"""

import sqlite3
from typing import List, Dict, Optional

DB_PATH = "db.sqlite"


def get_connection():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row  # rows behave like dicts
    return con


# ─── Products ────────────────────────────────────────────────────────────────

def get_all_raw_materials() -> List[Dict]:
    """Return every raw-material product from the DB."""
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT p.Id, p.SKU, c.Name as CompanyName
        FROM Product p
        JOIN Company c ON p.CompanyId = c.Id
        WHERE p.Type = 'raw-material'
        ORDER BY p.SKU
    """)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def get_product_by_sku(sku: str) -> Optional[Dict]:
    con = get_connection()
    cur = con.cursor()
    cur.execute("SELECT Id, SKU, CompanyId FROM Product WHERE SKU = ?", (sku,))
    row = cur.fetchone()
    con.close()
    return dict(row) if row else None


# ─── Suppliers ────────────────────────────────────────────────────────────────

def get_current_network_suppliers(ingredient_keyword: str) -> List[Dict]:
    """
    Current network = suppliers already linked in Supplier_Product table.
    These are suppliers your company already buys from.
    """
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT DISTINCT
            s.Id,
            s.Name,
            'CURRENT NETWORK' as status,
            sk.ComplianceTier,
            sk.Certifications,
            sk.EFSAStatus,
            sk.Notes,
            sk.IngredientCategory,
            sk.SourceURL
        FROM Supplier s
        JOIN Supplier_Product sp ON s.Id = sp.SupplierId
        JOIN Product p ON sp.ProductId = p.Id
        LEFT JOIN Supplier_Knowledge_Product skp ON p.Id = skp.ProductId
        LEFT JOIN Supplier_Knowledge sk ON skp.SupplierKnowledgeId = sk.Id
                                       AND sk.SupplierId = s.Id
        WHERE p.Type = 'raw-material'
          AND p.SKU LIKE ?
    """, (f"%{ingredient_keyword}%",))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def get_discovered_suppliers(ingredient_keyword: str) -> List[Dict]:
    """
    Discovered = suppliers in Supplier_Knowledge but NOT yet in Supplier_Product.
    These are new suppliers found through external enrichment.
    """
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT DISTINCT
            s.Id,
            s.Name,
            'DISCOVERED' as status,
            sk.ComplianceTier,
            sk.Certifications,
            sk.EFSAStatus,
            sk.Notes,
            sk.IngredientCategory,
            sk.SourceURL
        FROM Product p
        JOIN Supplier_Knowledge_Product skp ON p.Id = skp.ProductId
        JOIN Supplier_Knowledge sk ON skp.SupplierKnowledgeId = sk.Id
        JOIN Supplier s ON sk.SupplierId = s.Id
        LEFT JOIN Supplier_Product sp ON (s.Id = sp.SupplierId AND p.Id = sp.ProductId)
        WHERE p.Type = 'raw-material'
          AND p.SKU LIKE ?
          AND sp.SupplierId IS NULL
          AND (sk.Notes IS NULL OR sk.Notes NOT LIKE '%ERROR%')
    """, (f"%{ingredient_keyword}%",))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def find_all_suppliers_for_ingredient(ingredient_keyword: str) -> Dict:
    """
    THE MAIN QUERY.
    Returns both current network and discovered suppliers for an ingredient.
    This is what Gemini uses to reason and recommend.
    """
    current = get_current_network_suppliers(ingredient_keyword)
    discovered = get_discovered_suppliers(ingredient_keyword)
    return {
        "ingredient": ingredient_keyword,
        "current_network": current,
        "discovered": discovered,
        "current_count": len(current),
        "discovered_count": len(discovered),
    }


# ─── Single Source Risk ───────────────────────────────────────────────────────

def get_single_source_risks() -> List[Dict]:
    """
    Find all ingredients that only have ONE supplier in the current network.
    These are high-risk — if that supplier fails, production stops.
    """
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT
            p.SKU,
            c.Name as CompanyName,
            COUNT(DISTINCT sp.SupplierId) as supplier_count,
            GROUP_CONCAT(s.Name, ', ') as suppliers
        FROM Product p
        JOIN Company c ON p.CompanyId = c.Id
        JOIN Supplier_Product sp ON p.Id = sp.ProductId
        JOIN Supplier s ON sp.SupplierId = s.Id
        WHERE p.Type = 'raw-material'
        GROUP BY p.Id
        HAVING supplier_count = 1
        LIMIT 20
    """)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


# ─── BOM Queries ─────────────────────────────────────────────────────────────

def get_bom_for_product(product_sku: str) -> List[Dict]:
    """Return all ingredients in the BOM for a given finished good SKU."""
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT
            fg.SKU as finished_good,
            co.Name as company,
            rm.SKU as ingredient_sku,
            rm.Id as ingredient_id
        FROM BOM b
        JOIN Product fg ON b.ProducedProductId = fg.Id
        JOIN Company co ON fg.CompanyId = co.Id
        JOIN BOM_Component bc ON b.Id = bc.BOMId
        JOIN Product rm ON bc.ConsumedProductId = rm.Id
        WHERE fg.SKU LIKE ?
    """, (f"%{product_sku}%",))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


# ─── Approve a Supplier ───────────────────────────────────────────────────────

def approve_supplier(supplier_id: int, ingredient_keyword: str) -> int:
    """
    When user approves a discovered supplier in the chat,
    this creates the Supplier_Product links — connecting them to the network.
    Returns the number of product links created.
    """
    con = get_connection()
    cur = con.cursor()

    # Find all product IDs for this ingredient
    cur.execute("""
        SELECT Id FROM Product
        WHERE Type = 'raw-material' AND SKU LIKE ?
    """, (f"%{ingredient_keyword}%",))
    product_ids = [r[0] for r in cur.fetchall()]

    inserted = 0
    for pid in product_ids:
        cur.execute("""
            INSERT OR IGNORE INTO Supplier_Product (SupplierId, ProductId)
            VALUES (?, ?)
        """, (supplier_id, pid))
        inserted += cur.rowcount

    con.commit()
    con.close()
    return inserted


# ─── Ingredient keyword extraction helper ────────────────────────────────────

INGREDIENT_MAP = {
    "mcc": "microcrystalline-cellulose",
    "microcrystalline cellulose": "microcrystalline-cellulose",
    "microcrystalline": "microcrystalline-cellulose",
    "cellulose": "microcrystalline-cellulose",
    "gelatin": "gelatin",
    "gelatine": "gelatin",
    "vitamin e": "vitamin-e",
    "vit e": "vitamin-e",
    "tocopherol": "vitamin-e",
    "vitamin a": "vitamin-a",
    "vit a": "vitamin-a",
    "retinol": "vitamin-a",
    "calcium carbonate": "calcium-carbonate",
    "calcium": "calcium-carbonate",
}

def extract_ingredient_keyword(text: str) -> Optional[str]:
    """
    Given a user message like 'Ashland MCC has a 10 day delay',
    return the DB keyword like 'microcrystalline-cellulose'.
    """
    text_lower = text.lower()
    for phrase, keyword in INGREDIENT_MAP.items():
        if phrase in text_lower:
            return keyword
    return None
