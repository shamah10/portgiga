#!/usr/bin/env bash
# Monta o APK da Carteira Portgiga (sem Gradle): aapt2 + javac + d8 + zipalign + apksigner.
# Ferramentas em ~/.local/share/android-build (JDK 17 e build-tools/platform 34 baixados do Ubuntu e do Google).
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
FERR="$HOME/.local/share/android-build"
export JAVA_HOME="$FERR/jdk/usr/lib/jvm/java-17-openjdk-amd64"; export PATH="$JAVA_HOME/bin:$PATH"
BT="$FERR/sdk/android-14"; JAR="$FERR/sdk/android-34/android.jar"
OBRA="$AQUI/obra"; rm -rf "$OBRA"; mkdir -p "$OBRA"/{res,assets/app,gen,classes,dex}

# telas do app (as mesmas do servidor) + endereços conhecidos do servidor
cp -r "$AQUI/../servidor/app/." "$OBRA/assets/app/"
rm -f "$OBRA/assets/app/sw.js" "$OBRA/assets/app/"*.apk
LINK="$(cat "$HOME/.local/share/portgiga/tunel/link.txt" 2>/dev/null || true)"
IP_CASA="$(hostname -I | awk '{print $1}')"
printf 'window.PTG_SERVIDORES = %s;\n' "$(python3 -c 'import json,sys; print(json.dumps([u for u in sys.argv[1:] if u]))' "$LINK" "http://$IP_CASA:7777")" > "$OBRA/assets/app/embutido.js"
printf "window.PTG_LINK_FIXO = %s;\n" "\"https://portgiga.pages.dev/link.json\"" >> "$OBRA/assets/app/embutido.js"   # endereço fixo grátis: o link do dia
sed -i 's#<script src="/app/qrcode.js"></script>#<script src="/app/embutido.js"></script>\n<script src="/app/qrcode.js"></script>#' "$OBRA/assets/app/index.html"
# no app as telas vêm de dentro do celular e o servidor é outro endereço: libera só a conexão (https e o PC de casa em http)
sed -i "s#connect-src 'self'#connect-src 'self' https: http:#" "$OBRA/assets/app/index.html"
grep -q "connect-src 'self' https: http:" "$OBRA/assets/app/index.html"
grep -q embutido.js "$OBRA/assets/app/index.html"

# ícones
cp -r "$AQUI/res/." "$OBRA/res/"
python3 - "$AQUI/../servidor/app/icones/icone-512.png" "$OBRA/res" <<'PY'
import sys, os
from PIL import Image
src, res = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGBA")
for pasta, tam in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)):
    d = os.path.join(res, "mipmap-" + pasta); os.makedirs(d, exist_ok=True)
    im.resize((tam, tam), Image.LANCZOS).save(os.path.join(d, "ic_launcher.png"))
PY

"$BT/aapt2" compile --dir "$OBRA/res" -o "$OBRA/res.zip"
"$BT/aapt2" link -o "$OBRA/base.apk" -I "$JAR" --manifest "$AQUI/AndroidManifest.xml" -A "$OBRA/assets" \
  --java "$OBRA/gen" $([ "${DEPURAR:-0}" = 1 ] && echo --debug-mode) --min-sdk-version 26 --target-sdk-version 34 "$OBRA/res.zip"
javac -nowarn -source 8 -target 8 -bootclasspath "$JAR" -d "$OBRA/classes" \
  $(find "$AQUI/src" "$OBRA/gen" -name '*.java') 2>&1 | grep -v "^warning\|^aviso\|^1 warning\|^1 aviso" || true
"$BT/d8" --release --min-api 26 --lib "$JAR" --output "$OBRA/dex" $(find "$OBRA/classes" -name '*.class')
(cd "$OBRA/dex" && zip -q "$OBRA/base.apk" classes.dex)
"$BT/zipalign" -p -f 4 "$OBRA/base.apk" "$OBRA/alinhado.apk"

# assinatura: a mesma chave sempre (senão o Android não aceita atualizar o app)
KS="$AQUI/assinatura.jks"; SENHA_ARQ="$AQUI/assinatura.senha"
if [ ! -f "$KS" ]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$SENHA_ARQ"; chmod 600 "$SENHA_ARQ"
  keytool -genkeypair -keystore "$KS" -storepass "$(cat "$SENHA_ARQ")" -keypass "$(cat "$SENHA_ARQ")" -alias portgiga \
    -keyalg RSA -keysize 3072 -validity 10000 -dname "CN=Portgiga, O=Rede Portgiga, C=BR" >/dev/null 2>&1
  chmod 600 "$KS"
fi
SAIDA="$AQUI/Portgiga-Carteira.apk"
"$BT/apksigner" sign --ks "$KS" --ks-pass "file:$SENHA_ARQ" --ks-key-alias portgiga --out "$SAIDA" "$OBRA/alinhado.apk"
cp "$SAIDA" "$AQUI/../servidor/app/Portgiga-Carteira.apk"   # para baixar pelo site: /app/Portgiga-Carteira.apk
"$BT/apksigner" verify "$SAIDA" && echo "APK pronto: $SAIDA ($(du -h "$SAIDA" | cut -f1)) servidores: $LINK http://$IP_CASA:7777"
