import logging
from odoo import http
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

            if blockbee_order['order_token'] != success_token or blockbee_order['order_number'] != order_number:
                raise Exception('Error due to token')

            order = request.env['sale.order'].sudo().search([('name', '=', order_number)], limit=1)

            if order:
                # Checking if is already paid to prevent doing anything else
                if order['state'] == 'done':
                    return '*ok*'

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

            invoice = request.env['account.move'].sudo().search([('name', '=', order_number), ('move_type', '=', 'out_invoice')], limit=1)

            if invoice:
                if invoice['payment_state'] == 'paid':
                    return '*ok*'

                blockbee_order.write({'order_is_paid': True})

                payment = request.env['payment.transaction'].sudo().search([('reference', '=', order_number)], limit=1)

                payment.write({'state': 'done'})

                invoice.write({'state': 'posted'})
                invoice.write({'payment_state': 'paid'})

                invoice.message_post(
                    body='BlockBee Payment confirmed for invoice. <ul>'
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
        except Exception:
            return 'Error'
