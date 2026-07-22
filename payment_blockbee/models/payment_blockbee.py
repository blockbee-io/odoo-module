from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PaymentBlockbee(models.Model):
    _name = 'blockbee.orders'
    _description = 'BlockBee Webhook Authentication Record'
    _rec_name = 'order_number'
    _check_company_auto = True

    transaction_id = fields.Many2one(
        comodel_name='payment.transaction',
        string='Payment Transaction',
        required=True,
        index=True,
        copy=False,
        readonly=True,
        ondelete='cascade',
        check_company=True,
    )
    provider_id = fields.Many2one(
        related='transaction_id.provider_id',
        store=True,
        index=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        related='transaction_id.company_id',
        store=True,
        index=True,
        readonly=True,
    )

    # Full payment.transaction reference. It must match the order_number echoed
    # back by BlockBee's webhook exactly; references are not length-limited by
    # Odoo, so this field must not truncate them either.
    order_number = fields.Char(required=True, index=True, copy=False)
    # Per-checkout secret embedded in notify_url and returned unchanged by
    # BlockBee. Access is restricted even for users who can read the model.
    order_token = fields.Char(required=True, copy=False, groups='base.group_system')
    # Canonical BlockBee checkout identifier. This is matched on every webhook
    # and is also the idempotency key recommended by BlockBee.
    payment_id = fields.Char(required=True, index=True, copy=False)
    # Persist the first checkout URL so repeated rendering reuses the same
    # payment rather than creating a second payable BlockBee checkout. The URL
    # may contain a browser token, so expose it only to system administrators.
    payment_url = fields.Char(required=True, copy=False, groups='base.group_system')
    order_is_paid = fields.Boolean(default=False, copy=False)

    _sql_constraints = [
        (
            'transaction_uniq',
            'unique(transaction_id)',
            'A BlockBee checkout already exists for this payment transaction.',
        ),
        (
            'payment_id_uniq',
            'unique(payment_id)',
            'A BlockBee payment identifier must be unique.',
        ),
    ]

    @api.constrains('transaction_id', 'order_number', 'payment_id')
    def _check_checkout_identity(self):
        """Require new checkout records to be bound to their exact transaction."""
        for order in self:
            if not order.transaction_id:
                raise ValidationError(_("A BlockBee checkout must have a transaction."))
            if order.transaction_id.provider_code != 'blockbee':
                raise ValidationError(_("The transaction must use the BlockBee provider."))
            if order.order_number != order.transaction_id.reference:
                raise ValidationError(_(
                    "The BlockBee order reference does not match the transaction."
                ))
            if (
                order.payment_id
                and order.transaction_id.provider_reference
                and order.payment_id != order.transaction_id.provider_reference
            ):
                raise ValidationError(_(
                    "The BlockBee payment identifier does not match the transaction."
                ))
