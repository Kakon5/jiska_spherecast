from typing import List, Optional
from enum import Enum
from dataclasses import dataclass, field

class ProductType(str, Enum):
    FINISHED_GOOD = "finished-good"
    RAW_MATERIAL = "raw-material"

class ComplianceType(str, Enum):
    EFSA = "efsa"
    FDA = "fda"
    GMP = "gmp"
    HALAL = "halal"
    KOSHER = "kosher"
    ORGANIC = "organic"

@dataclass
class Product:
    id: int
    sku: str
    name: str
    type: ProductType
    assay_purity: float = 0.0
    efsa_compliant: bool = True
    lead_ppm: float = 0.0
    compliance_requirements: List[ComplianceType] = None
    quality_attributes: dict = None
    external_sources: List[str] = None

    def __post_init__(self):
        if self.compliance_requirements is None:
            self.compliance_requirements = []
        if self.quality_attributes is None:
            self.quality_attributes = {}
        if self.external_sources is None:
            self.external_sources = []

@dataclass
class BOMComponent:
    bom_id: int
    consumed_product_id: int
    raw_material: Product
    quantity: float = 1.0
    unit: str = "kg"

@dataclass
class BOM:
    id: int
    produced_product_id: int
    components: List[BOMComponent]
    compliance_requirements: List[ComplianceType] = None

    def __post_init__(self):
        if self.compliance_requirements is None:
            self.compliance_requirements = []

@dataclass
class Supplier:
    id: int
    name: str
    certifications: List[ComplianceType] = None
    lead_time_days: int = 30
    reliability_score: float = 0.8
    # NEW: internal = already in network, discovered = found via enrichment
    source: str = "internal"
    compliance_tier: str = "verified"
    certifications_text: str = ""
    efsa_status: str = ""
    notes: str = ""
    source_url: str = ""

    def __post_init__(self):
        if self.certifications is None:
            self.certifications = []

@dataclass
class SupplierProduct:
    supplier_id: int
    product_id: int
    unit_cost: float
    supplier_name: str
    availability: bool = True

@dataclass
class Substitute:
    original_product_id: int
    substitute_product_id: int
    confidence_score: float = 0.0
    evidence: List[str] = None
    compliance_verified: bool = False
    quality_impact: str = "neutral"

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = []
