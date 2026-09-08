"""Country-code dropdown for every phone field on the site that splits entry into a calling-code
select plus a free-text local number - the guest-facing reservation form and Manage Booking
Contact Details (bookings.forms.ReservationForm/GuestContactDetailsForm) and the Owner Suite
Contact Details page (owners/views.py::OwnerContactDetailsView). None of these fields are two
DB columns - every phone field in this codebase (Guest, Owner, Accountant, ManagementCompany
contacts) stores one freeform string; split_phone()/join_phone() below are purely a form-layer
convenience either side of that single column, per Thomas 2026-09-08 (deliberately not
splitting the schema - see project_klt_web_owner_currency_field_cleanup memory-equivalent
reasoning: no downstream consumer ever needs the calling code on its own, and two columns would
leave every pre-existing row with no calling code to backfill).

CALLING_CODES is the standard ISO 3166-1 alpha-2 -> E.164 calling code table; PHONE_COUNTRY_CHOICES
below turns it into one dropdown option per *unique calling code* (not per country) since several
countries share a code - e.g. +1 covers the US, Canada and most of the Caribbean, +44 covers the
UK, Guernsey, Jersey and the Isle of Man. Deduplicating means the dropdown's value is always an
unambiguous calling code, so a stored phone number round-trips back to the same selected option -
there'd be no way to tell "+1 555…" apart as US vs. Jamaica vs. Bermuda if every country kept its
own option instead.

PRIMARY_COUNTRY_FOR_CODE picks which country's name labels a shared code's option (the most
populous/best-known one, e.g. "United States" for +1 rather than "American Samoa") - every code
not listed there falls back to whichever country name sorts first alphabetically, which is fine
for the obscure groupings (+590 Guadeloupe/Saint Martin/Saint Barthélemy etc.) where no option
reads as more "correct" than another.
"""

CALLING_CODES = {
    'AD': '+376', 'AE': '+971', 'AF': '+93', 'AG': '+1', 'AI': '+1', 'AL': '+355', 'AM': '+374',
    'AO': '+244', 'AQ': '+672', 'AR': '+54', 'AS': '+1', 'AT': '+43', 'AU': '+61', 'AW': '+297',
    'AX': '+358', 'AZ': '+994', 'BA': '+387', 'BB': '+1', 'BD': '+880', 'BE': '+32', 'BF': '+226',
    'BG': '+359', 'BH': '+973', 'BI': '+257', 'BJ': '+229', 'BL': '+590', 'BM': '+1', 'BN': '+673',
    'BO': '+591', 'BQ': '+599', 'BR': '+55', 'BS': '+1', 'BT': '+975', 'BW': '+267', 'BY': '+375',
    'BZ': '+501', 'CA': '+1', 'CC': '+61', 'CD': '+243', 'CF': '+236', 'CG': '+242', 'CH': '+41',
    'CI': '+225', 'CK': '+682', 'CL': '+56', 'CM': '+237', 'CN': '+86', 'CO': '+57', 'CR': '+506',
    'CU': '+53', 'CV': '+238', 'CW': '+599', 'CX': '+61', 'CY': '+357', 'CZ': '+420', 'DE': '+49',
    'DJ': '+253', 'DK': '+45', 'DM': '+1', 'DO': '+1', 'DZ': '+213', 'EC': '+593', 'EE': '+372',
    'EG': '+20', 'EH': '+212', 'ER': '+291', 'ES': '+34', 'ET': '+251', 'FI': '+358', 'FJ': '+679',
    'FK': '+500', 'FM': '+691', 'FO': '+298', 'FR': '+33', 'GA': '+241', 'GB': '+44', 'GD': '+1',
    'GE': '+995', 'GF': '+594', 'GG': '+44', 'GH': '+233', 'GI': '+350', 'GL': '+299', 'GM': '+220',
    'GN': '+224', 'GP': '+590', 'GQ': '+240', 'GR': '+30', 'GT': '+502', 'GU': '+1', 'GW': '+245',
    'GY': '+592', 'HK': '+852', 'HN': '+504', 'HR': '+385', 'HT': '+509', 'HU': '+36', 'ID': '+62',
    'IE': '+353', 'IL': '+972', 'IM': '+44', 'IN': '+91', 'IO': '+246', 'IQ': '+964', 'IR': '+98',
    'IS': '+354', 'IT': '+39', 'JE': '+44', 'JM': '+1', 'JO': '+962', 'JP': '+81', 'KE': '+254',
    'KG': '+996', 'KH': '+855', 'KI': '+686', 'KM': '+269', 'KN': '+1', 'KP': '+850', 'KR': '+82',
    'KW': '+965', 'KY': '+1', 'KZ': '+7', 'LA': '+856', 'LB': '+961', 'LC': '+1', 'LI': '+423',
    'LK': '+94', 'LR': '+231', 'LS': '+266', 'LT': '+370', 'LU': '+352', 'LV': '+371', 'LY': '+218',
    'MA': '+212', 'MC': '+377', 'MD': '+373', 'ME': '+382', 'MF': '+590', 'MG': '+261', 'MH': '+692',
    'MK': '+389', 'ML': '+223', 'MM': '+95', 'MN': '+976', 'MO': '+853', 'MP': '+1', 'MQ': '+596',
    'MR': '+222', 'MS': '+1', 'MT': '+356', 'MU': '+230', 'MV': '+960', 'MW': '+265', 'MX': '+52',
    'MY': '+60', 'MZ': '+258', 'NA': '+264', 'NC': '+687', 'NE': '+227', 'NF': '+672', 'NG': '+234',
    'NI': '+505', 'NL': '+31', 'NO': '+47', 'NP': '+977', 'NR': '+674', 'NU': '+683', 'NZ': '+64',
    'OM': '+968', 'PA': '+507', 'PE': '+51', 'PF': '+689', 'PG': '+675', 'PH': '+63', 'PK': '+92',
    'PL': '+48', 'PM': '+508', 'PR': '+1', 'PS': '+970', 'PT': '+351', 'PW': '+680', 'PY': '+595',
    'QA': '+974', 'RE': '+262', 'RO': '+40', 'RS': '+381', 'RU': '+7', 'RW': '+250', 'SA': '+966',
    'SB': '+677', 'SC': '+248', 'SD': '+249', 'SE': '+46', 'SG': '+65', 'SH': '+290', 'SI': '+386',
    'SK': '+421', 'SL': '+232', 'SM': '+378', 'SN': '+221', 'SO': '+252', 'SR': '+597', 'SS': '+211',
    'ST': '+239', 'SV': '+503', 'SX': '+1', 'SY': '+963', 'SZ': '+268', 'TC': '+1', 'TD': '+235',
    'TG': '+228', 'TH': '+66', 'TJ': '+992', 'TK': '+690', 'TL': '+670', 'TM': '+993', 'TN': '+216',
    'TO': '+676', 'TR': '+90', 'TT': '+1', 'TV': '+688', 'TW': '+886', 'TZ': '+255', 'UA': '+380',
    'UG': '+256', 'US': '+1', 'UY': '+598', 'UZ': '+998', 'VA': '+379', 'VC': '+1', 'VE': '+58',
    'VG': '+1', 'VI': '+1', 'VN': '+84', 'VU': '+678', 'WF': '+681', 'WS': '+685', 'XK': '+383',
    'YE': '+967', 'YT': '+262', 'ZA': '+27', 'ZM': '+260', 'ZW': '+263',
}

PRIMARY_COUNTRY_FOR_CODE = {
    '+1': 'US', '+7': 'RU', '+44': 'GB', '+61': 'AU', '+212': 'MA', '+358': 'FI',
}


def phone_country_choices():
    """(calling code, "Country name (+code)") pairs, one per unique calling code, sorted by
    country name - Portugal first, since this business is based there and it's the overwhelmingly
    likely default for anyone entering a phone number on this site."""
    from django_countries import countries

    names = dict(countries)
    by_code = {}
    for alpha2, code in CALLING_CODES.items():
        if alpha2 not in names:
            continue
        if code not in by_code or names[alpha2] < names[by_code[code]]:
            by_code[code] = alpha2
    # Second pass so PRIMARY_COUNTRY_FOR_CODE always wins, regardless of alphabetical order.
    for code, alpha2 in PRIMARY_COUNTRY_FOR_CODE.items():
        if code in by_code and alpha2 in names:
            by_code[code] = alpha2

    choices = [(code, f"{names[alpha2]} ({code})") for code, alpha2 in by_code.items()]
    choices.sort(key=lambda pair: pair[1])
    choices.sort(key=lambda pair: pair[0] != '+351')
    return choices


def split_phone(raw):
    """Splits a stored phone string into (calling_code, local_number) for prefilling the form -
    e.g. "+351 912345678" -> ("+351", "912345678"). Falls back to ("", raw) when the string
    doesn't start with a recognised calling code (no country selected, whole value shown in the
    local-number field) - covers both a blank/None phone and any legacy value saved before this
    dropdown existed."""
    raw = (raw or '').strip()
    if not raw.startswith('+'):
        return '', raw
    # E.164 calling codes are prefix-free by design (no code is a leading substring of another),
    # so at most one of these can ever match - the length-descending order is just defensive.
    for code in sorted(set(CALLING_CODES.values()), key=len, reverse=True):
        if raw == code:
            return code, ''
        if raw.startswith(code):
            return code, raw[len(code):].strip(' -')
    return '', raw


def join_phone(calling_code, local_number):
    """Inverse of split_phone - combines the dropdown's calling code and the free-text local
    number back into the single string the model's phone field stores. No calling code selected
    just stores the local number as-is (matches how every phone field in this codebase stores a
    single freeform string)."""
    local_number = (local_number or '').strip()
    calling_code = (calling_code or '').strip()
    if not calling_code:
        return local_number
    if not local_number:
        return calling_code
    return f"{calling_code} {local_number}"
