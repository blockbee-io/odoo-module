from odoo import models, fields


class PaymentBlockbee(models.Model):
    _name = 'blockbee.orders'
    _description = 'Created by BlockBee to store important order information (order number and success token used to confirm a payment)'

    order_number = fields.Char(size=32)
    # token is always 64 chars
    order_token = fields.Char(size=64)
    # field so IPN can check if order was marked as paid already.
    order_is_paid = fields.Boolean(default=False)
