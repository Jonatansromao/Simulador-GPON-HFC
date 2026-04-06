import stripe
import os
from datetime import datetime, timedelta

# Configurar chave Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")

class StripeGateway:
    """Integração com Stripe para processamento de pagamentos"""
    @staticmethod
    def is_configured():
        return bool(stripe.api_key and STRIPE_PUBLISHABLE_KEY)
    
    @staticmethod
    def criar_sessao_checkout(professor_id, professor_email, valor=250.00):
        """
        Cria uma sessão de checkout do Stripe
        Retorna URL para redirecionar o cliente
        """
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[
                    {
                        "price_data": {
                            "currency": "brl",
                            "product_data": {
                                "name": "Assinatura Premium Simulador HFC/GPON",
                                "description": "Acesso premium por 30 dias",
                                "images": [],
                            },
                            "unit_amount": int(valor * 100),  # Valor em centavos
                        },
                        "quantity": 1,
                    }
                ],
                mode="payment",
                success_url="http://localhost:5000/professor/premium/sucesso?session_id={CHECKOUT_SESSION_ID}",
                cancel_url="http://localhost:5000/professor/premium",
                customer_email=professor_email,
                metadata={
                    "professor_id": professor_id,
                    "tipo_servico": "premium_subscription"
                }
            )
            return session.url, session.id
        except stripe.error.StripeError as e:
            return None, str(e)
    
    @staticmethod
    def criar_pagamento_pix(professor_id, professor_email, valor=250.00, payment_id=None):
        """
        Cria um Payment Link do Stripe com PIX como método de pagamento
        Retorna URL para redirecionar o cliente
        """
        try:
            if not StripeGateway.is_configured():
                return None, "Stripe não está configurado. Defina STRIPE_SECRET_KEY e STRIPE_PUBLISHABLE_KEY."

            # Preparar metadados
            metadata = {
                "professor_id": professor_id,
                "tipo_servico": "premium_subscription",
                "metodo": "pix"
            }
            
            # Adicionar payment_id se fornecido
            if payment_id:
                metadata["payment_id"] = str(payment_id)

            # Criar Payment Link com PIX
            payment_link = stripe.PaymentLink.create(
                line_items=[
                    {
                        "price_data": {
                            "currency": "brl",
                            "product_data": {
                                "name": "Assinatura Premium Simulador HFC/GPON",
                                "description": "Acesso premium por 30 dias",
                            },
                            "unit_amount": int(valor * 100),  # Valor em centavos
                        },
                        "quantity": 1,
                    }
                ],
                payment_method_types=["pix"],
                after_completion={
                    "type": "redirect",
                    "redirect": {
                        "url": "http://localhost:5000/professor/premium/sucesso_pix"
                    }
                },
                metadata=metadata
            )
            return payment_link.url, payment_link.id
        except stripe.error.StripeError as e:
            error_text = str(e)
            if "payment method type provided: pix is invalid" in error_text.lower():
                return None, (
                    "Stripe PIX não está habilitado para esta conta. "
                    "Ative PIX no painel Stripe ou use o QR code manual."
                )
            return None, error_text
        """
        Processa pagamento com token de cartão gerado pelo Stripe.js
        Retorna dicionário com resultado
        """
        try:
            # Criar cobranç a com o token
            charge = stripe.Charge.create(
                amount=int(valor * 100),  # Valor em centavos
                currency="brl",
                source=token,
                description=f"Assinatura Premium - Professor {professor_id}",
                receipt_email=professor_email,
                metadata={
                    "professor_id": professor_id,
                    "tipo_servico": "premium_subscription"
                }
            )
            
            return {
                "success": True,
                "charge_id": charge.id,
                "status": charge.status,
                "paid": charge.paid,
                "message": "Pagamento processado com sucesso"
            }
        except stripe.error.CardError as e:
            # Erro no cartão
            return {
                "success": False,
                "error": e.user_message or "Erro ao processar cartão",
                "code": e.code
            }
        except stripe.error.RateLimitError:
            return {
                "success": False,
                "error": "Muitas requisições. Tente novamente em alguns segundos."
            }
        except stripe.error.InvalidRequestError:
            return {
                "success": False,
                "error": "Erro na requisição. Verifique os dados."
            }
        except stripe.error.AuthenticationError:
            return {
                "success": False,
                "error": "Erro de autenticação com o gateway"
            }
        except stripe.error.StripeError as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    @staticmethod
    def obter_session_checkout(session_id):
        """Obtém informações sobre uma sessão de checkout"""
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            return {
                "success": True,
                "payment_status": session.payment_status,
                "customer_email": session.customer_email,
                "metadata": session.metadata
            }
        except stripe.error.StripeError as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    @staticmethod
    def obter_pagamento(payment_intent_id):
        """Obtém detalhes de um pagamento"""
        try:
            payment_intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            return {
                "success": True,
                "status": payment_intent.status,
                "amount": payment_intent.amount,
                "currency": payment_intent.currency
            }
        except stripe.error.StripeError as e:
            return {
                "success": False,
                "error": str(e)
            }

def get_stripe_publishable_key():
    """Retorna a chave publicável do Stripe para o frontend"""
    return STRIPE_PUBLISHABLE_KEY
