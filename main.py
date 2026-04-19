import os
from dotenv import load_dotenv
from ingest_data import ingest_data
from reasoner import load_data_to_ontology, run_reasoner

# 1. Load environment variables from .env
load_dotenv()

def main():
    print("Initializing Ontology for Project")

    products, suppliers, bom, substitutes, supplier_products = ingest_data(enable_enrichment=False)

    load_data_to_ontology(products, suppliers, substitutes, supplier_products)
    run_reasoner()

    print("Ontology built successfully!")

if __name__ == "__main__":
    main()
