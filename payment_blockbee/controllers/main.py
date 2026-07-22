import base64
import binascii
import logging
from urllib.parse import urlsplit

import requests
from cryptography import exceptions as cryptography_exceptions
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment_blockbee import const

_logger = logging.getLogger(__name__)

_BLOCKBEE_PUBLIC_KEY_TIMEOUT = 3
_BLOCKBEE_WEBHOOK_PATH = '/payment/blockbee/webhook'


def _get_blockbee_public_key():
    """Fetch and validate BlockBee's current webhook key for this request."""
    try:
        response = requests.get(
            const.WEBHOOK_PUBLIC_KEY_URL,
            timeout=_BLOCKBEE_PUBLIC_KEY_TIMEOUT,
            allow_redirects=False,
        )
        response.raise_for_status()
        response_data = response.json()
        if (
            not isinstance(response_data, dict)
            or response_data.get('status') != 'success'
            or not response_data.get('pubkey')
        ):
            raise ValueError("BlockBee returned an invalid public-key response")
        public_key = serialization.load_pem_public_key(
            response_data['pubkey'].encode('ascii')
        )
        if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 1024:
            raise ValueError("BlockBee returned an invalid RSA public key")
    except (
        requests.RequestException,
        cryptography_exceptions.UnsupportedAlgorithm,
        TypeError,
        ValueError,
    ):
        # Verification must fail closed if the authoritative key endpoint is
        # unavailable or malformed. There is intentionally no cached or
        # embedded fallback key.
        _logger.warning("BlockBee: could not fetch a valid webhook public key")
        return None

    return public_key


def _signature_matches(public_key, signature, signed_data):
    """Return whether an RSA key validates a BlockBee webhook signature."""
    try:
        public_key.verify(
            signature,
            signed_data,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except cryptography_exceptions.InvalidSignature:
        return False
    return True


def _get_raw_request_target(http_request):
    """Return the original path and query without re-encoding query values."""
    environ = getattr(http_request, 'environ', {}) or {}
    raw_uri = environ.get('RAW_URI') or environ.get('REQUEST_URI')
    if isinstance(raw_uri, bytes):
        raw_uri = raw_uri.decode('latin-1')

    if raw_uri:
        parsed_uri = urlsplit(raw_uri)
        path = parsed_uri.path or _BLOCKBEE_WEBHOOK_PATH
        query = parsed_uri.query or environ.get('QUERY_STRING', '')
        if isinstance(query, bytes):
            query = query.decode('latin-1')
    else:
        parsed_url = urlsplit(http_request.url)
        path = parsed_url.path or _BLOCKBEE_WEBHOOK_PATH
        query = environ.get('QUERY_STRING', parsed_url.query)
        if isinstance(query, bytes):
            query = query.decode('latin-1')

    return f"{path}?{query}" if query else path


def _get_blockbee_signed_data_candidates():
    """Build the possible external URLs without changing the raw query string.

    BlockBee signs the public URL supplied during checkout. Behind a reverse
    proxy, Werkzeug's ``request.url`` can contain an internal scheme/host even
    though the callback was sent to the public HTTPS URL. Signature checks are
    therefore attempted against a small set of equivalent external origins;
    every candidate still has to validate cryptographically.
    """
    http_request = request.httprequest
    request_target = _get_raw_request_target(http_request)
    candidates = []

    def add_candidate(value):
        if not value or not isinstance(value, str):
            return
        encoded_value = value.encode('utf-8')
        if encoded_value not in candidates:
            candidates.append(encoded_value)

    def add_origin(scheme, host):
        scheme = (scheme or '').lower().strip()
        host = (host or '').strip()
        if (
            scheme not in {'http', 'https'}
            or not host
            or len(host) > 255
            or any(char in host for char in '/\\\r\n')
        ):
            return
        add_candidate(f"{scheme}://{host}{request_target}")

    # First try Werkzeug's absolute URL for direct/non-proxied deployments.
    add_candidate(http_request.url)

    headers = http_request.headers
    forwarded_proto = (headers.get('X-Forwarded-Proto') or '').split(',', 1)[0]
    forwarded_host = (headers.get('X-Forwarded-Host') or '').split(',', 1)[0]
    host = (headers.get('Host') or '').split(',', 1)[0]
    request_scheme = getattr(http_request, 'scheme', '')

    # Standard reverse-proxy combinations. Some proxies preserve Host but only
    # forward the original scheme, while others forward both values.
    add_origin(forwarded_proto, forwarded_host)
    add_origin(forwarded_proto, host)
    add_origin(request_scheme, host)

    # This is the same configured origin used by get_base_url() when the
    # checkout notify_url is created, and works even when proxy headers are not
    # available to Odoo.
    env = getattr(request, 'env', None)
    if env is not None:
        try:
            public_base_url = env['ir.config_parameter'].sudo().get_param('web.base.url')
        except (AttributeError, KeyError, TypeError):
            public_base_url = None
        if public_base_url:
            parsed_base_url = urlsplit(public_base_url)
            add_origin(parsed_base_url.scheme, parsed_base_url.netloc)

    return candidates


class BlockBeeController(http.Controller):
    @http.route(
        ['/payment/blockbee/return'],
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False,
        save_session=False,
    )
    def blockbee_return(self, **data):
        return request.redirect('/payment/status')

    @http.route(
        [_BLOCKBEE_WEBHOOK_PATH],
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False,
        save_session=False
    )
    def blockbee_ipn(self, **data):
        if not self._verify_webhook_signature():
            _logger.warning("BlockBee: rejected webhook with missing or invalid signature")
            return http.Response(status=403)

        _logger.info(
            "BlockBee: webhook received (order_number=%s, status=%s)",
            data.get('order_number'),
            data.get('status') or data.get('payment_status'),
        )

        # This internal marker is added only after verifying the signature and
        # is required again in the model. It prevents another controller or
        # integration from accidentally processing a notification without
        # performing the authenticity check first.
        data['_blockbee_signature_verified'] = True

        try:
            request.env['payment.transaction'].sudo()._handle_notification_data(
                'blockbee', data
            )
        except ValidationError:
            _logger.warning('BlockBee: rejected invalid payment notification')
            return http.Response(status=400)

        # Return 200 so BlockBee knows we processed it successfully
        return http.Response("*ok*", status=200, content_type="text/plain")

    @staticmethod
    def _verify_webhook_signature():
        """Verify BlockBee's RSA-SHA256 signature over the full callback URL."""
        signature_b64 = request.httprequest.headers.get('x-ca-signature')
        if not signature_b64:
            return False

        try:
            signature = base64.b64decode(signature_b64, validate=True)
            signed_data_candidates = _get_blockbee_signed_data_candidates()
        except (binascii.Error, ValueError):
            return False

        public_key = _get_blockbee_public_key()
        if not public_key:
            return False
        return any(
            _signature_matches(public_key, signature, signed_data)
            for signed_data in signed_data_candidates
        )
