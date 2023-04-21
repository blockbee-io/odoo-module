import json
import logging
import requests

from requests.models import PreparedRequest

from odoo import fields, models, api

_logger = logging.getLogger(__name__)

"""
@todo: order is not being created for some reason
"""


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('blockbee', "BlockBee")],
        ondelete={'blockbee': 'set default'}
    )
    blockbee_api_key = fields.Char(string='BlockBee API Key')

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'blockbee').update({
            'support_fees': True,
            'support_tokenization': False,
            'support_refund': False,
            'support_express_checkout': False,
        })

    @api.model
    def _get_payment_method_information(self):
        res = super()._get_payment_method_information()
        res['blockbee'] = {'mode': 'unique', 'domain': [('type', '=', 'bank')]}
        return res

    def _blockbee_get_api_url(self):
        """
        Return the API URL.
        """
        self.ensure_one()
        return {
            'host': 'api.blockbee.io',
            'url': 'https://api.blockbee.io/'
        }

    def _blockbee_request(self, redirect_url, notify_url, api_key, value, parameters={}, bb_parameters={}):
        if parameters:
            req = PreparedRequest()
            req.prepare_url(notify_url, parameters)
            notify_url = req.url

        params = {
            'redirect_url': redirect_url,
            'notify_url': notify_url,
            'apikey': api_key,
            'value': value,
            **bb_parameters
        }

        _request = self._blockbee_process_request(endpoint='checkout/request', params=params)
        if _request['status'] == 'success':
            return {
                'success_token': _request['success_token'],
                'payment_url': _request['payment_url']
            }
        return None

    def _blockbee_process_request(self, endpoint, params):
        response = requests.get(
            url="{base_url}{endpoint}/".format(
                base_url=self._blockbee_get_api_url()['url'],
                endpoint=endpoint,
            ),
            params=params,
            headers={'Host': self._blockbee_get_api_url()['host']},
        )

        return response.json()

    def _blockbee_search_records(self, order_number):
        return self.env['blockbee.orders'].search([('order_number', 'in', order_number)], limit=1)
