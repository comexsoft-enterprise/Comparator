import logging
import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Configure logging with colors and proper formatting
from config.logs import setup_logging
setup_logging(level=logging.INFO)

from src.ingestion.mongodb_ingest import MongoDBCSVIngestor

def main():
    """Run the CSV ingestion process for MongoDB."""
    logging.info("🚀 Starting MongoDB CSV Ingestion Process...")
    print("=" * 50)
    
    # Create ingestor instance
    ingestor = MongoDBCSVIngestor()

    folder_name = "validated"
    
    # Check if CSV files exist
    csv_files = ingestor.get_csv_files(folder=folder_name)
    if not csv_files:
        logging.warning("❌ No CSV files found in the enriched data directory.")
        logging.info(f"📁 Directory: {ingestor.processed_data_dir}/{folder_name}")
        return
    
    logging.info(f"📄 Found {len(csv_files)} CSV files to process:")
    for csv_file in csv_files:
        logging.info(f"   • {Path(csv_file).name}")
    
    logging.info("🔗 Attempting to connect to MongoDB database...")
    
    try:
        # Define index fields for better query performance (optional)
        index_fields = ["siid", "uuid"]  # Adjust based on your data structure
        
        # Run the ingestion process
        success = ingestor.ingest_all_csv_files(
            folder=folder_name, 
            batch_size=500,
            index_fields=index_fields,  # Optional: remove if you don't want indexes
            id_field='siid',  # Specify the ID field to check for existing documents
            watch_fields=[]  # Fields to monitor for changes
        )
        
        if success:
            logging.info("✅ CSV ingestion completed successfully!")
            logging.info("📊 Data has been inserted into MongoDB collections")
            if index_fields:
                logging.info(f"🔍 Indexes created on fields: {', '.join(index_fields)}")
        else:
            logging.error("❌ CSV ingestion failed. Check the logs for details.")
            
    except KeyboardInterrupt:
        logging.warning("⚠️  Ingestion interrupted by user")
    except Exception as e:
        logging.error(f"❌ Unexpected error: {e}")
        logging.error("   Check your MongoDB connection settings and ensure the database is running.")


if __name__ == "__main__":
    main()