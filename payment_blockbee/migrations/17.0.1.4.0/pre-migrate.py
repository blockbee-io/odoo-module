def migrate(cr, version):
    """Discard legacy authentication rows that cannot verify signed webhooks.

    Versions before 1.4.0 did not bind these rows to an Odoo transaction and
    did not retain BlockBee's payment identifier. Their payment links are
    intentionally invalid after the security upgrade and must be regenerated.
    """
    cr.execute("DELETE FROM blockbee_orders")
