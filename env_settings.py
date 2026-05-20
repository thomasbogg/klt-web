"""
Global settings and configuration values for the KLT application.

This module contains configuration settings, API credentials, and constants
used throughout the application.
"""
import os

try:
    # Check if running in deployed environment (e.g., on a server) 
    # where environment variables are set directly
    LOCAL: bool = os.getenv('LOCAL').lower() == 'true'
except Exception:
    # Load environment variables from .env file
    from dotenv import load_dotenv
    load_dotenv()
    LOCAL: bool = os.getenv('LOCAL').lower() == 'true'

#######################################################
# APPLICATION SETTINGS
#######################################################

# Debug/Test mode flag
TEST: bool = os.getenv('TEST', 'False').lower() == 'true'

# Website information
WEBSITE_LINK: str = 'https://www.algarvebeachapartments.com/'
KLT_WEBHOOK_URL: str = 'https://klt-hooks.up.railway.app/'

# Default language
DEFAULT_LANGUAGE = 'EN-GB'

# Database in use warning
DATABASE_IN_USE_EMAIL_FOLDER = 'Updates with Database'
DATABASE_IN_USE_EMAIL_SUBJECT = 'RUNNING UPDATE WITH DATABASE'

# DJANGO SETTINGS
DJANGO_SECRET_KEY: str = os.getenv('DJANGO_SECRET_KEY')

#######################################################
# FILESYSTEM SETTINGS
#######################################################

# Directory paths
DIR: str = os.getcwd()
LOCAL_STORAGE_DIR: str = os.path.abspath('cache')
BROWSER_DIR: str = '/snap/chromium/current/usr/lib/chromium-browser/'
BROWSER_USER_DATA_DIR: str = os.path.join(os.path.expanduser('~'), '.browser_data')

# Database configuration
DATABASE_NAME: str = os.getenv('DATABASE_NAME')
DATABASE_USER: str = os.getenv('DATABASE_USER')
DATABASE_PASSWORD: str = os.getenv('DATABASE_PASSWORD')
DATABASE_PATH: str = os.path.join(DIR, DATABASE_NAME)


#######################################################
# PROPERTY SETTINGS
#######################################################

# List of managed properties
PROPERTIES: tuple[str, ...] = (
    'Quinta da Barracuda', 
    'Clube do Monaco', 
    'Parque da Corcovada'
)

# Valid booking status values
VALID_BOOKING_STATUSES: tuple[str, ...] = (
    'Booking confirmed',
    'Guests have departed', 
    'Guests on-site', 
    'Holiday completed'
)


#######################################################
# BOOKING PLATFORM SETTINGS
#######################################################

# Supported booking platforms
PLATFORMS: tuple[str, ...] = (
    'Airbnb',
    'Booking.com',
    'Vrbo'
)

# Currency conversion rate
GBP_EUR_EXCHANGE_RATE: float = 1.1111


#######################################################
# PLATFORM CREDENTIALS
#######################################################

# Credentials loaded from environment variables
# These are now stored in the .env file (keep it private!)

# PIMS credentials
PIMS_USERNAME: str = os.getenv('PIMS_USERNAME', '')
PIMS_PASSWORD: str = os.getenv('PIMS_PASSWORD', '')

# Booking.com credentials
BOOKINGCOM_USERNAME: str = os.getenv('BOOKINGCOM_USERNAME', '')
BOOKINGCOM_PASSWORD: str = os.getenv('BOOKINGCOM_PASSWORD', '')

# VRBO credentials
VRBO_USERNAME: str = os.getenv('VRBO_USERNAME', '')
VRBO_PASSWORD: str = os.getenv('VRBO_PASSWORD', '')

# API Keys
DEEPL_KEY: str = os.getenv('DEEPL_KEY', '')

# TMT credentials
TMT_USERNAME: str = os.getenv('TMT_USERNAME', '')
TMT_PASSWORD: str = os.getenv('TMT_PASSWORD', '')


#######################################################
# GOOGLE CREDENTIALS & ACCOUNTS
#######################################################

if LOCAL:  
    GOOGLE_API_CREDENTIALS = os.path.abspath(os.getenv('GOOGLE_CREDS_DIR', None))
else:
    GOOGLE_API_CREDENTIALS = (
        {
            "type": os.getenv("type"),
            "project_id": os.getenv("project_id"),
            "private_key_id": os.getenv("private_key_id"),
            "private_key": '\n'.join(os.getenv("private_key").split('\\n')),
            "client_email": os.getenv("client_email"),
            "client_id": os.getenv("client_id"),
            "auth_uri": os.getenv("auth_uri"),
            "token_uri": os.getenv("token_uri"),
            "auth_provider_x509_cert_url": os.getenv("auth_provider_x509_cert_url"),
            "client_x509_cert_url": os.getenv("client_x509_cert_url"),
            "universe_domain": os.getenv("universe_domain"),
        },
        os.getenv('GOOGLE_API_SERVICE_ACCOUNT_USERNAME'),
    )


##################################################
# BANKING CREDENTIALS
##################################################

REVOLUT_API_SECRET_KEY = os.getenv('REVOLUT_API_SECRET_KEY')
REVOLUT_API_VERSION = os.getenv('REVOLUT_API_VERSION')
REVOLUT_BASE_PAYMENT_LINK = 'https://checkout.revolut.com/payment-link/'

##################################################
# TOURIST TAX SETTINGS
##################################################

TOURIST_TAX_PER_NIGHT = 2.0  # Flat rate per night for tourist tax calculation