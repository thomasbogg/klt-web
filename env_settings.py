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
DATABASE_HOST: str = os.getenv('DATABASE_HOST', 'localhost')
DATABASE_PORT: str = os.getenv('DATABASE_PORT', '5432')
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

# Valid booking status values. 'Guests have departed'/'Guests on-site'/'Holiday completed' were
# inherited from PIMS' own status vocabulary but removed here 2026-08-25 - booking_stage() (staff/
# utils.py) never actually distinguished between any of these and 'Booking confirmed', deriving
# "started"/"ended" purely from arrival_date/departure_date vs today regardless of which one a
# booking had, and nothing anywhere ever set them - confirmed via a real grep, not assumption.
# Thomas: he never gave the PIMS-inherited variants individual meaning locally either.
VALID_BOOKING_STATUSES: tuple[str, ...] = (
    'Booking confirmed',
)

# Booking status values that hold dates without confirming them. Same 2026-08-25 trim as above -
# 'Provisional booking'/'Dates agreed and held' were likewise never set by any code and never
# checked individually, only 'Awaiting payment' actually drives anything (hold creation/expiry,
# the payment-timer gate in bookings/views.py).
PROVISIONAL_BOOKING_STATUSES: tuple[str, ...] = (
    'Awaiting payment',
)

# 'Payment failed' (klt-hooks, on a Revolut decline/fail/cancel), 'Hold expired'
# (bookings/utils.py::expire_stale_holds()), 'Cancelled by guest'
# (bookings/utils.py::cancel_booking_hold()), 'Cancelled by platform' (bookings/utils.py::
# sync_ical_link(), when a previously-imported platform booking's UID disappears from that
# platform's latest iCal feed), 'Cancelled by staff' (staff/views.py::
# StaffBookingDetailView._cancel_booking(), a direct staff cancellation - kept distinct from
# 'Cancelled by guest' so the record still shows who actually cancelled it; for a platform-sourced
# booking the real cancellation still has to happen on the platform itself, or the next iCal sync
# can recreate the row), and 'Payment received - needs review' (klt-hooks,
# mark_payment_paid(), when ORDER_COMPLETED arrives for a booking whose hold already expired and
# another booking now legitimately occupies the dates - the payment is real and recorded, but the
# calendar conflict needs a human to resolve which guest keeps the dates) are deliberately NOT in
# either status tuple above - that's what makes them stop blocking the calendar, without any extra
# query logic needed.


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

# ISO 3166-1 alpha-2 codes (matching Guest.country/django_countries) for the UK + the 27 EU member
# states - guests outside this list don't get asked for the cash security deposit (2026-08-29, per
# Thomas: the bank-transfer return has extra cost/hassle outside the UK/EU, on top of the existing
# returning-guest waiver - see staff/views.py::StaffCheckinDetailView and bookings/utils.py::
# booking_confirmation_context).
UK_EU_COUNTRY_CODES: tuple[str, ...] = (
    'GB',  # United Kingdom
    'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IE', 'IT',
    'LV', 'LT', 'LU', 'MT', 'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE',
)


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

# Static Wise business pay page - guest enters the amount and reference themselves, nothing is
# created per-booking via API. Used for Nov-Mar arrivals - see bookings/utils.py::determine_payment_provider.
WISE_BASE_PAYMENT_LINK = 'https://wise.com/pay/business/algarvebeachapartments'

##################################################
# TOURIST TAX SETTINGS
##################################################

TOURIST_TAX_PER_NIGHT = 2.0  # Flat rate per night for tourist tax calculation