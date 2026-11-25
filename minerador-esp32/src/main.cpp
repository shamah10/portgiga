// MINERADOR DA REDE PORTGIGA (PTG) - ESP32
// Pede um trabalho ao servidor, testa 0, 1, 2... ate achar o numero n em que sha256(base + n) == alvo
// e devolve a resposta. A dificuldade se ajusta sozinha para ~1 resposta a cada 10 s.
//
// Configuracao sem programar: na primeira vez (ou segurando o botao BOOT por 5 s com a placa ligada) ela cria
// a rede Wi-Fi "Portgiga-XXXXXX". Conecte o celular nela e preencha Wi-Fi, carteira, nome e servidor.
// Depois de configurada, a mesma pagina fica em http://<ip da placa>/ para ver o status ou mudar algo.
//
// SEGURANCA (v1.4): na configuracao voce cria a SENHA DA PLACA (8 a 32 caracteres). Ela e pedida para mudar
// qualquer coisa pela pagina, e vira a senha da rede "Portgiga-XXXXXX" quando a placa volta ao modo configuracao
// (sem ela, um vizinho poderia trocar a carteira). 5 senhas erradas = pagina bloqueada por 5 min. Esqueceu a senha?
// Grave de novo pelo cabo USB (quem tem a placa na mao manda nela). Por https a placa confere o certificado do servidor.
//
// v1.5: SEGUE O LINK SOZINHO. Enquanto a rede nao tem dominio proprio, o endereco publico (…trycloudflare.com)
// muda quando o PC do servidor reinicia. Se o servidor (https) parar de responder por ~1 min, a placa le
// https://portgiga.pages.dev/link.json (conferindo o certificado), pega o endereco novo, salva e volta a minerar.
// Placas da rede de casa (http://) nao mudam sozinhas.
//
// LED azul: piscada curta = resposta aceita | aceso direto = conectando no Wi-Fi |
//           piscando devagar = servidor fora do ar | duas piscadas seguidas = modo configuracao
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <mbedtls/sha256.h>
#include <time.h>
#include "sha_bloco.h"
#include "raizes.h"

static const char *VERSAO = "1.5";
static const char *LINK_FIXO = "https://portgiga.pages.dev/link.json";   // o site fixo publica o endereco de agora
static const char *SERVIDOR_PADRAO = "http://10.0.0.186:7777";
static const int LED = 2;
static const int BOTAO = 0;   // botao BOOT da placa

Preferences prefs;
WebServer web(80);
DNSServer dns;
String cfgSsid, cfgSenha, cfgCarteira, cfgNome, cfgServidor, cfgPinHash, pinAP;
uint8_t errosPin = 0;
uint32_t bloqueadoAte = 0;
String idMinerador, ultimoStatus = "iniciando";
uint32_t aceitas = 0, recusadas = 0;
float ultimaVelocidade = 0;
bool modoConfig = false;
uint32_t wifiForaDesde = 0, wifiUltimaTentativa = 0;   // reconexao no meio da mineracao
uint8_t falhasSeguidas = 0;                            // pedidos ao servidor que falharam em seguida
uint32_t ultimaBuscaLink = 0;

// ------------------------------------------------------------------ utilidades
String campo(const String &json, const char *chave) {
  String k = String("\"") + chave + "\":";
  int i = json.indexOf(k);
  if (i < 0) return "";
  i += k.length();
  while (json[i] == ' ') i++;
  if (json[i] == '"') { int f = json.indexOf('"', i + 1); return json.substring(i + 1, f); }
  int f = i;
  while (f < (int)json.length() && json[f] != ',' && json[f] != '}') f++;
  return json.substring(i, f);
}

String codificar(const String &s) {   // para mandar o nome na URL
  String r;
  const char *hex = "0123456789ABCDEF";
  for (unsigned char c : s) {
    if (isalnum(c) || c == '-' || c == '_' || c == '.') r += (char)c;
    else { r += '%'; r += hex[c >> 4]; r += hex[c & 15]; }
  }
  return r;
}

String html(const String &s) {
  String r = s;
  r.replace("&", "&amp;"); r.replace("<", "&lt;"); r.replace(">", "&gt;"); r.replace("\"", "&quot;"); r.replace("'", "&#39;");
  return r;
}

// senha da placa: guarda so o hash (sha256 de "portgiga-pin:" + id da placa + senha)
String hashPin(const String &pin) {
  String t = "portgiga-pin:" + idMinerador + ":" + pin;
  uint8_t h[32];
  mbedtls_sha256((const unsigned char *)t.c_str(), t.length(), h, 0);
  String r;
  char b[3];
  for (int i = 0; i < 32; i++) { snprintf(b, sizeof(b), "%02x", h[i]); r += b; }
  return r;
}

bool pinValido(const String &p) { return p.length() >= 8 && p.length() <= 32; }

// confere a senha com bloqueio contra tentativa e erro; devolve "" se ok, ou o motivo
String conferirPin(const String &pin) {
  if (!cfgPinHash.length()) return "";
  if (bloqueadoAte && (int32_t)(millis() - bloqueadoAte) < 0) return "Muitas senhas erradas: espere 5 minutos";
  if (hashPin(pin) == cfgPinHash) { errosPin = 0; bloqueadoAte = 0; return ""; }
  if (++errosPin >= 5) { bloqueadoAte = millis() + 300000; errosPin = 0; }
  return "Senha da placa errada";
}

bool carteiraValida(const String &c) {
  if (c.length() != 42 || !c.startsWith("ptg1")) return false;
  for (int i = 4; i < 42; i++) if (!isxdigit(c[i]) || isupper(c[i])) return false;
  return true;
}

// http:// (rede de casa) ou https:// (servidor na internet). A conexao fica aberta entre os pedidos,
// porque abrir uma conexao segura custa ~1 s na ESP32. Por https a placa CONFERE o certificado (raizes.h):
// sem isso, alguem no meio do caminho poderia trocar a carteira do pedido e ficar com as moedas.
bool httpGet(const String &url, String &resp) {
  static HTTPClient http;
  static WiFiClientSecure tls;
  static WiFiClient simples;
  static bool iniciado = false;
  if (!iniciado) {
    tls.setCACert(RAIZES_TLS);
    http.setReuse(true); http.setTimeout(10000); iniciado = true;
    if (url.startsWith("https://")) {          // relogio certo para conferir a validade do certificado
      configTime(0, 0, "pool.ntp.org", "time.google.com");
      for (int i = 0; i < 40 && time(nullptr) < 1700000000; i++) delay(250);
    }
  }
  bool ok = url.startsWith("https://") ? http.begin(tls, url) : http.begin(simples, url);
  if (!ok) return false;
  int cod = http.GET();
  if (cod == 200) resp = http.getString();
  http.end();
  if (cod < 0) { tls.stop(); simples.stop(); }   // conexao caiu: a proxima abre de novo
  return cod == 200;
}

// so aceita endereco https do tunel da Cloudflare ou do dominio da rede (o link.json nao pode mandar a placa para qualquer lugar)
bool enderecoPublicoOk(const String &u) {
  if (!u.startsWith("https://") || u.length() > 120) return false;
  String host = u.substring(8);
  int barra = host.indexOf('/');
  if (barra >= 0) host = host.substring(0, barra);
  for (unsigned i = 0; i < host.length(); i++) {
    char c = host[i];
    if (!(isalnum(c) || c == '-' || c == '.')) return false;
  }
  return host.endsWith(".trycloudflare.com") || host == "portgiga.com.br" || host.endsWith(".portgiga.com.br");
}

// servidor publico sumiu: le o endereco de agora no site fixo. Devolve true se mudou (e ja salvou).
bool seguirLink() {
  if (!cfgServidor.startsWith("https://")) return false;              // rede de casa (http): fica como esta
  if (ultimaBuscaLink && millis() - ultimaBuscaLink < 120000) return false;   // no maximo a cada 2 min
  ultimaBuscaLink = millis();
  if (time(nullptr) < 1700000000) {
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    for (int i = 0; i < 40 && time(nullptr) < 1700000000; i++) delay(250);
  }
  WiFiClientSecure cli;
  cli.setCACert(RAIZES_TLS);
  HTTPClient http;
  http.setTimeout(10000);
  if (!http.begin(cli, LINK_FIXO)) return false;
  int cod = http.GET();
  String resp = cod == 200 ? http.getString() : "";
  http.end();
  cli.stop();
  String novo = campo(resp, "portgiga");
  while (novo.endsWith("/")) novo.remove(novo.length() - 1);
  Serial.println("procurando o endereco novo em " + String(LINK_FIXO) + ": " + (novo.length() ? novo : String("(nada)")));
  if (!enderecoPublicoOk(novo) || novo == cfgServidor) return false;
  prefs.begin("portgiga", false);
  prefs.putString("servidor", novo);
  prefs.end();
  Serial.println("endereco do servidor mudou: " + cfgServidor + " -> " + novo + " (reiniciando)");
  return true;
}

void hexParaBytes(const String &h, uint8_t *out) {
  for (int i = 0; i < 32; i++) out[i] = strtoul(h.substring(2 * i, 2 * i + 2).c_str(), nullptr, 16);
}

void carregarConfig() {
  prefs.begin("portgiga", true);
  cfgPinHash = prefs.getString("pin", "");
  pinAP = prefs.getString("pinap", "");      // a rede Wi-Fi de configuracao precisa da senha em si (WPA2)
  cfgSsid = prefs.getString("ssid", "");
  cfgSenha = prefs.getString("senha", "");
  cfgCarteira = prefs.getString("carteira", "");
  cfgNome = prefs.getString("nome", "");
  cfgServidor = prefs.getString("servidor", SERVIDOR_PADRAO);
  prefs.end();
}

void salvarConfig(const String &ssid, const String &senha, const String &carteira, const String &nome, String servidor, const String &novoPin = "") {
  servidor.trim();
  while (servidor.endsWith("/")) servidor.remove(servidor.length() - 1);
  if (!servidor.startsWith("http")) servidor = "http://" + servidor;
  prefs.begin("portgiga", false);
  prefs.putString("ssid", ssid);
  prefs.putString("senha", senha);
  prefs.putString("carteira", carteira);
  prefs.putString("nome", nome);
  prefs.putString("servidor", servidor);
  if (novoPin.length()) { prefs.putString("pin", hashPin(novoPin)); prefs.putString("pinap", novoPin); }
  prefs.putBool("config", false);
  prefs.end();
}

// segurar o BOOT por 5 s com a placa ligada = volta para o modo configuracao
void checarBotao() {
  static uint32_t apertadoDesde = 0;
  if (digitalRead(BOTAO) == LOW) {
    if (!apertadoDesde) apertadoDesde = millis();
    if (millis() - apertadoDesde > 5000) {
      Serial.println("BOOT segurado por 5 s: entrando no modo configuracao");
      prefs.begin("portgiga", false);
      prefs.putBool("config", true);
      prefs.end();
      for (int i = 0; i < 10; i++) { digitalWrite(LED, i % 2); delay(80); }
      ESP.restart();
    }
  } else apertadoDesde = 0;
}

// ------------------------------------------------------------------ pagina de configuracao
const char CSS[] PROGMEM = "<style>body{font-family:system-ui,sans-serif;background:#0a120e;color:#e6f0ea;margin:0;padding:16px}"
  ".c{max-width:460px;margin:0 auto;background:#111d17;border:1px solid #1d2e25;border-radius:16px;padding:18px}"
  "h1{font-size:20px;margin:0 0 4px}p{color:#93a69b;font-size:13px}label{display:block;font-size:12px;color:#93a69b;margin-top:12px}"
  "input,select{width:100%;box-sizing:border-box;padding:11px;border-radius:10px;border:1px solid #1d2e25;background:#0a120e;color:#e6f0ea;font-size:15px;margin-top:4px}"
  "button{width:100%;margin-top:18px;padding:13px;border:0;border-radius:12px;background:#3ccf7d;color:#06210f;font-weight:700;font-size:16px}"
  ".s{background:#14301f;border-radius:10px;padding:10px;font-size:13px;margin-top:10px}.e{color:#ef6b5c}b{color:#fff}</style>";

void paginaConfig() {
  String redes;
  int n = WiFi.scanComplete();
  if (n == WIFI_SCAN_FAILED || n == WIFI_SCAN_RUNNING) n = 0;
  for (int i = 0; i < n; i++) {
    String s = WiFi.SSID(i);
    if (s.length() && redes.indexOf(">" + html(s) + "<") < 0) redes += "<option value=\"" + html(s) + "\">" + html(s) + "</option>";
  }
  WiFi.scanNetworks(true);   // atualiza a lista para a proxima vez
  String h = F("<!doctype html><html lang='pt-br'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Minerador Portgiga</title>");
  h += FPSTR(CSS);
  h += F("</head><body><div class='c'><h1>&#127795; Minerador Portgiga</h1><p>Preencha e toque em Salvar. A placa reinicia e comeca a minerar.</p>");
  if (!modoConfig) {
    h += "<div class='s'>Status: <b>" + html(ultimoStatus) + "</b><br>Respostas aceitas: <b>" + String(aceitas) + "</b> | recusadas: " + String(recusadas) +
         "<br>Velocidade: <b>" + String(ultimaVelocidade / 1000.0, 1) + " kH/s</b><br>ID: " + idMinerador + " | versao " + VERSAO + "</div>";
  }
  if (web.hasArg("erro")) h += "<p class='e'>" + html(web.arg("erro")) + "</p>";
  h += F("<form method='post' action='/salvar'><label>Rede Wi-Fi da casa (2,4 GHz)</label><input name='ssid' list='redes' required value='");
  h += html(cfgSsid) + "'><datalist id='redes'>" + redes + "</datalist>";
  h += F("<label>Senha do Wi-Fi</label><input name='senha' type='password' placeholder='(deixe vazio para manter a atual)'>");
  h += F("<label>Carteira que recebe as moedas (ptg1...)</label><input name='carteira' required placeholder='ptg1...' value='");
  h += html(cfgCarteira) + F("'><label>Nome deste minerador no painel</label><input name='nome' maxlength='24' placeholder='ex.: Placa do Joao' value='");
  h += html(cfgNome) + F("'><label>Endereco do servidor da rede</label><input name='servidor' required value='");
  h += html(cfgServidor) + "'>";
  if (cfgPinHash.length()) {
    h += F("<label>Senha da placa (obrigatoria para salvar)</label><input name='pin' type='password' required autocomplete='current-password'>"
           "<label>Nova senha da placa (opcional, 8 a 32 caracteres)</label><input name='novopin' type='password' minlength='8' maxlength='32' autocomplete='new-password'>");
  } else {
    h += F("<label>Crie a SENHA DA PLACA (8 a 32 caracteres) - guarde, ela protege a placa</label>"
           "<input name='novopin' type='password' required minlength='8' maxlength='32' autocomplete='new-password'>"
           "<label>Repita a senha da placa</label><input name='novopin2' type='password' required minlength='8' maxlength='32'>");
  }
  h += F("<button>Salvar e comecar a minerar</button></form>"
       "<p>Nao tem carteira? Abra o painel da rede Portgiga, va em Carteira e toque em Criar carteira nova. Guarde a chave secreta.</p></div></body></html>");
  web.send(200, "text/html; charset=utf-8", h);
}

void voltarComErro(const char *msg) {
  String m = msg;
  m.replace(" ", "+");
  web.sendHeader("Location", "/?erro=" + m);
  web.send(303);
}

void salvarDoFormulario() {
  String motivo = conferirPin(web.arg("pin"));
  if (motivo.length()) { voltarComErro(motivo.c_str()); return; }
  String novoPin = web.arg("novopin");
  if (!cfgPinHash.length()) {                       // primeira configuracao: a senha da placa e obrigatoria
    if (!pinValido(novoPin)) { voltarComErro("Crie uma senha da placa com 8 a 32 caracteres"); return; }
    if (novoPin != web.arg("novopin2")) { voltarComErro("As senhas da placa nao sao iguais"); return; }
  } else if (novoPin.length() && !pinValido(novoPin)) { voltarComErro("A nova senha da placa precisa de 8 a 32 caracteres"); return; }
  String carteira = web.arg("carteira");
  carteira.trim();
  carteira.toLowerCase();
  String senha = web.arg("senha");
  if (!senha.length() && web.arg("ssid") == cfgSsid) senha = cfgSenha;
  if (!carteiraValida(carteira)) {
    web.sendHeader("Location", "/?erro=Carteira+invalida:+deve+comecar+com+ptg1+e+ter+42+caracteres");
    web.send(303);
    return;
  }
  salvarConfig(web.arg("ssid"), senha, carteira, web.arg("nome").substring(0, 24), web.arg("servidor"), novoPin);
  web.send(200, "text/html; charset=utf-8", String(F("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>")) +
           FPSTR(CSS) + F("<div class='c'><h1>Salvo!</h1><p>A placa vai reiniciar e conectar no seu Wi-Fi. Quando o LED azul der piscadas curtas, ela esta minerando. "
           "Confira o nome dela no painel da rede.</p></div>"));
  delay(1500);
  ESP.restart();
}

void iniciarModoConfig() {
  modoConfig = true;
  String rede = "Portgiga-" + idMinerador.substring(6);
  WiFi.mode(WIFI_AP_STA);
  // placa nova (sem senha): rede aberta so para a primeira configuracao. Placa que ja tem senha: a rede usa ela.
  if (cfgPinHash.length() && pinAP.length() >= 8) WiFi.softAP(rede.c_str(), pinAP.c_str());
  else WiFi.softAP(rede.c_str());
  WiFi.scanNetworks(true);
  dns.start(53, "*", WiFi.softAPIP());   // qualquer endereco digitado cai na pagina de configuracao
  web.on("/", paginaConfig);
  web.on("/salvar", HTTP_POST, salvarDoFormulario);
  web.onNotFound([] { web.sendHeader("Location", "http://192.168.4.1/"); web.send(302); });
  web.begin();
  Serial.println("MODO CONFIGURACAO: conecte na rede Wi-Fi '" + rede + "' e abra http://192.168.4.1/");
  uint32_t inicio = millis();
  for (;;) {
    dns.processNextRequest();
    web.handleClient();
    uint32_t t = millis() % 1200;
    digitalWrite(LED, t < 100 || (t > 200 && t < 300));
    // 10 min sem ninguem configurar e ja existe configuracao: tenta minerar de novo (ex.: o Wi-Fi da casa caiu)
    if (cfgSsid.length() && carteiraValida(cfgCarteira) && millis() - inicio > 600000) ESP.restart();
    delay(2);
  }
}

// configuracao pelo cabo USB (para quem grava muitas placas). Quem tem a placa na mao manda nela:
// CONFIG rede|senha|carteira|nome|servidor|senha_da_placa   (a senha da placa e opcional; vazia = mantem a atual)
void checarSerial() {
  if (!Serial.available()) return;
  String l = Serial.readStringUntil('\n');
  l.trim();
  if (!l.startsWith("CONFIG ")) return;
  String p[6];
  int ini = 7;
  for (int i = 0; i < 6; i++) {
    int f = l.indexOf('|', ini);
    p[i] = f < 0 ? l.substring(ini) : l.substring(ini, f);
    ini = f + 1;
    if (f < 0) break;
  }
  if (!carteiraValida(p[2])) { Serial.println("CONFIG recusada: carteira invalida"); return; }
  if (p[5].length() && !pinValido(p[5])) { Serial.println("CONFIG recusada: senha da placa precisa de 8 a 32 caracteres"); return; }
  salvarConfig(p[0], p[1], p[2], p[3], p[4].length() ? p[4] : SERVIDOR_PADRAO, p[5]);
  Serial.println("CONFIG salva, reiniciando");
  delay(300);
  ESP.restart();
}

// ------------------------------------------------------------------ mineracao
void setup() {
  pinMode(LED, OUTPUT);
  pinMode(BOTAO, INPUT_PULLUP);
  digitalWrite(LED, HIGH);
  Serial.begin(115200);
  Serial.setTimeout(200);
  delay(300);
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char id[20];
  snprintf(id, sizeof(id), "esp32-%02x%02x%02x", mac[3], mac[4], mac[5]);
  idMinerador = id;
  carregarConfig();
  Serial.printf("\nMINERADOR PORTGIGA v%s %s\n", VERSAO, id);
  // 2 s para mandar CONFIG pelo cabo antes de comecar
  for (uint32_t t = millis(); millis() - t < 2000;) { checarSerial(); delay(10); }

  prefs.begin("portgiga", true);
  bool pediuConfig = prefs.getBool("config", false);
  prefs.end();
  if (pediuConfig || !cfgSsid.length() || !carteiraValida(cfgCarteira)) iniciarModoConfig();

  Serial.println("carteira " + cfgCarteira + " | servidor " + cfgServidor + " | Wi-Fi " + cfgSsid);
  WiFi.mode(WIFI_STA);
  WiFi.begin(cfgSsid.c_str(), cfgSenha.c_str());
  // placa ja configurada nunca abre o modo configuracao sozinha: se o Wi-Fi caiu (falta de luz, roteador
  // reiniciando), fica tentando de novo a cada 30 s. O modo configuracao so abre segurando o BOOT por 5 s.
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    checarSerial();
    checarBotao();
    digitalWrite(LED, (millis() / 500) % 2);
    if (millis() - t0 > 30000) {
      Serial.println("Wi-Fi ainda fora do ar: tentando de novo");
      WiFi.disconnect(true);
      delay(200);
      WiFi.mode(WIFI_STA);
      WiFi.begin(cfgSsid.c_str(), cfgSenha.c_str());
      t0 = millis();
    }
  }
  Serial.println("Wi-Fi conectado: " + WiFi.localIP().toString() + " | pagina da placa: http://" + WiFi.localIP().toString() + "/");
  delay(1500);   // da tempo da rede se acertar antes do primeiro pedido ao servidor
  web.on("/", paginaConfig);
  web.on("/salvar", HTTP_POST, salvarDoFormulario);
  web.begin();
  digitalWrite(LED, LOW);
}

void loop() {
  web.handleClient();
  checarSerial();
  checarBotao();
  if (WiFi.status() == WL_CONNECTED) wifiForaDesde = 0;
  else {
    // caiu no meio da mineracao: reconecta; se passar de 1 min fora, recomeca a conexao do zero a cada 30 s
    uint32_t &foraDesde = wifiForaDesde, &ultimaTentativa = wifiUltimaTentativa;
    if (!foraDesde) { foraDesde = millis(); ultimaTentativa = 0; }
    ultimoStatus = "sem Wi-Fi";
    digitalWrite(LED, HIGH);
    if (millis() - foraDesde > 60000 && millis() - ultimaTentativa > 30000) {
      Serial.println("Wi-Fi fora ha mais de 1 min: reconectando do zero");
      WiFi.disconnect(true);
      delay(200);
      WiFi.mode(WIFI_STA);
      WiFi.begin(cfgSsid.c_str(), cfgSenha.c_str());
      ultimaTentativa = millis();
    } else if (millis() - ultimaTentativa > 30000 || !ultimaTentativa) {
      WiFi.reconnect();
      ultimaTentativa = millis();
    }
    delay(500);
    return;
  }

  String resp;
  if (!httpGet(cfgServidor + "/api/trabalho?carteira=" + cfgCarteira + "&aparelho=esp32&id=" + idMinerador + "&nome=" + codificar(cfgNome), resp)) {
    ultimoStatus = "servidor fora do ar (" + cfgServidor + ")";
    Serial.println(ultimoStatus + ", tentando em 5 s");
    if (falhasSeguidas < 255) falhasSeguidas++;
    if (falhasSeguidas >= 10 && seguirLink()) { delay(300); ESP.restart(); }   // ~1 min sem resposta
    for (int i = 0; i < 5; i++) { digitalWrite(LED, HIGH); delay(250); web.handleClient(); checarBotao(); digitalWrite(LED, LOW); delay(750); }
    return;
  }
  falhasSeguidas = 0;
  String erro = campo(resp, "erro");
  if (erro.length()) { ultimoStatus = "servidor recusou: " + erro; Serial.println(ultimoStatus); delay(10000); return; }
  String trabalho = campo(resp, "trabalho"), base = campo(resp, "base");
  uint32_t limite = strtoul(campo(resp, "max").c_str(), nullptr, 10);
  uint8_t alvo[32];
  hexParaBytes(campo(resp, "alvo"), alvo);
  uint32_t alvoW[8], h[8];
  for (int i = 0; i < 8; i++) alvoW[i] = ((uint32_t)alvo[4 * i] << 24) | ((uint32_t)alvo[4 * i + 1] << 16) | ((uint32_t)alvo[4 * i + 2] << 8) | alvo[4 * i + 3];
  ultimoStatus = "minerando";

  uint8_t bloco[64];
  int lb = base.length();
  if (lb > 44) { Serial.println("trabalho com base grande demais"); delay(5000); return; }
  memcpy(bloco, base.c_str(), lb);
  uint32_t t0 = millis();
  int64_t achado = -1;
  for (uint32_t n = 0; n < limite; n++) {
    montarBloco(bloco, lb, n);
    sha256Bloco(bloco, h);
    if (h[0] == alvoW[0] && memcmp(h, alvoW, 32) == 0) { achado = n; break; }
    if ((n & 0x3FFF) == 0) { yield(); web.handleClient(); checarBotao(); }
  }
  float seg = (millis() - t0) / 1000.0;
  if (achado < 0) { Serial.println("nao achou (trabalho invalido?)"); return; }
  ultimaVelocidade = (achado + 1) / max(seg, 0.001f);

  if (httpGet(cfgServidor + "/api/resultado?trabalho=" + trabalho + "&nonce=" + String((uint32_t)achado), resp) && campo(resp, "aceito") == "true") {
    aceitas++;
    digitalWrite(LED, HIGH); delay(60); digitalWrite(LED, LOW);
    Serial.printf("ACEITA #%u | %.1f s | %.1f kH/s | saldo %s PTG\n", aceitas, seg, ultimaVelocidade / 1000.0, campo(resp, "saldo").c_str());
  } else {
    recusadas++;
    Serial.println("recusada: " + resp);
  }
}
