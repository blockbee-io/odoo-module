import logging
import re

import requests
from requests.models import PreparedRequest
from werkzeug import urls

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

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
            'support_fees': True,
            'support_tokenization': False,
            'support_refund': False,
            'support_manual_capture': False,
            'support_express_checkout': False,
        })

    # === BLOCKBEE === #

    def _blockbee_request(
        self, redirect_url, notify_url, api_key, value, parameters=None, bb_parameters=None,
    ):
        """Create a BlockBee checkout and return its identifiers.

        :return: A dict with `payment_id`, `success_token`, and `payment_url`,
                 or None if BlockBee returned a non-success response.
        """
        self.ensure_one()
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

        url = urls.url_join(const.API_BASE_URL, 'checkout/request/')
        try:
            response = requests.get(
                url,
                params=params,
                # The API key travels in a header, never in the URL query
                # string: prepared URLs embedded in requests exceptions and
                # proxy/access logs must not contain it.
                headers={'apikey': api_key},
                timeout=const.API_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as error:
            # Prepared URLs inside requests exceptions contain the private
            # webhook token from notify_url. Scrub before logging and never
            # log the raw exception or traceback.
            _logger.warning(
                "BlockBee: API request failed: %s", self._blockbee_redact(str(error))
            )
            raise ValidationError(
                "BlockBee: " + _("Could not establish the connection to the API.")
            ) from None
        except ValueError:
            _logger.warning("BlockBee: API returned a non-JSON response")
            raise ValidationError(
                "BlockBee: " + _("The API returned an invalid response.")
            ) from None

        if not isinstance(payload, dict) or payload.get('status') != 'success':
            return None
        return {
            'payment_id': payload.get('payment_id'),
            'success_token': payload.get('success_token'),
            'payment_url': payload.get('payment_url'),
        }

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
