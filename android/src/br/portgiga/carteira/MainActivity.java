package br.portgiga.carteira;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.Manifest;
import android.content.pm.PackageManager;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;

/** Carteira Portgiga: as telas ficam dentro do app (assets/app) e são servidas em https://appassets.androidplatform.net/app/.
 *  Origem fixa = a carteira (trancada pelo PIN no armazenamento do WebView) nunca se perde quando o link do servidor muda. */
public class MainActivity extends Activity {
    private static final String HOST = "appassets.androidplatform.net";
    private static final int PEDIDO_ARQUIVO = 7;
    private static final int PEDIDO_CAMERA = 8;
    private PermissionRequest esperandoCamera;
    private WebView web;
    private ValueCallback<Uri[]> esperandoArquivo;

    @Override
    protected void onCreate(Bundle salvo) {
        super.onCreate(salvo);
        // só na versão de diagnóstico (DEPURAR=1 no construir-apk.sh) o WebView aceita o inspetor do Chrome
        if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0) WebView.setWebContentsDebuggingEnabled(true);
        web = new WebView(this);
        web.setBackgroundColor(0xFF050B08);
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);   // o PC de casa é http
        s.setMediaPlaybackRequiresUserGesture(true);
        web.addJavascriptInterface(new Nativo(), "PortgigaNativo");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                if (!HOST.equals(u.getHost()) || u.getPath() == null || !u.getPath().startsWith("/app/")) return null;
                String caminho = u.getPath().substring(1);
                if (caminho.equals("app/")) caminho = "app/index.html";
                if (caminho.contains("..")) return null;
                try {
                    InputStream in = getAssets().open(caminho);
                    Map<String, String> cab = new HashMap<>();
                    cab.put("Cache-Control", "no-cache");
                    return new WebResourceResponse(tipo(caminho), "utf-8", 200, "OK", cab, in);
                } catch (Exception e) {
                    return new WebResourceResponse("text/plain", "utf-8", 404, "Not Found", new HashMap<String, String>(), null);
                }
            }
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                if (HOST.equals(u.getHost())) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception e) { }
                return true;   // links de fora abrem no navegador
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            // câmera só para ler QR code de carteira; só a página do próprio app pode pedir
            @Override
            public void onPermissionRequest(final PermissionRequest p) {
                runOnUiThread(new Runnable() { public void run() {
                    if (!HOST.equals(p.getOrigin().getHost())) { p.deny(); return; }
                    if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                        p.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
                    } else {
                        esperandoCamera = p;
                        requestPermissions(new String[]{Manifest.permission.CAMERA}, PEDIDO_CAMERA);
                    }
                }});
            }
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (esperandoArquivo != null) esperandoArquivo.onReceiveValue(null);
                esperandoArquivo = cb;
                Intent i = new Intent(Intent.ACTION_GET_CONTENT);
                i.addCategory(Intent.CATEGORY_OPENABLE);
                i.setType("*/*");
                try { startActivityForResult(Intent.createChooser(i, "Arquivo da chave"), PEDIDO_ARQUIVO); }
                catch (Exception e) { esperandoArquivo = null; return false; }
                return true;
            }
        });
        if (salvo != null) web.restoreState(salvo);
        else web.loadUrl("https://" + HOST + "/app/");
    }

    private static String tipo(String c) {
        if (c.endsWith(".html")) return "text/html";
        if (c.endsWith(".js")) return "text/javascript";
        if (c.endsWith(".css")) return "text/css";
        if (c.endsWith(".svg")) return "image/svg+xml";
        if (c.endsWith(".png")) return "image/png";
        if (c.endsWith(".webmanifest")) return "application/manifest+json";
        return "application/octet-stream";
    }

    @Override
    protected void onActivityResult(int pedido, int resultado, Intent dados) {
        if (pedido == PEDIDO_ARQUIVO && esperandoArquivo != null) {
            Uri u = (resultado == RESULT_OK && dados != null) ? dados.getData() : null;
            esperandoArquivo.onReceiveValue(u == null ? null : new Uri[]{u});
            esperandoArquivo = null;
        }
    }

    @Override
    public void onRequestPermissionsResult(int pedido, String[] perms, int[] res) {
        if (pedido == PEDIDO_CAMERA && esperandoCamera != null) {
            if (res.length > 0 && res[0] == PackageManager.PERMISSION_GRANTED)
                esperandoCamera.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            else esperandoCamera.deny();
            esperandoCamera = null;
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle b) { super.onSaveInstanceState(b); web.saveState(b); }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack(); else moveTaskToBack(true);
    }

    @Override
    protected void onPause() { super.onPause(); web.onPause(); }
    @Override
    protected void onResume() { super.onResume(); web.onResume(); }

    /** Ponte mínima com o Android: só compartilhar texto (endereço/valor) pela janela do sistema. */
    private class Nativo {
        @JavascriptInterface
        public void sair() { runOnUiThread(new Runnable() { public void run() { moveTaskToBack(true); } }); }

        @JavascriptInterface
        public void compartilhar(final String texto) {
            runOnUiThread(new Runnable() { public void run() {
                Intent i = new Intent(Intent.ACTION_SEND);
                i.setType("text/plain");
                i.putExtra(Intent.EXTRA_TEXT, texto);
                startActivity(Intent.createChooser(i, "Compartilhar endereço"));
            }});
        }
    }
}
