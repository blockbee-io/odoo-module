import logging
import re

from requests.models import PreparedRequest

from odoo import fields, models, api

from odoo.addons.payment_blockbee import const


_logger = logging.getLogger(__name__)

# Patterns used to scrub secrets out of anything that reaches the logs:
# - 'apikey' as a query-string parameter (request/response URLs),
# - 'success_token' as a query-string parameter (e.g. embedded in payment_url),
# - our per-checkout 'webhook_token' embedded in notify_url,
# - token values as JSON keys in raw response bodies.
_BLOCKBEE_REDACTIONS = (
    (re.compile(r'(apikey=)[^&#\s\'"]*', re.IGNORECASE), r'\g<1>***'),
    (re.compile(r'(success_token=)[^&#\s\'"]*', re.IGNORECASE), r'\g<1>***'),
    (re.compile(r'(webhook_token=)[^&#\s\'"]*', re.IGNORECASE), r'\g<1>***'),
    (re.compile(r'(success_token%3D)[^&\s\'"]*', re.IGNORECASE), r'\g<1>***'),
    (re.compile(r'(webhook_token%3D)[^&\s\'"]*', re.IGNORECASE), r'\g<1>***'),
    (re.compile(r'("success_token"\s*:\s*")[^"]*'), r'\g<1>***'),
    (re.compile(r'("webhook_token"\s*:\s*")[^"]*'), r'\g<1>***'),
)

# Payload dict keys whose values must never be logged verbatim.
_BLOCKBEE_SECRET_KEYS = ('apikey', 'success_token', 'webhook_token')


class _BlockbeeRedactedResponse:
    """Read-only proxy around a `requests.Response` that exposes redacted
    `url` and `text` attributes while delegating every other attribute to the
    wrapped response. This lets us pass a scrubbed view of the response to the
    core `_log_response` without copying or mutating the real response object.
    """

    def __init__(self, response, redactor):
        object.__setattr__(self, '_response', response)
        object.__setattr__(self, 'url', redactor(response.url))
        object.__setattr__(self, 'text', redactor(response.text))

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_response'), name)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('blockbee', "BlockBee")],
        ondelete={'blockbee': 'set default'}
    )

    blockbee_api_key = fields.Char(
        string='BlockBee API Key',
        required_if_provider='blockbee',
        groups='base.group_system',
    )

    # === COMPUTE METHODS ===#

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'blockbee').update({
            'support_tokenization': False,
            'support_refund': False,
            'support_manual_capture': False,
            'support_express_checkout': False,
        })

    def _get_default_payment_method_codes(self):
        """Return the payment methods activated with this Odoo 19 provider."""
        self.ensure_one()
        if self.code != 'blockbee':
            return super()._get_default_payment_method_codes()
        return {'blockbee'}

    def _get_reset_values(self):
        """Clear the API credential when an administrator resets the provider."""
        values = super()._get_reset_values()
        if self.code == 'blockbee':
            values['blockbee_api_key'] = None
        return values

    def _get_supported_currencies(self):
        """ Override of `payment` to return the supported currencies. """
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'blockbee':
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    def _build_request_url(self, endpoint, **kwargs):
        """Build the BlockBee request URL or fall back to the default for other providers."""
        if self.code != 'blockbee':
            return super()._build_request_url(endpoint, **kwargs)

        self.ensure_one()
        base_url = "https://api.blockbee.io/"
        # avoid double slashes
        endpoint = (endpoint or '').lstrip('/')
        return f"{base_url}{endpoint}"

    # === BLOCKBEE === #

    def _build_request_headers(self, method, endpoint, payload, **kwargs):
        """Send the API key in a header, never in the URL query string."""
        headers = super()._build_request_headers(method, endpoint, payload, **kwargs)
        if self.code == 'blockbee' and kwargs.get('blockbee_api_key'):
            headers['apikey'] = kwargs['blockbee_api_key']
        return headers

    def _blockbee_request(
        self, redirect_url, notify_url, api_key, value, parameters=None, bb_parameters=None,
    ):
        parameters = parameters or {}
        bb_parameters = bb_parameters or {}
        if parameters:
            req = PreparedRequest()
            req.prepare_url(notify_url, parameters)
            notify_url = req.url

        params = {
            'redirect_url': redirect_url,
            'notify_url': notify_url,
            'value': value,
            **bb_parameters
        }

        response = self._blockbee_process_request(
            endpoint='checkout/request/',
            params=params,
            api_key=api_key,
            reference=parameters.get('order_number'),
        )
        if isinstance(response, dict) and response.get('status') == 'success':
            return {
                'payment_id': response.get('payment_id'),
                'success_token': response.get('success_token'),
                'payment_url': response.get('payment_url'),
            }
        return None

    def _blockbee_process_request(self, endpoint, params, api_key, reference=None):
        self.ensure_one()
        response = self._send_api_request(
            method='GET',
            endpoint=endpoint,
            params=params,
            reference=reference,
            blockbee_api_key=api_key,
        )
        return response

    # === LOG REDACTION === #

    @api.model
    def _blockbee_redact(self, value):
        """Scrub BlockBee API and webhook secrets from a string.

        :param value: The value to redact; returned unchanged if not a str.
        :return: The redacted value.
        """
        if not isinstance(value, str):
            return value
        for pattern, replacement in _BLOCKBEE_REDACTIONS:
            value = pattern.sub(replacement, value)
        return value

    def _log_request(self, method, url, payload, *, reference=None):
        """Override of `payment` to redact secrets before core logs the request.

        Core `_log_request` pformat-logs the payload dict at INFO. The BlockBee
        payload contains the private webhook token inside notify_url, so a
        scrubbed copy is passed to `super()`.
        """
        if self.code != 'blockbee':
            return super()._log_request(method, url, payload, reference=reference)

        safe_url = self._blockbee_redact(url)
        if isinstance(payload, dict):
            safe_payload = {
                key: '***'
                if str(key).lower() in _BLOCKBEE_SECRET_KEYS
                else self._blockbee_redact(value)
                for key, value in payload.items()
            }
        else:
            safe_payload = self._blockbee_redact(payload)
        return super()._log_request(method, safe_url, safe_payload, reference=reference)

    def _log_response(self, response, *, reference=None):
        """Override of `payment` to redact secrets before core logs the response.

        Core `_log_response` logs `response.url` and `response.text` (which
        contains success_token) at INFO/ERROR. A proxy exposing redacted
        `url`/`text` is passed to `super()`; all other attributes pass through.
        """
        if self.code != 'blockbee':
            return super()._log_response(response, reference=reference)

        safe_response = _BlockbeeRedactedResponse(response, self._blockbee_redact)
        return super()._log_response(safe_response, reference=reference)
