import hmac
import logging
import math
import secrets
from decimal import Decimal

from werkzeug import urls

from odoo import _, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

BLOCKBEE_IPN_PATH = '/payment/blockbee/webhook'
BLOCKBEE_RETURN_PATH = '/payment/blockbee/return'


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    # -------------------------------------------------------------------------
    # CHECKOUT CREATION
    # -------------------------------------------------------------------------
    def _get_specific_rendering_values(self, processing_values):
        """Return BlockBee specific rendering values."""
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'blockbee':
            return res

        self.ensure_one()
        provider = self.provider_id

        api_key = provider.blockbee_api_key
        if not api_key:
            raise ValidationError(_("BlockBee API key is missing on the payment provider."))

        payload = self._blockbee_payload()

        # Rendering can be triggered more than once (for example by a browser
        # retry). Serialize checkout creation for this transaction so two
        # workers cannot create different payable links and invalidate each
        # other's webhook credentials.
        self.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM payment_transaction WHERE id = %s FOR UPDATE",
            [self.id],
        )
        blockbee_order = self.env['blockbee.orders'].sudo().search(
            [('transaction_id', '=', self.id)],
            limit=1,
        )
        if self.state == 'done' or (blockbee_order and blockbee_order.order_is_paid):
            raise ValidationError(_("BlockBee: this payment is already completed."))

        if blockbee_order and all((
            blockbee_order.order_token,
            blockbee_order.payment_id,
            blockbee_order.payment_url,
        )):
            if (
                blockbee_order.order_number != self.reference
                or blockbee_order.provider_id != provider
                or blockbee_order.company_id != self.company_id
                or (
                    self.provider_reference
                    and self.provider_reference != blockbee_order.payment_id
                )
            ):
                raise ValidationError(_("BlockBee: invalid stored checkout identity."))
            self.provider_reference = blockbee_order.payment_id
            res['api_url'] = blockbee_order.payment_url
            return res

        webhook_token = secrets.token_urlsafe(32)

        try:
            bb_resp = provider._blockbee_request(
                redirect_url=payload['return_url'],
                notify_url=payload['ipn_url'],
                api_key=api_key,
                value=payload['order_total'],
                parameters={
                    'order_number': payload['order_number'],
                    # BlockBee preserves custom notify_url query parameters in
                    # the signed webhook. This secret is independent from the
                    # browser-facing success_token returned by checkout/request.
                    'webhook_token': webhook_token,
                },
                bb_parameters={
                    'item_description': f"Ref: {payload['order_number']}",
                    'currency': payload['currency'].lower(),
                },
            )
        except ValidationError as e:
            # The request URL contains the private webhook token inside the
            # notify_url parameter. Scrub all BlockBee secrets before logging.
            safe_msg = provider._blockbee_redact(str(e))
            _logger.error(
                "BlockBee: error while creating checkout for tx %s: %s",
                self.reference, safe_msg,
            )
            raise ValidationError(_("BlockBee: failing to create a payment.")) from None

        if not bb_resp:
            raise ValidationError(_("BlockBee: failing to create a payment."))

        payment_url = bb_resp.get('payment_url')
        success_token = bb_resp.get('success_token')
        payment_id = bb_resp.get('payment_id')

        if not payment_url or not success_token or not payment_id:
            # Never log the raw response body (it contains success_token); the
            # response keys are enough to investigate.
            _logger.error(
                "BlockBee: incomplete checkout response "
                "for tx %s (response keys: %s)",
                self.reference, list(bb_resp.keys()),
            )
            raise ValidationError(_("BlockBee: failing to create a payment."))

        # payment_id is BlockBee's canonical, non-secret checkout identifier.
        # Keep it as the provider reference; never replace it with webhook data.
        self.provider_reference = payment_id

        # Maintain the private webhook-authentication/idempotency record.
        order_number = payload['order_number']
        if blockbee_order:
            blockbee_order.write({
                'order_number': order_number,
                'order_token': webhook_token,
                'payment_id': payment_id,
                'payment_url': payment_url,
                'order_is_paid': False,
            })
        else:
            self.env['blockbee.orders'].sudo().create({
                'transaction_id': self.id,
                'order_number': order_number,
                'order_token': webhook_token,
                'payment_id': payment_id,
                'payment_url': payment_url,
                'order_is_paid': False,
            })

        res.update({
            'api_url': payment_url,
        })
        return res

    def _blockbee_payload(self):
        """Build payload for BlockBee checkout creation."""
        self.ensure_one()
        base_url = self.provider_id.get_base_url()
        ipn_url = urls.url_join(base_url, BLOCKBEE_IPN_PATH)
        return_url = urls.url_join(base_url, BLOCKBEE_RETURN_PATH)

        return {
            'ipn_url': ipn_url,
            'return_url': return_url,
            # Full reference: this exact value is echoed back by BlockBee's IPN
            # and is the single key used for the tx lookup (_search_by_reference)
            # and the blockbee.orders token row. Never truncate it, or the
            # authenticity guard cannot find its row.
            'order_number': str(self.reference),
            'order_total': Decimal(str(self.amount)),
            'currency': self.currency_id.name,
        }

    def _extract_reference(self, provider_code, payment_data):
        """Extract transaction reference from BlockBee data."""
        if provider_code != 'blockbee':
            return super()._extract_reference(provider_code, payment_data)

        ref = payment_data.get('order_number') or payment_data.get('reference')
        if not ref:
            # Log only the keys: the payload may carry a success_token.
            _logger.warning(
                "BlockBee: missing order_number/reference in payment_data (keys: %s)",
                list(payment_data.keys()),
            )
        return ref

    def _search_by_reference(self, provider_code, payment_data):
        """Find the transaction for the given BlockBee payment data."""
        if provider_code != 'blockbee':
            return super()._search_by_reference(provider_code, payment_data)

        reference = self._extract_reference(provider_code, payment_data)
        if not reference:
            return self.env['payment.transaction']

        tx = self.search([
            ('reference', '=', reference),
            ('provider_code', '=', 'blockbee'),
        ], limit=1)

        if not tx:
            _logger.warning("BlockBee: no transaction found for reference %s", reference)

        return tx

    def _extract_amount_data(self, payment_data):
        """Extract BlockBee's signed checkout amount and FIAT currency."""
        if self.provider_code != 'blockbee':
            return super()._extract_amount_data(payment_data)

        self.ensure_one()
        # Odoo calls this hook before performing amount/currency validation,
        # so authenticate first to ensure invalid data cannot even move the
        # transaction into the error state.
        self._validate_blockbee_notification(payment_data)
        return self._parse_blockbee_amount_data(payment_data)

    def _parse_blockbee_amount_data(self, payment_data):
        """Parse a previously authenticated BlockBee amount and currency."""
        amount = payment_data.get('value')
        currency_code = payment_data.get('currency')
        try:
            amount = float(amount) if amount is not None else None
        except (ValueError, TypeError):
            _logger.warning("BlockBee: rejected webhook with an invalid amount")
            raise ValidationError(_("BlockBee: Invalid amount in payment notification"))
        if amount is None or not math.isfinite(amount) or amount <= 0:
            _logger.warning("BlockBee: rejected webhook with a non-positive or non-finite amount")
            raise ValidationError(_("BlockBee: Invalid amount in payment notification"))
        if not isinstance(currency_code, str) or not currency_code.strip():
            _logger.warning("BlockBee: rejected webhook with an invalid currency")
            raise ValidationError(_("BlockBee: Invalid currency in payment notification"))
        return {
            'amount': amount,
            'currency_code': currency_code.strip().upper(),
        }

    def _validate_blockbee_notification(self, payment_data):
        """Fail closed unless the signed webhook identifies this checkout."""
        self.ensure_one()

        if payment_data.get('_blockbee_signature_verified') is not True:
            _logger.warning(
                "BlockBee: rejected unsigned processing attempt for tx %s",
                self.reference,
            )
            raise ValidationError(_("BlockBee: invalid webhook signature"))

        order_number = payment_data.get('order_number')
        webhook_token = payment_data.get('webhook_token')
        payment_id = payment_data.get('payment_id')
        blockbee_order = self.env['blockbee.orders'].sudo().search(
            [('transaction_id', '=', self.id)],
            limit=1,
        )

        stored_token = (blockbee_order.order_token or '') if blockbee_order else ''
        stored_payment_id = (blockbee_order.payment_id or '') if blockbee_order else ''
        provider_reference = self.provider_reference or ''
        if (
            order_number != self.reference
            or not blockbee_order
            or blockbee_order.order_number != self.reference
            or blockbee_order.provider_id != self.provider_id
            or blockbee_order.company_id != self.company_id
            or not stored_token
            or not webhook_token
            or not hmac.compare_digest(str(stored_token), str(webhook_token))
            or not stored_payment_id
            or not provider_reference
            or not hmac.compare_digest(str(stored_payment_id), str(provider_reference))
            or not payment_id
            or not hmac.compare_digest(str(stored_payment_id), str(payment_id))
        ):
            _logger.warning(
                "BlockBee: rejected webhook failing transaction identity checks for tx %s",
                self.reference,
            )
            raise ValidationError(_("BlockBee: invalid payment notification"))

        return blockbee_order

    def _apply_updates(self, payment_data):
        """
        Update the transaction based on BlockBee notification data.

        This is the main hook used by `_process('blockbee', payment_data)`.
        """
        if self.provider_code != 'blockbee':
            return super()._apply_updates(payment_data)

        self.ensure_one()

        order_number = payment_data.get('order_number')

        # --- Mandatory fail-closed authenticity check ------------------------
        # Signature verification proves that the payload came from BlockBee.
        # The per-checkout secret and payment_id also bind it to this exact Odoo
        # transaction. Authenticity requires ALL of:
        #   order_number present -> matching blockbee.orders row -> non-empty
        #   stored order_token -> non-empty webhook_token matching it -> stored
        #   payment_id -> incoming payment_id matching it
        # (constant-time). Otherwise processing raises before changing state.
        blockbee_order = self._validate_blockbee_notification(payment_data)

        # Odoo's generic validator returns early only when it *newly* moves a
        # transaction to `error`. If a transaction was already errored, core
        # continues to this hook even after another mismatch. Reassert the
        # amount and currency at the success boundary while still allowing a
        # later correct notification to recover the transaction.
        amount_data = self._parse_blockbee_amount_data(payment_data)
        if (
            self.currency_id.compare_amounts(amount_data['amount'], self.amount) != 0
            or amount_data['currency_code'] != self.currency_id.name
        ):
            _logger.warning(
                "BlockBee: rejected completed webhook with an amount or currency mismatch"
            )
            raise ValidationError(
                _("BlockBee: payment amount or currency does not match the transaction")
            )

        status = payment_data.get('status') or payment_data.get('payment_status')
        is_paid = payment_data.get('is_paid')
        if str(status or '').lower() != 'done' or str(is_paid) != '1':
            _logger.warning(
                "BlockBee: verified webhook for order %s is not a completed payment",
                order_number,
            )
            return

        # Lock and refresh the helper row before checking the idempotency bit.
        # Concurrent callback workers then process a completed payment exactly
        # once; later workers observe the committed flag and acknowledge it.
        blockbee_order.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM blockbee_orders WHERE id = %s FOR UPDATE",
            [blockbee_order.id],
        )
        blockbee_order.invalidate_recordset(['order_is_paid'])
        if blockbee_order.order_is_paid:
            _logger.info(
                "BlockBee: payment for order %s is already processed; ignoring replay",
                order_number,
            )
            return

        # Use Odoo's guarded state machine. Odoo's normal post-processing then
        # handles linked sale orders and, when account_payment is installed,
        # posts invoices, creates account.payment, and reconciles receivables.
        # Never write sale/order/invoice/accounting states directly here.
        self._set_done(extra_allowed_states=('cancel',))
        if self.state != 'done':
            raise ValidationError(_("BlockBee: could not confirm payment transaction"))
        blockbee_order.write({'order_is_paid': True})
