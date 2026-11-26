package com.durhamlistings.app;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.wifi.WifiManager;
import android.os.SystemClock;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Finds the home's current internet address by asking the home router itself, the same way the
 * computer asks it to forward the port (UPnP). Internet providers change that address from time
 * to time, and the phone, which only knows the old one, loses the computer. On the home Wi-Fi the
 * router can tell it the new one, with no outside service involved.
 */
final class RouterLookup {

    private static final String SSDP_HOST = "239.255.255.250";
    private static final int SSDP_PORT = 1900;
    private static final String[] SEARCH_TARGETS = {
            "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
            "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
            "upnp:rootdevice",
    };
    private static final String[] WAN_SERVICES = {
            "urn:schemas-upnp-org:service:WANIPConnection:2",
            "urn:schemas-upnp-org:service:WANIPConnection:1",
            "urn:schemas-upnp-org:service:WANPPPConnection:1",
    };
    private static final int SEARCH_MS = 3000;
    private static final int HTTP_TIMEOUT_MS = 4000;

    private RouterLookup() {}

    /**
     * Addresses to try when none of the saved ones answer: each saved internet address with the
     * router's current address in its place (same port). Empty when the phone isn't on Wi-Fi, no
     * router answers, or the address hasn't changed. What happened is kept for the reports.
     */
    static List<String> newAddresses(Context context, List<String> saved) {
        List<String> out = new ArrayList<>();
        long started = SystemClock.elapsedRealtime();
        String ip;
        try {
            ip = externalAddress(context);
        } catch (Exception e) {
            ConnectionProblems.add(context, null, "router", "asking the home router for the internet "
                    + "address failed: " + ServerLink.describe(e), SystemClock.elapsedRealtime() - started, null);
            return out;
        }
        if (ip == null) {
            ConnectionProblems.add(context, null, "router", "not on Wi-Fi, or no router answered",
                    SystemClock.elapsedRealtime() - started, null);
            return out;
        }
        Set<String> candidates = new LinkedHashSet<>();
        boolean hadPublic = false;
        for (String url : saved) {
            try {
                URL u = new URL(url);
                if (!"https".equals(u.getProtocol()) || !isPublic(u.getHost())) continue;
                hadPublic = true;
                if (!u.getHost().equals(ip)) {
                    candidates.add("https://" + ip + (u.getPort() > 0 ? ":" + u.getPort() : ""));
                }
            } catch (Exception ignored) {
                // not an address this can stand in for
            }
        }
        if (!hadPublic) candidates.add("https://" + ip + ":8443");  // the computer's usual port
        out.addAll(candidates);
        ConnectionProblems.add(context, null, "router", "the home router says the internet address is "
                + ip + (out.isEmpty() ? " (unchanged)" : "; trying it"),
                SystemClock.elapsedRealtime() - started, null);
        return out;
    }

    /** The router's internet address, or null if the phone isn't on Wi-Fi or no router answers. */
    static String externalAddress(Context context) throws Exception {
        Network wifi = wifiNetwork(context);
        if (wifi == null) return null;
        WifiManager wm = (WifiManager) context.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        WifiManager.MulticastLock lock = wm == null ? null : wm.createMulticastLock("router-lookup");
        if (lock != null) {
            lock.setReferenceCounted(false);
            lock.acquire();
        }
        try {
            for (String location : search(wifi)) {
                try {
                    String control = controlUrl(wifi, location);
                    if (control == null) continue;
                    String ip = askExternalIp(wifi, control);
                    if (ip != null && isPublic(ip)) return ip;
                } catch (Exception ignored) {
                    // try the next device that answered
                }
            }
            return null;
        } finally {
            if (lock != null) lock.release();
        }
    }

    /** The Wi-Fi network itself, so a VPN on the phone doesn't carry the search elsewhere. */
    private static Network wifiNetwork(Context context) {
        ConnectivityManager cm = (ConnectivityManager)
                context.getApplicationContext().getSystemService(Context.CONNECTIVITY_SERVICE);
        for (Network n : cm.getAllNetworks()) {
            NetworkCapabilities c = cm.getNetworkCapabilities(n);
            if (c != null && c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                    && !c.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) {
                return n;
            }
        }
        return null;
    }

    /** Description addresses of the devices on the home network that answer a router search. */
    private static List<String> search(Network wifi) throws Exception {
        Set<String> locations = new LinkedHashSet<>();
        try (DatagramSocket socket = new DatagramSocket()) {
            wifi.bindSocket(socket);
            InetAddress group = InetAddress.getByName(SSDP_HOST);
            for (String target : SEARCH_TARGETS) {
                byte[] msg = ("M-SEARCH * HTTP/1.1\r\n"
                        + "HOST: " + SSDP_HOST + ":" + SSDP_PORT + "\r\n"
                        + "MAN: \"ssdp:discover\"\r\n"
                        + "MX: 2\r\n"
                        + "ST: " + target + "\r\n\r\n").getBytes(StandardCharsets.US_ASCII);
                socket.send(new DatagramPacket(msg, msg.length, group, SSDP_PORT));
            }
            long deadline = SystemClock.elapsedRealtime() + SEARCH_MS;
            byte[] buf = new byte[4096];
            while (true) {
                long left = deadline - SystemClock.elapsedRealtime();
                if (left <= 0) break;
                socket.setSoTimeout((int) left);
                DatagramPacket reply = new DatagramPacket(buf, buf.length);
                try {
                    socket.receive(reply);
                } catch (SocketTimeoutException e) {
                    break;
                }
                String text = new String(reply.getData(), 0, reply.getLength(), StandardCharsets.US_ASCII);
                Matcher m = Pattern.compile("(?im)^location:\\s*(\\S+)").matcher(text);
                if (!m.find()) continue;
                String location = m.group(1);
                // Only a device on the home network itself: never an address elsewhere
                InetAddress host = InetAddress.getByName(new URL(location).getHost());
                if (host.isSiteLocalAddress()) locations.add(location);
            }
        }
        return new ArrayList<>(locations);
    }

    /** The router's control address for its internet connection, from its description. */
    private static String controlUrl(Network wifi, String location) throws Exception {
        String xml = fetch(wifi, location, null, null);
        String base = location;
        Matcher b = Pattern.compile("<URLBase>\\s*([^<]+?)\\s*</URLBase>").matcher(xml);
        if (b.find()) base = b.group(1);
        Matcher services = Pattern.compile("<service>(.*?)</service>", Pattern.DOTALL).matcher(xml);
        List<String[]> found = new ArrayList<>();
        while (services.find()) {
            String block = services.group(1);
            Matcher type = Pattern.compile("<serviceType>\\s*([^<]+?)\\s*</serviceType>").matcher(block);
            Matcher control = Pattern.compile("<controlURL>\\s*([^<]+?)\\s*</controlURL>").matcher(block);
            if (type.find() && control.find()) found.add(new String[]{type.group(1), control.group(1)});
        }
        for (String wanted : WAN_SERVICES) {
            for (String[] s : found) {
                if (s[0].equals(wanted)) return new URL(new URL(base), s[1]) + "#" + wanted;
            }
        }
        return null;
    }

    private static String askExternalIp(Network wifi, String controlAndType) throws Exception {
        int hash = controlAndType.lastIndexOf('#');
        String control = controlAndType.substring(0, hash), type = controlAndType.substring(hash + 1);
        if (!InetAddress.getByName(new URL(control).getHost()).isSiteLocalAddress()) return null;
        String body = "<?xml version=\"1.0\"?>"
                + "<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\" "
                + "s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\"><s:Body>"
                + "<u:GetExternalIPAddress xmlns:u=\"" + type + "\"></u:GetExternalIPAddress>"
                + "</s:Body></s:Envelope>";
        String reply = fetch(wifi, control, body, "\"" + type + "#GetExternalIPAddress\"");
        Matcher m = Pattern.compile("<NewExternalIPAddress>\\s*([0-9.]+)\\s*</NewExternalIPAddress>").matcher(reply);
        return m.find() ? m.group(1) : null;
    }

    private static String fetch(Network wifi, String url, String soapBody, String soapAction) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) wifi.openConnection(new URL(url));
        try {
            conn.setConnectTimeout(HTTP_TIMEOUT_MS);
            conn.setReadTimeout(HTTP_TIMEOUT_MS);
            if (soapBody != null) {
                byte[] bytes = soapBody.getBytes(StandardCharsets.UTF_8);
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "text/xml; charset=\"utf-8\"");
                conn.setRequestProperty("SOAPAction", soapAction);
                conn.setFixedLengthStreamingMode(bytes.length);
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(bytes);
                }
            }
            try (InputStream in = conn.getInputStream()) {
                ByteArrayOutputStream buf = new ByteArrayOutputStream();
                byte[] chunk = new byte[4096];
                for (int n; (n = in.read(chunk)) > 0 && buf.size() < 256 * 1024; ) buf.write(chunk, 0, n);
                return buf.toString("UTF-8");
            }
        } finally {
            conn.disconnect();
        }
    }

    /** A literal IPv4 internet address (not a home, carrier-shared, or loopback one). */
    static boolean isPublic(String host) {
        if (host == null || !host.matches("\\d{1,3}(\\.\\d{1,3}){3}")) return false;
        try {
            InetAddress a = InetAddress.getByName(host);
            if (!(a instanceof Inet4Address)) return false;
            byte[] b = a.getAddress();
            int first = b[0] & 0xff, second = b[1] & 0xff;
            boolean carrierShared = first == 100 && second >= 64 && second <= 127;  // 100.64.0.0/10
            return !(a.isSiteLocalAddress() || a.isLoopbackAddress() || a.isLinkLocalAddress()
                    || a.isAnyLocalAddress() || a.isMulticastAddress() || carrierShared || first == 0);
        } catch (Exception e) {
            return false;
        }
    }
}
