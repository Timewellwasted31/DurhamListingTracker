package com.durhamlistings.app;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInstaller;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.widget.ProgressBar;
import android.widget.Toast;

import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;

import org.json.JSONObject;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.security.MessageDigest;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Updates the app from the paired computer, which offers the newest build it has. The APK comes
 * over the same pinned connection as everything else, is checked against the SHA-256 the server
 * reported, and goes to Android's installer, which asks the user and accepts it only when it is
 * signed with the same key as the installed app.
 */
final class AppUpdater {

    private static final String KEY_DECLINED = "declined_update"; // version the user put off
    private static final String RELEASE_PATH = "/api/app/android";

    private static final class Release {
        final long code;
        final String name;
        final long size;
        final String sha256;

        Release(JSONObject json) throws Exception {
            code = json.getLong("version_code");
            name = json.getString("version_name");
            size = json.getLong("size");
            sha256 = json.getString("sha256");
        }
    }

    private final AppCompatActivity activity;
    private final String serverUrl;
    private final String apiKey;
    private final String fingerprint;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private Release waitingForPermission;
    private volatile boolean cancelled;

    AppUpdater(AppCompatActivity activity, String serverUrl, String apiKey, String fingerprint) {
        this.activity = activity;
        this.serverUrl = serverUrl;
        this.apiKey = apiKey;
        this.fingerprint = fingerprint;
    }

    /** Asks the server for its build. A check the user asked for always reports back. */
    void check(boolean userAsked) {
        worker.execute(() -> {
            Release release;
            try {
                release = fetchRelease();
            } catch (Exception e) {
                if (userAsked) toast(activity.getString(R.string.update_check_failed));
                return;
            }
            activity.runOnUiThread(() -> offer(release, userAsked));
        });
    }

    /** Call from the activity's onResume: picks up an update waiting on the install setting. */
    void onResume() {
        Release release = waitingForPermission;
        if (release != null && canInstall()) {
            waitingForPermission = null;
            download(release);
        }
    }

    void close() {
        cancelled = true;
        worker.shutdownNow();
    }

    private Release fetchRelease() throws Exception {
        HttpURLConnection conn = ServerLink.get(serverUrl + RELEASE_PATH, apiKey, fingerprint);
        try {
            int code = conn.getResponseCode();
            if (code == 404) return null; // the computer has no phone build to offer
            if (code != 200) throw new IOException("HTTP " + code);
            return new Release(ServerLink.readJson(conn));
        } finally {
            conn.disconnect();
        }
    }

    private void offer(Release release, boolean userAsked) {
        if (activity.isFinishing()) return;
        if (release == null || release.code <= BuildConfig.VERSION_CODE) {
            if (userAsked) {
                toast(release == null ? activity.getString(R.string.update_none)
                        : activity.getString(R.string.update_latest, BuildConfig.VERSION_NAME));
            }
            return;
        }
        SharedPreferences prefs = activity.getSharedPreferences(ServerLink.PREFS, Context.MODE_PRIVATE);
        if (!userAsked && prefs.getLong(KEY_DECLINED, 0) == release.code) return;

        String megabytes = String.format(java.util.Locale.CANADA, "%.1f", release.size / 1048576.0);
        new AlertDialog.Builder(activity)
                .setTitle(R.string.update_title)
                .setMessage(activity.getString(R.string.update_message,
                        release.name, BuildConfig.VERSION_NAME, megabytes))
                .setNegativeButton(R.string.update_later,
                        (d, w) -> prefs.edit().putLong(KEY_DECLINED, release.code).apply())
                .setPositiveButton(R.string.update_now, (d, w) -> install(release))
                .show();
    }

    private boolean canInstall() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.O
                || activity.getPackageManager().canRequestPackageInstalls();
    }

    private void install(Release release) {
        if (canInstall()) {
            download(release);
            return;
        }
        // Android asks once for permission to install from this app; onResume carries on
        waitingForPermission = release;
        new AlertDialog.Builder(activity)
                .setTitle(R.string.update_allow_title)
                .setMessage(R.string.update_allow_message)
                .setNegativeButton(android.R.string.cancel, (d, w) -> waitingForPermission = null)
                .setPositiveButton(R.string.update_open_settings, (d, w) -> activity.startActivity(
                        new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                Uri.parse("package:" + activity.getPackageName()))))
                .show();
    }

    private void download(Release release) {
        cancelled = false;
        ProgressBar bar = new ProgressBar(activity, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100);
        int pad = Math.round(24 * activity.getResources().getDisplayMetrics().density);
        bar.setPadding(pad, pad / 2, pad, 0);
        AlertDialog progress = new AlertDialog.Builder(activity)
                .setTitle(R.string.update_downloading)
                .setView(bar)
                .setCancelable(false)
                .setNegativeButton(android.R.string.cancel, (d, w) -> cancelled = true)
                .show();

        worker.execute(() -> {
            int failure = writeToInstaller(release, bar);
            activity.runOnUiThread(() -> {
                progress.dismiss();
                if (failure != 0 && !cancelled) toast(activity.getString(failure));
            });
        });
    }

    /** Streams the APK into an installer session and commits it. Returns 0, or a message id. */
    private int writeToInstaller(Release release, ProgressBar bar) {
        PackageInstaller installer = activity.getPackageManager().getPackageInstaller();
        int sessionId;
        try {
            PackageInstaller.SessionParams params =
                    new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
            params.setAppPackageName(activity.getPackageName());
            params.setSize(release.size);
            sessionId = installer.createSession(params);
        } catch (IOException e) {
            return R.string.update_download_failed;
        }

        try (PackageInstaller.Session session = installer.openSession(sessionId)) {
            HttpURLConnection conn = ServerLink.get(serverUrl + RELEASE_PATH + "/apk", apiKey, fingerprint);
            MessageDigest sha = MessageDigest.getInstance("SHA-256");
            try (InputStream in = conn.getInputStream();
                 OutputStream out = session.openWrite("base.apk", 0, release.size)) {
                byte[] buf = new byte[64 * 1024];
                long done = 0;
                int shown = -1;
                for (int n; (n = in.read(buf)) > 0; ) {
                    if (cancelled) throw new IOException("cancelled");
                    out.write(buf, 0, n);
                    sha.update(buf, 0, n);
                    done += n;
                    int percent = (int) (done * 100 / release.size);
                    if (percent != shown) {
                        shown = percent;
                        activity.runOnUiThread(() -> bar.setProgress(percent));
                    }
                }
                session.fsync(out);
            } finally {
                conn.disconnect();
            }
            if (!ServerLink.hex(sha.digest()).equals(release.sha256)) {
                installer.abandonSession(sessionId);
                return R.string.update_damaged;
            }
            session.commit(InstallResultReceiver.statusSender(activity, sessionId));
            return 0;
        } catch (Exception e) {
            try {
                installer.abandonSession(sessionId);
            } catch (Exception ignored) {
                // already committed or gone
            }
            return R.string.update_download_failed;
        }
    }

    private void toast(String text) {
        activity.runOnUiThread(() -> Toast.makeText(activity, text, Toast.LENGTH_LONG).show());
    }
}
