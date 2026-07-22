import base64
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.payment.tests.common import PaymentCommon
from odoo.addons.payment_blockbee import const
from odoo.addons.payment_blockbee.controllers import main as blockbee_controller
from odoo.addons.payment_blockbee.models import payment_provider as blockbee_provider


@tagged('post_install', '-at_install')
class TestBlockBeeSecurity(PaymentCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls._prepare_provider(
            'blockbee',
            update_values={'blockbee_api_key': 'test-api-key'},
        )
        cls.currency = cls.currency_euro

    def _create_blockbee_tx(self, reference='SECURITY-ORDER-1'):
        return self._create_transaction('redirect', reference=reference)

    def _create_webhook_data(self, tx, **overrides):
        webhook_token = 'merchant-generated-webhook-secret'
        payment_id = 'blockbee-payment-id'
        tx.provider_reference = payment_id
        order = self.env['blockbee.orders'].sudo().create({
            'transaction_id': tx.id,
            'order_number': tx.reference,
            'order_token': webhook_token,
            'payment_id': payment_id,
            'payment_url': 'https://pay.blockbee.io/test-checkout',
        })
        data = {
            '_blockbee_signature_verified': True,
            'order_number': tx.reference,
            'webhook_token': webhook_token,
            'payment_id': payment_id,
            'value': str(tx._blockbee_total_amount()),
            'currency': tx.currency_id.name,
            'status': 'done',
            'is_paid': '1',
        }
        data.update(overrides)
        return order, data

    def _handle(self, data):
        return self.env['payment.transaction'].sudo()._handle_notification_data(
            'blockbee', data
        )

    def _enable_fixed_fees(self, amount=5.0):
        self.provider.write({
            'fees_active': True,
            'fees_dom_fixed': amount,
            'fees_dom_var': 0.0,
            'fees_int_fixed': amount,
            'fees_int_var': 0.0,
        })

    def test_unsigned_notification_is_rejected_without_state_change(self):
        tx = self._create_blockbee_tx()
        order, data = self._create_webhook_data(
            tx, _blockbee_signature_verified=False,
        )

        with self.assertRaises(ValidationError):
            self._handle(data)

        self.assertEqual(tx.state, 'draft')
        self.assertFalse(order.order_is_paid)

    def test_missing_webhook_token_is_rejected_without_state_change(self):
        tx = self._create_blockbee_tx()
        order, data = self._create_webhook_data(tx, webhook_token=None)

        with self.assertRaises(ValidationError):
            self._handle(data)

        self.assertEqual(tx.state, 'draft')
        self.assertFalse(order.order_is_paid)

    def test_missing_authentication_record_is_rejected_without_state_change(self):
        tx = self._create_blockbee_tx()
        data = {
            '_blockbee_signature_verified': True,
            'order_number': tx.reference,
            'webhook_token': 'merchant-generated-webhook-secret',
            'payment_id': 'blockbee-payment-id',
            'value': str(tx.amount),
            'currency': tx.currency_id.name,
            'status': 'done',
            'is_paid': '1',
        }

        with self.assertRaises(ValidationError):
            self._handle(data)

        self.assertEqual(tx.state, 'draft')

    def test_unknown_reference_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._handle({
                '_blockbee_signature_verified': True,
                'order_number': 'NO-SUCH-REFERENCE',
                'status': 'done',
                'is_paid': '1',
            })

    def test_mismatched_payment_id_is_rejected_without_state_change(self):
        tx = self._create_blockbee_tx()
        order, data = self._create_webhook_data(tx, payment_id='wrong-payment-id')

        with self.assertRaises(ValidationError):
            self._handle(data)

        self.assertEqual(tx.state, 'draft')
        self.assertFalse(order.order_is_paid)

    def test_mismatched_provider_reference_is_rejected_without_state_change(self):
        tx = self._create_blockbee_tx()
        order, data = self._create_webhook_data(tx)
        tx.provider_reference = 'different-provider-reference'

        with self.assertRaises(ValidationError):
            self._handle(data)

        self.assertEqual(tx.state, 'draft')
        self.assertFalse(order.order_is_paid)

    def test_completed_payment_uses_odoo_state_machine(self):
        tx = self._create_blockbee_tx()
        order, data = self._create_webhook_data(tx)

        self._handle(data)

        self.assertEqual(tx.state, 'done')
        self.assertTrue(order.order_is_paid)

    def test_non_completed_payment_has_no_side_effect(self):
        tx = self._create_blockbee_tx()
        order, data = self._create_webhook_data(tx, is_paid='0')

        self._handle(data)

        self.assertEqual(tx.state, 'draft')
        self.assertFalse(order.order_is_paid)

    def test_completed_payment_replay_is_idempotent(self):
        tx = self._create_blockbee_tx()
        order, data = self._create_webhook_data(tx)
        self._handle(data)
        state_change_date = tx.last_state_change

        self._handle(data)

        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.last_state_change, state_change_date)
        self.assertTrue(order.order_is_paid)

    def test_amount_parser_normalizes_currency_case(self):
        tx = self._create_blockbee_tx()
        _order, data = self._create_webhook_data(tx)

        data['currency'] = tx.currency_id.name.lower()
        amount_data = tx._parse_blockbee_amount_data(data)

        self.assertEqual(amount_data['amount'], tx.amount)
        self.assertEqual(amount_data['currency_code'], tx.currency_id.name)

    def test_non_finite_amount_is_rejected(self):
        tx = self._create_blockbee_tx()
        order, data = self._create_webhook_data(tx, value='nan')

        with self.assertRaises(ValidationError):
            self._handle(data)

        self.assertEqual(tx.state, 'draft')
        self.assertFalse(order.order_is_paid)

    def test_currency_mismatch_never_marks_payment_processed(self):
        tx = self._create_blockbee_tx()
        order, data = self._create_webhook_data(tx, currency='USD')

        self._handle(data)

        self.assertEqual(tx.state, 'error')
        self.assertFalse(order.order_is_paid)

    def test_mismatch_cannot_complete_a_transaction_already_in_error(self):
        tx = self._create_blockbee_tx()
        tx._set_error("Previous processing error")
        order, data = self._create_webhook_data(tx, value=str(tx.amount + 1))

        self._handle(data)

        self.assertEqual(tx.state, 'error')
        self.assertFalse(order.order_is_paid)

    def test_correct_notification_recovers_an_errored_transaction(self):
        tx = self._create_blockbee_tx()
        tx._set_error("Previous processing error")
        order, data = self._create_webhook_data(tx)

        self._handle(data)

        self.assertEqual(tx.state, 'done')
        self.assertTrue(order.order_is_paid)

    def test_checkout_total_includes_provider_fees(self):
        self._enable_fixed_fees(5.0)
        tx = self._create_blockbee_tx(reference='FEES-ORDER-1')

        payload = tx._blockbee_payload()

        self.assertAlmostEqual(tx.fees, 5.0, places=2)
        self.assertAlmostEqual(
            float(payload['order_total']), tx.amount + tx.fees, places=2,
        )

    def test_webhook_value_without_fees_is_rejected(self):
        self._enable_fixed_fees(5.0)
        tx = self._create_blockbee_tx(reference='FEES-ORDER-2')
        order, data = self._create_webhook_data(tx, value=str(tx.amount))

        self._handle(data)

        self.assertEqual(tx.state, 'error')
        self.assertFalse(order.order_is_paid)

    def test_full_reference_is_used_as_checkout_identity(self):
        tx = self._create_blockbee_tx(reference='ORDER-WITH-HYPHENS-2')

        payload = tx._blockbee_payload()

        self.assertEqual(payload['order_number'], tx.reference)

    def test_repeated_rendering_reuses_the_same_checkout(self):
        tx = self._create_blockbee_tx(reference='RENDER-ONCE-1')
        response = {
            'payment_url': 'https://pay.blockbee.io/reused-checkout',
            'success_token': 'browser-success-token',
            'payment_id': 'unique-render-payment-id',
        }

        with patch.object(
            self.env.registry['payment.provider'],
            '_blockbee_request',
            return_value=response,
        ) as create_checkout:
            first_values = tx._get_specific_rendering_values({})
            second_values = tx._get_specific_rendering_values({})

        self.assertEqual(create_checkout.call_count, 1)
        self.assertEqual(first_values['api_url'], response['payment_url'])
        self.assertEqual(second_values['api_url'], response['payment_url'])
        order = self.env['blockbee.orders'].sudo().search([
            ('transaction_id', '=', tx.id),
        ])
        self.assertEqual(len(order), 1)
        self.assertEqual(order.payment_id, response['payment_id'])
        self.assertEqual(order.provider_id, tx.provider_id)
        self.assertEqual(order.company_id, tx.company_id)

    def test_failed_checkout_creation_raises_a_user_safe_error(self):
        tx = self._create_blockbee_tx(reference='FAIL-RENDER-1')

        with patch.object(
            self.env.registry['payment.provider'],
            '_blockbee_request',
            return_value=None,
        ):
            with self.assertRaises(ValidationError):
                tx._get_specific_rendering_values({})

    def test_api_key_is_sent_in_header_with_timeout(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            'status': 'success',
            'payment_id': 'header-test-payment-id',
            'success_token': 'header-test-success-token',
            'payment_url': 'https://pay.blockbee.io/header-test',
        }

        with patch.object(
            blockbee_provider.requests, 'get', return_value=response,
        ) as api_request:
            result = self.provider._blockbee_request(
                redirect_url='https://example.com/payment/blockbee/return',
                notify_url='https://example.com/payment/blockbee/webhook',
                api_key='test-api-key',
                value='10.00',
            )

        kwargs = api_request.call_args.kwargs
        self.assertEqual(kwargs['headers']['apikey'], 'test-api-key')
        self.assertEqual(kwargs['timeout'], const.API_TIMEOUT)
        self.assertNotIn('apikey', kwargs['params'])
        self.assertEqual(result['payment_id'], 'header-test-payment-id')

    def test_api_connection_failure_raises_a_sanitized_error(self):
        with patch.object(
            blockbee_provider.requests, 'get', side_effect=requests.ConnectionError(
                'https://api.blockbee.io/?webhook_token=secret'
            ),
        ):
            with self.assertRaises(ValidationError):
                self.provider._blockbee_request(
                    redirect_url='https://example.com/payment/blockbee/return',
                    notify_url='https://example.com/payment/blockbee/webhook',
                    api_key='test-api-key',
                    value='10.00',
                )

    def test_log_redaction_covers_checkout_secrets(self):
        message = (
            'apikey=api-secret&success_token=success-secret'
            '&webhook_token=webhook-secret'
        )

        redacted = self.provider._blockbee_redact(message)

        self.assertNotIn('api-secret', redacted)
        self.assertNotIn('success-secret', redacted)
        self.assertNotIn('webhook-secret', redacted)

    def test_signature_fetches_current_public_key(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        public_key_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        signed_url = 'https://example.com/payment/blockbee/webhook?order_number=TEST'
        signature = private_key.sign(
            signed_url.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        http_request = SimpleNamespace(
            headers={'x-ca-signature': base64.b64encode(signature).decode()},
            url=signed_url,
        )
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            'status': 'success',
            'pubkey': public_key_pem.decode(),
        }
        with (
            patch.object(
                blockbee_controller,
                'request',
                SimpleNamespace(httprequest=http_request),
            ),
            patch.object(
                blockbee_controller.requests,
                'get',
                return_value=response,
            ) as get_public_key,
        ):
            self.assertTrue(
                blockbee_controller.BlockBeeController._verify_webhook_signature()
            )

        get_public_key.assert_called_once_with(
            const.WEBHOOK_PUBLIC_KEY_URL,
            timeout=blockbee_controller._BLOCKBEE_PUBLIC_KEY_TIMEOUT,
            allow_redirects=False,
        )

    def test_signature_uses_public_proxy_origin_and_raw_query(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        raw_query = (
            'order_number=ORDER%2F1&webhook_token=secret%2Bvalue'
            '&payment_id=payment-1'
        )
        request_target = f'/payment/blockbee/webhook?{raw_query}'
        signed_url = f'https://odoo.example.com{request_target}'
        signature = private_key.sign(
            signed_url.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        http_request = SimpleNamespace(
            headers={
                'x-ca-signature': base64.b64encode(signature).decode(),
                'Host': '127.0.0.1:8069',
                'X-Forwarded-Proto': 'https',
                'X-Forwarded-Host': 'odoo.example.com',
            },
            url=f'http://127.0.0.1:8069{request_target}',
            scheme='http',
            environ={
                'RAW_URI': request_target,
                'QUERY_STRING': raw_query,
            },
        )

        with (
            patch.object(
                blockbee_controller,
                'request',
                SimpleNamespace(httprequest=http_request),
            ),
            patch.object(
                blockbee_controller,
                '_get_blockbee_public_key',
                return_value=private_key.public_key(),
            ),
        ):
            self.assertTrue(
                blockbee_controller.BlockBeeController._verify_webhook_signature()
            )

    def test_public_key_endpoint_failure_fails_closed(self):
        with patch.object(
            blockbee_controller.requests,
            'get',
            side_effect=requests.ConnectionError,
        ):
            public_key = blockbee_controller._get_blockbee_public_key()

        self.assertIsNone(public_key)
