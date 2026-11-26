# Windows Firewall rules that let the phone app reach Durham Listing Tracker.
#
# Run as administrator. The installer runs it after installing (and with -Remove when
# uninstalling); it can also be run by hand:
#   powershell -NoProfile -ExecutionPolicy Bypass -File firewall.ps1 -Exe "<path>\DurhamListingTracker.exe"
#
# The app serves the phone on TCP 8000 (home network, pairing) and 8443 (encrypted, the one the
# router forwards). The rules allow it:
#   - on the home network (Windows' Private and Domain network types), and
#   - from devices on this computer's own Mobile hotspot, which Windows always numbers
#     192.168.137.x and treats as a Public network. That is all it allows on Public networks:
#     the app stays closed on cafe Wi-Fi and the like.
# Windows adds its own rules for a program the first time it listens; one that blocks it on
# Public networks would override the hotspot rule (a block always wins), so those are removed.

param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [switch]$Remove,
    [string]$Log = ""
)

$ErrorActionPreference = "Stop"
if ($Log) { Start-Transcript -Path $Log -Force | Out-Null }

$HomeRule = "Durham Listing Tracker - phone on the home network"
$HotspotRule = "Durham Listing Tracker - phone on this computer's hotspot"
$HotspotRange = "192.168.137.0/24"
$Ports = @("8000", "8443")
$Exe = [System.IO.Path]::GetFullPath($Exe)

function Rules-ForExe([string]$action) {
    Get-NetFirewallRule -Direction Inbound -Action $action -ErrorAction SilentlyContinue | Where-Object {
        (($_ | Get-NetFirewallApplicationFilter).Program -eq $Exe)
    }
}

try {
    # Our own rules: replaced each time, so running this again changes nothing
    foreach ($name in @($HomeRule, $HotspotRule)) {
        Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }

    if ($Remove) {
        "Removed the phone rules for $Exe"
    } else {
        # Windows' own rules that block the app's connections on Public networks
        foreach ($r in Rules-ForExe "Block") {
            $tcp = ($r | Get-NetFirewallPortFilter).Protocol -in @("TCP", "Any")
            if ($tcp -and ($r.Profile -eq "Any" -or $r.Profile.ToString() -match "Public")) {
                "Removing Windows' block rule '$($r.DisplayName)' ($($r.Profile))"
                $r | Remove-NetFirewallRule
            }
        }

        # An older rule allowed port 8000 on every network type; keep it to the home network
        foreach ($r in Get-NetFirewallRule -DisplayName "Durham Listing Tracker API" -ErrorAction SilentlyContinue) {
            "Limiting '$($r.DisplayName)' to home networks"
            $r | Set-NetFirewallRule -Profile Domain, Private
        }

        New-NetFirewallRule -DisplayName $HomeRule -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort $Ports -Program $Exe -Profile Domain, Private | Out-Null
        New-NetFirewallRule -DisplayName $HotspotRule -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort $Ports -Program $Exe -Profile Public -RemoteAddress $HotspotRange | Out-Null
        "Allowed $Exe on TCP $($Ports -join ', ') from the home network and from $HotspotRange"
    }
    "DONE"
} catch {
    "FAILED: $_"
    exit 1
} finally {
    if ($Log) { Stop-Transcript | Out-Null }
}
