"""
MongoDB Product Similarity Module

This module provides functionality to find similar products in MongoDB
based on shared attributes and weighted scoring.
"""

import logging
import os, sys
from pathlib import Path
from typing import List, Dict, Any
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure


# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Configure logging with colors and proper formatting
from config.logs import setup_logging
setup_logging(level=logging.INFO)

# Import MongoDB configuration from settings
from config.settings import MONGO_CONFIG


# MongoDB Configuration from settings.py
MONGODB_URI = f"mongodb://{MONGO_CONFIG['user']}:{MONGO_CONFIG['password']}@{MONGO_CONFIG['uri']}"
MONGODB_DATABASE = MONGO_CONFIG['database']
MONGODB_COLLECTION = os.getenv("MONGODB_COLLECTION", "eroski_enriched_20251104_132047")


weight = {
    "brand": 0.25,
    "category": 0.30,
    "measuring_unit": 0.15,
    "product_type": 0.10
}


class MongoDBController:
    """
    A class to handle MongoDB operations for product similarity search.
    
    This class provides methods to:
    - Connect to MongoDB database
    - Execute queries
    - Find similar products based on attributes
    """
    
    def __init__(self, database_name: str = None, collection_name: str = None):
        """
        Initialize the MongoDB Controller.
        
        Args:
            database_name (str): Name of the MongoDB database
            collection_name (str): Name of the collection to use
        """
        self.client = None
        self.db = None
        self.collection = None
        self.database_name = database_name or MONGODB_DATABASE
        self.collection_name = collection_name or MONGODB_COLLECTION
        
    def connect_to_mongodb(self) -> bool:
        """
        Establish connection to MongoDB database.
        
        Returns:
            bool: True if connection successful, False otherwise.
        """
        try:
            self.client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
            # Test connection
            self.client.admin.command('ping')
            self.db = self.client[self.database_name]
            self.collection = self.db[self.collection_name]
            logging.info(f"Successfully connected to MongoDB database: {self.database_name}")
            return True
        except ConnectionFailure as e:
            logging.error(f"Failed to connect to MongoDB: {e}")
            return False
        except Exception as e:
            logging.error(f"Error connecting to MongoDB: {e}")
            return False
    
    def close_connection(self):
        """Close the MongoDB connection."""
        if self.client:
            self.client.close()
            logging.info("MongoDB connection closed")
    
    def test_connection(self) -> Dict[str, Any]:
        """
        Test the MongoDB connection and retrieve database information.
        
        Returns:
            dict: Dictionary containing connection test results
        """
        if not self.client:
            return {
                'success': False,
                'error': 'Client not initialized. Call connect_to_mongodb() first.'
            }
        
        try:
            # Get database stats
            db_stats = self.db.command("dbStats")
            
            # Get collection stats
            collection_stats = self.db.command("collStats", self.collection_name)
            
            # List all collections
            collections = self.db.list_collection_names()
            
            # Get MongoDB version
            server_info = self.client.server_info()
            
            return {
                'success': True,
                'mongodb_version': server_info.get('version', 'Unknown'),
                'database': self.database_name,
                'collection': self.collection_name,
                'document_count': collection_stats.get('count', 0),
                'collections': collections,
                'database_size_mb': round(db_stats.get('dataSize', 0) / (1024 * 1024), 2),
                'uri': MONGODB_URI
            }
                
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_product_by_id(self, product_id: int) -> Dict[str, Any]:
        """
        Get a product by its ID.
        
        Args:
            product_id (int): ID of the product
            
        Returns:
            Dict: Product document or empty dict if not found
        """
        if self.collection is None:
            logging.error("Collection not initialized. Call connect_to_mongodb() first.")
            return {}
        
        try:
            product = self.collection.find_one({"id": product_id})
            if product:
                # Convert ObjectId to string for serialization
                product['_id'] = str(product['_id'])
            return product or {}
        except Exception as e:
            logging.error(f"Error getting product by ID: {e}")
            return {}
    
    def search_products_by_name(self, search_term: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for products by name or description (case-insensitive).
        
        Args:
            search_term (str): Search term
            limit (int): Maximum number of results
            
        Returns:
            List[Dict]: List of matching products
        """
        if self.collection is None:
            logging.error("Collection not initialized. Call connect_to_mongodb() first.")
            return []
        
        try:
            # Use regex for case-insensitive search
            query = {
                "$or": [
                    {"name": {"$regex": search_term, "$options": "i"}},
                    {"description": {"$regex": search_term, "$options": "i"}}
                ]
            }
            
            results = list(self.collection.find(query).limit(limit))
            
            # Convert ObjectId to string
            for doc in results:
                doc['_id'] = str(doc['_id'])
            
            return results
        except Exception as e:
            logging.error(f"Error searching products: {e}")
            return []
    
    def get_product_details(self, product_id: int, print_details: bool = True) -> Dict[str, Any]:
        """
        Get detailed information about a product.
        
        Args:
            product_id (int): ID of the product
            print_details (bool): Whether to print the details (default: True)
            
        Returns:
            Dict: Product details
        """
        product = self.get_product_by_id(product_id)
        
        if print_details:
            if product:
                print(f"\n  📊 Target Product Details (ID: {product_id}):")
                print(f"    UUID: {product.get('uuid', 'N/A')}")
                print(f"    Description: {product.get('description', 'N/A')}")
                print(f"    Price: {product.get('price', 'N/A')}")
                print(f"    Brand: {product.get('brand', 'N/A')}")
                print(f"    Category: {product.get('category', 'N/A')}")
                print(f"    Product Type: {product.get('product_type', 'N/A')}")
                print(f"    Measuring Unit: {product.get('measuring_unit', 'N/A')}")
                print(f"    Supermarket: {product.get('supermarket', 'N/A')}")
            else:
                print(f"    ❌ Product with ID {product_id} not found!")
        
        return product
    

    
    def get_collection_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the collection.
        
        Returns:
            Dict: Collection statistics
        """
        if self.collection is None:
            logging.error("Collection not initialized. Call connect_to_mongodb() first.")
            return {}
        
        try:
            stats = self.db.command("collStats", self.collection_name)
            
            # Get sample document to show fields
            sample_doc = self.collection.find_one()
            fields = list(sample_doc.keys()) if sample_doc else []
            
            return {
                'count': stats.get('count', 0),
                'size_mb': round(stats.get('size', 0) / (1024 * 1024), 2),
                'avg_document_size': stats.get('avgObjSize', 0),
                'fields': fields
            }
        except Exception as e:
            logging.error(f"Error getting collection stats: {e}")
            return {}
    
    def print_all_documents(self, collection_name: str = None, limit: int = None, fields: List[str] = None):
        """
        Print all documents from a collection.
        
        Args:
            collection_name (str): Name of the collection. If None, uses default collection.
            limit (int): Maximum number of documents to print. If None, prints all.
            fields (List[str]): List of field names to print. If None, prints all fields.
        """
        if self.client is None:
            logging.error("Client not initialized. Call connect_to_mongodb() first.")
            return
        
        # Use specified collection or default
        coll_name = collection_name or self.collection_name
        collection = self.db[coll_name]
        
        try:
            # Get total count
            total_count = collection.count_documents({})
            
            print(f"\n📚 Collection: {coll_name}")
            print(f"   Total documents: {total_count:,}")
            
            if total_count == 0:
                print("   ⚠️  Collection is empty")
                return
            
            # Get documents
            query_limit = limit if limit else total_count
            print(f"   Showing: {min(query_limit, total_count):,} documents")
            
            if fields:
                print(f"   Fields: {', '.join(fields)}\n")
            else:
                print(f"   Showing all fields\n")
            
            cursor = collection.find().limit(query_limit) if limit else collection.find()
            
            for i, doc in enumerate(cursor, 1):
                print(f"  📄 Document #{i}:")
                print(f"     _id: {doc.get('_id', 'N/A')}")
                
                # Print specified fields or all fields
                if fields:
                    # Only print specified fields
                    for key in fields:
                        value = doc.get(key, 'N/A')
                        # Truncate long strings
                        if isinstance(value, str) and len(value) > 100:
                            value = value[:100] + "..."
                        print(f"     {key}: {value}")
                else:
                    # Print all fields except _id
                    for key, value in doc.items():
                        if key != '_id':
                            # Truncate long strings
                            if isinstance(value, str) and len(value) > 100:
                                value = value[:100] + "..."
                            print(f"     {key}: {value}")
                print()
                
        except Exception as e:
            logging.error(f"Error printing documents from {coll_name}: {e}")
    
    def list_all_collections(self):
        """
        List all collections in the database and print their documents.
        """
        if self.client is None:
            logging.error("Client not initialized. Call connect_to_mongodb() first.")
            return
        
        try:
            collections = self.db.list_collection_names()
            
            if not collections:
                print("\n⚠️  No collections found in database")
                return
            
            print(f"\n📚 Database: {self.database_name}")
            print(f"   Total collections: {len(collections)}\n")
            
            for coll_name in collections:
                collection = self.db[coll_name]
                count = collection.count_documents({})
                print(f"  📁 {coll_name}: {count:,} documents")
            
        except Exception as e:
            logging.error(f"Error listing collections: {e}")
    
    def get_column_statistics(self, collection_name: str = None) -> Dict[str, Dict[str, Any]]:
        """
        Get detailed statistics for all columns in a collection.
        
        Args:
            collection_name (str): Name of the collection. If None, uses default collection.
            
        Returns:
            Dict: Statistics for each field/column including:
                - total_count: Total documents in collection
                - non_null_count: Number of documents where field is not null
                - null_count: Number of documents where field is null or missing
                - null_percentage: Percentage of null/missing values
                - unique_count: Number of unique values
                - data_types: Set of data types found
                - sample_values: Sample of values (up to 5)
                - min_value: Minimum value (for numeric fields)
                - max_value: Maximum value (for numeric fields)
                - avg_value: Average value (for numeric fields)
        """
        if self.client is None:
            logging.error("Client not initialized. Call connect_to_mongodb() first.")
            return {}
        
        # Use specified collection or default
        coll_name = collection_name or self.collection_name
        collection = self.db[coll_name]
        
        try:
            # Get total document count
            total_docs = collection.count_documents({})
            
            if total_docs == 0:
                print(f"⚠️  Collection '{coll_name}' is empty")
                return {}
            
            # Get all field names from a sample of documents
            sample_doc = collection.find_one()
            if not sample_doc:
                return {}
            
            fields = [key for key in sample_doc.keys() if key != '_id']
            
            statistics = {}
            
            print(f"\n📊 Analyzing {len(fields)} fields in collection '{coll_name}'...")
            print(f"   Total documents: {total_docs:,}\n")
            
            for field in fields:
                # Count non-null values (exclude None, NaN, and missing fields)
                # MongoDB stores NaN as a special double value, we need to check for it
                non_null_count = collection.count_documents({
                    field: {
                        "$exists": True,
                        "$ne": None,
                        "$ne": float('nan')  # Explicitly check for NaN
                    }
                })
                
                # Also filter out NaN using $type check (NaN is type "double" but fails $eq comparison)
                # A more robust approach: count documents where field exists AND is not null AND is not NaN
                pipeline_null_check = [
                    {"$match": {field: {"$exists": True}}},
                    {"$project": {
                        "isValid": {
                            "$and": [
                                {"$ne": [f"${field}", None]},
                                {"$eq": [f"${field}", f"${field}"]}  # NaN != NaN, so this filters out NaN
                            ]
                        }
                    }},
                    {"$match": {"isValid": True}},
                    {"$count": "count"}
                ]
                
                non_null_result = list(collection.aggregate(pipeline_null_check))
                non_null_count = non_null_result[0]['count'] if non_null_result else 0
                
                null_count = total_docs - non_null_count
                null_percentage = (null_count / total_docs) * 100
                
                # Get unique values count (excluding None and NaN)
                pipeline_distinct = [
                    {"$match": {field: {"$exists": True}}},
                    {"$project": {
                        field: 1,
                        "isValid": {
                            "$and": [
                                {"$ne": [f"${field}", None]},
                                {"$eq": [f"${field}", f"${field}"]}  # Filters out NaN
                            ]
                        }
                    }},
                    {"$match": {"isValid": True}},
                    {"$group": {"_id": f"${field}"}},
                    {"$count": "count"}
                ]
                
                unique_result = list(collection.aggregate(pipeline_distinct))
                unique_count = unique_result[0]['count'] if unique_result else 0
                
                # Get data types
                pipeline = [
                    {"$project": {field: 1}},
                    {"$limit": 100},
                    {"$group": {
                        "_id": {"$type": f"${field}"},
                        "count": {"$sum": 1}
                    }}
                ]
                type_results = list(collection.aggregate(pipeline))
                data_types = [r['_id'] for r in type_results if r['_id']]
                
                # Get sample values (up to 5 non-null, non-NaN values)
                sample_values = []
                pipeline_samples = [
                    {"$match": {field: {"$exists": True}}},
                    {"$project": {
                        field: 1,
                        "isValid": {
                            "$and": [
                                {"$ne": [f"${field}", None]},
                                {"$eq": [f"${field}", f"${field}"]}  # Filters out NaN
                            ]
                        }
                    }},
                    {"$match": {"isValid": True}},
                    {"$limit": 5}
                ]
                
                sample_docs = list(collection.aggregate(pipeline_samples))
                for doc in sample_docs:
                    val = doc.get(field)
                    if val is not None:
                        sample_values.append(val)
                
                # Initialize stats
                stats = {
                    'total_count': total_docs,
                    'non_null_count': non_null_count,
                    'null_count': null_count,
                    'null_percentage': round(null_percentage, 2),
                    'unique_count': unique_count,
                    'data_types': data_types,
                    'sample_values': sample_values[:5]
                }
                
                # For numeric fields, calculate min, max, avg (excluding NaN)
                if 'double' in data_types or 'int' in data_types or 'long' in data_types or 'decimal' in data_types:
                    try:
                        numeric_stats = collection.aggregate([
                            {"$match": {field: {"$exists": True, "$type": ["double", "int", "long", "decimal"]}}},
                            {"$project": {
                                field: 1,
                                "isValid": {
                                    "$and": [
                                        {"$ne": [f"${field}", None]},
                                        {"$eq": [f"${field}", f"${field}"]}  # Filters out NaN
                                    ]
                                }
                            }},
                            {"$match": {"isValid": True}},
                            {"$group": {
                                "_id": None,
                                "min": {"$min": f"${field}"},
                                "max": {"$max": f"${field}"},
                                "avg": {"$avg": f"${field}"}
                            }}
                        ])
                        
                        numeric_result = list(numeric_stats)
                        if numeric_result:
                            stats['min_value'] = numeric_result[0].get('min')
                            stats['max_value'] = numeric_result[0].get('max')
                            stats['avg_value'] = round(numeric_result[0].get('avg', 0), 2) if numeric_result[0].get('avg') else None
                    except Exception:
                        pass
                
                statistics[field] = stats
            
            return statistics
            
        except Exception as e:
            logging.error(f"Error getting column statistics: {e}")
            return {}
    
    def print_column_statistics(self, collection_name: str = None):
        """
        Print detailed statistics for all columns in a collection.
        
        Args:
            collection_name (str): Name of the collection. If None, uses default collection.
        """
        stats = self.get_column_statistics(collection_name)
        
        if not stats:
            return
        
        coll_name = collection_name or self.collection_name
        
        print(f"\n{'='*80}")
        print(f"  COLUMN STATISTICS - Collection: '{coll_name}'")
        print(f"{'='*80}\n")
        
        for field, field_stats in stats.items():
            print(f"📊 Field: {field}")
            print(f"   {'─'*70}")
            print(f"   Total Documents:     {field_stats['total_count']:,}")
            print(f"   Non-Null Count:      {field_stats['non_null_count']:,}")
            print(f"   Null/Missing Count:  {field_stats['null_count']:,} ({field_stats['null_percentage']:.2f}%)")
            print(f"   Unique Values:       {field_stats['unique_count']:,}")
            print(f"   Data Types:          {', '.join(field_stats['data_types']) if field_stats['data_types'] else 'N/A'}")
            
            # Show numeric statistics if available
            if 'min_value' in field_stats:
                print(f"   Min Value:           {field_stats['min_value']}")
                print(f"   Max Value:           {field_stats['max_value']}")
                print(f"   Average Value:       {field_stats['avg_value']}")
            
            # Show sample values
            if field_stats['sample_values']:
                print(f"   Sample Values:")
                for i, val in enumerate(field_stats['sample_values'][:5], 1):
                    # Truncate long strings
                    if isinstance(val, str) and len(val) > 60:
                        val = val[:60] + "..."
                    print(f"      {i}. {val}")
            
            print()


def print_header(title: str):
    """Print a formatted header."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_section(title: str):
    """Print a formatted section header."""
    print(f"\n📋 {title}")
    print("-" * 70)


def main():
    """
    Main function to test MongoDB connection and execute sample queries.
    """
    # Load environment variables
    print(MONGODB_URI)
    from dotenv import load_dotenv
    load_dotenv()
    
    print_header("MONGODB CONNECTION & QUERY TEST")
    
    # Display configuration
    print_section("Configuration")
    print(f"  URI: {MONGODB_URI}")
    print(f"  Database: {MONGODB_DATABASE}")
    print(f"  Collection: {MONGODB_COLLECTION}")
    
    # Initialize controller
    print_section("Initializing MongoDB Controller")
    controller = MongoDBController()
    
    # Test connection
    print_section("Testing Connection")
    print("🔌 Attempting to connect to MongoDB...")
    
    if not controller.connect_to_mongodb():
        print("\n❌ Failed to connect to MongoDB database")
        print("\n💡 Troubleshooting steps:")
        print("  1. Verify MongoDB is running")
        print("  2. Check connection URI in .env file")
        print("  3. Verify database and collection names")
        print("  4. Check firewall settings")
        return
    
    print("✅ Successfully connected to MongoDB!")
    
    # Run connection test
    print_section("Database Information")
    test_results = controller.test_connection()
    
    if not test_results['success']:
        print(f"❌ Connection test failed: {test_results['error']}")
        controller.close_connection()
        return
    
    print(f"  Database Name: {test_results['database']}")
    print(f"  Collection Name: {test_results['collection']}")
    print(f"  MongoDB Version: {test_results['mongodb_version']}")
    print(f"  Total Documents: {test_results['document_count']:,}")
    print(f"  Database Size: {test_results['database_size_mb']} MB")
    
    if test_results['collections']:
        print(f"\n  Collections ({len(test_results['collections'])}):")
        for coll in test_results['collections']:
            print(f"    - {coll}")
    
    # List all collections with document counts
    print_section("All Collections in Database")
    controller.list_all_collections()
    
    # Check if collection is empty
    if test_results['document_count'] == 0:
        print("\n💡 Note: Collection is empty. No data to query.")
        controller.close_connection()
        return
    
    # Get collection stats
    print_section("Collection Statistics")
    stats = controller.get_collection_stats()
    print(f"  Document Count: {stats.get('count', 0):,}")
    print(f"  Collection Size: {stats.get('size_mb', 0)} MB")
    print(f"  Avg Document Size: {stats.get('avg_document_size', 0)} bytes")
    
    if stats.get('fields'):
        print(f"\n  Document Fields ({len(stats['fields'])}):")
        for field in stats['fields'][:10]:  # Show first 10 fields
            print(f"    - {field}")
    
    # Get detailed column statistics
    print_section("Detailed Column Statistics")
    controller.print_column_statistics(collection_name=MONGODB_COLLECTION)
    
    # Define specific fields to print
    selected_fields = [
        "price",
        "offer_price",
        "unit_price",
        "nutrition_information_calories_value",
        "nutrition_information_fat_value",
        "nutrition_information_sugars_value",
        "nutrition_information_carbohydrates_value",
        "nutrition_information_protein_value",
        "nutrition_information_salt_value",
        "nutrition_information_fiber_value"
    ]
    
    # # Print all documents from the main collection (limited to first 10)
    # print_section(f"Sample Documents from '{MONGODB_COLLECTION}' Collection")
    # print(f"  Showing first 10 documents with selected fields...\n")
    # controller.print_all_documents(
    #     collection_name=MONGODB_COLLECTION, 
    #     limit=10,
    #     fields=selected_fields
    # )
    
    
    # Test product search and similarity
    target_id = 26719799
    
    # Get detailed information about the target product
    controller.get_product_details(target_id, print_details=True)
    
   
    
    # Close connection
    controller.close_connection()
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
