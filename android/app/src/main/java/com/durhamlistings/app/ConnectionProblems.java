package com.durhamlistings.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.TimeZone;
import java.util.UUID;

/**
 * Problems the app ran into while connecting, before the listings page is open (the page keeps
 * its own). A failure can't be reported while the computer is out of reach, so each one is kept
 * on the phone and sent once a connection works again. The computer writes them to
 * logs/phone-errors.log, in the same form as the page's reports.
 */
final class ConnectionProblems {

    private static final String KEY = "connection_problems";
    private static final int KEPT = 100;

    private ConnectionProblems() {}

    /** Keep one failed attempt to reach `url`. */
    static void add(Context context, String url, String kind, String message, long ms, String requestId) {
        try {
            JSONObject p = new JSONObject();
            p.put("id", UUID.randomUUID().toString());
            p.put("at", now());
            p.put("kind", kind);
            p.put("source", "connection screen");
            if (url != null) {
                URL u = new URL(url);
                p.put("server", u.getHost() + (u.getPort() > 0 ? ":" + u.getPort() : ""));
                if ("connect".equals(kind)) {
                    p.put("method", "GET");
                    p.put("path", "/api/key-check");
                }
            }
            p.put("message", message);
            if (ms >= 0) p.put("ms", ms);
            if (requestId != null) p.put("request_id", requestId);
            Network active = connectivity(context).getActiveNetwork();
            p.put("online", active != null);
            p.put("network", network(context));
            synchronized (ConnectionProblems.class) {
                JSONArray list = load(context);
                list.put(p);
                while (list.length() > KEPT) list.remove(0);
                save(context, list);
            }
        } catch (Exception ignored) {
            // a report that can't be made is not worth failing the connection over
        }
    }

    /** Send the kept problems to the server that just answered. Call off the main thread. */
    static void send(Context context, String serverUrl, String apiKey, String fingerprint) {
        JSONArray pending;
        synchronized (ConnectionProblems.class) {
            pending = load(context);
        }
        if (pending.length() == 0) return;
        HttpURLConnection conn = null;
        try {
            JSONObject body = new JSONObject();
            body.put("errors", pending);
            body.put("agent", "Durham Listings Android app " + BuildConfig.VERSION_NAME);
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);

            conn = ServerLink.open(serverUrl + "/api/client-errors", fingerprint);
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setConnectTimeout(6000);
            conn.setReadTimeout(15000);
            conn.setRequestProperty("X-API-Key", apiKey);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream out = conn.getOutputStream()) {
                out.write(bytes);
            }
            if (conn.getResponseCode() != 200) return;

            Set<String> sent = new HashSet<>();
            for (int i = 0; i < pending.length(); i++) sent.add(pending.getJSONObject(i).optString("id"));
            synchronized (ConnectionProblems.class) {
                JSONArray now = load(context), left = new JSONArray();  // keep any added meanwhile
                for (int i = 0; i < now.length(); i++) {
                    JSONObject p = now.getJSONObject(i);
                    if (!sent.contains(p.optString("id"))) left.put(p);
                }
                save(context, left);
            }
        } catch (Exception ignored) {
            // still out of reach: they stay kept for next time
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /** How the phone is connected, with its own addresses, e.g. "wifi 192.168.2.45". */
    static String network(Context context) {
        try {
            ConnectivityManager cm = connectivity(context);
            Network active = cm.getActiveNetwork();
            if (active == null) return "no network";
            List<String> parts = new ArrayList<>();
            NetworkCapabilities caps = cm.getNetworkCapabilities(active);
            if (caps != null) {
                if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) parts.add("vpn");
                if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) parts.add("wifi");
                if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) parts.add("mobile data");
                if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) parts.add("ethernet");
            }
            // With a VPN on, the Wi-Fi or mobile network under it is listed too
            for (Network n : cm.getAllNetworks()) {
                LinkProperties lp = cm.getLinkProperties(n);
                NetworkCapabilities c = cm.getNetworkCapabilities(n);
                if (lp == null || c == null) continue;
                String what = c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ? "wifi"
                        : c.hasTransport(NetworkCapabilities.TRANSPORT_VPN) ? "vpn"
                        : c.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) ? "mobile" : "other";
                for (LinkAddress la : lp.getLinkAddresses()) {
                    if (la.getAddress() instanceof Inet4Address) {
                        parts.add(what + " " + la.getAddress().getHostAddress());
                    }
                }
            }
            return parts.isEmpty() ? "unknown" : String.join(", ", parts);
        } catch (Exception e) {
            return "unknown";
        }
    }

    private static ConnectivityManager connectivity(Context context) {
        return (ConnectivityManager) context.getApplicationContext().getSystemService(Context.CONNECTIVITY_SERVICE);
    }

    private static String now() {
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        return f.format(new Date());
    }

    private static SharedPreferences prefs(Context context) {
        return context.getApplicationContext().getSharedPreferences(ServerLink.PREFS, Context.MODE_PRIVATE);
    }

    private static JSONArray load(Context context) {
        try {
            return new JSONArray(prefs(context).getString(KEY, "[]"));
        } catch (Exception e) {
            return new JSONArray();
        }
    }

    private static void save(Context context, JSONArray list) {
        prefs(context).edit().putString(KEY, list.toString()).apply();
    }
}
