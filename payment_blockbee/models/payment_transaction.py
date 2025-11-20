import logging
from decimal import Decimal

from markupsafe import Markup
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

        try:
            bb_resp = provider._blockbee_request(
                redirect_url=payload['return_url'],
                notify_url=payload['ipn_url'],
                api_key=api_key,
                value=payload['order_total'],
                parameters={
                    'order_number': payload['order_number'],
                },
                bb_parameters={
                    'item_description': f"Ref: {payload['order_number']}",
                },
            )
        except Exception:
            _logger.exception("BlockBee: error while creating checkout for tx %s", self.reference)
            raise ValidationError(_("BlockBee: failing to create a payment."))

        if not bb_resp:
            raise ValidationError(_("BlockBee: failing to create a payment."))

        payment_url = bb_resp.get('payment_url')
        success_token = bb_resp.get('success_token')

        if not payment_url:
            _logger.error(
                "BlockBee: missing payment_url in response for tx %s -> %s",
                self.reference, bb_resp,
            )
            raise ValidationError(_("BlockBee: failing to create a payment."))

        # Store success_token for traceability
        if success_token:
            self.provider_reference = success_token

        # Maintain blockbee.orders as a log table
        order_number = payload['order_number']
        blockbee_order = self.env['blockbee.orders'].sudo().search(
            [('order_number', '=', order_number)],
            limit=1,
        )
        if blockbee_order:
            blockbee_order.write({
                'order_token': success_token,
                'order_is_paid': False,
            })
        else:
            self.env['blockbee.orders'].sudo().create({
                'order_number': order_number,
                'order_token': success_token,
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
            'order_number': str(self.reference).split('-', 1)[0],
            'order_total': Decimal(str(self.amount)),
        }

    def _extract_reference(self, provider_code, payment_data):
        """Extract transaction reference from BlockBee data."""
        if provider_code != 'blockbee':
            return super()._extract_reference(provider_code, payment_data)

        ref = payment_data.get('order_number') or payment_data.get('reference')
        if not ref:
            _logger.warning("BlockBee: missing order_number/reference in payment_data: %s", payment_data)
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

    def _validate_amount(self, payment_data):
        """Verify that the payment amount matches the transaction amount."""
        if self.provider_code != 'blockbee':
            return super()._validate_amount(payment_data)

        self.ensure_one()
        
        # Convert payment_data to a plain dict, handling werkzeug MultiDict objects
        if hasattr(payment_data, 'to_dict'):
            # Handle werkzeug MultiDict/ImmutableMultiDict
            temp_dict = payment_data.to_dict(flat=False)
            # Flatten single-item lists (werkzeug MultiDict behavior)
            clean_data = {k: v[0] if isinstance(v, list) and len(v) == 1 else v 
                         for k, v in temp_dict.items()}
        else:
            # Create a fresh plain dict to avoid any subclass issues
            clean_data = {}
            clean_data.update(payment_data)
        
        # Map 'value' to 'amount' if 'amount' is missing
        if 'amount' not in clean_data:
            clean_data['amount'] = (
                clean_data.get('value') or
                clean_data.get('paid_amount_fiat') or 
                clean_data.get('paid_amount')
            )
        
        # Ensure 'amount' exists and is not None
        if 'amount' not in clean_data or clean_data['amount'] is None:
            _logger.error("BlockBee: Cannot determine amount from payment_data: %s", payment_data)
            raise ValidationError(_("BlockBee: Missing amount in payment notification"))
        
        # Convert amount to float for comparison
        try:
            payment_amount = float(clean_data['amount'])
        except (ValueError, TypeError):
            _logger.error("BlockBee: Invalid amount value '%s' in payment_data", clean_data.get('amount'))
            raise ValidationError(_("BlockBee: Invalid amount in payment notification"))
        
        # Validate that the payment amount matches the transaction amount
        # Allow small floating point differences (tolerance of 0.01)
        transaction_amount = float(self.amount)
        if abs(payment_amount - transaction_amount) > 0.01:
            _logger.warning(
                "BlockBee: Amount mismatch. Transaction: %s, Payment: %s",
                transaction_amount, payment_amount
            )
            raise ValidationError(
                _("BlockBee: Payment amount mismatch. Expected %s, got %s") % 
                (transaction_amount, payment_amount)
            )
        
        return True

    def _apply_updates(self, payment_data):
        """
        Update the transaction based on BlockBee notification data.

        This is the main hook used by `_process('blockbee', payment_data)`.
        """
        super()._apply_updates(payment_data)
        self.ensure_one()

        if self.provider_code != 'blockbee':
            return

        env = self.env

        status = payment_data.get('status') or payment_data.get('payment_status')
        success_token = payment_data.get('success_token')
        order_number = payment_data.get('order_number')
        paid_amount = payment_data.get('paid_amount_fiat') or payment_data.get('paid_amount')
        paid_coin = payment_data.get('paid_coin')
        address = payment_data.get('address')
        txid = payment_data.get('txid')

        # Use txid as provider_reference, fallback to success_token
        if txid:
            self.provider_reference = txid
        elif success_token and not self.provider_reference:
            self.provider_reference = success_token

        # Validate against blockbee.orders if present
        blockbee_order = self.env['blockbee.orders']
        if order_number:
            blockbee_order = env['blockbee.orders'].search(
                [('order_number', '=', order_number)],
                limit=1,
            )

        if blockbee_order:
            if success_token and blockbee_order.order_token and blockbee_order.order_token != success_token:
                _logger.error(
                    "BlockBee: token mismatch for order %s, expected %s got %s",
                    order_number, blockbee_order.order_token, success_token,
                )
                try:
                    self._set_error()
                except (TypeError, AttributeError):
                    self.write({'state': 'error'})
                return
        else:
            _logger.warning("BlockBee: no blockbee.orders row for order_number %s", order_number)

        # Set transaction state based on BlockBee status
        status_str = (status or '').lower()
        if status_str == 'done':
            try:
                self._set_done()
            except TypeError:
                # Fallback for Odoo versions where _set_done doesn't exist or has different signature
                self.write({'state': 'done'})
            if blockbee_order:
                blockbee_order.write({'order_is_paid': True})
        elif status_str in ('pending', 'waiting'):
            try:
                self._set_pending()
            except TypeError:
                self.write({'state': 'pending'})
        elif status_str in ('cancelled', 'canceled', 'expired', 'failed'):
            try:
                self._set_canceled()
            except TypeError:
                self.write({'state': 'cancel'})
        else:
            # Unknown status, just log and leave state as is
            _logger.warning("BlockBee: unknown status %s for order %s", status, order_number)

        # --- Sale order handling ---
        try:
            order = env['sale.order'].search([('name', '=', order_number)], limit=1) if order_number else env['sale.order']
            if order and order.state not in ['sale', 'done']:
                order.action_confirm()
                html_body = (
                    '<p>BlockBee Payment confirmed for order.</p>'
                    '<ul>'
                    '<li><strong>Amount:</strong> {amount} {coin}</li>'
                    '<li><strong>Address:</strong> {address}</li>'
                    '<li><strong>Success Token:</strong> {success_token}</li>'
                    '<li><strong>TXID:</strong> {txid}</li>'
                    '</ul>'
                ).format(
                    success_token=success_token or '',
                    amount=paid_amount or '',
                    coin=paid_coin or '',
                    address=address or '',
                    txid=txid or '',
                )
                order.message_post(body=Markup(html_body))
        except Exception as e:
            _logger.exception("BlockBee: error while processing sale.order %s: %s", order_number, e)

        try:
            invoice = env['account.move'].search(
                [('name', '=', order_number), ('move_type', '=', 'out_invoice')],
                limit=1,
            ) if order_number else env['account.move']

            if invoice and invoice.payment_state != 'paid':
                if blockbee_order:
                    blockbee_order.write({'order_is_paid': True})

                # Make sure the tx is done
                try:
                    self._set_done()
                except (TypeError, AttributeError):
                    self.write({'state': 'done'})

                # Post the invoice if still draft
                if invoice.state != 'posted':
                    try:
                        invoice.action_post()
                    except Exception:
                        invoice.write({'state': 'posted'})

                if invoice.payment_state != 'paid':
                    invoice.write({'payment_state': 'paid'})

                html_body = (
                    '<p>BlockBee Payment confirmed for invoice.</p>'
                    '<ul>'
                    '<li><strong>Amount:</strong> {amount} {coin}</li>'
                    '<li><strong>Address:</strong> {address}</li>'
                    '<li><strong>Success Token:</strong> {success_token}</li>'
                    '<li><strong>TXID:</strong> {txid}</li>'
                    '</ul>'
                ).format(
                    success_token=success_token or '',
                    amount=paid_amount or '',
                    coin=paid_coin or '',
                    address=address or '',
                    txid=txid or '',
                )
                invoice.message_post(body=Markup(html_body))
        except Exception as e:
            _logger.exception("BlockBee: error while processing invoice for %s: %s", order_number, e)