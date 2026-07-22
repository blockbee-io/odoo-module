

SUPPORTED_CURRENCIES = [
    'AED',
    'AUD',
    'BRL',
    'BGN',
    'CAD',
    'CNY',
    'CZK',
    'DKK',
    'EUR',
    'GBP',
    'HKD',
    'HUF',
    'INR',
    'IDR',
    'JPY',
    'MYR',
    'MXN',
    'NGN',
    'NOK',
    'PHP',
    'PLN',
    'RON',
    'RUB',
    'SEK',
    'SGD',
    'THB',
    'TRY',
    'USD',
    'ZAR',
]

# List of countries where BlockBee is supported.
# KYC is locked to supported countries and without it user cannot get an API Key.
# Will leave here if needed.
SUPPORTED_COUNTRIES = {
    'AE',
    'AU',
    'BR',
    'BG',
    'CA',
    'CN',
    'HR',
    'CZ',
    'DK',
    'EE',
    'FI',
    'FR',
    'DE',
    'GR',
    'HK',
    'HU',
    'IN',
    'ID',
    'IE',
    'IT',
    'JP',
    'LV',
    'LT',
    'LU',
    'MY',
    'MT',
    'MX',
    'NL',
    'NG',
    'NO',
    'PH',
    'PL',
    'PT',
    'RO',
    'RU',
    'SG',
    'SK',
    'SI',
    'ZA',
    'KR',
    'ES',
    'SE',
    'CH',
    'TW',
    'TH',
    'TR',
    'GB',
    'US',
    'UG',
}


# BlockBee API host. Hard-coded HTTPS: no user-controlled outbound target.
API_BASE_URL = 'https://api.blockbee.io/'

# Connect/read timeout in seconds for BlockBee API requests.
API_TIMEOUT = 10

# Authoritative source for BlockBee's current webhook-signing public key.
WEBHOOK_PUBLIC_KEY_URL = 'https://api.blockbee.io/pubkey/'
