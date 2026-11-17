package com.durhamlistings.app;

import android.content.Intent;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.view.KeyEvent;
import android.webkit.JavascriptInterface;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.appcompat.app.AppCompatActivity;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

public class WebViewActivity extends AppCompatActivity {

    private WebView webView;
    private SwipeRefreshLayout swipeRefresh;
    private String serverUrl;
    private String apiKey;
    private String certFp;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_webview);

        // Only PairActivity starts this screen, always after reaching the server
        serverUrl = getIntent().getStringExtra("server_url");
        apiKey = getIntent().getStringExtra("api_key");
        certFp = getIntent().getStringExtra("cert_fp");

        if (serverUrl == null || apiKey == null) {
            startActivity(new Intent(this, PairActivity.class));
            finish();
            return;
        }

        webView = findViewById(R.id.webView);
        swipeRefresh = findViewById(R.id.swipeRefresh);

        setupWebView();
        setupSwipeRefresh();

        // The key rides in the fragment, which the page reads but the browser never sends
        webView.loadUrl(serverUrl + "/#key=" + Uri.encode(apiKey));
    }

    private void setupWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        // Listing photos come from the listing sites; allow plain-http photos but never scripts
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        settings.setAllowFileAccess(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                swipeRefresh.setRefreshing(false);
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                // The server's certificate is self-signed; accept it only if it is the paired one
                if (ServerLink.matches(error.getCertificate(), certFp)) {
                    handler.proceed();
                } else {
                    handler.cancel();
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                // If the server becomes unreachable, offer a retry; the pairing itself is kept.
                // Failed photos and other sub-resources are left to the page.
                int code = error.getErrorCode();
                if (request.isForMainFrame() && (code == ERROR_CONNECT || code == ERROR_TIMEOUT ||
                        code == ERROR_HOST_LOOKUP)) {
                    Intent intent = new Intent(WebViewActivity.this, PairActivity.class);
                    intent.putExtra(PairActivity.EXTRA_CONNECTION_LOST, true);
                    startActivity(intent);
                    finish();
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient());
        webView.addJavascriptInterface(new AppBridge(), "AndroidApp");
    }

    /** Actions the page's connection menu hands to the app, which owns the saved pairing. */
    private final class AppBridge {
        @JavascriptInterface
        public void reconnect() {
            runOnUiThread(() -> {
                if (!fromServerPage()) return;
                // PairActivity tries every saved address and keeps the first that answers
                startActivity(new Intent(WebViewActivity.this, PairActivity.class));
                finish();
            });
        }

        @JavascriptInterface
        public void disconnect() {
            runOnUiThread(() -> {
                if (!fromServerPage()) return;
                ServerLink.confirmForget(WebViewActivity.this, () -> {
                    webView.clearCache(true);
                    startActivity(new Intent(WebViewActivity.this, PairActivity.class)
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK));
                    finish();
                });
            });
        }
    }

    /** The bridge is only honoured while the paired server's own page is showing. */
    private boolean fromServerPage() {
        String current = webView.getUrl();
        return current != null && current.startsWith(serverUrl + "/");
    }

    private void setupSwipeRefresh() {
        swipeRefresh.setColorSchemeResources(R.color.accent);
        swipeRefresh.setProgressBackgroundColorSchemeResource(R.color.bg_card);
        swipeRefresh.setOnRefreshListener(() -> webView.reload());
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
