import discord
import requests
import asyncio
from discord.ext import commands
import uuid
import os
import json
import firebase_admin
from firebase_admin import credentials, auth, firestore
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('DC_TOKEN')
MP_ACCESS_TOKEN = os.getenv('MP_TOKEN')

GUILD_ID = 1312247419374932038
CATEGORY_ID = 1343778028215599185
ADMIN_ROLE_ID = 1341946258775867492
SEU_CANAL_TEXTO_ID = 1343778123065462815

# Caminho dos arquivos de configuração
FIREBASE_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "firebase_config.json")
SERVICE_ACCOUNT_PATH = os.path.join(os.path.dirname(__file__), "service_account.json")

# Carrega API Key do Firebase
with open(FIREBASE_CONFIG_PATH, "r") as file:
    firebase_config = json.load(file)

API_KEY = firebase_config["apiKey"]  # Pega a API Key do Firebase

# Inicializa Firebase Admin SDK com a conta de serviço
if not firebase_admin._apps:
    cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
    firebase_admin.initialize_app(cred)

# Conectar ao Firestore
db = firestore.client()

# Inicializa o bot
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

class PurchaseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_buttons()

    def add_buttons(self):
        options = [
            ("Spoofer Diário - R$60", "daily", 60),
            ("Spoofer Semanal - R$100", "weekly", 100),
            ("Spoofer Lifetime - R$300 + Suporte", "lifetime", 300)
        ]
        for label, custom_id, price in options:
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id)
            button.callback = lambda interaction, p=price, c=custom_id: asyncio.create_task(self.create_ticket(interaction, p, c))
            self.add_item(button)

    async def create_ticket(self, interaction: discord.Interaction, price, option):
        
        guild = bot.get_guild(GUILD_ID)
        admin_role = guild.get_role(ADMIN_ROLE_ID)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            admin_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        ticket_channel = await guild.create_text_channel(name=f"ticket-{interaction.user.name}", category=discord.utils.get(guild.categories, id=CATEGORY_ID), overwrites=overwrites)

        # Gera QR Code via Mercado Pago
        qr_code_url, transaction_id = generate_mp_qr(price)

        if qr_code_url:
            await ticket_channel.send(f"Olá {interaction.user.mention}, escaneie o QR Code abaixo para pagar:", file=discord.File(qr_code_url))
            await ticket_channel.send(f"Seu pagamento está sendo processado. Assim que for confirmado, você receberá sua chave no privado!")
            asyncio.create_task(check_payment(transaction_id, ticket_channel, interaction.user, option, price))
        else:
            await ticket_channel.send("Houve um erro ao gerar o QR Code. Tente novamente mais tarde.")

        await interaction.response.send_message(f"Ticket criado: {ticket_channel.mention}", ephemeral=True)

def generate_mp_qr(amount):
    url = "https://api.mercadopago.com/v1/payments"
    headers = {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": str(uuid.uuid4())
    }
    data = {
        "transaction_amount": amount,
        "payment_method_id": "pix",
        "payer": {
            "email": "pagador@gmail.com"
        }
    }
    
    response = requests.post(url, headers=headers, json=data)
    
    if response.status_code == 201:
        payment_info = response.json()
        qr_code = payment_info.get("point_of_interaction", {}).get("transaction_data", {}).get("qr_code")
        qr_code_base64 = payment_info.get("point_of_interaction", {}).get("transaction_data", {}).get("qr_code_base64")
        if qr_code and qr_code_base64:
            with open("qrcode.png", "wb") as f:
                import base64
                f.write(base64.b64decode(qr_code_base64))
            return "qrcode.png", payment_info["id"]
    
    print("Erro ao gerar QR Code:", response.json())
    return None, None

async def save_key_firestone(key, option):

    if option == "daily":
        option = 1
    elif option == "weekly":
        option = 5
    else:
        option = 200

    await db.collection("keys").document(key).set({
        "days": option,
        "used": False,
    })
     
async def check_payment(transaction_id, ticket_channel, user, option, price):

    url = f"https://api.mercadopago.com/v1/payments/{transaction_id}"
    headers = {"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}
    
    for _ in range(30):  # Tenta verificar por 10 minutos (30 tentativas a cada 20s)
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            payment_info = response.json()
            if payment_info["status"] == "approved":
                await ticket_channel.send("✅ Pagamento confirmado! Sua chave será enviada no privado.")
                key = generate_key()
                await save_key_firestone(key, option)
                
                # Envia o resumo da compra no privado do usuário
                message = (
                    f"🛒 **Compra Confirmada!**\n\n"
                    f"🔹 **Produto:** {option.replace('-', ' ').title()}\n"
                    f"💰 **Valor:** R${price}\n"
                    f"🔑 **Chave:** `{key}`\n\n"
                    f"tutorial em <#1343807789566529657>\n\n"
                    f"Obrigado pela compra! Qualquer dúvida, entre em contato com o suporte.\n\n"
                )
                await user.send(message)

                # Fecha o ticket
                await ticket_channel.send("🔒 Fechando o ticket...")
                await asyncio.sleep(5)
                await ticket_channel.delete()
                return
        
        await asyncio.sleep(20)  # Aguarda 20 segundos antes de checar novamente
    
    await ticket_channel.send("⛔ Pagamento não detectado em 10 minutos. Ticket fechado.")
    await asyncio.sleep(5)
    await ticket_channel.delete()

def generate_key():
    import secrets
    return secrets.token_hex(16)  # Gera uma chave aleatória de 16 bytes

@bot.event
async def on_ready():
    
    print(f"Bot conectado como {bot.user}")
    channel = bot.get_channel(SEU_CANAL_TEXTO_ID)
    await channel.purge(limit=None)
    
    if channel:
        
        embed = discord.Embed(
            title="🕹️ Spoofer Arcade Grid - Proteção Definitiva",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="📌 Sobre o Produto",
            value="✅ Compatível com Windows 10 e 11\n"
                  "✅ Funciona em uma ampla variedade de processadores e placas de vídeo\n"
                  "✅ Desenvolvido para chips Intel e AMD\n"
                  "✅ Voce usa apenas uma vez",
            inline=False
        )

        embed.add_field(
            name="🎯 Aprovado em",
            value="▶️ Fortnite\n"
                  "▶️ Valorant\n",
            inline=False
        )

        embed.add_field(
            name="Por que Escolher o Arcade Grid?",
            value="🔹 Processo simples e rápido, sem complicações\n"
                  "🔹 Totalmente funcional e eficiente\n"
                  "🔹 Se houver problemas insolúveis, seu dinheiro será devolvido\n"
                  "🔹 Só precisara usar novamente caso leve outro ban HWID!!!",
            inline=False
        )

        embed.set_image(url="https://media.discordapp.net/attachments/1285438728860598357/1341938234179190995/aaaaaa.png?ex=67b7d0df&is=67b67f5f&hm=b5a3dee80075bb854cf80704a4384ed534d6cf69cff28b694e2bd1edde73f26b&=&format=webp&quality=lossless&width=644&height=644")  # Substitua pela URL da imagem se necessário
        embed.set_footer(text="Copyright © Arcade Grid - 2025")

        message = await channel.send(embed=embed)
    
    if channel:
        await channel.send("Todas as informações seram enviadas após a compra\nEscolha uma opção", view=PurchaseView())

bot.run(TOKEN)