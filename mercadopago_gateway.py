import os
import uuid
import requests

BASE_URL = "https://api.mercadopago.com"


def get_access_token():
    return os.getenv("MERCADOPAGO_ACCESS_TOKEN")


def get_base_url():
    return (os.getenv("APP_BASE_URL") or os.getenv("NGROK_URL") or "http://localhost:5000").rstrip("/")


def is_sandbox_mode():
    return os.getenv("MERCADOPAGO_SANDBOX", "false").lower() in ("1", "true", "yes")


def build_headers(access_token, use_idempotency=False):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    if use_idempotency:
        headers["X-Idempotency-Key"] = str(uuid.uuid4())
    return headers


class MercadoPagoGateway:
    """Integração leve com Mercado Pago usando requests."""

    @staticmethod
    def is_configured():
        return bool(get_access_token())

    @staticmethod
    def criar_preferencia(professor_id, professor_email, valor=250.00, payment_id=None):
        access_token = get_access_token()
        if not access_token:
            return None, "Mercado Pago não está configurado. Defina MERCADOPAGO_ACCESS_TOKEN."

        base_url = get_base_url()
        preference = {
            "items": [
                {
                    "title": "Assinatura Premium Simulador HFC/GPON",
                    "quantity": 1,
                    "currency_id": "BRL",
                    "unit_price": float(valor),
                    "description": "Acesso premium por 30 dias",
                }
            ],
            "external_reference": str(payment_id) if payment_id else None,
            "back_urls": {
                "success": f"{base_url}/professor/premium/sucesso_mp",
                "failure": f"{base_url}/professor/premium",
                "pending": f"{base_url}/professor/premium"
            },
            "auto_return": "approved",
            "payment_methods": {
                "excluded_payment_types": [
                    {"id": "ticket"}
                ],
                "installments": 12
            },
            "notification_url": f"{base_url}/webhook/mercadopago"
        }

        response = requests.post(
            f"{BASE_URL}/checkout/preferences",
            json=preference,
            headers=build_headers(access_token),
            timeout=15,
        )
        if response.status_code not in (200, 201):
            return None, f"Mercado Pago: {response.status_code} - {response.text}"

        data = response.json()
        checkout_url = data.get("sandbox_init_point") if is_sandbox_mode() else data.get("init_point")
        return checkout_url, data.get("id")

    @staticmethod
    def criar_pagamento_pix(professor_email, valor=250.00, payment_id=None, nome=None):
        access_token = get_access_token()
        if not access_token:
            return {"success": False, "error": "Mercado Pago não está configurado. Defina MERCADOPAGO_ACCESS_TOKEN."}

        base_url = get_base_url()
        nome = (nome or "Cliente ETN").strip()
        partes_nome = nome.split()
        primeiro_nome = partes_nome[0] if partes_nome else "Cliente"
        sobrenome = " ".join(partes_nome[1:]) if len(partes_nome) > 1 else "ETN"

        payload = {
            "transaction_amount": float(valor),
            "description": "Assinatura Premium Simulador HFC/GPON",
            "payment_method_id": "pix",
            "external_reference": str(payment_id) if payment_id else None,
            "notification_url": f"{base_url}/webhook/mercadopago",
            "payer": {
                "email": professor_email,
                "first_name": primeiro_nome,
                "last_name": sobrenome,
            },
        }

        response = requests.post(
            f"{BASE_URL}/v1/payments",
            json=payload,
            headers=build_headers(access_token, use_idempotency=True),
            timeout=20,
        )
        if response.status_code not in (200, 201):
            return {"success": False, "error": f"Mercado Pago PIX: {response.status_code} - {response.text}"}

        data = response.json()
        transaction_data = ((data.get("point_of_interaction") or {}).get("transaction_data") or {})
        return {
            "success": True,
            "payment_id": data.get("id"),
            "status": data.get("status"),
            "qr_code": transaction_data.get("qr_code"),
            "qr_code_base64": transaction_data.get("qr_code_base64"),
            "ticket_url": transaction_data.get("ticket_url"),
            "payment": data,
        }

    @staticmethod
    def obter_pagamento(payment_id):
        access_token = get_access_token()
        if not access_token:
            return {"success": False, "error": "Mercado Pago não está configurado."}

        response = requests.get(
            f"{BASE_URL}/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15
        )
        if response.status_code != 200:
            return {"success": False, "error": f"Mercado Pago: {response.status_code} - {response.text}"}

        data = response.json()
        return {"success": True, "payment": data}
