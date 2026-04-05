# Implementação de Pagamentos - Simulador ETN

## PIX - Implementado ✅

O sistema já está gerando:
- **QR Code PIX** estático com o CPF: 049.229.249-30
- **Dados de recebedor**: Jonatan Silva
- **Valor**: R$ 250,00 por mês
- **Confirmação**: Botão "Confirmar Pagamento PIX"

Ao clicar em "Confirmar Pagamento PIX", a assinatura é ativada por 30 dias.

---

## Cartão de Crédito/Débito - Opções de Integração

### 🏦 Opção 1: Stripe (Recomendado)

**Vantagens:**
- Mais seguro do mercado
- Parcelamento automático
- Suporte webhooks
- Dashboard analítico

**Integração:**
```bash
pip install stripe
```

**Arquivo: payment_gateway.py (novo)**
```python
import stripe
import os

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def processar_pagamento_stripe(token_cartao, email, professor_id, valor=250.00):
    """Processa pagamento com Stripe"""
    try:
        charge = stripe.Charge.create(
            amount=int(valor * 100),  # Centavos
            currency="brl",
            source=token_cartao,  # Token do cliente
            description=f"Assinatura Premium - Professor {professor_id}",
            receipt_email=email
        )
        return {
            'success': True,
            'charge_id': charge.id,
            'status': charge.status
        }
    except stripe.error.CardError as e:
        return {
            'success': False,
            'error': f"Erro no cartão: {e.user_message}"
        }
```

**Rota atualizada:**
```python
@html_bp.route("/professor/pagar_cartao", methods=["POST"])
@api_login_required_professor
def professor_pagar_cartao():
    professor = Professor.query.get(session["usuario"]["id"])
    
    # Usar Stripe.js no frontend para gerar token seguro
    stripe_token = request.form.get("stripeToken")
    
    result = processar_pagamento_stripe(
        stripe_token,
        professor.email,
        professor.id
    )
    
    if result['success']:
        # Ativar premium
        professor.is_premium = True
        professor.premium_expires_at = datetime.utcnow() + timedelta(days=30)
        db.session.commit()
        flash("Pagamento confirmado!", "success")
    else:
        flash(f"Erro: {result['error']}", "danger")
    
    return redirect(url_for("html_bp.professor_dashboard"))
```

**Variáveis de ambiente (.env):**
```
STRIPE_PUBLIC_KEY=pk_live_sua_chave_publica
STRIPE_SECRET_KEY=sk_live_sua_chave_secreta
```

---

### 💳 Opção 2: Mercado Pago (Mais Popular no Brasil)

**Vantagens:**
- Muito popular no Brasil
- Integração simples
- Suporta cartão e boleto
- Taxa competitiva

**Integração:**
```bash
pip install mercadopago
```

**Arquivo: payment_gateway.py (novo)**
```python
import mercadopago

class MercadoPagoGateway:
    def __init__(self, access_token):
        self.sdk = mercadopago.SDK(access_token)
    
    def criar_preferencia_pagamento(self, professor_id, email, valor=250.00):
        """Cria preferência de pagamento no Mercado Pago"""
        preference_data = {
            "items": [
                {
                    "title": "Assinatura Premium Simulador ETN",
                    "quantity": 1,
                    "unit_price": valor
                }
            ],
            "payer": {
                "email": email
            },
            "back_url": "https://seu-dominio.com/professor/premium",
            "notification_url": "https://seu-dominio.com/webhook/mercado_pago",
            "auto_return": "approved"
        }
        
        preference_response = self.sdk.preference().create(preference_data)
        return preference_response["response"]["init_point"]
```

**Variáveis de ambiente (.env):**
```
MERCADO_PAGO_ACCESS_TOKEN=seu_token_aqui
```

---

### 🏤 Opção 3: PagSeguro

**Vantagens:**
- Integração direta com conta
- Suporta transferência bancária
- Dashboard completo

**Integração:**
```bash
pip install pagseguro-api
```

---

### 💰 Opção 4: Integração Direta com Banco

Para receber direto em sua conta bancária sem intermediários:

**Configuração de Conta Bancária:**
1. **Banco**: Qual banco você utiliza? (Itaú, Bradesco, BB, Caixa, etc)
2. **Tipo de Conta**: Corrente ou Poupança?
3. **Agência**: 
4. **Conta**: 
5. **CPF/CNPJ**: 049.229.249-30

Com esses dados, você pode:
- Solicitar ferramentas de cobrança do seu banco
- Gerar boletos com dados da sua conta
- Receber via transferência automática

---

## Recomendação Final

**Para sua situação, recomendo:**

1. **PIX** ✅ (Já implementado)
   - Instantâneo
   - Sem taxa adicional
   - Perfeito para teste

2. **Stripe** (Próximo passo)
   - Segurança máxima
   - Infraestrutura robusta
   - Melhor para escalar

3. **Mercado Pago** (Alternativa Brasil)
   - Popular localmente
   - Interface amigável

---

## Implementação Passo a Passo - Stripe

### 1. Acessar site e criar conta
- Ir para: https://dashboard.stripe.com/register
- Usar seu email
- Confirmar dados

### 2. Pegar chaves
- Dashboard → Settings → API Keys
- Copiar `Publishable key`
- Copiar `Secret key`

### 3. Adicionar ao projeto
```bash
# .env
STRIPE_PUBLIC_KEY=pk_live_xxx
STRIPE_SECRET_KEY=sk_live_xxx
```

### 4. Instalar biblioteca
```bash
pip install stripe
```

### 5. Usar no template (HTML)
```html
<script src="https://js.stripe.com/v3/"></script>
<script>
    var stripe = Stripe('{{ stripe_public_key }}');
    // Gerar token seguro do cartão
</script>
```

---

## Segurança - Boas Práticas

### ❌ NÃO Armazene:
- Números de cartão
- CVV
- Data de validade

### ✅ SIM Armazene:
- Token de pagamento (Stripe, MP, etc)
- ID do cliente
- Histórico de transações com ID externo

### 🔐 Use:
- HTTPS em produção (obrigatório)
- Validação no servidor (não apenas frontend)
- Webhook para confirmar pagamentos
- PCI DSS compliance (se necessário)

---

## Webhook - Confirmar Pagamento Automaticamente

```python
@html_bp.route("/webhook/stripe", methods=["POST"])
def webhook_stripe():
    """Recebe confirmação de pagamento do Stripe"""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return "Invalid payload", 400
    
    if event['type'] == 'charge.succeeded':
        charge = event['data']['object']
        professor_id = charge['description'].split()[-1]
        
        professor = Professor.query.get(professor_id)
        if professor:
            professor.is_premium = True
            professor.premium_expires_at = datetime.utcnow() + timedelta(days=30)
            db.session.commit()
    
    return "OK", 200
```

---

## Próximos Passos

1. ✅ PIX funcionando
2. 🔳 Escolher gateway (recomendo Stripe)
3. 🔳 Gerar chaves de API
4. 🔳 Integrar bibliotecas
5. 🔳 Testar em sandbox
6. 🔳 Ativar em produção

Qualquer dúvida, me avise! 📞
