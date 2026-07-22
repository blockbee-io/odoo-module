-- disable BlockBee payment provider
UPDATE payment_provider
   SET blockbee_api_key = NULL,
       state = 'disabled',
       is_published = FALSE
 WHERE code = 'blockbee';

-- Checkout URLs and webhook tokens have no purpose in a neutralized database.
DELETE FROM blockbee_orders;
