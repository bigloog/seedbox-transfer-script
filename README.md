# Seedbox Torrent Mover

Automatically moves completed torrents from a Deluge-based seedbox to an rTorrent-based seedbox when storage gets full.

## What It Does

This script monitors your fast Deluge seedbox and automatically:
1. **Checks disk usage** - Triggers when usage exceeds your threshold (default: 40%)
2. **Identifies old torrents** - Finds completed torrents 14+ days old
3. **Transfers files** - Copies files via rsync through your localhost
4. **Adds to rTorrent** - Continues seeding on your slow seedbox
5. **Cleans up** - Removes torrents and files from fast seedbox

### The Flow

```
Fast Seedbox (Deluge)  →  Your Localhost  →  Slow Seedbox (rTorrent)
    [New torrents]         [Temporary relay]      [Long-term seeding]
    [High speed]           [Bridge]               [Lots of storage]
    [Limited space]                               [Slower upload]
```

## Requirements

### Prerequisites
- Python 3.6+
- SSH access to both seedboxes
- SSH keys set up for passwordless login
- Both seedboxes accessible from localhost

### Python Packages
All required packages are in Python's standard library - no pip installs needed!

## Configuration

Edit `seedbox_mover_web.py` and update these sections:

### 1. Fast Seedbox (Deluge) Settings

```python
FAST_SEEDBOX = {
    'host': '192.168.0.0',                    # Your fast seedbox IP
    'web_url': 'https://user.seedbox.domain/deluge/',  # Deluge Web UI URL
    'password': 'YOUR_PASSWORD',                 # Deluge Web UI password
    'ssh_host': 'user@192.168.0.0',     # SSH connection string
    'data_path': '/home/YOUR_USERNAME/downloads/deluge',  # Where Deluge stores files
    'session_path': '/home/YOUR_USERNAME/.config/deluge/state',  # .torrent file location
    'storage_threshold': 40  # Trigger when disk usage exceeds this %
}
```

**How to find these values:**
- `web_url`: The URL you use to access Deluge in your browser
- `password`: Your Deluge Web UI password
- `ssh_host`: Run `ssh user@seedbox-ip` to test
- `data_path`: Check in Deluge settings or via SSH: `ls ~/downloads`
- `session_path`: Usually `~/.config/deluge/state/` - contains .torrent files

### 2. Slow Seedbox (rTorrent) Settings

```python
SLOW_SEEDBOX = {
    'host': 'hostname.seedboxhost.com',  # Your slow seedbox hostname
    'rpc_url': 'https://hostname.seedboxhost.com/YOUR_USERNAME/rutorrent/plugins/httprpc/action.php',
    'username': 'YOUR_USERNAME',                     # Your username
    'password': 'YOUR_PASSWORD',              # ruTorrent password
    'ssh_host': 'YOUR_USERNAME@hostname.seedboxhost.com',
    'data_path': '/home/YOUR_USERNAME/data/rTorrent',  # Where rTorrent stores files
}
```

**How to find these values:**
- `rpc_url`: Usually `https://your-seedbox/user-username/rutorrent/plugins/httprpc/action.php`
- `username` and `password`: Your seedbox login credentials
- `data_path`: Check rTorrent config at `~/.rtorrent.rc` for `directory.default.set`

### 3. Threshold Settings

```python
MIN_AGE_DAYS = 14    # Only move torrents at least this old
DRY_RUN = True       # Set to False to actually move files
```

**Customization:**
- Change `MIN_AGE_DAYS` to 7, 21, 30, etc.
- Change `storage_threshold` to 50, 80, 90, etc.
- Keep `DRY_RUN = True` for testing, change to `False` when ready

## Setup

### 1. Set Up SSH Keys (Required!)

The script needs passwordless SSH access to both seedboxes:

```bash
# Generate SSH key if you don't have one
ssh-keygen -t ed25519

# Copy to fast seedbox
ssh-copy-id YOUR_USERNAME@192.168.0.0

# Copy to slow seedbox
ssh-copy-id YOUR_USERNAME@hostname.seedboxhost.com

# Test both work without password
ssh YOUR_USERNAME@192.168.0.0 "echo OK"
ssh YOUR_USERNAME@hostname.seedboxhost.com "echo OK"
```

**If your SSH key has a passphrase:**
```bash
# Start ssh-agent
eval "$(ssh-agent -s)"

# Add your key (enter passphrase once)
ssh-add ~/.ssh/id_ed25519
```

### 2. Test Run (Dry Run Mode)

With `DRY_RUN = True`, the script shows what it would do without making changes:

```bash
python3 seedbox_mover_web.py
```

**Expected output:**
```
2026-01-01 12:00:00 - INFO - Disk usage = 90%
2026-01-01 12:00:00 - WARNING - Fast seedbox usage (92%) exceeds threshold (90%)
2026-01-01 12:00:00 - INFO - Found 51 torrents eligible for moving
2026-01-01 12:00:00 - WARNING - DRY RUN MODE - No actual changes will be made
2026-01-01 12:00:00 - INFO -   Would move: Some.Show.S01E01 (4.2 GB, 21.3 days old, ratio: 3.45)
```

### 3. Real Run

Once you're happy with the dry run:

1. Edit `seedbox_mover_web.py`
2. Change: `DRY_RUN = False`
3. Run: `python3 seedbox_mover_web.py`

The script will:
- Move torrents oldest first
- Stop when disk usage drops below threshold
- Log all actions to `~/seedbox_mover.log`

## Usage

### Manual Run
```bash
python3 seedbox_mover_web.py
```

### Automated Run (Cron)

Run automatically every 6 hours:

```bash
# Edit crontab
crontab -e

# Add this line (runs at 5 minutes past every 6th hour)
5 */6 * * * /usr/bin/python3 ~/seedbox-mover/seedbox_mover_web.py >> ~/seedbox_mover_cron.log 2>&1
```

**Other schedules:**
```bash
# Every hour
5 * * * * /usr/bin/python3 ~/seedbox-mover/seedbox_mover_web.py >> ~/seedbox_mover_cron.log 2>&1

# Once per day at 3am
0 3 * * * /usr/bin/python3 ~/seedbox-mover/seedbox_mover_web.py >> ~/seedbox_mover_cron.log 2>&1

# Twice per day (3am and 3pm)
0 3,15 * * * /usr/bin/python3 ~/seedbox-mover/seedbox_mover_web.py >> ~/seedbox_mover_cron.log 2>&1
```

## Monitoring

### View Logs

```bash
# Real-time log watching
tail -f ~/seedbox_mover.log

# Last 50 lines
tail -50 ~/seedbox_mover.log

# Search for errors
grep ERROR ~/seedbox_mover.log

# Check cron output
tail -f ~/seedbox_mover_cron.log
```

### What the Script Logs

- ✅ Successful transfers with file sizes
- ⚠️ Warnings when storage threshold exceeded
- ❌ Errors with detailed messages
- 📊 Disk usage before and after
- 📝 List of torrents moved

## How It Works

### The Transfer Process

1. **Check Storage**
   - Connects via SSH to fast seedbox
   - Checks disk usage of download directory
   - Exits if below threshold

2. **Find Candidates**
   - Connects to Deluge Web UI
   - Gets list of all torrents
   - Filters: completed + 14+ days old
   - Sorts by oldest first

3. **Transfer Files** (for each torrent)
   - Downloads .torrent file from fast seedbox
   - **Pull**: rsync from fast seedbox → localhost (`/tmp/seedbox_transfer/`)
   - **Push**: rsync from localhost → slow seedbox
   - Cleans up temp files on localhost

4. **Update Torrents**
   - Adds torrent to rTorrent on slow seedbox
   - Removes torrent from Deluge on fast seedbox
   - Deletes files from fast seedbox

5. **Check & Repeat**
   - Checks disk usage again
   - If still over threshold, moves next torrent
   - Stops when below threshold

### Why Localhost as Intermediary?

Rsync can't transfer directly between two remote servers. Your localhost acts as a bridge:
- Fast seedbox → Localhost: Uses your download bandwidth
- Localhost → Slow seedbox: Uses your upload bandwidth

Files are temporarily stored in `/tmp/seedbox_transfer/` and automatically deleted after transfer.

## Troubleshooting

### "Failed to connect to Deluge Web UI"

**Check:**
- Is the `web_url` correct? (copy from browser)
- Is the `password` correct?
- Can you access Deluge Web UI in browser?

**Test:**
```bash
curl -k https://hostname.seedboxhost.com/deluge/
```

### "SSH connection failed" or "Enter passphrase"

**Fix:**
```bash
# Set up SSH keys
ssh-copy-id user@seedbox

# Or use ssh-agent for keys with passphrases
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

### "Rsync failed"

**Check:**
- SSH keys are set up for both seedboxes
- Paths exist on both seedboxes
- You have enough free space on localhost

**Test manually:**
```bash
rsync -avz user@fast-seedbox:/path/to/file /tmp/
rsync -avz /tmp/file user@slow-seedbox:/path/to/dest/
```

### "Failed to remove torrent from Deluge"

This can happen if:
- Torrent is actively uploading (wait and try again)
- Deluge has old API version (script handles this automatically)
- You can manually remove these torrents from Deluge Web UI

### "No space left on device"

If your localhost runs out of space during transfer:
- Free up space in `/tmp/`
- Torrents are too large for your localhost
- Consider using a different temp directory with more space

Edit the script and change `/tmp/seedbox_transfer` to a path with more space.

## Customization

### Change Age Threshold

Move torrents after 7 days instead of 14:
```python
MIN_AGE_DAYS = 7
```

### Change Storage Threshold

Trigger at 80% instead of 90%:
```python
'storage_threshold': 80
```

### Add Ratio Filter

Only move torrents with ratio > 2.0:

Find the `get_torrents_to_move()` function and add:
```python
# After the age check, add:
if torrent.get('ratio', 0) < 2.0:
    continue
```

### Change Temp Directory

Use a different location for temporary files:

Find the `rsync_files()` function and change:
```python
temp_dir = "/tmp/seedbox_transfer"
```
to:
```python
temp_dir = "/home/yourusername/temp_transfers"
```

## Safety Features

- ✅ **Dry run mode** - Test before running
- ✅ **Oldest first** - Moves longest-seeded torrents first
- ✅ **Automatic cleanup** - Removes temp files
- ✅ **Detailed logging** - Track everything
- ✅ **Stops when done** - Doesn't move more than needed
- ✅ **Age check** - Won't move fresh torrents
- ✅ **Completion check** - Only moves finished downloads

## File Structure

```
~/seedbox-mover/
├── seedbox_mover_web.py    # Main script
└── seedbox_mover.log        # Auto-generated log

/tmp/
└── seedbox_transfer/        # Temporary files (auto-cleanup)
```

## Support

If something isn't working:

1. Check the logs: `tail -50 ~/seedbox_mover.log`
2. Run in dry-run mode to see what would happen
3. Test SSH connections manually
4. Verify all paths exist on seedboxes
5. Check you have the latest version of the script

## Version Info

- **Script**: seedbox_mover_web.py
- **Python**: 3.6+
- **Deluge**: 1.3.15+ (Web UI API)
- **rTorrent**: Any version with XML-RPC enabled

---

**Pro tip**: Set up monitoring with the cron job and check logs weekly to ensure everything runs smoothly!