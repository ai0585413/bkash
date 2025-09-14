from odoo import fields, models, api
import requests

class PaymentProvider(models.Model):
    _inherit = 'payment.provider'
    

    code = fields.Selection(
        selection_add=[('bkash', "bKash")],
        ondelete={'bkash': 'set default'}
    )
  


    bkash_app_key = fields.Char("bKash App Key")
    bkash_app_secret = fields.Char("bKash App Secret")
    bkash_username = fields.Char("bKash Username")
    bkash_password = fields.Char("bKash Password")
    bkash_base_url = fields.Char("Base URL", default="https://tokenized.sandbox.bka.sh/v1.2.0-beta")

    def _bkash_get_token(self):
        """Grant Token from bKash"""
        self.ensure_one()
        url = f"{self.bkash_base_url}/tokenized/checkout/token/grant"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "username": self.bkash_username,
            "password": self.bkash_password
        }
        payload = {
            "app_key": self.bkash_app_key,
            "app_secret": self.bkash_app_secret
        }
        res = requests.post(url, json=payload, headers=headers, timeout=40)
        res.raise_for_status()
        return res.json().get("id_token")
