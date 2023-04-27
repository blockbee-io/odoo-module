import json
import logging
import pprint
from _decimal import Decimal

from werkzeug import urls

from odoo import _, models, api
from odoo.http import request
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)
from odoo.addons.payment_blockbee.controllers.main import BlockBeeController


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _get_specific_rendering_values(self, processing_values):
        """ Override of `payment` to return BlockBee-specific rendering values.
        Note: self.ensure_one() from `_get_rendering_values`.
        :param dict processing_values: The generic and specific processing values of the transaction
        :return: The dict of provider-specific processing values.
        :rtype: dict
        """

        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'blockbee':
            return res

        # Initiate the payment and retrieve the payment link data.
        api_key = self.env['payment.provider'].search([('code', '=', 'blockbee')]).blockbee_api_key

        payload = self._blockbee_payload()

        _logger.info(
            "Sending '/checkout/preferences' request for link creation:\n%s",
            pprint.pformat(payload),
        )

        try:
            # Request the Payment URL to BlockBee
            _request = self.provider_id._blockbee_request(
                redirect_url=request.httprequest.host_url,
                notify_url=payload['ipn_url'],
                api_key=api_key,
                value=payload['order_total'],
                parameters={
                    'order_number': payload['order_number']
                },
                bb_parameters={
                    'item_description': 'Ref: {ref}'.format(ref=payload['order_number'])
                }
            )

            if _request:
                api_url = _request['payment_url']
                order_number = payload['order_number']
                blockbee_order = self.env['blockbee.orders'].sudo().search([('order_number', '=', order_number)], limit=1)

                # Checks if order row already exists, if not creates new
                if blockbee_order['order_number'] != order_number:
                    self.env['blockbee.orders'].sudo().create({
                        'order_number': order_number,
                        'order_token': _request['success_token'],
                        'order_is_paid': False
                    })
                else:
                    blockbee_order.write({'order_token': _request['success_token']})

                # Extract the payment link URL and embed it in the redirect form.
                rendering_values = {
                    'api_url': api_url,
                }
                return rendering_values

        except Exception:
            raise ValidationError(
                "BlockBee: " + _("Failing to create a payment.")
            )

    def _blockbee_payload(self):
        base_url = self.provider_id.get_base_url()
        ipn_url = urls.url_join(base_url, BlockBeeController._ipn_url)

        amount = self.amount

        # Calculating the fee set by the customer
        fee_amount = self.provider_id._compute_fees(
            self.amount,
            self.currency_id,
            self.partner_country_id
        )

        if fee_amount is not None:
            amount = amount + fee_amount

        return {
            'ipn_url': ipn_url,
            'order_number': str(self.reference).split('-', 1)[0],
            'order_total': Decimal(amount)
        }
