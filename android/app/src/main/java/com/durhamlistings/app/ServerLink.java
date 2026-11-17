package com.durhamlistings.app;

import android.app.Activity;
import android.content.Context;
import android.net.http.SslCertificate;
import android.os.Build;
import android.os.Bundle;
import android.webkit.WebStorage;

import androidx.appcompat.app.AlertDialog;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.security.cert.Certificate;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * Connections to the paired server. Its certificate is self-signed, so an HTTPS connection is
 * trusted only when the certificate's SHA-256 fingerprint equals the one learned at pairing.
 */
final class ServerLink {

    static final String PREFS = "durham_listings";
    static final String KEY_SERVER = "server_url";
    static final String KEY_URLS = "server_urls";
    static final String KEY_API = "api_key";
    static final String KEY_FP = "cert_fp";

    /** A server address that answered, plus the addresses the server says it has now. */
    static final class Reached {
        final String url;
        final List<String> urls;

        Reached(String url, List<String> urls) {
            this.url = url;
            this.urls = urls;
        }
    }

    private ServerLink() {}

    /** Tries every address at once and returns the first that accepts the key, or null. */
    static Reached firstReachable(List<String> urls, String apiKey, String fingerprint) {
        if (urls.isEmpty()) return null;
        List<Callable<Reached>> attempts = new ArrayList<>();
        for (String url : urls) {
            attempts.add(() -> check(url, apiKey, fingerprint));
        }
        ExecutorService pool = Executors.newFixedThreadPool(attempts.size());
        try {
            return pool.invokeAny(attempts, 10, TimeUnit.SECONDS);
        } catch (Exception e) {
            return null;
        } finally {
            pool.shutdownNow();
        }
    }

    private static Reached check(String url, String apiKey, String fingerprint) throws Exception {
        HttpURLConnection conn = open(url + "/api/key-check", fingerprint);
        try {
            conn.setRequestProperty("X-API-Key", apiKey);
            conn.setConnectTimeout(6000);
            conn.setReadTimeout(6000);
            if (conn.getResponseCode() != 200) throw new Exception("HTTP " + conn.getResponseCode());

            List<String> current = new ArrayList<>();
            try (InputStream in = conn.getInputStream()) {
                ByteArrayOutputStream body = new ByteArrayOutputStream();
                byte[] buf = new byte[4096];
                for (int n; (n = in.read(buf)) > 0; ) body.write(buf, 0, n);
                JSONArray arr = new JSONObject(body.toString("UTF-8")).optJSONArray("urls");
                for (int i = 0; arr != null && i < arr.length(); i++) current.add(arr.getString(i));
            }
            return new Reached(url, current);
        } finally {
            conn.disconnect();
        }
    }

    // One factory per pinned certificate: connections, and TLS sessions, are only reused
    // between requests that share a factory.
    private static String factoryFp;
    private static SSLSocketFactory factory;

    private static synchronized SSLSocketFactory pinnedFactory(String fingerprint) throws Exception {
        if (factory == null || !fingerprint.equals(factoryFp)) {
            SSLContext ctx = SSLContext.getInstance("TLS");
            ctx.init(null, new TrustManager[]{new PinnedTrustManager(fingerprint)}, null);
            factory = ctx.getSocketFactory();
            factoryFp = fingerprint;
        }
        return factory;
    }

    static HttpURLConnection open(String url, String fingerprint) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        if (conn instanceof HttpsURLConnection && fingerprint != null && !fingerprint.isEmpty()) {
            HttpsURLConnection https = (HttpsURLConnection) conn;
            https.setSSLSocketFactory(pinnedFactory(fingerprint));
            // The server is addressed by IP; its identity is the pinned certificate, not a name.
            https.setHostnameVerifier((host, session) -> {
                try {
                    Certificate[] chain = session.getPeerCertificates();
                    return chain.length > 0 && matches(chain[0].getEncoded(), fingerprint);
                } catch (Exception e) {
                    return false;
                }
            });
        }
        return conn;
    }

    static boolean matches(byte[] certDer, String fingerprint) {
        if (certDer == null || fingerprint == null || fingerprint.isEmpty()) return false;
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(certDer);
            StringBuilder hex = new StringBuilder();
            for (byte b : digest) hex.append(String.format("%02x", b));
            return MessageDigest.isEqual(
                    hex.toString().getBytes("US-ASCII"),
                    normalize(fingerprint).getBytes("US-ASCII"));
        } catch (Exception e) {
            return false;
        }
    }

    static boolean matches(SslCertificate cert, String fingerprint) {
        if (cert == null) return false;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                X509Certificate x509 = cert.getX509Certificate();
                return x509 != null && matches(x509.getEncoded(), fingerprint);
            }
            Bundle state = SslCertificate.saveState(cert);
            return state != null && matches(state.getByteArray("x509-certificate"), fingerprint);
        } catch (Exception e) {
            return false;
        }
    }

    /** Forgets the paired server, including what its page stored in the web view. */
    static void forget(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply();
        WebStorage.getInstance().deleteAllData();
    }

    static void confirmForget(Activity activity, Runnable onForgotten) {
        new AlertDialog.Builder(activity)
                .setTitle(R.string.disconnect_title)
                .setMessage(R.string.disconnect_message)
                .setNegativeButton(android.R.string.cancel, null)
                .setPositiveButton(R.string.disconnect, (d, w) -> {
                    forget(activity);
                    onForgotten.run();
                })
                .show();
    }

    static String normalize(String fingerprint) {
        return fingerprint == null ? "" : fingerprint.replace(":", "").trim().toLowerCase();
    }

    private static final class PinnedTrustManager implements X509TrustManager {
        private final String fingerprint;

        PinnedTrustManager(String fingerprint) {
            this.fingerprint = fingerprint;
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String authType)
                throws CertificateException {
            throw new CertificateException("Client certificates are not used");
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String authType)
                throws CertificateException {
            if (chain == null || chain.length == 0 || !matches(chain[0].getEncoded(), fingerprint)) {
                throw new CertificateException("Server certificate does not match the paired one");
            }
        }

        @Override
        public X509Certificate[] getAcceptedIssuers() {
            return new X509Certificate[0];
        }
    }
}
