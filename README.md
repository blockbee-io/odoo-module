[<img src="https://blockbee.io/static/assets/images/blockbee_logo_nospaces.png" width="300"/>](image.png)

# BlockBee Payment Gateway for Odoo
Accept cryptocurrency payments in Odoo.

### Requirements:

```
Odoo = 17
```

### Description

Accept payments in Bitcoin, Bitcoin Cash, Litecoin, Ethereum, Doge and Matic directly to your crypto wallet.

#### Allow your customers to pay with cryptocurrency

The BlockBee module enables your Odoo store to receive payments in cryptocurrency, with a simple setup.

#### Accepted cryptocurrencies and tokens include:

* (BTC) Bitcoin
* (ETH) Ethereum
* (BCH) Bitcoin Cash
* (LTC) Litecoin
* (MATIC) Polygon
* (TRX) Tron
* (BNB) Binance Coin
* (DOGE) Dogecoin

You can get the full list in our [cryptocurrencies](https://blockbee.io/cryptocurrencies) page.

### Supported currencies for automatic exchange rates are:

* (AED) United Arab Emirates Dirham
* (AUD) Australian Dollar
* (BGN) Bulgarian Lev
* (BRL) Brazilian Real
* (CAD) Canadian Dollar
* (CNY) Chinese Yuan
* (CZK) Czech Koruna
* (DKK) Danish Krone
* (EUR) Euro
* (GBP) British Pound
* (HKD) Hong Kong Dollar
* (HUF) Hungarian Forint
* (IDR) Indonesian Rupiah
* (INR) Indian Rupee
* (JPY) Japanese Yen
* (MXN) Mexican Peso
* (MYR) Malaysian Ringgit
* (NGN) Nigerian Naira
* (NOK) Norwegian Krone
* (PHP) Philippine Peso
* (PLN) Polish Zloty
* (RON) Romanian Leu
* (RUB) Russian Ruble
* (SEK) Swedish Krona
* (SGD) Singapore Dollar
* (THB) Thai Baht
* (TRY) Turkish Lira
* (USD) United States Dollar
* (ZAR) South African Rand

If your Odoo currency is not in this list, the module will not offer BlockBee at checkout. [Contact us](https://blockbee.io/contacts/) to ask for the fiat currency you need.

#### Why choose BlockBee?

BlockBee has no setup fees, no monthly fees and no hidden costs!

BlockBee has a low 1% fee (plus blockchain fees) on the transactions processed.
For more info on our fees [click here](https://blockbee.io/fees/)

### Installation and Configuration

1. Upload our module code to your addons folder inside a folder named `payment_blockbee`.
2. Enable Odoo's debug mode.
3. Go to the **Apps** page and click **Install**.
4. Once installed click **Activate**.
5. Now, click the menu in the top left and go to **Website**. There click **Configuration** and then **Payment Providers**
6. Select **BlockBee Payment Gateway**. You will need an API Key which you might get at [BlockBee Dashboard](https://dash.blockbee.io/).
7. Finally we advise you to make a test order in order to verify if you set up BlockBee's Module correctly.

### Frequently Asked Questions

#### Do I need an API key?

Yes. To use our service you will need to register at our [dashboard](https://dash.blockbee.io/) and create a new API Key.

#### How long do payments take before they're confirmed?

This depends on the cryptocurrency you're using. Bitcoin usually takes up to 11 minutes, Ethereum usually takes less than a minute.

#### Is there a minimum for a payment?

Yes, the minimums change according to the chosen cryptocurrency and can be checked [here](https://blockbee.io/fees/).
If the Odoo order total is below the chosen cryptocurrency's minimum, an error is raised to the user.

#### Where can I find more documentation on your service?

You can find more documentation about our service on our [website](https://blockbee.io/), our [technical documentation](https://docs.blockbee.io/) page or our [e-commerce](https://blockbee.io/ecommerce/) page.
If there's anything else you need that is not covered on those pages, please get in touch with us, we're here to help you!

#### Where can I get support?

The easiest and fastest way is via our live chat on our [website](https://blockbee.io) or via our [contact form](https://blockbee.io/contacts/).

### Changelog 

#### 1.4.0
* Require signed, transaction-bound BlockBee webhooks with strict payment validation
* Fetch BlockBee's current webhook public key from the API for every signed webhook
* Verify the original raw callback URL across direct and reverse-proxy deployments
* Bind checkouts to their exact Odoo transaction, provider, company, and BlockBee payment ID
* Serialize checkout creation and callbacks for concurrency-safe idempotency
* Reuse unpaid checkout links instead of rotating webhook credentials
* Use Odoo's standard payment, order, accounting, and reconciliation processing
* Send API keys in headers, add request timeouts, and redact payment credentials and secrets from logs
* Disable and unpublish BlockBee and remove checkout secrets during database neutralization

#### 1.2.0
* Support Odoo 17

### Upgrade Notice
* Payment links created before 1.4.0 do not have the new signed-webhook identity
  fields. Their obsolete authentication rows are removed during the module
  upgrade, so unpaid links must be regenerated afterward.
