<p align="center"><img src="portgiga.svg" width="140" alt="pau-brasil, símbolo da Portgiga"></p>

# Portgiga (PTG)

Uma moeda experimental feita em casa, minerada por placas **ESP32**, celulares e PCs. O símbolo é o pau-brasil.

> **PTG é uma moeda de valor próprio e ninguém promete lucro.** É um projeto de hobby, aprendizado e comunidade.

A rede começou com 8 placas ESP32 de R$ 30 minerando juntas numa rede de casa. Este repositório tem tudo: o servidor, o painel, o firmware das placas e um verificador independente.

**➡️ Tutorial passo a passo: [Minere sua própria moeda com uma ESP32](docs/tutorial-esp32.md)** · **English: [Mine your own coin on an ESP32](docs/tutorial-esp32-en.md)** · Firmware: [release v1.5](https://github.com/shamah10/portgiga/releases/tag/v1.5)

## Como funciona

- **Mineração justa entre aparelhos.** O servidor manda um desafio (achar `n` tal que `sha256(base + n) = alvo`) e ajusta a dificuldade de cada aparelho para ele responder a cada ~10 s. Uma ESP32 (~40 mil hashes/s) ganha parecido com um celular. A ideia é a mesma da Duino-Coin.
- **Emissão fixa.** Um bloco por minuto com 10 PTG, divididos entre as respostas aceitas naquele minuto. A emissão cai pela metade a cada 1.051.200 blocos (2 anos), com no máximo 21.024.000 PTG.
- **Carteiras com assinatura Ed25519.** A chave secreta nunca sai do aparelho. A página assina cada transferência e o servidor só confere. Cada transferência tem número de ordem, então não dá para repetir uma transferência assinada.
- **Corrente de blocos conferível.** Cada bloco guarda o hash do anterior. Qualquer pessoa baixa a corrente inteira e confere com o `verificar.py`.
- **Mural de trocas.** Troca de PTG por coisas e serviços, com anúncios assinados pela carteira. Anúncios de venda por dinheiro são recusados.

### O que ainda é centralizado (sem esconder)

Quem ganha cada bloco é decidido por um único servidor. O verificador prova que ninguém mexeu na corrente, que nunca nasceu PTG além da regra e que nenhuma moeda saiu de uma carteira sem a assinatura do dono. **Ele não prova** que as recompensas foram divididas com justiça. Descentralizar, com vários servidores independentes, é o próximo grande passo.

## Peças

| Pasta | O que é |
|---|---|
| `servidor/servidor.py` | Servidor da rede: só biblioteca padrão do Python + `cryptography`, com banco SQLite |
| `servidor/pagina.html` | Painel: rede ao vivo, minerador no navegador, carteira, mural de trocas |
| `servidor/verificar.py` | Verificador independente (só biblioteca padrão, Ed25519 em Python puro) |
| `minerador-esp32/` | Firmware PlatformIO: portal de configuração pelo celular, SHA-256 otimizado, HTTPS |
| `distribuir/COMO-INSTALAR.html` | Passo a passo para gravar a placa pelo navegador |

## Rodar o servidor

```bash
pip install cryptography
python3 servidor/servidor.py          # abre em http://localhost:7777
```

## Link público temporário

```bash
./link-publico.sh          # sobe o servidor se preciso e abre um link https://….trycloudflare.com
./link-publico.sh parar    # fecha o túnel
```

- Precisa do `cloudflared` em `~/.local/bin`. O link muda a cada reinício do túnel.
- As rotas de dono (ativar carteira antiga) continuam só no próprio PC: pedidos que chegam pelo túnel são recusados.
- Se a Arena Fichas estiver em `~/projetos/plataforma-jogos`, o cartão "Minere Portgiga" do lobby dela passa a apontar para o link novo (e some ao fechar).

## Gravar uma ESP32

Passo a passo completo, com imagens: [docs/tutorial-esp32.md](docs/tutorial-esp32.md). Resumo: baixe o `.bin` na página de [*Releases*](https://github.com/shamah10/portgiga/releases/tag/v1.5) (ou compile com `pio run` em `minerador-esp32/`) e grave pelo navegador em <https://espressif.github.io/esptool-js/>, no endereço `0x0`. Depois conecte o celular na rede Wi-Fi `Portgiga-xxxxxx` que a placa abre e preencha o Wi-Fi da casa, o endereço da carteira (`ptg1...`) e o servidor.

A placa só precisa do **endereço** da carteira, nunca da chave secreta.

## Conferir a rede

```bash
python3 verificar.py https://endereco-da-rede
```

## Licença

MIT

## App da carteira para celular (Android e iPhone)

Em `servidor/app/` (servido em `/app/`). É um app instalável (PWA): abre pelo navegador e vai para a tela
inicial como um app comum, em tela cheia, sem loja.

- Instalar: abra `https://<link público>/app/` no celular. Android (Chrome): menu ⋮ → **Instalar app** (ou o botão
  "Instalar o app" na primeira tela). iPhone (Safari): **Compartilhar → Adicionar à Tela de Início**.
- Precisa do link `https` (o túnel): a criptografia do navegador não funciona em `http://IP-DO-PC`.
- Três jeitos de começar: criar carteira nova (com cópia obrigatória da chave), importar chave secreta, ou entrar
  com a conta do site (mesma senha; a chave chega trancada e só abre no celular).
- Segurança: a chave fica trancada pelo PIN de 6 dígitos (PBKDF2 210.000 + AES-GCM), tranca sozinho após 5 min
  em segundo plano, espera crescente depois de 5 PINs errados, CSP fechado (só arquivos do próprio servidor).
- Receber: QR code (`portgiga:<endereço>?valor=X`), copiar e compartilhar. Enviar: confirmação com PIN; leitura
  de QR pela câmera onde o navegador suporta (Chrome no Android).
- Limite atual: o app instalado fica preso ao endereço do link. Quando o link do túnel muda, é preciso abrir o
  link novo e importar a chave de novo (por isso a cópia da chave é obrigatória). Um domínio fixo resolve.
- QR code: `ferramentas/empacotar_qrcode.js` junta a biblioteca `qrcode` (MIT) em `servidor/app/qrcode.js`.
- Testes: `testes/test_app.py` (rotas) e `testes/test_app_navegador.py` (fluxo completo num Chromium de celular).
