import logging
import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Configure logging with colors and proper formatting
from config.logs import setup_logging
setup_logging(level=logging.INFO)

from src.ingestion.neo4j_ingest import Neo4jCSVIngestor

def main():
    """Run the CSV ingestion process."""
    logging.info("🚀 Starting Neo4j CSV Ingestion Process...")
    print("=" * 50)
    
    # Create ingestor instance
    ingestor = Neo4jCSVIngestor()

    folder_name = "fixed"
    
    # Check if CSV files exist
    csv_files = ingestor.get_csv_files(folder=folder_name)
    if not csv_files:
        logging.warning("❌ No CSV files found in the fixed data directory.")
        logging.info(f"📁 Directory: {ingestor.processed_data_dir}/{folder_name}")
        return
    
    logging.info(f"📄 Found {len(csv_files)} CSV files to process:")
    for csv_file in csv_files:
        logging.info(f"   • {Path(csv_file).name}")
    
    logging.info("🔗 Attempting to connect to Neo4j database...")
    
    try:
        # Run the ingestion process
        success = ingestor.ingest_all_csv_files(folder=folder_name, batch_size=500)
        
        if success:
            logging.info("✅ CSV ingestion completed successfully!")
            
        else:
            logging.error("❌ CSV ingestion failed. Check the logs for details.")
            
    except KeyboardInterrupt:
        logging.warning("⚠️  Ingestion interrupted by user")
    except Exception as e:
        logging.error(f"❌ Unexpected error: {e}")
        logging.error("   Check your Neo4j connection settings and ensure the database is running.")


if __name__ == "__main__":
    main()