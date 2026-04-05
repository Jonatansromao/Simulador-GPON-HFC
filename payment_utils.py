import io
import base64
from datetime import datetime

try:
    import qrcode
except ImportError:
    qrcode = None

# CPF do recebedor
RECEIVER_CPF = "04922924930"
# Chave PIX (pode ser CPF, email, telefone ou chave aleatória)
PIX_KEY = RECEIVER_CPF

def _emv_field(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def _crc16(payload: str) -> str:
    polynomial = 0x1021
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ polynomial) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def generate_pix_qrcode(amount: float, description: str = "Assinatura Premium Simulador ETN"):
    """
    Gera um QR code PIX estático com o valor desejado.
    Se a biblioteca de QR não estiver disponível, retorna apenas o código PIX.
    """

    merchant_name = "Lais R Batista"
    merchant_city = "SAO PAULO"
    txid = "***"

    merchant_info = (
        _emv_field("00", "br.gov.bcb.pix") +
        _emv_field("01", PIX_KEY)
    )
    additional_data = _emv_field("05", txid)

    brcode = (
        _emv_field("00", "01") +
        _emv_field("26", merchant_info) +
        _emv_field("52", "0000") +
        _emv_field("53", "986") +
        _emv_field("54", f"{amount:.2f}") +
        _emv_field("58", "BR") +
        _emv_field("59", merchant_name) +
        _emv_field("60", merchant_city) +
        _emv_field("62", additional_data) +
        "6304"
    )
    brcode += _crc16(brcode)

    if qrcode is None:
        return None, brcode

    # Gerar QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=2,
    )
    qr.add_data(brcode)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Converter para base64
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()

    return f"data:image/png;base64,{img_str}", brcode


def get_pix_display_data():
    """Retorna informações para exibição do PIX."""
    return {
        "cpf": RECEIVER_CPF,
        "holder": "Lais Romao Batista",
        "bank": "Itaú",
        "amount": 250.00,
        "description": "Assinatura Premium Simulador ETN"
    }
