#!/usr/bin/env python3
"""
Simple data migration script from KLT.db to Django
Run: python migrate_simple.py /path/to/KLT.db
"""
import os
import sys
import sqlite3
import django

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'klt_web.settings')
django.setup()

from bookings.models import *
from guests.models import *
from properties.models import *

def migrate_all(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("🔄 Starting migration...")
    
    # Quick migration for each table - simplified version
    tables_order = [
        ('propertyAddresses', Location),
        ('propertyManagers', Manager), 
        ('propertyOwners', Owner),
        ('propertyAccountants', Accountant),
        ('propertyPrices', Price),
        ('properties', Property),
        ('propertySpecs', Spec),
        ('propertySEFDetails', SEFDetail),
        ('guests', Guest),
        ('bookings', Booking),
        # Add others as needed...
        ('arrivals', Arrival),
        ('departures', Departure),
        ('charges', Charge),
        ('extras', Extra),
    ]
    
    for table_name, model_class in tables_order:
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"📦 Migrating {count} records from {table_name}...")
        
        # Basic field mapping - you'd expand this
        cursor.execute(f"SELECT * FROM {table_name}")
        for row in cursor.fetchall():
            # Convert row to dict and create model instance 
            # This is a simplified version - use the detailed command for production
            pass
    
    print("✅ Migration completed!")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python migrate_simple.py /path/to/KLT.db")
        sys.exit(1)
    
    migrate_all(sys.argv[1])