import logging
import json
from odoo import _, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class BlockBeeController(http.Controller):
    _ipn_url = '/payment/blockbee/ipn'

    @http.route(_ipn_url, type='http', auth='public', methods=['GET'], csrf=False, save_session=False)
    def _blockbee_ipn(self, **data):
        try:
            # Getting data from the GET parameters in the request (this will be all the data we need)
            success_token = data['success_token']
            order_number = data['order_number']

            # Fetching Odoo and BlockBee order data from DB
            blockbee_order = request.env['blockbee.orders'].sudo().search([('order_number', '=', order_number)], limit=1)
            order = request.env['sale.order'].sudo().with_context(check_access_rights=True).search(
                [('name', '=', order_number)], limit=1)

            # Checking if is already paid to prevent doing anything else
            if order['state'] == 'done':
                return '*ok*'

            if blockbee_order['order_token'] != success_token or blockbee_order['order_number'] != order_number:
                raise Exception


            # Here will be marking the order as paid and updating the row
            blockbee_order.write({'order_is_paid': True})
            order.write({'state': 'done'})

            order.message_post(
                body='BlockBee Payment confirmed for order. <ul>'
                     '<li><strong>Amount:</strong> {amount} {coin}</li>'
                     '<li><strong>Address:</strong> {address}</li>'
                     '<li><strong>Success Token:</strong> {success_token}</li>'
                     '<li><strong>TXID:</strong> {txid}</li>'
                     '</ul>'
                .format(
                    success_token=success_token,
                    amount=data['paid_amount'],
                    coin=data['paid_coin'],
                    address=data['address'],
                    txid=data['txid'],
                )
            )

            # If everything was confirmed correctly, we return '*ok*' response so BlockBee doesn't send any more callbacks.
            return '*ok*'
        except:
            return 'Error'
