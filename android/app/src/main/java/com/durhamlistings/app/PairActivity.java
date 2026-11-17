package com.durhamlistings.app;

import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.View;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.appcompat.app.AppCompatActivity;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.textfield.TextInputEditText;
import com.journeyapps.barcodescanner.ScanContract;
import com.journeyapps.barcodescanner.ScanIntentResult;
import com.journeyapps.barcodescanner.ScanOptions;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class PairActivity extends AppCompatActivity {

    private static final String PREFS = "durham_listings";
    private static final String KEY_SERVER = "server_url";
    private static final String KEY_API = "api_key";

    private TextInputEditText inputServerUrl, inputApiKey;
    private TextView txtError;
    private MaterialButton btnConnect, btnScanQr;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    private final ActivityResultLauncher<ScanOptions> qrLauncher =
            registerForActivityResult(new ScanContract(), this::onQrResult);

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // If already paired, go straight to the app
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        String savedUrl = prefs.getString(KEY_SERVER, "");
        String savedKey = prefs.getString(KEY_API, "");
        if (!savedUrl.isEmpty() && !savedKey.isEmpty()) {
            verifyAndLaunch(savedUrl, savedKey, true);
            return;
        }

        setContentView(R.layout.activity_pair);

        inputServerUrl = findViewById(R.id.inputServerUrl);
        inputApiKey = findViewById(R.id.inputApiKey);
        txtError = findViewById(R.id.txtError);
        btnConnect = findViewById(R.id.btnConnect);
        btnScanQr = findViewById(R.id.btnScanQr);

        btnScanQr.setOnClickListener(v -> startQrScan());
        btnConnect.setOnClickListener(v -> manualConnect());
    }

    private void startQrScan() {
        ScanOptions options = new ScanOptions();
        options.setDesiredBarcodeFormats(ScanOptions.QR_CODE);
        options.setPrompt("Point at the QR code on the desktop app");
        options.setCameraId(0);
        options.setBeepEnabled(false);
        options.setBarcodeImageEnabled(false);
        options.setOrientationLocked(true);
        qrLauncher.launch(options);
    }

    private void onQrResult(ScanIntentResult result) {
        if (result.getContents() == null) return;

        try {
            JSONObject json = new JSONObject(result.getContents());
            String url = json.optString("url", "");
            String key = json.optString("key", "");

            if (url.isEmpty() || key.isEmpty()) {
                showError(getString(R.string.invalid_qr));
                return;
            }

            inputServerUrl.setText(url);
            inputApiKey.setText(key);
            verifyAndLaunch(url, key, false);

        } catch (Exception e) {
            showError(getString(R.string.invalid_qr));
        }
    }

    private void manualConnect() {
        String url = inputServerUrl.getText() != null ? inputServerUrl.getText().toString().trim() : "";
        String key = inputApiKey.getText() != null ? inputApiKey.getText().toString().trim() : "";

        if (url.isEmpty() || key.isEmpty()) {
            showError("Please enter both the server URL and API key.");
            return;
        }

        // Strip trailing slash
        if (url.endsWith("/")) url = url.substring(0, url.length() - 1);

        verifyAndLaunch(url, key, false);
    }

    private void verifyAndLaunch(String serverUrl, String apiKey, boolean silent) {
        if (!silent) {
            btnConnect.setEnabled(false);
            btnScanQr.setEnabled(false);
            btnConnect.setText(R.string.connecting);
            txtError.setVisibility(View.GONE);
        }

        executor.execute(() -> {
            boolean ok = false;
            try {
                URL url = new URL(serverUrl + "/api/key-check?key=" + apiKey);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(5000);
                int code = conn.getResponseCode();
                ok = (code == 200);
                conn.disconnect();
            } catch (Exception e) {
                // Connection failed
            }

            final boolean success = ok;
            runOnUiThread(() -> {
                if (success) {
                    // Save credentials
                    getSharedPreferences(PREFS, MODE_PRIVATE)
                            .edit()
                            .putString(KEY_SERVER, serverUrl)
                            .putString(KEY_API, apiKey)
                            .apply();

                    // Launch the app
                    Intent intent = new Intent(this, WebViewActivity.class);
                    intent.putExtra("server_url", serverUrl);
                    intent.putExtra("api_key", apiKey);
                    startActivity(intent);
                    finish();
                } else {
                    if (silent) {
                        // Stored credentials are bad — show pairing screen
                        getSharedPreferences(PREFS, MODE_PRIVATE)
                                .edit().clear().apply();
                        setContentView(R.layout.activity_pair);
                        inputServerUrl = findViewById(R.id.inputServerUrl);
                        inputApiKey = findViewById(R.id.inputApiKey);
                        txtError = findViewById(R.id.txtError);
                        btnConnect = findViewById(R.id.btnConnect);
                        btnScanQr = findViewById(R.id.btnScanQr);
                        btnScanQr.setOnClickListener(v -> startQrScan());
                        btnConnect.setOnClickListener(v -> manualConnect());
                    } else {
                        showError(getString(R.string.connection_failed));
                        btnConnect.setEnabled(true);
                        btnScanQr.setEnabled(true);
                        btnConnect.setText(R.string.connect);
                    }
                }
            });
        });
    }

    private void showError(String msg) {
        txtError.setText(msg);
        txtError.setVisibility(View.VISIBLE);
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        executor.shutdown();
    }
}
