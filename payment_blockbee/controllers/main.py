import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class BlockBeeController(http.Controller):
    @http.route(
        ['/payment/blockbee/return'],
        type='http',
        auth='public',
        csrf=False
    )
    def blockbee_return(self, **data):
        return request.redirect('/payment/status')

    @http.route(
        ['/payment/blockbee/webhook'],
        type='http',
        auth='public',
        methods=['GET', 'POST'],
        csrf=False,
        save_session=False
    )
    def blockbee_ipn(self, **data):
        _logger.info('BlockBee Webhook received: %s', data)

        try:
            request.env['payment.transaction'].sudo()._process('blockbee', data)
        except Exception:
            _logger.exception('BlockBee: error while handling IPN')
            return http.Response(status=400)

        # Return 200 so BlockBee knows we processed it successfully
        return http.Response("*ok*", status=200, content_type="text/plain")
