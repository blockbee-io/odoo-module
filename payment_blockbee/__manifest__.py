{
    'name': 'BlockBee Payment Gateway',
    'version': '1.4.0',
    'category': 'Accounting/Payment Acquirers',
    'sequence': 350,
    'author': 'BlockBee',
    'website': 'https://blockbee.io/',
    'depends': ['payment'],
    'data': [
        'security/ir.model.access.csv',

        'views/payment_template.xml',
        'views/payment_blockbee_templates.xml',

        'data/payment_provider_data.xml',
    ],
    'images': [
        'static/description/blockbee_description.jpg',
        'static/description/blockbee_screenshot.png',
    ],
    'external_dependencies': {
        'python': ['cryptography'],
    },
    'installable': True,
    'application': True,
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'license': 'LGPL-3',
}
