package com.durhamlistings.app;

import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.KeyEvent;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.appcompat.app.AppCompatActivity;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

public class WebViewActivity extends AppCompatActivity {

    private static final String PREFS = "durham_listings";
    private static final String KEY_SERVER = "server_url";
    private static final String KEY_API = "api_key";

    private WebView webView;
    private SwipeRefreshLayout swipeRefresh;
    private String serverUrl;
    private String apiKey;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_webview);

        serverUrl = getIntent().getStringExtra("server_url");
        apiKey = getIntent().getStringExtra("api_key");

        if (serverUrl == null || apiKey == null) {
            SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
            serverUrl = prefs.getString(KEY_SERVER, "");
            apiKey = prefs.getString(KEY_API, "");
        }

        if (serverUrl.isEmpty() || apiKey.isEmpty()) {
            startActivity(new Intent(this, PairActivity.class));
            finish();
            return;
        }

        webView = findViewById(R.id.webView);
        swipeRefresh = findViewById(R.id.swipeRefresh);

        setupWebView();
        setupSwipeRefresh();

        String appUrl = serverUrl + "?key=" + apiKey;
        webView.loadUrl(appUrl);
    }

    private void setupWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                swipeRefresh.setRefreshing(false);
            }

            @Override
            public void onReceivedError(WebView view, int errorCode,
                                        String description, String failingUrl) {
                // If the server becomes unreachable, go back to pairing
                if (errorCode == ERROR_CONNECT || errorCode == ERROR_TIMEOUT ||
                        errorCode == ERROR_HOST_LOOKUP) {
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit().clear().apply();
                    startActivity(new Intent(WebViewActivity.this, PairActivity.class));
                    finish();
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient());
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
