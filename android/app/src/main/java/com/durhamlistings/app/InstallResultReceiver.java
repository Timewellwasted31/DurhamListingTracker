package com.durhamlistings.app;

import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentSender;
import android.content.pm.PackageInstaller;
import android.os.Build;
import android.widget.Toast;

/** Hears back from Android's installer about an update AppUpdater handed it. */
public class InstallResultReceiver extends BroadcastReceiver {

    /** Where the installer reports. Mutable so it can add the status; explicit, so only we get it. */
    static IntentSender statusSender(Context context, int sessionId) {
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) flags |= PendingIntent.FLAG_MUTABLE;
        Intent intent = new Intent(context, InstallResultReceiver.class);
        return PendingIntent.getBroadcast(context, sessionId, intent, flags).getIntentSender();
    }

    @Override
    @SuppressWarnings("deprecation") // the untyped getParcelableExtra, used only before Android 13
    public void onReceive(Context context, Intent intent) {
        int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        switch (status) {
            case PackageInstaller.STATUS_PENDING_USER_ACTION:
                // Android's own confirmation screen; the user approves the update there
                Intent confirm = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                        ? intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent.class)
                        : intent.getParcelableExtra(Intent.EXTRA_INTENT);
                if (confirm != null) {
                    context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                }
                break;
            case PackageInstaller.STATUS_SUCCESS:
            case PackageInstaller.STATUS_FAILURE_ABORTED: // the user said no
                break;
            case PackageInstaller.STATUS_FAILURE_CONFLICT:
            case PackageInstaller.STATUS_FAILURE_INCOMPATIBLE:
                Toast.makeText(context, R.string.update_signature, Toast.LENGTH_LONG).show();
                break;
            default:
                String detail = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
                Toast.makeText(context, context.getString(R.string.update_failed,
                        detail != null ? detail : String.valueOf(status)), Toast.LENGTH_LONG).show();
        }
    }
}
