from odoo import http
from odoo.http import request
import requests
from requests.exceptions import Timeout, RequestException
import logging

logger = logging.getLogger(__name__)

# ⚠️ Replace with your actual public IP and forwarded port
PUBLIC_IP = "103.145.138.193"
PUBLIC_PORT = 8069

class BkashController(http.Controller):

    # -------------------- Callback from bKash --------------------
    @http.route(['/payment/bkash/return', '/payment/bkash/return/'],
                type='http', auth='public', csrf=False, redirect=True)
    def bkash_return(self, **post):
        """Handle bKash callback after payment attempt"""

        logger.info(f"Incoming bKash callback: {post}")

        # Clean trailing slashes from query parameters
        cleaned_post = {k: (v.rstrip('/') if isinstance(v, str) else v) for k, v in post.items()}
        payment_id = cleaned_post.get("paymentID")
        reference = cleaned_post.get("reference")
        status_from_bkash = cleaned_post.get("status")  # success, failure, cancel

        # Find transaction
        tx = None
        if payment_id:
            tx = request.env['payment.transaction'].sudo().search(
                [('bkash_payment_id', '=', payment_id)], limit=1)
        if not tx and reference:
            tx = request.env['payment.transaction'].sudo().search(
                [('reference', '=', reference)], limit=1)

        if not tx:
            logger.warning(f"No transaction found for bKash callback: {cleaned_post}")
            return request.redirect('/payment/status?reference=unknown')

        # Sandbox or direct status handling
        if status_from_bkash:
            status_lower = status_from_bkash.lower()
            if status_lower == "success":
                tx._set_done()
            elif status_lower == "failure":
                tx._set_error("Payment failed")
            elif status_lower == "cancel":
                tx._set_canceled()
            logger.info(f"Transaction updated from sandbox callback: {tx.reference}, status={status_lower}")
            return request.redirect(f'/payment/status?reference={tx.reference}')

        # Execute tokenized payment if no direct status
        provider = tx.provider_id
        try:
            token = provider._bkash_get_token()
            url = f"{provider.bkash_base_url}/tokenized/checkout/execute"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "X-APP-Key": provider.bkash_app_key
            }
            payload = {"paymentID": payment_id or tx.bkash_payment_id}

            logger.info(f"Executing bKash payment: {payload}")
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            logger.info(f"bKash execute response: {data}")

            status = data.get("transactionStatus", "").lower()
            if status == "completed":
                tx._set_done()
            elif status in ("initiated", "processing"):
                tx._set_pending()
            elif status == "cancelled":
                tx._set_canceled()
            else:
                tx._set_error(f"bKash error: {data}")

        except Timeout:
            tx._set_error("bKash request timed out. Please try again.")
            logger.error(f"bKash timeout for transaction {tx.reference}")
        except RequestException as e:
            tx._set_error(f"bKash request failed: {str(e)}")
            logger.error(f"bKash request exception for transaction {tx.reference}: {e}")

        return request.redirect(f'/payment/status?reference={tx.reference}')

    # -------------------- Payment Status Page --------------------
    @http.route(['/payment/status'], type='http', auth='public', website=True)
    def payment_status(self, **kw):
        reference = kw.get("reference")
        tx = None
        if reference:
            tx = request.env['payment.transaction'].sudo().search(
                [('reference', '=', reference)], limit=1)
        values = {'transaction': tx}
        return request.render('bkash.payment_status_template', values)

    # -------------------- Public Callback URLs --------------------
    @http.route(['/payment/bkash/get_callback_urls'], type='json', auth='public')
    def get_callback_urls(self):
        base_url = f"http://{PUBLIC_IP}:{PUBLIC_PORT}/payment/bkash/return"
        return {
            "callbackURL": base_url,
            "successCallbackURL": f"{base_url}?status=success",
            "failureCallbackURL": f"{base_url}?status=failure",
            "cancelledCallbackURL": f"{base_url}?status=cancel",
        }

    # -------------------- Create bKash Payment --------------------
    @http.route(['/payment/bkash/create'], type='json', auth='public')
    def create_bkash_payment(self, amount, partner_id, reference):
        """Create a bKash payment and return the redirect URL for sandbox"""

        provider = request.env['payment.provider'].sudo().search([('code', '=', 'bkash')], limit=1)
        if not provider:
            return {"error": "bKash provider not found."}

        # Prepare callback URLs
        base_url = f"http://{PUBLIC_IP}:{PUBLIC_PORT}/payment/bkash/return"
        callback_urls = {
            "callbackURL": base_url,
            "successCallbackURL": f"{base_url}?status=success",
            "failureCallbackURL": f"{base_url}?status=failure",
            "cancelledCallbackURL": f"{base_url}?status=cancel",
        }

        # Create transaction in Odoo
        tx = request.env['payment.transaction'].sudo().create({
            "amount": amount,
            "partner_id": partner_id,
            "reference": reference,
            "provider_id": provider.id,
            "bkash_payment_id": False,  # will be updated after bKash response
        })

        # Request bKash sandbox payment creation
        token = provider._bkash_get_token()
        payload = {
            "amount": amount,
            "payerReference": f"OdooCustomer_{partner_id}",
            **callback_urls
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-APP-Key": provider.bkash_app_key
        }

        try:
            response = requests.post(f"{provider.bkash_base_url}/tokenized/checkout/create", 
                                     json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            tx.sudo().write({"bkash_payment_id": data.get("paymentID")})
            redirect_url = data.get("bkashURL") or data.get("redirect_url")
            return {"redirect_url": redirect_url, "transaction_reference": tx.reference}
        except Exception as e:
            logger.error(f"Error creating bKash payment: {e}")
            tx._set_error(str(e))
            return {"error": str(e)}
