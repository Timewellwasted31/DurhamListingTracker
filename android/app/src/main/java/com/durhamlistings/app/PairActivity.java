package com.durhamlistings.app;

import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.View;
import android.widget.TextView;

import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.appcompat.app.AppCompatActivity;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.textfield.TextInputEditText;
import com.journeyapps.barcodescanner.ScanContract;
import com.journeyapps.barcodescanner.ScanIntentResult;
import com.journeyapps.barcodescanner.ScanOptions;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class PairActivity extends AppCompatActivity {

    static final String EXTRA_CONNECTION_LOST = "connection_lost";

    private enum Mode { QR, MANUAL, UNREACHABLE }

    private View groupQr, groupManual, groupUnreachable;
    private TextInputEditText inputServerUrl, inputApiKey;
    private TextView txtError;
    private MaterialButton btnScanQr, btnConnect, btnRetry, btnForget, btnToggleMode;
    private Mode mode = Mode.QR;
    private CharSequence busyLabel;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    private List<String> savedUrls = Collections.emptyList();
    private String savedKey = "";
    private String savedFp = "";

    private final ActivityResultLauncher<ScanOptions> qrLauncher =
            registerForActivityResult(new ScanContract(), this::onQrResult);

    private final OnBackPressedCallback backToQr = new OnBackPressedCallback(false) {
        @Override
        public void handleOnBackPressed() {
            showMode(Mode.QR);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getOnBackPressedDispatcher().addCallback(this, backToQr);

        SharedPreferences prefs = getSharedPreferences(ServerLink.PREFS, MODE_PRIVATE);
        savedUrls = loadUrls(prefs);
        savedKey = prefs.getString(ServerLink.KEY_API, "");
        savedFp = prefs.getString(ServerLink.KEY_FP, "");

        boolean paired = !savedUrls.isEmpty() && !savedKey.isEmpty();
        if (paired && !getIntent().getBooleanExtra(EXTRA_CONNECTION_LOST, false)) {
            verifyAndLaunch(savedUrls, savedKey, savedFp, null);
            return;
        }

        showPairScreen();
        showMode(paired ? Mode.UNREACHABLE : Mode.QR);
    }

    private static List<String> loadUrls(SharedPreferences prefs) {
        List<String> urls = new ArrayList<>();
        try {
            JSONArray arr = new JSONArray(prefs.getString(ServerLink.KEY_URLS, "[]"));
            for (int i = 0; i < arr.length(); i++) urls.add(arr.getString(i));
        } catch (Exception ignored) {
        }
        String last = prefs.getString(ServerLink.KEY_SERVER, "");
        if (!last.isEmpty() && !urls.contains(last)) urls.add(0, last);
        return urls;
    }

    private void showPairScreen() {
        setContentView(R.layout.activity_pair);
        groupQr = findViewById(R.id.groupQr);
        groupManual = findViewById(R.id.groupManual);
        groupUnreachable = findViewById(R.id.groupUnreachable);
        inputServerUrl = findViewById(R.id.inputServerUrl);
        inputApiKey = findViewById(R.id.inputApiKey);
        txtError = findViewById(R.id.txtError);
        btnScanQr = findViewById(R.id.btnScanQr);
        btnConnect = findViewById(R.id.btnConnect);
        btnRetry = findViewById(R.id.btnRetry);
        btnForget = findViewById(R.id.btnForget);
        btnToggleMode = findViewById(R.id.btnToggleMode);

        btnScanQr.setOnClickListener(v -> startQrScan());
        btnConnect.setOnClickListener(v -> manualConnect());
        btnRetry.setOnClickListener(v -> verifyAndLaunch(savedUrls, savedKey, savedFp, btnRetry));
        btnForget.setOnClickListener(v -> confirmForget());
        btnToggleMode.setOnClickListener(v -> showMode(mode == Mode.MANUAL ? Mode.QR : Mode.MANUAL));
    }

    private void showMode(Mode next) {
        mode = next;
        groupQr.setVisibility(next == Mode.QR ? View.VISIBLE : View.GONE);
        groupManual.setVisibility(next == Mode.MANUAL ? View.VISIBLE : View.GONE);
        groupUnreachable.setVisibility(next == Mode.UNREACHABLE ? View.VISIBLE : View.GONE);
        btnToggleMode.setVisibility(next == Mode.UNREACHABLE ? View.GONE : View.VISIBLE);
        btnToggleMode.setText(next == Mode.MANUAL ? R.string.back_to_qr : R.string.enter_manually);
        txtError.setVisibility(View.GONE);
        backToQr.setEnabled(next == Mode.MANUAL);
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
            String key = json.optString("key", "");
            String fp = ServerLink.normalize(json.optString("fp", ""));

            // "urls" holds the secure addresses; "url" is the plain home-network address, kept for
            // the browser version and servers too old to offer HTTPS. The key would travel
            // unencrypted on it, so it is used only when there is nothing else.
            List<String> urls = new ArrayList<>();
            JSONArray arr = json.optJSONArray("urls");
            for (int i = 0; arr != null && i < arr.length(); i++) urls.add(arr.getString(i));
            String plain = json.optString("url", "");
            if (urls.isEmpty() && !plain.isEmpty()) urls.add(plain);

            if (urls.isEmpty() || key.isEmpty()) {
                showError(getString(R.string.invalid_qr));
                return;
            }
            verifyAndLaunch(urls, key, fp, btnScanQr);

        } catch (Exception e) {
            showError(getString(R.string.invalid_qr));
        }
    }

    private void manualConnect() {
        String text = inputServerUrl.getText() != null ? inputServerUrl.getText().toString().trim() : "";
        String key = inputApiKey.getText() != null ? inputApiKey.getText().toString().trim() : "";

        if (text.isEmpty() || key.isEmpty()) {
            showError("Please enter both the server address and API key.");
            return;
        }

        // A secure address carries the certificate fingerprint after '#'
        String url = text, fp = "";
        int hash = text.indexOf('#');
        if (hash >= 0) {
            url = text.substring(0, hash);
            fp = ServerLink.normalize(text.substring(hash + 1));
        }
        while (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        if (!url.contains("://")) url = "http://" + url;

        if (url.startsWith("https://") && fp.isEmpty()) {
            showError(getString(R.string.fingerprint_needed));
            return;
        }

        verifyAndLaunch(Collections.singletonList(url), key, fp, btnConnect);
    }

    /** `trigger` is the button the user pressed, or null for the silent check at startup. */
    private void verifyAndLaunch(List<String> urls, String apiKey, String fp, MaterialButton trigger) {
        if (trigger != null) setBusy(trigger, true);

        executor.execute(() -> {
            ServerLink.Reached first = ServerLink.firstReachable(this, urls, apiKey, fp);
            if (first == null) {
                // The internet provider may have changed the home's address: on the home Wi-Fi the
                // router can say what it is now
                List<String> fresh = RouterLookup.newAddresses(this, urls);
                if (!fresh.isEmpty()) first = ServerLink.firstReachable(this, fresh, apiKey, fp);
            }
            final ServerLink.Reached reached = first;
            if (reached != null) {
                // Whatever went wrong while it was out of reach goes to the computer now
                android.content.Context app = getApplicationContext();
                new Thread(() -> ConnectionProblems.send(app, reached.url, apiKey, fp),
                        "send-problems").start();
            }

            runOnUiThread(() -> {
                if (isDestroyed()) return;
                if (reached != null) {
                    // The server reports its current addresses, so a changed home IP is picked up
                    // the next time the phone reaches it by any route.
                    List<String> known = new ArrayList<>(reached.urls.isEmpty() ? urls : reached.urls);
                    if (!known.contains(reached.url)) known.add(reached.url);

                    getSharedPreferences(ServerLink.PREFS, MODE_PRIVATE)
                            .edit()
                            .putString(ServerLink.KEY_SERVER, reached.url)
                            .putString(ServerLink.KEY_URLS, new JSONArray(known).toString())
                            .putString(ServerLink.KEY_API, apiKey)
                            .putString(ServerLink.KEY_FP, fp)
                            .apply();

                    Intent intent = new Intent(this, WebViewActivity.class);
                    intent.putExtra("server_url", reached.url);
                    intent.putExtra("api_key", apiKey);
                    intent.putExtra("cert_fp", fp);
                    startActivity(intent);
                    finish();
                } else if (trigger == null) {
                    showPairScreen();
                    showMode(Mode.UNREACHABLE);
                } else {
                    setBusy(trigger, false);
                    showError(getString(trigger == btnScanQr ? R.string.qr_connect_failed
                            : trigger == btnRetry ? R.string.still_unreachable
                            : R.string.connection_failed));
                }
            });
        });
    }

    private void setBusy(MaterialButton trigger, boolean busy) {
        for (MaterialButton b : new MaterialButton[]{btnScanQr, btnConnect, btnRetry, btnForget, btnToggleMode}) {
            b.setEnabled(!busy);
        }
        if (busy) {
            busyLabel = trigger.getText();
            trigger.setText(R.string.connecting);
            txtError.setVisibility(View.GONE);
        } else {
            trigger.setText(busyLabel);
        }
    }

    private void confirmForget() {
        ServerLink.confirmForget(this, () -> {
            savedUrls = Collections.emptyList();
            savedKey = "";
            savedFp = "";
            showMode(Mode.QR);
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
