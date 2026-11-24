#!/usr/bin/env python3
"""Gera o cartão com QR code da carteira (link público atual + /app/) na área de trabalho.
Chamado pelo tunel-servico.sh sempre que o link muda. Uso: qr_carteira.py <link>"""
import os, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFont

link = sys.argv[1].rstrip('/') + '/app/'
AQUI = os.path.dirname(os.path.abspath(__file__))
NODE_MODULES = os.path.expanduser('~/projetos/plataforma-jogos/node_modules')
SAIDA = os.path.expanduser('~/Área de trabalho/App Carteira Portgiga - QR.png')
tmp = tempfile.mktemp(suffix='.png')
subprocess.run(['node', '-e', "require('qrcode').toFile(process.argv[1], process.argv[2], {width:600, margin:2, errorCorrectionLevel:'H', color:{dark:'#062b18', light:'#ffffff'}}, e => process.exit(e ? 1 : 0))", tmp, link],
               check=True, env={**os.environ, 'NODE_PATH': NODE_MODULES})
fonte = lambda t, b=True: ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf' % ('-Bold' if b else ''), t)
W, H = 760, 1080
c = Image.new('RGB', (W, H), '#050b08'); d = ImageDraw.Draw(c)
logo = Image.open(os.path.join(AQUI, '..', 'servidor', 'app', 'icones', 'icone-512.png')).convert('RGBA').resize((110, 110))
c.paste(logo, ((W - 110) // 2, 40), logo)
d.text((W // 2, 185), 'Carteira Portgiga', font=fonte(46), fill='#f3c552', anchor='mm')
d.text((W // 2, 240), 'Abra no celular (Android ou iPhone)', font=fonte(28, False), fill='#edf6f0', anchor='mm')
d.rounded_rectangle(((W - 600) // 2, 280, (W + 600) // 2, 880), radius=30, fill='#ffffff')
c.paste(Image.open(tmp).convert('RGB').resize((560, 560)), ((W - 560) // 2, 300))
lg = logo.resize((90, 90)); c.paste(lg, ((W - 90) // 2, 535), lg)
d.text((W // 2, 925), link.replace('https://', ''), font=fonte(19, False), fill='#a3b8ab', anchor='mm')
d.text((W // 2, 975), 'iPhone: Compartilhar → Adicionar à Tela de Início', font=fonte(23, False), fill='#a3b8ab', anchor='mm')
d.text((W // 2, 1015), 'Android: baixe o app pelo botão na tela inicial', font=fonte(23, False), fill='#a3b8ab', anchor='mm')
c.save(SAIDA); os.remove(tmp)
print('QR atualizado:', SAIDA)
